import sqlite3

import aiosqlite
import pytest

from app.database import Database
from app.migrations import get_version, run_migrations, set_version

from .conftest import LATEST_SCHEMA_VERSION


def _schema(path: str) -> tuple[str, str]:
    conn = sqlite3.connect(path)
    try:
        row = conn.execute(
            "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = 'plugin_uids'"
        ).fetchone()
        assert row is not None
        info = conn.execute("PRAGMA table_info(plugin_uids)").fetchall()
        return " ".join(row[0].split()), repr(info)
    finally:
        conn.close()


@pytest.mark.asyncio
async def test_fresh_and_migrated_databases_have_the_same_uid_table(tmp_path):
    fresh = tmp_path / "fresh.db"
    database = Database(str(fresh))
    await database.connect()
    try:
        async with database.readonly() as conn:
            assert await get_version(conn) == LATEST_SCHEMA_VERSION >= 121
    finally:
        await database.disconnect()

    migrated = tmp_path / "migrated.db"
    conn = await aiosqlite.connect(migrated)
    try:
        await set_version(conn, 120)
        assert await run_migrations(conn) == LATEST_SCHEMA_VERSION - 120
    finally:
        await conn.close()

    assert _schema(str(migrated)) == _schema(str(fresh))


@pytest.mark.asyncio
async def test_a_uid_belongs_to_one_instance_and_is_never_root():
    conn = await aiosqlite.connect(":memory:")
    try:
        await set_version(conn, 120)
        await run_migrations(conn)
        await conn.execute(
            "INSERT INTO plugin_uids (instance_id, uid, allocated_at) VALUES ('a', 40000, 1)"
        )
        with pytest.raises(sqlite3.IntegrityError):
            await conn.execute(
                "INSERT INTO plugin_uids (instance_id, uid, allocated_at) VALUES ('b', 40000, 1)"
            )
        with pytest.raises(sqlite3.IntegrityError):
            await conn.execute(
                "INSERT INTO plugin_uids (instance_id, uid, allocated_at) VALUES ('c', 0, 1)"
            )
    finally:
        await conn.close()
