"""The copy made before migrating, and the refusals at startup (MSHL-460)."""

import asyncio
import hashlib
import os
import re
import shutil
import sqlite3
import stat
import subprocess
import sys
from pathlib import Path, PurePath

import pytest
import yaml

import app.database_copy as database_copy
import app.migrations as migrations
from app.database import Database
from app.database_copy import (
    CopyFailedError,
    SchemaTooNewError,
    copy_name,
    key_copy_path,
    list_copies,
    marker_path,
    prepare_for_migrations,
)

ROOT = Path(__file__).resolve().parents[1]
LATEST = migrations.latest_known_version()


def _make_db(path: Path, schema: int, rows: int = 3) -> None:
    conn = sqlite3.connect(path)
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("CREATE TABLE IF NOT EXISTS items (id INTEGER PRIMARY KEY, value TEXT)")
    conn.executemany("INSERT INTO items (value) VALUES (?)", [(f"row {i}",) for i in range(rows)])
    conn.execute(f"PRAGMA user_version = {schema}")
    conn.commit()
    conn.close()


def _rows(path: Path) -> list[str]:
    conn = sqlite3.connect(path)
    try:
        return [row[0] for row in conn.execute("SELECT value FROM items ORDER BY id")]
    finally:
        conn.close()


def _schema(path: Path) -> int:
    conn = sqlite3.connect(path)
    try:
        return conn.execute("PRAGMA user_version").fetchone()[0]
    finally:
        conn.close()


def _digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _names(directory: Path) -> set[str]:
    return {entry.name for entry in directory.iterdir()}


def _version(monkeypatch, version: str = "5.0.0") -> None:
    class Info:
        pass

    info = Info()
    info.version = version  # type: ignore[attr-defined]
    monkeypatch.setattr("app.version_info.get_app_build_info", lambda: info)


@pytest.fixture
def db_path(tmp_path, monkeypatch) -> Path:
    _version(monkeypatch)
    return tmp_path / "meshcore.db"


async def _connected(path: Path) -> Database:
    database = Database(str(path))
    await database.connect()
    return database


async def _fully_migrated_then_one_behind(path: Path) -> None:
    """A real database with exactly one migration pending."""
    database = await _connected(path)
    async with database.tx() as conn:
        await conn.execute("CREATE TABLE user_data (value TEXT)")
        await conn.execute("INSERT INTO user_data VALUES ('kept')")
    await database.disconnect()
    conn = sqlite3.connect(path)
    conn.execute(f"PRAGMA user_version = {LATEST - 1}")
    conn.commit()
    conn.close()


# ---------------------------------------------------------------- the real start path


@pytest.mark.asyncio
async def test_pending_migrations_on_a_populated_database_make_a_copy_first(db_path) -> None:
    await _fully_migrated_then_one_behind(db_path)
    (db_path.parent / "secrets.key").write_bytes(b"k" * 32)

    database = await _connected(db_path)
    await database.disconnect()

    copy = db_path.with_name(f"meshcore.db.schema{LATEST - 1}.before-5.0.0.db")
    assert _schema(copy) == LATEST - 1
    assert _schema(db_path) == LATEST
    conn = sqlite3.connect(copy)
    assert conn.execute("SELECT value FROM user_data").fetchall() == [("kept",)]
    assert conn.execute("PRAGMA integrity_check").fetchone() == ("ok",)
    conn.close()
    assert key_copy_path(copy).read_bytes() == b"k" * 32
    for path in (copy, key_copy_path(copy)):
        assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert not marker_path(db_path).exists()
    leftovers = [
        n for n in _names(db_path.parent) if "copy-in-progress" in n and not n.endswith(".lock")
    ]
    assert leftovers == []


@pytest.mark.asyncio
async def test_a_fresh_database_gets_no_copy(db_path) -> None:
    database = await _connected(db_path)
    await database.disconnect()
    assert list_copies(db_path) == []
    assert not marker_path(db_path).exists()


@pytest.mark.asyncio
async def test_no_pending_migration_makes_no_copy(db_path) -> None:
    await (await _connected(db_path)).disconnect()
    await (await _connected(db_path)).disconnect()
    assert list_copies(db_path) == []


@pytest.mark.asyncio
async def test_a_newer_schema_stops_the_start_before_touching_anything(db_path) -> None:
    _make_db(db_path, LATEST + 1)
    before = _digest(db_path)
    names = _names(db_path.parent)

    database = Database(str(db_path))
    with pytest.raises(SchemaTooNewError, match=rf"schema {LATEST + 1}.*schema {LATEST}"):
        await database.connect()

    assert _digest(db_path) == before
    assert _names(db_path.parent) == names
    assert database._connection is None


@pytest.mark.asyncio
async def test_a_failed_copy_stops_the_start_and_migrates_nothing(db_path, monkeypatch) -> None:
    await _fully_migrated_then_one_behind(db_path)

    def full_disk(*_args, **_kwargs) -> None:
        raise sqlite3.OperationalError("database or disk is full")

    monkeypatch.setattr(database_copy, "_run_backup", full_disk)
    with pytest.raises(CopyFailedError, match="disk is full"):
        await Database(str(db_path)).connect()

    assert _schema(db_path) == LATEST - 1
    assert list_copies(db_path) == []
    assert not marker_path(db_path).exists()
    assert [
        name
        for name in _names(db_path.parent)
        if "copy-in-progress" in name and not name.endswith(".lock")
    ] == []


@pytest.mark.asyncio
async def test_a_start_interrupted_past_the_copy_reuses_it(db_path, monkeypatch) -> None:
    await _fully_migrated_then_one_behind(db_path)
    conn = sqlite3.connect(db_path)
    conn.execute(f"PRAGMA user_version = {LATEST - 2}")
    conn.commit()
    conn.close()
    real = migrations.run_migrations

    async def failing(conn) -> int:
        await migrations.set_version(conn, LATEST - 1)
        await conn.commit()
        raise RuntimeError("migration failed")

    monkeypatch.setattr(migrations, "run_migrations", failing)
    with pytest.raises(RuntimeError, match="migration failed"):
        database = Database(str(db_path))
        try:
            await database.connect()
        finally:
            await database.disconnect()
    copies = list_copies(db_path)
    assert len(copies) == 1
    assert marker_path(db_path).read_text() == copies[0].path.name
    taken = copies[0].path.stat().st_mtime_ns

    monkeypatch.setattr(migrations, "run_migrations", real)
    await (await _connected(db_path)).disconnect()

    assert [c.path.stat().st_mtime_ns for c in list_copies(db_path)] == [taken]
    assert not marker_path(db_path).exists()
    assert _schema(db_path) == LATEST


@pytest.mark.asyncio
async def test_a_migration_failing_at_its_first_step_copies_again_without_piling_up(
    db_path, monkeypatch
) -> None:
    await _fully_migrated_then_one_behind(db_path)

    async def failing(conn) -> int:
        raise RuntimeError("migration failed")

    monkeypatch.setattr(migrations, "run_migrations", failing)
    for _ in range(3):
        database = Database(str(db_path))
        with pytest.raises(RuntimeError, match="migration failed"):
            try:
                await database.connect()
            finally:
                await database.disconnect()
        assert len(list_copies(db_path)) == 1
    assert _schema(list_copies(db_path)[0].path) == LATEST - 1


def test_going_back_after_an_interrupted_migration_then_upgrading_copies_again(db_path) -> None:
    """The marker survives a return: what was received since must get into a new copy."""
    _make_db(db_path, 87)
    first = prepare_for_migrations(str(db_path))
    conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA user_version = 100")  # Killed in the middle of the migrations.
    conn.commit()
    conn.close()
    assert prepare_for_migrations(str(db_path)) is None  # A plain restart carries on.

    shutil.copy2(first, db_path)  # Back to 4.18, the marker left behind.
    for suffix in ("-wal", "-shm"):
        Path(f"{db_path}{suffix}").unlink(missing_ok=True)
    conn = sqlite3.connect(db_path)
    conn.execute("INSERT INTO items (value) VALUES ('received after going back')")
    conn.commit()
    conn.close()

    again = prepare_for_migrations(str(db_path))
    assert again == first
    assert "received after going back" in _rows(again)
    assert marker_path(db_path).read_text() == again.name


def test_an_interrupted_first_start_takes_no_copy(db_path) -> None:
    assert prepare_for_migrations(str(db_path)) is None
    assert marker_path(db_path).read_text() == "fresh"
    conn = sqlite3.connect(db_path)
    conn.execute("CREATE TABLE messages (id INTEGER PRIMARY KEY)")
    conn.execute("PRAGMA user_version = 99")
    conn.commit()
    conn.close()
    assert prepare_for_migrations(str(db_path)) is None
    assert list_copies(db_path) == []


def test_a_database_put_over_an_interrupted_first_start_is_copied(db_path) -> None:
    assert prepare_for_migrations(str(db_path)) is None
    conn = sqlite3.connect(db_path)
    conn.execute("CREATE TABLE messages (id INTEGER PRIMARY KEY)")
    conn.execute("INSERT INTO messages DEFAULT VALUES")
    conn.execute("PRAGMA user_version = 87")
    conn.commit()
    conn.close()
    assert prepare_for_migrations(str(db_path)) is not None


def test_reading_the_state_creates_and_deletes_no_side_file(db_path) -> None:
    _make_db(db_path, LATEST)
    names = _names(db_path.parent)

    def no_unlink(self, missing_ok=False):
        raise AssertionError(f"{self} removed: it may be another process's")

    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(Path, "unlink", no_unlink)
        assert prepare_for_migrations(str(db_path)) is None
    assert _names(db_path.parent) == names
    # A -wal already there is read, and left alone.
    writer = sqlite3.connect(db_path)
    writer.execute("PRAGMA wal_autocheckpoint = 0")
    writer.execute("INSERT INTO items (value) VALUES ('in the wal')")
    writer.execute(f"PRAGMA user_version = {LATEST + 1}")
    writer.commit()
    wal = Path(f"{db_path}-wal").read_bytes()
    with pytest.raises(SchemaTooNewError):
        prepare_for_migrations(str(db_path))
    assert Path(f"{db_path}-wal").read_bytes() == wal
    writer.close()


@pytest.mark.skipif(database_copy.fcntl is None, reason="no flock")
def test_a_second_process_is_refused_and_leaves_the_temporary_alone(db_path) -> None:
    _make_db(db_path, 87)
    temporary = db_path.with_name("meshcore.db.copy-in-progress")
    temporary.write_bytes(b"being written")
    holder = os.open(db_path.with_name("meshcore.db.copy-in-progress.lock"), os.O_CREAT | os.O_RDWR)
    try:
        database_copy.fcntl.flock(holder, database_copy.fcntl.LOCK_EX)
        with pytest.raises(CopyFailedError, match="Another Meshloom process"):
            prepare_for_migrations(str(db_path))
        assert temporary.read_bytes() == b"being written"
    finally:
        os.close(holder)
    assert prepare_for_migrations(str(db_path)) is not None


def test_a_partial_write_is_finished(tmp_path, monkeypatch) -> None:
    real = os.write
    monkeypatch.setattr(database_copy.os, "write", lambda fd, data: real(fd, bytes(data)[:1]))
    path = tmp_path / "file"
    database_copy._write_private(path, b"abcd")
    assert path.read_bytes() == b"abcd"


# ---------------------------------------------------------------- prepare_for_migrations


def test_the_copy_holds_what_is_still_in_the_wal(db_path) -> None:
    _make_db(db_path, LATEST - 5)
    writer = sqlite3.connect(db_path)
    writer.execute("PRAGMA wal_autocheckpoint = 0")
    writer.execute("INSERT INTO items (value) VALUES ('only in the wal')")
    writer.commit()
    assert Path(f"{db_path}-wal").stat().st_size > 0
    copy = prepare_for_migrations(str(db_path))
    writer.close()
    assert copy is not None
    assert "only in the wal" in _rows(copy)


def test_no_key_file_means_no_key_copy(db_path) -> None:
    _make_db(db_path, 87)
    copy = prepare_for_migrations(str(db_path))
    assert copy == db_path.with_name("meshcore.db.schema87.before-5.0.0.db")
    assert not key_copy_path(copy).exists()


def test_an_old_database_with_no_table_is_not_populated(db_path) -> None:
    db_path.write_bytes(b"")
    assert prepare_for_migrations(str(db_path)) is None
    sqlite3.connect(db_path).close()
    assert prepare_for_migrations(str(db_path)) is None
    assert prepare_for_migrations(":memory:") is None
    assert prepare_for_migrations(str(db_path.with_name("absent.db"))) is None


@pytest.mark.parametrize(
    ("version", "expected"),
    [("5.0.0", "5.0.0"), ("5.0.0-rc.1", "5.0.0-rc.1"), ("1.0+a/b c", "1.0+a_b_c"), ("", "unknown")],
)
def test_copy_name_stays_one_safe_file_name(version, expected) -> None:
    assert copy_name("meshcore.db", 87, version) == f"meshcore.db.schema87.before-{expected}.db"


def test_too_little_disk_space_refuses_with_the_numbers(db_path, monkeypatch) -> None:
    _make_db(db_path, 87)
    usage = database_copy.shutil.disk_usage(db_path.parent)._replace(free=1024 * 1024)
    monkeypatch.setattr(database_copy.shutil, "disk_usage", lambda _path: usage)
    before = _digest(db_path)
    with pytest.raises(CopyFailedError, match=r"64 MB needed, 1 MB free") as refused:
        prepare_for_migrations(str(db_path))
    assert str(db_path.parent) in str(refused.value)
    assert _digest(db_path) == before
    assert list_copies(db_path) == []


def test_refusal_for_disk_space_names_the_older_copy_and_keeps_it(db_path, monkeypatch) -> None:
    monkeypatch.setattr(migrations, "latest_known_version", lambda: 1000)
    old = db_path.with_name("meshcore.db.schema87.before-5.0.0.db")
    _make_db(old, 87)
    _make_db(db_path, 144)
    usage = database_copy.shutil.disk_usage(db_path.parent)._replace(free=0)
    monkeypatch.setattr(database_copy.shutil, "disk_usage", lambda _path: usage)
    with pytest.raises(CopyFailedError, match=re.escape(old.name)):
        prepare_for_migrations(str(db_path))
    assert old.exists()


def test_disk_space_counts_the_copy_that_will_be_removed(db_path, monkeypatch) -> None:
    way_back = db_path.with_name("meshcore.db.schema87.before-5.0.0.db")
    middle = db_path.with_name("meshcore.db.schema100.before-5.1.0.db")
    _make_db(way_back, 87)
    _make_db(middle, 100)
    with open(middle, "ab") as fh:
        fh.truncate(70 * 1024 * 1024)
    _make_db(db_path, 120)
    usage = database_copy.shutil.disk_usage(db_path.parent)._replace(free=0)
    monkeypatch.setattr(database_copy.shutil, "disk_usage", lambda _path: usage)
    assert prepare_for_migrations(str(db_path)) is not None


def test_retention_keeps_the_way_back_and_the_newest(db_path, monkeypatch) -> None:
    monkeypatch.setattr(migrations, "latest_known_version", lambda: 1000)
    _make_db(db_path, 87)
    first = prepare_for_migrations(str(db_path))
    database_copy.migrations_finished(str(db_path))
    key_of_second = None
    for schema, version in ((144, "5.1.0"), (150, "5.2.0"), (155, "5.3.0")):
        conn = sqlite3.connect(db_path)
        conn.execute(f"PRAGMA user_version = {schema}")
        conn.commit()
        conn.close()
        _version(monkeypatch, version)
        (db_path.parent / "secrets.key").write_bytes(b"k" * 32)
        made = prepare_for_migrations(str(db_path))
        database_copy.migrations_finished(str(db_path))
        if schema == 144:
            key_of_second = key_copy_path(made)
            assert key_of_second.exists()
    assert {c.path.name for c in list_copies(db_path)} == {
        first.name,
        "meshcore.db.schema155.before-5.3.0.db",
    }
    assert not key_of_second.exists()


def test_a_later_copy_of_the_same_schema_supersedes_the_older_one(db_path, monkeypatch) -> None:
    """Went back to 4.18, used it, upgraded again: the older copy has less."""
    _make_db(db_path, 87)
    first = prepare_for_migrations(str(db_path))
    database_copy.migrations_finished(str(db_path))
    conn = sqlite3.connect(db_path)
    conn.execute("INSERT INTO items (value) VALUES ('after going back')")
    conn.commit()
    conn.close()
    _version(monkeypatch, "5.0.1")
    second = prepare_for_migrations(str(db_path))
    assert not first.exists()
    assert "after going back" in _rows(second)
    assert [c.path for c in list_copies(db_path)] == [second]


def test_same_version_again_replaces_the_copy(db_path) -> None:
    _make_db(db_path, 87)
    first = prepare_for_migrations(str(db_path))
    database_copy.migrations_finished(str(db_path))
    conn = sqlite3.connect(db_path)
    conn.execute("INSERT INTO items (value) VALUES ('newer')")
    conn.commit()
    conn.close()
    second = prepare_for_migrations(str(db_path))
    assert second == first
    assert "newer" in _rows(second)
    assert len(list_copies(db_path)) == 1


def test_a_stale_temporary_is_replaced(db_path) -> None:
    _make_db(db_path, 87)
    db_path.with_name("meshcore.db.copy-in-progress").write_bytes(b"half a copy")
    copy = prepare_for_migrations(str(db_path))
    assert _rows(copy) == _rows(db_path)
    assert not db_path.with_name("meshcore.db.copy-in-progress").exists()


def test_a_marker_naming_no_copy_is_ignored(db_path) -> None:
    _make_db(db_path, 87)
    marker_path(db_path).write_text("../../etc/passwd")
    assert prepare_for_migrations(str(db_path)) is not None


def test_the_copy_is_never_wider_than_0600_even_with_an_open_umask(db_path) -> None:
    _make_db(db_path, 87)
    (db_path.parent / "secrets.key").write_bytes(b"k" * 32)
    previous = os.umask(0)
    try:
        copy = prepare_for_migrations(str(db_path))
    finally:
        os.umask(previous)
    assert stat.S_IMODE(copy.stat().st_mode) == 0o600
    assert stat.S_IMODE(key_copy_path(copy).stat().st_mode) == 0o600
    assert stat.S_IMODE(marker_path(db_path).stat().st_mode) == 0o600


def test_the_addon_keeps_copies_out_of_home_assistant_backups() -> None:
    manifest = yaml.safe_load((ROOT / "meshloom" / "config.yaml").read_text(encoding="utf-8"))
    patterns = manifest["backup_exclude"]
    name = copy_name("meshcore.db", 87, "5.0.0")
    for candidate in (
        name,
        key_copy_path(Path(name)).name,
        "meshcore.db.copy-in-progress",
        "meshcore.db.copy-in-progress-wal",
        "meshcore.db.copy-in-progress.key",
        "meshcore.db.copy-in-progress.lock",
        "meshcore.db.migration-in-progress",
    ):
        assert any(PurePath("data", candidate).match(pattern) for pattern in patterns)
    assert not any(PurePath("data", "meshcore.db").match(pattern) for pattern in patterns)


# ---------------------------------------------------------------- what the log says (R-04)


def _start_uvicorn(database_path: Path) -> subprocess.CompletedProcess[str]:
    env = {
        **os.environ,
        "MESHCORE_DATABASE_PATH": str(database_path),
        "MESHCORE_DISABLE_PLUGINS": "true",
        "MESHCORE_LOG_LEVEL": "INFO",
    }
    return subprocess.run(
        [sys.executable, "-m", "uvicorn", "app.main:app", "--port", "0", "--no-proxy-headers"],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
    )


def test_a_failed_start_logs_the_cause_in_full(tmp_path) -> None:
    # A folder where the database should be: sqlite3 cannot open it.
    path = tmp_path / "meshcore.db"
    path.mkdir()
    result = _start_uvicorn(path)
    output = result.stdout + result.stderr
    assert result.returncode != 0
    assert "unable to open database file" in output
    assert "[truncated:" not in output


def test_a_newer_schema_logs_one_clear_line_and_no_traceback(tmp_path) -> None:
    path = tmp_path / "meshcore.db"
    _make_db(path, LATEST + 1)
    before = _digest(path)
    result = _start_uvicorn(path)
    output = result.stdout + result.stderr
    assert result.returncode != 0
    assert f"at schema {LATEST + 1}, newer than this version" in output
    assert "Traceback" not in output
    assert _digest(path) == before


def test_the_docker_image_never_syncs_and_works_for_any_uid() -> None:
    """R-01, R-06: ``uv run`` would install the dev group, and needs a cache it can create."""
    dockerfile = (ROOT / "Dockerfile").read_text(encoding="utf-8")
    final = dockerfile[dockerfile.rindex("\nFROM ") :]
    assert "ENV UV_NO_SYNC=1" in final
    assert re.search(r"ENV UV_CACHE_DIR=/tmp/", final)


def test_prepare_runs_off_the_event_loop_thread(db_path, monkeypatch) -> None:
    import threading

    _make_db(db_path, LATEST)
    seen: list[int] = []
    real = database_copy.prepare_for_migrations

    def spy(path: str):
        seen.append(threading.get_ident())
        return real(path)

    monkeypatch.setattr("app.database.prepare_for_migrations", spy)

    async def run() -> None:
        database = Database(str(db_path))
        await database.connect()
        await database.disconnect()
        seen.append(threading.get_ident())

    asyncio.run(run())
    assert seen[0] != seen[1]
