import sqlite3

import aiosqlite
import pytest

from app.database import Database
from app.migrations import get_version, run_migrations, set_version

from .conftest import LATEST_SCHEMA_VERSION

TABLES = ("plugin_send_guard", "plugin_send_limits")


def _schemas(path: str) -> dict[str, tuple[str, str]]:
    conn = sqlite3.connect(path)
    try:
        result = {}
        for table in TABLES:
            row = conn.execute(
                "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = ?", (table,)
            ).fetchone()
            assert row is not None
            info = conn.execute(f"PRAGMA table_info({table})").fetchall()
            result[table] = (" ".join(row[0].split()), repr(info))
        return result
    finally:
        conn.close()


@pytest.mark.asyncio
async def test_fresh_and_migrated_databases_have_the_same_guard_tables(tmp_path):
    fresh = tmp_path / "fresh.db"
    database = Database(str(fresh))
    await database.connect()
    try:
        async with database.readonly() as conn:
            assert await get_version(conn) == LATEST_SCHEMA_VERSION >= 106
    finally:
        await database.disconnect()

    migrated = tmp_path / "migrated.db"
    conn = await aiosqlite.connect(migrated)
    try:
        await set_version(conn, 105)
        assert await run_migrations(conn) == LATEST_SCHEMA_VERSION - 105
    finally:
        await conn.close()

    assert _schemas(str(migrated)) == _schemas(str(fresh))


@pytest.mark.asyncio
async def test_scope_and_limit_names_are_checked():
    conn = await aiosqlite.connect(":memory:")
    try:
        await set_version(conn, 105)
        await run_migrations(conn)
        with pytest.raises(sqlite3.IntegrityError):
            await conn.execute(
                "INSERT INTO plugin_send_guard (scope, subject, period_start, count)"
                " VALUES ('galaxy', 'x', 0, 1)"
            )
        with pytest.raises(sqlite3.IntegrityError):
            await conn.execute("INSERT INTO plugin_send_limits (name, value) VALUES ('other', 3)")
        with pytest.raises(sqlite3.IntegrityError):
            await conn.execute(
                "INSERT INTO plugin_send_limits (name, value) VALUES ('instance_per_hour', 0)"
            )
    finally:
        await conn.close()


@pytest.mark.asyncio
async def test_running_migration_106_twice_is_harmless():
    from app.migrations._106_plugin_send_guard import migrate

    conn = await aiosqlite.connect(":memory:")
    try:
        await set_version(conn, 105)
        await run_migrations(conn)
        await conn.execute(
            "INSERT INTO plugin_send_limits (name, value) VALUES ('channel_per_hour', 5)"
        )
        await migrate(conn)
        async with conn.execute("SELECT value FROM plugin_send_limits") as cursor:
            assert [row[0] for row in await cursor.fetchall()] == [5]
    finally:
        await conn.close()
