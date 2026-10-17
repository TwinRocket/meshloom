import aiosqlite
import pytest

from app.migrations import get_version, run_migrations, set_version

from .conftest import LATEST_SCHEMA_VERSION


@pytest.mark.asyncio
async def test_adds_wifi_prompt_state_as_an_empty_object():
    conn = await aiosqlite.connect(":memory:")
    try:
        await set_version(conn, 94)
        await conn.execute("CREATE TABLE app_settings (id INTEGER PRIMARY KEY CHECK (id = 1))")
        await conn.execute("INSERT INTO app_settings (id) VALUES (1)")
        await conn.commit()
        await run_migrations(conn)
        cursor = await conn.execute("SELECT wifi_prompt_state FROM app_settings")
        assert (await cursor.fetchone())[0] == "{}"
        assert await get_version(conn) == LATEST_SCHEMA_VERSION
    finally:
        await conn.close()


@pytest.mark.asyncio
async def test_is_idempotent_when_the_column_exists():
    conn = await aiosqlite.connect(":memory:")
    try:
        await set_version(conn, 94)
        await conn.execute(
            "CREATE TABLE app_settings (id INTEGER PRIMARY KEY CHECK (id = 1),"
            " wifi_prompt_state TEXT DEFAULT '{}')"
        )
        await conn.commit()
        await run_migrations(conn)
        cursor = await conn.execute("PRAGMA table_info(app_settings)")
        names = [row[1] for row in await cursor.fetchall()]
        assert names.count("wifi_prompt_state") == 1
    finally:
        await conn.close()


@pytest.mark.asyncio
async def test_tolerates_a_database_without_app_settings():
    conn = await aiosqlite.connect(":memory:")
    try:
        await set_version(conn, 94)
        await conn.commit()
        await run_migrations(conn)
        assert await get_version(conn) == LATEST_SCHEMA_VERSION
    finally:
        await conn.close()
