"""Migration 137: escalating ban durations (MSHL-434)."""

import aiosqlite
import pytest

from app.migrations import get_version, run_migrations, set_version

from .conftest import LATEST_SCHEMA_VERSION


async def _columns(conn, table: str) -> list[str]:
    async with conn.execute(f"PRAGMA table_info({table})") as cursor:
        return [row[1] for row in await cursor.fetchall()]


@pytest.mark.asyncio
async def test_adds_the_cap_and_the_strikes_keeping_the_rows():
    from app.migrations import _137_ip_ban_escalation as migration

    conn = await aiosqlite.connect(":memory:")
    try:
        await set_version(conn, 136)
        await conn.execute("CREATE TABLE auth_settings (id INTEGER PRIMARY KEY CHECK (id = 1))")
        await conn.execute("INSERT INTO auth_settings (id) VALUES (1)")
        await conn.execute(
            "CREATE TABLE ip_bans (address TEXT PRIMARY KEY, banned_at INTEGER NOT NULL,"
            " expires_at INTEGER NOT NULL, failures INTEGER NOT NULL, route TEXT NOT NULL)"
        )
        await conn.execute("INSERT INTO ip_bans VALUES ('198.51.100.1', 1, 2, 10, 'login')")
        await conn.commit()
        assert await run_migrations(conn) == LATEST_SCHEMA_VERSION - 136
        assert await get_version(conn) == LATEST_SCHEMA_VERSION
        async with conn.execute("SELECT ip_ban_max_duration_minutes FROM auth_settings") as c:
            assert (await c.fetchone())[0] == 1440
        async with conn.execute("SELECT address, strikes FROM ip_bans") as c:
            assert tuple(await c.fetchone()) == ("198.51.100.1", 1)
        settings, bans = await _columns(conn, "auth_settings"), await _columns(conn, "ip_bans")
        await migration.migrate(conn)  # a second run changes nothing
        assert await _columns(conn, "auth_settings") == settings
        assert await _columns(conn, "ip_bans") == bans
    finally:
        await conn.close()


@pytest.mark.asyncio
async def test_missing_tables_are_left_alone():
    from app.migrations import _137_ip_ban_escalation as migration

    conn = await aiosqlite.connect(":memory:")
    try:
        await migration.migrate(conn)
        async with conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'") as c:
            assert await c.fetchall() == []
    finally:
        await conn.close()
