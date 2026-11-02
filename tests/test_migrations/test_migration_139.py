"""Migration 139: keep_channels_on_radio setting (MSHL-41)."""

import aiosqlite
import pytest

from app.migrations import get_version, run_migrations, set_version

from .conftest import LATEST_SCHEMA_VERSION


async def _columns(conn) -> list[str]:
    async with conn.execute("PRAGMA table_info(app_settings)") as cursor:
        return [row[1] for row in await cursor.fetchall()]


@pytest.mark.asyncio
async def test_adds_the_setting_off_keeping_the_row():
    from app.migrations import _139_keep_channels_on_radio as migration

    conn = await aiosqlite.connect(":memory:")
    try:
        await set_version(conn, 138)
        await conn.execute("CREATE TABLE app_settings (id INTEGER PRIMARY KEY CHECK (id = 1))")
        await conn.execute("INSERT INTO app_settings (id) VALUES (1)")
        await conn.commit()
        assert await run_migrations(conn) == LATEST_SCHEMA_VERSION - 138
        assert await get_version(conn) == LATEST_SCHEMA_VERSION
        async with conn.execute("SELECT keep_channels_on_radio FROM app_settings") as cursor:
            assert (await cursor.fetchone())[0] == 0
        before = await _columns(conn)
        await migration.migrate(conn)  # a second run changes nothing
        assert await _columns(conn) == before
    finally:
        await conn.close()


@pytest.mark.asyncio
async def test_missing_table_is_left_alone():
    from app.migrations import _139_keep_channels_on_radio as migration

    conn = await aiosqlite.connect(":memory:")
    try:
        await migration.migrate(conn)
        async with conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'") as c:
            assert await c.fetchall() == []
    finally:
        await conn.close()
