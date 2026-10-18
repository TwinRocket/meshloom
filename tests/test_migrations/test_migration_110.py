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
            "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = 'webauthn_credentials'"
        ).fetchone()
        index = conn.execute(
            "SELECT sql FROM sqlite_master WHERE type = 'index' AND name = 'idx_webauthn_credentials_user'"
        ).fetchone()
        assert table is not None and index is not None
        info = conn.execute("PRAGMA table_info(webauthn_credentials)").fetchall()
        return " ".join(table[0].split()), " ".join(index[0].split()), repr(info)
    finally:
        conn.close()


@pytest.mark.asyncio
async def test_fresh_and_migrated_databases_have_the_same_webauthn_credentials_table(tmp_path):
    fresh = tmp_path / "fresh.db"
    database = Database(str(fresh))
    await database.connect()
    try:
        async with database.readonly() as conn:
            assert await get_version(conn) == LATEST_SCHEMA_VERSION >= 110
    finally:
        await database.disconnect()

    migrated = tmp_path / "migrated.db"
    conn = await aiosqlite.connect(migrated)
    try:
        await set_version(conn, 109)
        assert await run_migrations(conn) == migrations_after(109)
    finally:
        await conn.close()
    assert _schema(str(migrated)) == _schema(str(fresh))


@pytest.mark.asyncio
async def test_is_idempotent_and_cascades_with_the_account():
    from app.migrations import _110_webauthn_credentials as migration

    conn = await aiosqlite.connect(":memory:")
    try:
        await conn.execute("PRAGMA foreign_keys = ON")
        await conn.execute("CREATE TABLE users (id INTEGER PRIMARY KEY)")
        await conn.execute("INSERT INTO users (id) VALUES (1)")
        await migration.migrate(conn)
        await migration.migrate(conn)
        await conn.execute(
            "INSERT INTO webauthn_credentials (user_id, credential_id, public_key, name,"
            " created_at) VALUES (1, x'01', x'02', 'key', 0)"
        )
        await conn.execute("DELETE FROM users WHERE id = 1")
        cursor = await conn.execute("SELECT COUNT(*) FROM webauthn_credentials")
        assert (await cursor.fetchone())[0] == 0
    finally:
        await conn.close()
