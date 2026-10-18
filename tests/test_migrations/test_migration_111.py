import aiosqlite
import pytest

from app.migrations import run_migrations, set_version
from app.migrations._111_notification_language import migrate


@pytest.mark.asyncio
async def test_adds_notification_language_defaulting_to_auto_for_an_existing_row():
    conn = await aiosqlite.connect(":memory:")
    try:
        await set_version(conn, 110)
        await conn.execute("CREATE TABLE app_settings (id INTEGER PRIMARY KEY)")
        await conn.execute("INSERT INTO app_settings (id) VALUES (1)")
        await conn.commit()
        await migrate(conn)
        await migrate(conn)  # idempotent
        cursor = await conn.execute("SELECT notification_language FROM app_settings")
        assert (await cursor.fetchone())[0] == "auto"
    finally:
        await conn.close()


@pytest.mark.asyncio
async def test_without_app_settings_it_is_a_no_op():
    conn = await aiosqlite.connect(":memory:")
    try:
        await set_version(conn, 110)
        await run_migrations(conn)  # no table at all: every migration skips
    finally:
        await conn.close()
