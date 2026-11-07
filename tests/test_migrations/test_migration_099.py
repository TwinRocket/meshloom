import aiosqlite
import pytest

from app.migrations import run_migrations, set_version


@pytest.mark.asyncio
async def test_adds_the_grace_at_60_and_an_empty_outage_for_an_existing_row():
    conn = await aiosqlite.connect(":memory:")
    try:
        await set_version(conn, 98)
        await conn.execute("CREATE TABLE app_settings (id INTEGER PRIMARY KEY CHECK (id = 1))")
        await conn.execute("INSERT INTO app_settings (id) VALUES (1)")
        await conn.commit()
        await run_migrations(conn)
        cursor = await conn.execute(
            "SELECT radio_offline_grace_s, radio_outage_started_at FROM app_settings"
        )
        assert tuple(await cursor.fetchone()) == (60, None)
    finally:
        await conn.close()


@pytest.mark.asyncio
async def test_is_idempotent_and_keeps_existing_values():
    conn = await aiosqlite.connect(":memory:")
    try:
        await set_version(conn, 98)
        await conn.execute(
            "CREATE TABLE app_settings (id INTEGER PRIMARY KEY CHECK (id = 1),"
            " radio_offline_grace_s INTEGER DEFAULT 60, radio_outage_started_at REAL)"
        )
        await conn.execute(
            "INSERT INTO app_settings (id, radio_offline_grace_s, radio_outage_started_at)"
            " VALUES (1, 0, 5.5)"
        )
        await conn.commit()
        await run_migrations(conn)
        cursor = await conn.execute("PRAGMA table_info(app_settings)")
        names = [row[1] for row in await cursor.fetchall()]
        assert names.count("radio_offline_grace_s") == 1
        assert names.count("radio_outage_started_at") == 1
        cursor = await conn.execute(
            "SELECT radio_offline_grace_s, radio_outage_started_at FROM app_settings"
        )
        assert tuple(await cursor.fetchone()) == (0, 5.5)
    finally:
        await conn.close()


@pytest.mark.asyncio
async def test_missing_app_settings_table_does_not_fail():
    conn = await aiosqlite.connect(":memory:")
    try:
        await set_version(conn, 98)
        await run_migrations(conn)
    finally:
        await conn.close()
