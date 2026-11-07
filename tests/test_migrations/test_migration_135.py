"""Migration 135: the IP ban settings and tables (MSHL-422)."""

import aiosqlite
import pytest

from app.migrations import get_version, run_migrations, set_version

from .conftest import LATEST_SCHEMA_VERSION

COLUMNS = {
    "ip_ban_enabled": 1,
    "ip_ban_max_failures": 10,
    "ip_ban_window_minutes": 10,
    "ip_ban_duration_minutes": 60,
}


async def _tables(conn) -> set[str]:
    async with conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'") as cursor:
        return {row[0] for row in await cursor.fetchall()}


@pytest.mark.asyncio
async def test_upgrades_an_existing_database_and_is_idempotent():
    from app.migrations import _135_ip_bans as migration

    conn = await aiosqlite.connect(":memory:")
    try:
        await set_version(conn, 134)
        await conn.execute("CREATE TABLE auth_settings (id INTEGER PRIMARY KEY CHECK (id = 1))")
        await conn.execute("INSERT INTO auth_settings (id) VALUES (1)")
        await conn.commit()
        assert await run_migrations(conn) == LATEST_SCHEMA_VERSION - 134
        assert await get_version(conn) == LATEST_SCHEMA_VERSION
        names = ", ".join(COLUMNS)
        async with conn.execute(f"SELECT {names} FROM auth_settings") as cursor:
            assert tuple(await cursor.fetchone()) == tuple(COLUMNS.values())
        assert {"ip_bans", "ip_ban_whitelist"} <= await _tables(conn)
        await conn.execute(
            "INSERT INTO ip_bans (address, banned_at, expires_at, failures, route)"
            " VALUES ('198.51.100.1', 1, 2, 10, 'login')"
        )
        await migration.migrate(conn)  # a second run changes nothing and keeps the rows
        async with conn.execute("PRAGMA table_info(auth_settings)") as cursor:
            columns = [row[1] for row in await cursor.fetchall()]
        assert all(columns.count(name) == 1 for name in COLUMNS)
        async with conn.execute("SELECT COUNT(*) FROM ip_bans") as cursor:
            assert (await cursor.fetchone())[0] == 1
    finally:
        await conn.close()


@pytest.mark.asyncio
async def test_without_auth_settings_creates_only_the_tables():
    from app.migrations import _135_ip_bans as migration

    conn = await aiosqlite.connect(":memory:")
    try:
        await migration.migrate(conn)
        assert {"ip_bans", "ip_ban_whitelist"} == await _tables(conn)
    finally:
        await conn.close()


@pytest.mark.asyncio
async def test_a_fresh_database_has_the_defaults(tmp_path):
    from app.database import Database

    database = Database(str(tmp_path / "fresh.db"))
    await database.connect()
    try:
        async with database.readonly() as conn:
            assert await get_version(conn) == LATEST_SCHEMA_VERSION
            names = ", ".join(COLUMNS)
            async with conn.execute(f"SELECT {names} FROM auth_settings") as cursor:
                assert tuple(await cursor.fetchone()) == tuple(COLUMNS.values())
            async with conn.execute("SELECT COUNT(*) FROM ip_bans") as cursor:
                assert (await cursor.fetchone())[0] == 0
    finally:
        await database.disconnect()
