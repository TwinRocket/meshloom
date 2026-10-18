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
            "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = 'recovery_codes'"
        ).fetchone()
        index = conn.execute(
            "SELECT sql FROM sqlite_master WHERE type = 'index' AND name = 'idx_recovery_codes_user'"
        ).fetchone()
        assert table is not None and index is not None
        info = conn.execute("PRAGMA table_info(recovery_codes)").fetchall()
        return " ".join(table[0].split()), " ".join(index[0].split()), repr(info)
    finally:
        conn.close()


@pytest.mark.asyncio
async def test_fresh_and_migrated_databases_have_the_same_recovery_codes_table(tmp_path):
    fresh = tmp_path / "fresh.db"
    database = Database(str(fresh))
    await database.connect()
    try:
        async with database.readonly() as conn:
            assert await get_version(conn) == LATEST_SCHEMA_VERSION >= 109
            async with conn.execute("SELECT require_2fa_for_admins FROM auth_settings") as cursor:
                assert (await cursor.fetchone())[0] == 0
    finally:
        await database.disconnect()

    migrated = tmp_path / "migrated.db"
    conn = await aiosqlite.connect(migrated)
    try:
        await set_version(conn, 108)
        assert await run_migrations(conn) == migrations_after(108)
    finally:
        await conn.close()
    assert _schema(str(migrated)) == _schema(str(fresh))


@pytest.mark.asyncio
async def test_adds_the_setting_off_and_is_idempotent():
    from app.migrations import _109_totp_recovery_codes as migration

    conn = await aiosqlite.connect(":memory:")
    try:
        await set_version(conn, 108)
        await conn.execute("CREATE TABLE auth_settings (id INTEGER PRIMARY KEY CHECK (id = 1))")
        await conn.execute("INSERT INTO auth_settings (id) VALUES (1)")
        await conn.commit()
        await run_migrations(conn)
        await migration.migrate(conn)
        cursor = await conn.execute("SELECT require_2fa_for_admins FROM auth_settings")
        assert (await cursor.fetchone())[0] == 0
    finally:
        await conn.close()
