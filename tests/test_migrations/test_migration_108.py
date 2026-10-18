import sqlite3

import aiosqlite
import pytest

from app.database import Database
from app.migrations import get_version, run_migrations, set_version

from .conftest import LATEST_SCHEMA_VERSION, migrations_after


def _schema(path: str) -> tuple[str, str, str]:
    conn = sqlite3.connect(path)
    try:
        table = conn.execute(
            "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = 'api_tokens'"
        ).fetchone()
        index = conn.execute(
            "SELECT sql FROM sqlite_master WHERE type = 'index' AND name = 'idx_api_tokens_user'"
        ).fetchone()
        assert table is not None and index is not None
        info = conn.execute("PRAGMA table_info(api_tokens)").fetchall()
        return " ".join(table[0].split()), " ".join(index[0].split()), repr(info)
    finally:
        conn.close()


@pytest.mark.asyncio
async def test_fresh_and_migrated_databases_have_the_same_api_tokens_table(tmp_path):
    fresh = tmp_path / "fresh.db"
    database = Database(str(fresh))
    await database.connect()
    try:
        async with database.readonly() as conn:
            assert await get_version(conn) == LATEST_SCHEMA_VERSION >= 108
    finally:
        await database.disconnect()

    migrated = tmp_path / "migrated.db"
    conn = await aiosqlite.connect(migrated)
    try:
        await set_version(conn, 107)
        assert await run_migrations(conn) == migrations_after(107)
    finally:
        await conn.close()

    assert _schema(str(migrated)) == _schema(str(fresh))


@pytest.mark.asyncio
async def test_origin_is_checked_and_running_migration_108_twice_is_harmless():
    from app.migrations._108_api_tokens import migrate

    conn = await aiosqlite.connect(":memory:")
    try:
        await set_version(conn, 107)
        await run_migrations(conn)
        await conn.execute(
            "INSERT INTO api_tokens (id, user_id, name, token_hash, scopes, created_at)"
            " VALUES ('t1', 1, 'phone', 'h1', '[\"read\"]', 0)"
        )
        with pytest.raises(sqlite3.IntegrityError):
            await conn.execute(
                "INSERT INTO api_tokens (id, user_id, name, token_hash, scopes, created_at, origin)"
                " VALUES ('t2', 1, 'x', 'h2', '[]', 0, 'qr')"
            )
        await migrate(conn)
        async with conn.execute("SELECT id, origin FROM api_tokens") as cursor:
            assert [tuple(row) for row in await cursor.fetchall()] == [("t1", "ui")]
    finally:
        await conn.close()
