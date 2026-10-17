"""Named sections of the full backup, and the hooks every restore runs.

Store, Notifications and Access add their data to ``meshloom-backup-v2``
through ``register_backup_section`` without touching the envelope; Links &
Files and Access regenerate their instance identities through
``register_on_restore``. The rules are in ``docs/dev/backup.md``:

- a section missing from a file leaves its data untouched;
- a section marked ``secrets_omitted`` never clears a stored secret (its
  restore function keeps the stored value where the file has none);
- a section the running version does not know is ignored, with a log line;
- an identity seed (``app_settings.*_seed``) never reaches a file, and every
  restore (JSON, or a SQLite snapshot put back in place) runs the hooks once.
"""

import json
import logging
import re
from collections.abc import Awaitable, Callable, Iterable
from dataclasses import dataclass
from graphlib import CycleError, TopologicalSorter
from typing import Any, Literal

import aiosqlite

from app.models import BackupSection

logger = logging.getLogger(__name__)

RestoreSource = Literal["json", "sqlite"]
SectionExport = Callable[[], Awaitable[BackupSection | None]]
SectionRestore = Callable[[BackupSection], Awaitable[list[str]]]
RestoreHook = Callable[[RestoreSource], Awaitable[None]]

SECTION_NAME = re.compile(r"^[a-z][a-z0-9_]{0,47}$")
# Written into every SQLite snapshot download; found at startup, it means the
# live database is a snapshot put back in place.
SQLITE_SNAPSHOT_MARKER = "meshloom_backup_snapshot"
# Seeds shorter than this are not looked for (a test value, never a real seed).
_MIN_SEED_LENGTH = 16


@dataclass(frozen=True)
class _Section:
    name: str
    export: SectionExport
    restore: SectionRestore
    requires: tuple[str, ...]


_sections: dict[str, _Section] = {}
_restore_hooks: dict[str, RestoreHook] = {}


def _restore_order(sections: dict[str, _Section]) -> list[str]:
    """Each section after the ones it requires."""
    sorter: TopologicalSorter[str] = TopologicalSorter()
    for spec in sections.values():
        sorter.add(spec.name, *(name for name in spec.requires if name in sections))
    return list(sorter.static_order())


def register_backup_section(
    name: str,
    export_fn: SectionExport,
    restore_fn: SectionRestore,
    *,
    requires: Iterable[str] = (),
) -> None:
    """Add a named section to the full backup.

    ``export_fn()`` returns the section, or None to leave it out of the file.
    ``restore_fn(section)`` writes it back and returns warnings (``code`` or
    ``code:param``, translated by the restore UI). ``requires`` names sections
    that must be restored, in the same restore, before this one: when one of
    them is missing from the file or failed, this one is skipped.
    """
    if not SECTION_NAME.match(name):
        raise ValueError(f"invalid backup section name: {name!r}")
    if name in _sections:
        raise ValueError(f"backup section {name!r} is already registered")
    candidate = {
        **_sections,
        name: _Section(name, export_fn, restore_fn, tuple(dict.fromkeys(requires))),
    }
    try:
        _restore_order(candidate)
    except CycleError:
        raise ValueError(f"backup section {name!r} closes a requires cycle") from None
    _sections[name] = candidate[name]


def unregister_backup_section(name: str) -> None:
    _sections.pop(name, None)


def registered_backup_sections() -> list[str]:
    return list(_sections)


async def export_sections() -> tuple[dict[str, BackupSection], list[str]]:
    """Every registered section that has something to write, and the warnings
    of the ones that failed (left out of the file)."""
    exported: dict[str, BackupSection] = {}
    warnings: list[str] = []
    for spec in list(_sections.values()):
        try:
            section = await spec.export()
        except Exception:
            logger.exception("Backup export: section %s failed, left out of the file", spec.name)
            warnings.append(f"section_export_failed:{spec.name}")
            continue
        if section is not None:
            exported[spec.name] = section
    return exported, warnings


async def restore_sections(sections: dict[str, BackupSection]) -> list[str]:
    """Restore the file's sections this version knows, dependencies first."""
    for name in sections:
        if name not in _sections:
            logger.info("Backup restore: section %r is not known to this version, ignored", name)
    registered = dict(_sections)
    warnings: list[str] = []
    restored: set[str] = set()
    for name in _restore_order(registered):
        section = sections.get(name)
        if section is None:
            continue  # not in the file: its data stays as it is
        spec = registered[name]
        missing = [required for required in spec.requires if required not in restored]
        if missing:
            logger.warning(
                "Backup restore: section %s skipped, it requires %s", name, ", ".join(missing)
            )
            warnings.append(f"section_skipped:{name}")
            continue
        try:
            warnings.extend(await spec.restore(section))
        except Exception:
            logger.exception("Backup restore: section %s failed", name)
            warnings.append(f"section_failed:{name}")
            continue
        restored.add(name)
    return warnings


# -- Identity seeds -------------------------------------------------------------


async def refuse_identity_seeds(document: Any) -> None:
    """Raise when the export holds an identity seed, wherever it came from.

    The seeds of the per-service instance identities (share, Remote) are never
    exported, even though the radio private key is: a restore regenerates them.
    """
    from app.repository import AppSettingsRepository
    from app.services.backup import BackupError

    seeds = [
        seed
        for seed in await AppSettingsRepository.get_identity_seed_values()
        if len(seed) >= _MIN_SEED_LENGTH
    ]
    if not seeds:
        return
    text = json.dumps(document)
    if any(seed in text for seed in seeds):
        logger.error("Backup export refused: it would hold an instance identity seed")
        raise BackupError(
            "backup_identity_seed_leak",
            status=500,
            message="This backup would hold an instance identity seed: export refused",
        )


# -- Restore hooks --------------------------------------------------------------


def register_on_restore(name: str, hook: RestoreHook) -> None:
    """Run ``hook(source)`` once after every restore (``json`` or ``sqlite``)."""
    if name in _restore_hooks:
        raise ValueError(f"on_restore hook {name!r} is already registered")
    _restore_hooks[name] = hook


def unregister_on_restore(name: str) -> None:
    _restore_hooks.pop(name, None)


async def run_on_restore_hooks(source: RestoreSource) -> None:
    """One failing hook is logged and does not stop the others."""
    for name, hook in list(_restore_hooks.items()):
        try:
            await hook(source)
        except Exception:
            logger.exception("on_restore hook %s failed (%s restore)", name, source)


async def mark_sqlite_snapshot(conn: aiosqlite.Connection, exported_at: int) -> None:
    """Mark a snapshot copy (never the live database)."""
    await conn.execute(
        f"CREATE TABLE IF NOT EXISTS {SQLITE_SNAPSHOT_MARKER} (exported_at INTEGER NOT NULL)"
    )
    await conn.execute(
        f"INSERT INTO {SQLITE_SNAPSHOT_MARKER} (exported_at) VALUES (?)", (exported_at,)
    )


async def run_sqlite_restore_hooks(database) -> bool:
    """At startup: a live database carrying the snapshot marker is a restored
    snapshot. Run the hooks, then drop the marker. True when they ran."""
    async with database.readonly() as conn:
        async with conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?",
            (SQLITE_SNAPSHOT_MARKER,),
        ) as cursor:
            found = await cursor.fetchone() is not None
    if not found:
        return False
    logger.info("The database is a restored snapshot: running the restore hooks")
    await run_on_restore_hooks("sqlite")
    async with database.tx() as conn:
        await conn.execute(f"DROP TABLE IF EXISTS {SQLITE_SNAPSHOT_MARKER}")
    return True
