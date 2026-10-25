"""Named sections of the full backup, and the hooks every restore runs.

Store, Notifications and Access add their data to ``meshloom-backup-v2``
through ``register_backup_section`` without touching the envelope; Links &
Files and Access regenerate their instance identities through
``register_on_restore``. The rules are in ``docs/dev/backup.md``:

- a section missing from a file leaves its data untouched;
- a section marked ``secrets_omitted`` never clears a stored secret (its
  restore function keeps the stored value where the file has none);
- a section the running version does not know, or one written by a newer
  version of its owner, is not restored;
- each section is restored in its own transaction: a failure rolls it back;
  a restore function that keeps the current data on purpose raises
  ``SectionKept`` (its warnings are kept, nothing is written);
- a section registered with ``restore_with=<owner>`` (tokens of the accounts)
  runs in its owner's transaction, right after it, and only when the owner was
  written: never against data the owner kept, failed or skipped, and a
  failure of either rolls both back;
- an identity seed (``app_settings.*_seed``) never reaches a file, and every
  restore (JSON, or a SQLite snapshot put back in place) runs the hooks once;
- a name in ``NEVER_EXPORTED_SECTIONS`` (login sessions, pairing codes, the
  audit trail, the Remote identity) cannot be registered, and a file section
  with that name is ignored.
"""

import asyncio
import json
import logging
import re
from collections.abc import Awaitable, Callable, Iterable, Mapping
from dataclasses import dataclass
from graphlib import CycleError, TopologicalSorter
from typing import Any, Literal

import aiosqlite

from app.models import BackupSection, BackupSectionSummary

logger = logging.getLogger(__name__)

RestoreSource = Literal["json", "sqlite"]
SectionExport = Callable[[], Awaitable[BackupSection | None]]
SectionRestore = Callable[[BackupSection, aiosqlite.Connection], Awaitable[list[str]]]
RestoreHook = Callable[[RestoreSource], Awaitable[None]]

SECTION_NAME = re.compile(r"^[a-z][a-z0-9_]{0,47}$")
DESCRIPTION_LANGUAGES = ("en", "fr")
# A restore function holds the database lock: one that waits on it (a
# repository call, which takes db.tx() itself) would hang the restore.
SECTION_RESTORE_TIMEOUT = 60.0
# Written into every SQLite snapshot download; found at startup, it means the
# live database is a snapshot put back in place.
SQLITE_SNAPSHOT_MARKER = "meshloom_backup_snapshot"
# Seeds shorter than this are not looked for (a test value, never a real seed).
_MIN_SEED_LENGTH = 16
# Never in a file, whatever registers or writes them: registering one of these
# names raises, and a file section carrying one is ignored (Access, cadrage Q19).
NEVER_EXPORTED_SECTIONS = frozenset({"sessions", "pairing_codes", "auth_audit", "remote_identity"})


class SectionKept(Exception):
    """Raised by a restore function that keeps the current data on purpose
    (the accounts restore without a usable administrator). Nothing it wrote
    is committed, its ``warnings`` reach the result, and the sections that
    require it or restore with it are skipped."""

    def __init__(self, warnings: Iterable[str] = ()) -> None:
        super().__init__("section kept the current data")
        self.warnings = list(warnings)


@dataclass(frozen=True)
class _Section:
    name: str
    export: SectionExport
    restore: SectionRestore
    requires: tuple[str, ...]
    restore_with: str | None
    version: int
    sensitive: bool
    description: dict[str, str]


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
    description: Mapping[str, str],
    sensitive: bool,
    version: int = 1,
    requires: Iterable[str] = (),
    restore_with: str | None = None,
) -> None:
    """Add a named section to the full backup.

    ``export_fn()`` returns the section, or None to leave it out of the file.
    ``restore_fn(section, conn)`` writes it back through ``conn`` only, inside
    one transaction of its own, and returns warnings (``code`` or
    ``code:param``, translated by the restore UI). ``description`` is what the
    restore summary shows (``en`` and ``fr``); ``sensitive`` says the section
    holds secrets (hashes included). ``version`` is the newest ``data`` shape
    this code reads: a file section with a higher one is not restored.
    ``requires`` names sections that must be restored, in the same restore,
    before this one: when one of them is missing from the file or failed,
    this one is skipped. ``restore_with`` names the section whose rows this
    one belongs to (implies ``requires``): its restore runs in that section's
    transaction, right after it, and only when it was written (not kept with
    ``SectionKept``, failed, skipped or missing); a failure of either rolls
    both back.
    """
    if not SECTION_NAME.match(name):
        raise ValueError(f"invalid backup section name: {name!r}")
    if name in NEVER_EXPORTED_SECTIONS:
        raise ValueError(f"backup section {name!r} is never exported")
    if name in _sections:
        raise ValueError(f"backup section {name!r} is already registered")
    if version < 1:
        raise ValueError(f"backup section {name!r}: version starts at 1")
    wanted: list[str] = [*requires, *([restore_with] if restore_with else [])]
    required: tuple[str, ...] = tuple(dict.fromkeys(wanted))
    texts = {
        language: str(description.get(language) or "").strip() for language in DESCRIPTION_LANGUAGES
    }
    if not all(texts.values()):
        raise ValueError(f"backup section {name!r} needs an en and a fr description")
    candidate = {
        **_sections,
        name: _Section(
            name,
            export_fn,
            restore_fn,
            required,
            restore_with,
            version,
            bool(sensitive),
            texts,
        ),
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


async def export_sections(
    *, skip: Iterable[str] = ()
) -> tuple[dict[str, BackupSection], list[str]]:
    """Every registered section that has something to write, and the warnings
    of the ones that failed (left out of the file). The registration sets
    each section's ``version`` and ``sensitive``, whatever the function says.
    ``skip`` names sections the user left out: their export is not called."""
    left_out = set(skip)
    exported: dict[str, BackupSection] = {}
    warnings: list[str] = []
    for spec in list(_sections.values()):
        if spec.name in left_out:
            continue
        try:
            section = await spec.export()
        except Exception:
            logger.exception("Backup export: section %s failed, left out of the file", spec.name)
            warnings.append(f"section_export_failed:{spec.name}")
            continue
        if section is not None:
            exported[spec.name] = section.model_copy(
                update={"version": spec.version, "sensitive": spec.sensitive}
            )
    return exported, warnings


def sensitive_section_names(sections: Mapping[str, BackupSection]) -> list[str]:
    return [name for name, section in sections.items() if section.sensitive]


def summarize_sections(sections: Mapping[str, BackupSection]) -> list[BackupSectionSummary]:
    """What the restore summary shows for each section of a file."""
    summaries: list[BackupSectionSummary] = []
    for name, section in sections.items():
        spec = _sections.get(name)
        summaries.append(
            BackupSectionSummary(
                name=name,
                known=spec is not None,
                too_new=spec is not None and section.version > spec.version,
                # The registration is authoritative; an unknown section says it
                # itself (it is not restored anyway).
                sensitive=spec.sensitive if spec is not None else section.sensitive,
                description=dict(spec.description) if spec is not None else None,
            )
        )
    return summaries


def _too_new(name: str, spec: _Section, section: BackupSection) -> bool:
    if section.version <= spec.version:
        return False
    logger.warning(
        "Backup restore: section %s is version %d, this version reads up to %d: skipped",
        name,
        section.version,
        spec.version,
    )
    return True


async def _restore_one(
    spec: _Section, section: BackupSection, riders: list[tuple[_Section, BackupSection]]
) -> list[str]:
    """Restore ``spec`` and the sections that restore with it, in one transaction."""
    from app import database

    async with asyncio.timeout(SECTION_RESTORE_TIMEOUT):
        # db.tx() commits on success and rolls back on any exception (the
        # timeout and ``SectionKept`` included): nothing is left half written.
        async with database.db.tx() as conn:
            warnings = list(await spec.restore(section, conn))
            for rider, rider_section in riders:
                warnings.extend(await rider.restore(rider_section, conn))
            return warnings


async def restore_sections(sections: dict[str, BackupSection]) -> list[str]:
    """Restore the file's sections this version knows, dependencies first."""
    for name in sections:
        if name in NEVER_EXPORTED_SECTIONS:
            logger.info("Backup restore: section %r is never restored, ignored", name)
        elif name not in _sections:
            logger.info("Backup restore: section %r is not known to this version, ignored", name)
    registered = dict(_sections)
    warnings: list[str] = []
    restored: set[str] = set()
    # Sections that restore with an owner and were decided in its turn.
    handled: set[str] = set()
    for name in _restore_order(registered):
        section = sections.get(name)
        if section is None:
            continue  # not in the file: its data stays as it is
        spec = registered[name]
        if spec.restore_with is not None:
            if name not in handled:
                # Restored with its owner, which was not written.
                logger.warning(
                    "Backup restore: section %s skipped, %s was not restored",
                    name,
                    spec.restore_with,
                )
                warnings.append(f"section_skipped:{name}")
            continue
        if _too_new(name, spec, section):
            warnings.append(f"section_too_new:{name}")
            continue
        missing = [required for required in spec.requires if required not in restored]
        if missing:
            logger.warning(
                "Backup restore: section %s skipped, it requires %s", name, ", ".join(missing)
            )
            warnings.append(f"section_skipped:{name}")
            continue
        riders: list[tuple[_Section, BackupSection]] = []
        for rider in registered.values():
            rider_section = sections.get(rider.name)
            if rider.restore_with != name or rider_section is None:
                continue
            handled.add(rider.name)
            if _too_new(rider.name, rider, rider_section):
                warnings.append(f"section_too_new:{rider.name}")
            elif any(r not in restored and r != name for r in rider.requires):
                warnings.append(f"section_skipped:{rider.name}")
            else:
                riders.append((rider, rider_section))
        try:
            warnings.extend(await _restore_one(spec, section, riders))
        except SectionKept as kept:
            logger.info("Backup restore: section %s kept the current data", name)
            warnings.extend(kept.warnings)
            warnings.extend(f"section_skipped:{rider.name}" for rider, _ in riders)
            continue
        except Exception:
            logger.exception("Backup restore: section %s failed, rolled back", name)
            warnings.append(f"section_failed:{name}")
            warnings.extend(f"section_failed:{rider.name}" for rider, _ in riders)
            continue
        restored.add(name)
        restored.update(rider.name for rider, _ in riders)
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


# Secrets the full backup excludes that a database copy would otherwise carry
# (the BLE PIN, also inside the previous-transport snapshot). Every
# ``app_settings.*_seed`` column is cleared too, whatever its name.
SNAPSHOT_SCRUBBED_COLUMNS: tuple[str, ...] = ("radio_ble_pin", "radio_previous_transport")

# Consent flags a database copy resets to 0 (the full backup excludes them too).
SNAPSHOT_RESET_COLUMNS: tuple[str, ...] = (
    "share_enabled",
    "share_terms_version",
    "share_terms_accepted_at",
    "store_online",
)

# Local accounts secrets (docs/dev/auth.md): the password and TOTP secrets and
# the setup code hash are set to NULL, and the sessions, TOTP recovery codes
# and device pairing codes are deleted. The plugin
# config secrets (docs/dev/plugins.md) are deleted too: a restored instance asks
# for them again. A restored
# snapshot keeps the accounts but none can log in with a password: recovery
# goes through the admin reset (Access tickets).
SNAPSHOT_SCRUBBED_AUTH_COLUMNS: dict[str, tuple[str, ...]] = {
    "users": ("password_hash", "totp_secret"),
    "auth_settings": ("setup_code_hash",),
}
SNAPSHOT_EMPTIED_TABLES: tuple[str, ...] = (
    "sessions",
    "recovery_codes",
    "pairing_codes",
    "plugin_secrets",
)


async def prepare_sqlite_snapshot(conn: aiosqlite.Connection, exported_at: int) -> list[str]:
    """Scrub and mark a snapshot copy (never the live database). Returns the
    cleared ``app_settings`` columns, then the cleared ``table.column`` names
    and the emptied tables."""
    async with conn.execute("PRAGMA table_info(app_settings)") as cursor:
        columns = [row[1] for row in await cursor.fetchall()]
    cleared = [
        column
        for column in columns
        if column.endswith("_seed") or column in SNAPSHOT_SCRUBBED_COLUMNS
    ]
    if cleared:
        assignments = ", ".join(f'"{column}" = NULL' for column in cleared)
        await conn.execute(f"UPDATE app_settings SET {assignments}")
    # Consents never travel: a copy put back elsewhere must not enable an egress.
    for column in SNAPSHOT_RESET_COLUMNS:
        if column in columns:
            await conn.execute(f'UPDATE app_settings SET "{column}" = 0')
            cleared.append(column)
    async with conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'") as cursor:
        tables = {row[0] for row in await cursor.fetchall()}
    for table, table_columns in SNAPSHOT_SCRUBBED_AUTH_COLUMNS.items():
        if table not in tables:
            continue
        assignments = ", ".join(f'"{column}" = NULL' for column in table_columns)
        await conn.execute(f'UPDATE "{table}" SET {assignments}')
        cleared.extend(f"{table}.{column}" for column in table_columns)
    for table in SNAPSHOT_EMPTIED_TABLES:
        if table in tables:
            await conn.execute(f'DELETE FROM "{table}"')
            cleared.append(table)
    await conn.execute(
        f"CREATE TABLE IF NOT EXISTS {SQLITE_SNAPSHOT_MARKER} (exported_at INTEGER NOT NULL)"
    )
    await conn.execute(
        f"INSERT INTO {SQLITE_SNAPSHOT_MARKER} (exported_at) VALUES (?)", (exported_at,)
    )
    return cleared


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
