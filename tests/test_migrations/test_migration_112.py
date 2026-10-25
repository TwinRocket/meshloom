import sqlite3

import aiosqlite
import pytest

from app.database import Database
from app.migrations import get_version, run_migrations, set_version

from .conftest import LATEST_SCHEMA_VERSION


def _schema(path: str) -> tuple[str, str, str]:
    conn = sqlite3.connect(path)
    try:
        table = conn.execute(
            "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = 'pairing_codes'"
        ).fetchone()
        index = conn.execute(
            "SELECT sql FROM sqlite_master WHERE type = 'index' AND name = 'idx_pairing_codes_user'"
        ).fetchone()
        assert table is not None and index is not None
        info = conn.execute("PRAGMA table_info(pairing_codes)").fetchall()
        return " ".join(table[0].split()), " ".join(index[0].split()), repr(info)
    finally:
        conn.close()


@pytest.mark.asyncio
async def test_fresh_and_migrated_databases_have_the_same_pairing_codes_table(tmp_path):
    fresh = tmp_path / "fresh.db"
    database = Database(str(fresh))
    await database.connect()
    try:
        async with database.readonly() as conn:
            assert await get_version(conn) == LATEST_SCHEMA_VERSION >= 112
    finally:
        await database.disconnect()

    migrated = tmp_path / "migrated.db"
    conn = await aiosqlite.connect(migrated)
    try:
        await set_version(conn, 111)
        assert await run_migrations(conn) == LATEST_SCHEMA_VERSION - 111
    finally:
        await conn.close()
    assert _schema(str(migrated)) == _schema(str(fresh))


@pytest.mark.asyncio
async def test_is_idempotent_cascades_with_the_account_and_forgets_a_deleted_token():
    from app.migrations import _108_api_tokens, _112_pairing_codes

    conn = await aiosqlite.connect(":memory:")
    try:
        await conn.execute("PRAGMA foreign_keys = ON")
        await conn.execute("CREATE TABLE users (id INTEGER PRIMARY KEY)")
        await conn.execute("INSERT INTO users (id) VALUES (1)")
        await _108_api_tokens.migrate(conn)
        await _112_pairing_codes.migrate(conn)
        await _112_pairing_codes.migrate(conn)
        await conn.execute(
            "INSERT INTO api_tokens (id, user_id, name, token_hash, scopes, created_at)"
            " VALUES ('t', 1, 'phone', 'h', '[\"read\"]', 0)"
        )
        await conn.execute(
            "INSERT INTO pairing_codes (id, code_hash, created_by, label, scopes, created_at,"
            " expires_at, used_at, token_id) VALUES ('p', 'c', 1, 'phone', '[]', 0, 1, 0, 't')"
        )
        await conn.execute("DELETE FROM api_tokens WHERE id = 't'")
        cursor = await conn.execute("SELECT token_id FROM pairing_codes")
        assert (await cursor.fetchone())[0] is None
        await conn.execute("DELETE FROM users WHERE id = 1")
        cursor = await conn.execute("SELECT COUNT(*) FROM pairing_codes")
        assert (await cursor.fetchone())[0] == 0
    finally:
        await conn.close()
