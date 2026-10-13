import logging

import aiosqlite
import pytest

from app.migrations import run_migrations, set_version


async def _legacy_db(broker: str, api: str) -> aiosqlite.Connection:
    conn = await aiosqlite.connect(":memory:")
    await set_version(conn, 90)
    await conn.execute(
        "CREATE TABLE app_settings (id INTEGER PRIMARY KEY CHECK (id = 1),"
        " community_enabled INTEGER DEFAULT 0, community_iata TEXT DEFAULT '',"
        " community_broker_host TEXT DEFAULT '', community_api_base TEXT DEFAULT '')"
    )
    await conn.execute(
        "INSERT INTO app_settings (id, community_enabled, community_iata,"
        " community_broker_host, community_api_base) VALUES (1, 1, 'CDG', ?, ?)",
        (broker, api),
    )
    await conn.commit()
    return conn


@pytest.mark.asyncio
async def test_empties_custom_addresses_and_logs_them(caplog):
    conn = await _legacy_db("mqtt.internal.test", "https://api.internal.test")
    try:
        with caplog.at_level(logging.WARNING):
            await run_migrations(conn)
        cursor = await conn.execute(
            "SELECT community_enabled, community_iata, community_broker_host,"
            " community_api_base, community_env_imported FROM app_settings"
        )
        assert await cursor.fetchone() == (1, "CDG", "", "", 0)
        assert "mqtt.internal.test" in caplog.text
        assert "https://api.internal.test" in caplog.text
        cursor = await conn.execute("PRAGMA table_info(app_settings)")
        names = [row[1] for row in await cursor.fetchall()]
        # Emptied, never dropped.
        assert "community_broker_host" in names and "community_api_base" in names
    finally:
        await conn.close()


@pytest.mark.asyncio
async def test_empty_addresses_log_nothing(caplog):
    conn = await _legacy_db("", "")
    try:
        with caplog.at_level(logging.WARNING):
            await run_migrations(conn)
        assert "Dropping app_settings" not in caplog.text
    finally:
        await conn.close()


@pytest.mark.asyncio
async def test_is_idempotent_when_the_marker_exists():
    conn = await aiosqlite.connect(":memory:")
    try:
        await set_version(conn, 90)
        await conn.execute(
            "CREATE TABLE app_settings (id INTEGER PRIMARY KEY CHECK (id = 1),"
            " community_env_imported INTEGER DEFAULT 1)"
        )
        await conn.execute("INSERT INTO app_settings (id) VALUES (1)")
        await conn.commit()
        await run_migrations(conn)
        cursor = await conn.execute("PRAGMA table_info(app_settings)")
        names = [row[1] for row in await cursor.fetchall()]
        assert names.count("community_env_imported") == 1
    finally:
        await conn.close()
