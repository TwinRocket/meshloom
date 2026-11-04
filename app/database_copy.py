"""The copy of the database taken before the first migration, and the two refusals.

Migrations are one way: an older release that opens a migrated database does not
refuse it and misreads what changed. So ``Database.connect`` calls
``prepare_for_migrations()`` before it opens the database for writing:

- **Schema newer than the code**: ``SchemaTooNewError``, nothing touched.
- **Pending migrations on a populated database**: a consistent copy beside it
  (SQLite backup API, ``secrets.key`` included when there is one).
- **Copy impossible** (no disk space, I/O error): ``CopyFailedError``, the start is
  refused and the database stays as it was. A refused start is fixed by freeing
  space; a migrated database without a copy has no way back.

The file names are a contract with the return to stable in ``install.sh``
(MSHL-466)::

    <db>.schema<source schema>.before-<version doing the upgrade>.db
    <db>.schema<source schema>.before-<version doing the upgrade>.secrets.key

A database does not record the release that last wrote it, so the source is named
by its schema: a release reads the copy when its highest migration is at least
that number (87 for all of 4.18.x).

At most two copies stay: the one with the lowest source schema, which is the way
back to the oldest release, and the newest. The others are removed before a new
copy is written, only once the disk check passed. A copy with the same source
schema is superseded: the database still at that schema is the fresher of the two.

A marker file (``<db>.migration-in-progress``, naming the copy) stays for the length
of the migrations. A start that finds it continues an interrupted run: the database
is partly migrated, the copy already taken is the good one, and none is taken.

Copies are ``0600``, and carry every secret of the database, unscrubbed. The full
backup is built from database sections and never reads the data folder; the Home
Assistant add-on excludes them through ``backup_exclude`` (``meshloom/config.yaml``).
"""

import logging
import os
import re
import shutil
import sqlite3
import time
from contextlib import closing
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger(__name__)

MIB = 1024 * 1024
# On top of the copy, room for the migrations themselves to run.
MIN_HEADROOM_BYTES = 64 * MIB
HEADROOM_DIVISOR = 4
_BACKUP_STEP_PAGES = 16384
DOCS_URL = "https://meshloom.app/en/docs/deep/install-paths/#going-back-to-an-older-version"


class StartupRefused(RuntimeError):
    """A start refused on purpose. The message is the whole story: no traceback."""


class SchemaTooNewError(StartupRefused):
    """The database was migrated by a newer release than this one."""


class CopyFailedError(StartupRefused):
    """The copy before the migrations could not be made."""


@dataclass(frozen=True)
class DatabaseCopy:
    path: Path
    source_schema: int
    mtime: float


def copy_name(db_name: str, source_schema: int, version: str) -> str:
    safe_version = re.sub(r"[^0-9A-Za-z.+-]", "_", version) or "unknown"
    return f"{db_name}.schema{source_schema}.before-{safe_version}.db"


def key_copy_path(copy: Path) -> Path:
    return copy.with_name(copy.name.removesuffix(".db") + ".secrets.key")


def marker_path(db_path: Path) -> Path:
    return db_path.with_name(f"{db_path.name}.migration-in-progress")


def _temporary_path(db_path: Path) -> Path:
    return db_path.with_name(f"{db_path.name}.copy-in-progress")


def list_copies(db_path: Path) -> list[DatabaseCopy]:
    """The finished copies beside ``db_path``, in no particular order."""
    pattern = re.compile(re.escape(db_path.name) + r"\.schema(\d+)\.before-.+\.db")
    copies = []
    try:
        entries = list(os.scandir(db_path.parent))
    except OSError:
        return []
    for entry in entries:
        match = pattern.fullmatch(entry.name)
        if match is None or entry.is_symlink() or not entry.is_file():
            continue
        copies.append(DatabaseCopy(Path(entry.path), int(match[1]), entry.stat().st_mtime))
    return copies


def _remove_copy(copy: Path) -> None:
    for path in (copy, key_copy_path(copy)):
        path.unlink(missing_ok=True)


def _remove_temporary(temporary: Path) -> None:
    for suffix in ("", "-wal", "-shm", "-journal", ".key"):
        temporary.with_name(temporary.name + suffix).unlink(missing_ok=True)


def _way_back(copies: list[DatabaseCopy]) -> DatabaseCopy | None:
    """The copy retention never removes: lowest source schema, freshest among equals."""
    return min(copies, key=lambda c: (c.source_schema, -c.mtime), default=None)


def _fsync_path(path: Path) -> None:
    """Best effort: SQLite already synced the copy, and Windows cannot do this."""
    try:
        fd = os.open(path, os.O_RDONLY)
    except OSError:
        return
    try:
        os.fsync(fd)
    except OSError:
        pass
    finally:
        os.close(fd)


def _write_private(path: Path, data: bytes) -> None:
    """A new file holding ``data``, mode ``0600`` from its first byte."""
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        os.write(fd, data)
        os.fsync(fd)
    finally:
        os.close(fd)


def _read_only_connection(db_path: Path) -> sqlite3.Connection:
    return sqlite3.connect(f"{db_path.resolve().as_uri()}?mode=ro", uri=True, timeout=30)


def _read_state(db_path: Path) -> tuple[int, int, int]:
    """Schema, number of schema objects and size in bytes, leaving the folder as found.

    A read-only connection to a WAL database cannot remove the ``-wal`` and ``-shm``
    it had to create, so they go here.
    """
    side_files = [db_path.with_name(db_path.name + suffix) for suffix in ("-wal", "-shm")]
    absent = [path for path in side_files if not path.exists()]
    with closing(_read_only_connection(db_path)) as conn:
        schema = conn.execute("PRAGMA user_version").fetchone()[0]
        objects = conn.execute("SELECT count(*) FROM sqlite_master").fetchone()[0]
        size = conn.execute("PRAGMA page_count").fetchone()[0]
        size *= conn.execute("PRAGMA page_size").fetchone()[0]
    for path in absent:
        path.unlink(missing_ok=True)
    return schema, objects, size


def _run_backup(db_path: Path, temporary: Path, source_schema: int, total_bytes: int) -> None:
    # The file exists first so that its mode is never wider than 0600.
    os.close(os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600))

    def progress(_status: int, remaining: int, total: int) -> None:
        if total and remaining:
            logger.info("Copying the database: %d%%", 100 * (total - remaining) // total)

    with closing(_read_only_connection(db_path)) as source:
        with closing(sqlite3.connect(temporary)) as target:
            pages = _BACKUP_STEP_PAGES if total_bytes > 256 * MIB else -1
            source.backup(target, pages=pages, progress=progress)
            copied = target.execute("PRAGMA user_version").fetchone()[0]
            tables = target.execute("SELECT count(*) FROM sqlite_master").fetchone()[0]
    if copied != source_schema or not tables:
        raise sqlite3.DatabaseError(f"the copy reads schema {copied} with {tables} objects")
    _fsync_path(temporary)


def _make_copy(
    db_path: Path, key_file: Path, source_schema: int, latest: int, version: str, total_bytes: int
) -> Path:
    copy = db_path.with_name(copy_name(db_path.name, source_schema, version))
    temporary = _temporary_path(db_path)
    logger.info(
        "Copying the database (%d MB) to %s before migrating it from schema %d to %d",
        total_bytes // MIB,
        copy,
        source_schema,
        latest,
    )
    started = time.monotonic()
    temporary_key = temporary.with_name(temporary.name + ".key")
    try:
        _remove_temporary(temporary)
        _run_backup(db_path, temporary, source_schema, total_bytes)
        # The key lands first: a copy that exists under its final name is complete.
        key = key_copy_path(copy)
        if key_file.is_file():
            _write_private(temporary_key, key_file.read_bytes())
            os.replace(temporary_key, key)
        else:
            key.unlink(missing_ok=True)
        os.replace(temporary, copy)
        _fsync_path(copy.parent)
    except (sqlite3.Error, OSError) as exc:
        try:
            _remove_temporary(temporary)
        except OSError:
            pass
        raise CopyFailedError(
            f"The database could not be copied before migrating it from schema {source_schema} "
            f"to {latest}: {exc}. Nothing was migrated and Meshloom will not start, because "
            f"migrating is one way. Fix the cause and start again. {DOCS_URL}"
        ) from exc
    finally:
        # A leftover -wal or -shm next to a temporary that was renamed.
        for suffix in ("-wal", "-shm", "-journal"):
            temporary.with_name(temporary.name + suffix).unlink(missing_ok=True)
    logger.info("Database copied to %s in %.1f s", copy, time.monotonic() - started)
    return copy


def _check_disk_space(
    db_path: Path, total_bytes: int, removable: list[DatabaseCopy], way_back: DatabaseCopy | None
) -> None:
    needed = total_bytes + max(MIN_HEADROOM_BYTES, total_bytes // HEADROOM_DIVISOR)
    try:
        free = shutil.disk_usage(db_path.parent).free
        free += sum(c.path.stat().st_size for c in removable)
    except OSError:
        return  # Unknown: the copy itself reports a full disk.
    if free >= needed:
        return
    hint = ""
    if way_back is not None:
        hint = (
            f" The older copy {way_back.path.name} can be deleted if you no longer need to go "
            "back to that version."
        )
    raise CopyFailedError(
        f"Not enough free disk space to copy the database before migrating it: {needed // MIB} MB "
        f"needed, {free // MIB} MB free in {db_path.parent}. Nothing was changed and Meshloom "
        f"will not start, because migrating is one way. Free some space and start again.{hint} "
        f"{DOCS_URL}"
    )


def prepare_for_migrations(db_path_text: str) -> Path | None:
    """Refuse a newer schema, copy a populated database with pending migrations.

    Returns the path of the copy made, if any. Runs before the database is opened
    for writing; raises a ``StartupRefused`` to stop the start.
    """
    if db_path_text == ":memory:":
        return None
    db_path = Path(db_path_text)
    if not db_path.is_file() or db_path.stat().st_size == 0:
        return None

    from app.migrations import latest_known_version
    from app.secret_store import KEY_FILENAME
    from app.version_info import get_app_build_info

    latest = latest_known_version()
    source_schema, objects, total_bytes = _read_state(db_path)

    if source_schema > latest:
        raise SchemaTooNewError(
            f"This database is at schema {source_schema}, newer than this version of Meshloom "
            f"knows (schema {latest}). Nothing was changed. Run the version that last used it, "
            f"or put back the copy made before the upgrade. {DOCS_URL}"
        )
    if source_schema == latest or objects == 0:
        return None

    marker = marker_path(db_path)
    copies = list_copies(db_path)
    try:
        interrupted_copy = marker.read_text(encoding="utf-8").strip()
    except (OSError, UnicodeDecodeError):
        interrupted_copy = ""
    if any(copy.path.name == interrupted_copy for copy in copies):
        logger.warning(
            "An earlier migration did not finish (schema %d now): carrying on, the copy made "
            "before it is %s",
            source_schema,
            interrupted_copy,
        )
        return None

    way_back = _way_back(copies)
    removable = [copy for copy in copies if copy != way_back]
    _check_disk_space(db_path, total_bytes, removable, way_back)
    try:
        for old in removable:
            logger.info("Removing the older database copy %s", old.path.name)
            _remove_copy(old.path)
    except OSError as exc:
        logger.warning("An older database copy could not be removed: %s", exc)

    copy = _make_copy(
        db_path,
        db_path.with_name(KEY_FILENAME),
        source_schema,
        latest,
        get_app_build_info().version,
        total_bytes,
    )
    if way_back is not None and way_back.source_schema == source_schema and way_back.path != copy:
        _remove_copy(way_back.path)
    try:
        marker.unlink(missing_ok=True)
        _write_private(marker, copy.name.encode("utf-8"))
    except OSError as exc:
        raise CopyFailedError(
            f"The marker of the migration could not be written ({exc}). Nothing was migrated "
            f"and Meshloom will not start. Fix the cause and start again. {DOCS_URL}"
        ) from exc
    return copy


def migrations_finished(db_path_text: str) -> None:
    """The migrations ran to the end: the next start is not an interrupted one."""
    if db_path_text == ":memory:":
        return
    try:
        marker_path(Path(db_path_text)).unlink(missing_ok=True)
    except OSError as exc:
        logger.warning("The migration marker could not be removed: %s", exc)
