import sqlite3

import aiosqlite
import pytest

from app.database import Database
from app.migrations import get_version, run_migrations, set_version

from .conftest import LATEST_SCHEMA_VERSION


def _quota_schema(path: str) -> tuple[str, str]:
    conn = sqlite3.connect(path)
    try:
        row = conn.execute(
            "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = 'plugin_send_quota'"
        ).fetchone()
        info = conn.execute("PRAGMA table_info(plugin_send_quota)").fetchall()
    finally:
        conn.close()
    assert row is not None
    return " ".join(row[0].split()), repr(info)


@pytest.mark.asyncio
async def test_fresh_and_migrated_databases_have_the_same_quota_table(tmp_path):
    fresh = tmp_path / "fresh.db"
    database = Database(str(fresh))
    await database.connect()
    try:
        async with database.readonly() as conn:
            assert await get_version(conn) == LATEST_SCHEMA_VERSION >= 105
    finally:
        await database.disconnect()

    migrated = tmp_path / "migrated.db"
    conn = await aiosqlite.connect(migrated)
    try:
        await set_version(conn, 104)
        assert await run_migrations(conn) == LATEST_SCHEMA_VERSION - 104
    finally:
        await conn.close()

    assert _quota_schema(str(migrated)) == _quota_schema(str(fresh))


@pytest.mark.asyncio
async def test_period_is_checked():
    conn = await aiosqlite.connect(":memory:")
    try:
        await set_version(conn, 104)
        await run_migrations(conn)
        with pytest.raises(sqlite3.IntegrityError):
            await conn.execute(
                "INSERT INTO plugin_send_quota (instance_id, period, period_start, count)"
                " VALUES ('a', 'week', 0, 1)"
            )
    finally:
        await conn.close()
