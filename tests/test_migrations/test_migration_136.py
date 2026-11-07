"""Migration 136: HTTP Basic auth removed, login-free on the LAN off (MSHL-100)."""

import json
import logging

import aiosqlite
import pytest

from app.migrations import get_version, run_migrations, set_version

from .conftest import LATEST_SCHEMA_VERSION


async def _columns(conn) -> list[str]:
    async with conn.execute("PRAGMA table_info(auth_settings)") as cursor:
        return [row[1] for row in await cursor.fetchall()]


async def _upgraded(rules: list) -> aiosqlite.Connection:
    conn = await aiosqlite.connect(":memory:")
    await set_version(conn, 135)
    await conn.execute(
        "CREATE TABLE auth_settings (id INTEGER PRIMARY KEY CHECK (id = 1),"
        " trusted_networks TEXT NOT NULL DEFAULT '[]',"
        " basic_imported INTEGER NOT NULL DEFAULT 0)"
    )
    await conn.execute(
        "INSERT INTO auth_settings (id, trusted_networks, basic_imported) VALUES (1, ?, 1)",
        (json.dumps(rules),),
    )
    await conn.commit()
    assert await run_migrations(conn) == LATEST_SCHEMA_VERSION - 135
    return conn


@pytest.mark.asyncio
async def test_existing_rules_stay_off_with_a_warning_and_basic_imported_goes(caplog):
    from app.migrations import _136_lan_login_free as migration

    rules = [{"cidr": "192.168.1.0/24", "user_id": 2}]
    with caplog.at_level(logging.WARNING, logger=migration.__name__):
        conn = await _upgraded(rules)
    try:
        assert await get_version(conn) == LATEST_SCHEMA_VERSION
        async with conn.execute("SELECT lan_login_free, trusted_networks FROM auth_settings") as c:
            enabled, stored = await c.fetchone()
        assert enabled == 0
        assert json.loads(stored) == rules  # the rules are kept, only switched off
        assert "1 trusted network rule(s)" in caplog.text
        columns = await _columns(conn)
        assert "basic_imported" not in columns
        await migration.migrate(conn)  # a second run changes nothing
        assert await _columns(conn) == columns
    finally:
        await conn.close()


@pytest.mark.asyncio
async def test_no_warning_without_rules(caplog):
    from app.migrations import _136_lan_login_free as migration

    with caplog.at_level(logging.WARNING, logger=migration.__name__):
        conn = await _upgraded([])
    try:
        assert "trusted network rule" not in caplog.text
    finally:
        await conn.close()


@pytest.mark.asyncio
async def test_without_auth_settings_does_nothing():
    from app.migrations import _136_lan_login_free as migration

    conn = await aiosqlite.connect(":memory:")
    try:
        await migration.migrate(conn)
        assert await _columns(conn) == []
    finally:
        await conn.close()


@pytest.mark.asyncio
async def test_a_fresh_database_is_off(tmp_path):
    from app.database import Database

    database = Database(str(tmp_path / "fresh.db"))
    await database.connect()
    try:
        async with database.readonly() as conn:
            assert await get_version(conn) == LATEST_SCHEMA_VERSION
            async with conn.execute("SELECT lan_login_free FROM auth_settings") as cursor:
                assert (await cursor.fetchone())[0] == 0
            assert "basic_imported" not in await _columns(conn)
    finally:
        await database.disconnect()
