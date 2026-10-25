import aiosqlite
import pytest

from app.database import Database
from app.migrations import run_migrations, set_version

_AUTH_SETTINGS = "CREATE TABLE auth_settings (id INTEGER PRIMARY KEY CHECK (id = 1))"
_USERS = "CREATE TABLE users (id INTEGER PRIMARY KEY, provider TEXT NOT NULL)"


async def _flag_after_upgrade(*providers: str) -> int:
    conn = await aiosqlite.connect(":memory:")
    try:
        await set_version(conn, 106)
        await conn.execute(_AUTH_SETTINGS)
        await conn.execute("INSERT INTO auth_settings (id) VALUES (1)")
        await conn.execute(_USERS)
        for provider in providers:
            await conn.execute("INSERT INTO users (provider) VALUES (?)", (provider,))
        await conn.commit()
        await run_migrations(conn)
        cursor = await conn.execute("SELECT ingress_admin_granted FROM auth_settings")
        return (await cursor.fetchone())[0]
    finally:
        await conn.close()


@pytest.mark.asyncio
async def test_backfill_marks_the_grant_only_when_an_ingress_identity_exists():
    assert await _flag_after_upgrade() == 0
    assert await _flag_after_upgrade("local") == 0
    assert await _flag_after_upgrade("local", "ingress") == 1


@pytest.mark.asyncio
async def test_fresh_database_starts_without_the_grant(tmp_path):
    database = Database(str(tmp_path / "fresh.db"))
    await database.connect()
    try:
        async with database.readonly() as conn:
            async with conn.execute("SELECT ingress_admin_granted FROM auth_settings") as cursor:
                assert (await cursor.fetchone())[0] == 0
    finally:
        await database.disconnect()


@pytest.mark.asyncio
async def test_is_idempotent():
    from app.migrations import _107_auth_ingress_admin_granted as migration

    conn = await aiosqlite.connect(":memory:")
    try:
        await conn.execute(_AUTH_SETTINGS)
        await conn.execute("INSERT INTO auth_settings (id) VALUES (1)")
        await migration.migrate(conn)
        await conn.execute("UPDATE auth_settings SET ingress_admin_granted = 1")
        await migration.migrate(conn)
        cursor = await conn.execute("SELECT ingress_admin_granted FROM auth_settings")
        assert (await cursor.fetchone())[0] == 1
    finally:
        await conn.close()
