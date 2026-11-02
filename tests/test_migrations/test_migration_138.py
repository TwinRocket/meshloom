"""Migration 138: Meshloom Remote seed column and tables (MSHL-79)."""

import aiosqlite
import pytest

from app.migrations import get_version, run_migrations, set_version

from .conftest import LATEST_SCHEMA_VERSION

TABLES = {"remote_state", "remote_devices", "remote_enroll_tokens"}


async def _tables(conn) -> set[str]:
    async with conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'") as cursor:
        return {row[0] for row in await cursor.fetchall()}


@pytest.mark.asyncio
async def test_upgrades_an_existing_database_and_is_idempotent():
    from app.migrations import _138_remote_identity as migration

    conn = await aiosqlite.connect(":memory:")
    try:
        await set_version(conn, 137)
        await conn.execute("CREATE TABLE app_settings (id INTEGER PRIMARY KEY CHECK (id = 1))")
        await conn.execute("INSERT INTO app_settings (id) VALUES (1)")
        await conn.commit()
        assert await run_migrations(conn) == LATEST_SCHEMA_VERSION - 137
        assert await get_version(conn) == LATEST_SCHEMA_VERSION
        async with conn.execute("SELECT remote_instance_seed FROM app_settings") as cursor:
            assert (await cursor.fetchone())[0] == ""
        assert await _tables(conn) >= TABLES
        async with conn.execute("SELECT enabled, slug, gate_gen FROM remote_state") as cursor:
            assert tuple(await cursor.fetchone()) == (0, "", 0)
        await conn.execute(
            "INSERT INTO remote_devices (id, user_id, kind, origin, gen, created_at,"
            " pass_expires_at) VALUES ('d', 1, 'browser', 'qr', 0, 1, 2)"
        )
        await migration.migrate(conn)  # a second run changes nothing and keeps the rows
        async with conn.execute("PRAGMA table_info(app_settings)") as cursor:
            columns = [row[1] for row in await cursor.fetchall()]
        assert columns.count("remote_instance_seed") == 1
        async with conn.execute("SELECT COUNT(*) FROM remote_devices") as cursor:
            assert (await cursor.fetchone())[0] == 1
        async with conn.execute("SELECT COUNT(*) FROM remote_state") as cursor:
            assert (await cursor.fetchone())[0] == 1
    finally:
        await conn.close()


@pytest.mark.asyncio
async def test_without_app_settings_creates_only_the_tables():
    from app.migrations import _138_remote_identity as migration

    conn = await aiosqlite.connect(":memory:")
    try:
        await migration.migrate(conn)
        assert await _tables(conn) == TABLES
    finally:
        await conn.close()
