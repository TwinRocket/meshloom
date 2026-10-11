import aiosqlite
import pytest

from app.migrations import run_migrations, set_version


@pytest.mark.asyncio
async def test_adds_community_status_clear_defaulting_to_nothing_pending():
    conn = await aiosqlite.connect(":memory:")
    try:
        await set_version(conn, 87)
        await conn.execute("CREATE TABLE app_settings (id INTEGER PRIMARY KEY CHECK (id = 1))")
        await conn.execute("INSERT INTO app_settings (id) VALUES (1)")
        await conn.commit()
        await run_migrations(conn)
        cursor = await conn.execute("SELECT community_status_clear FROM app_settings")
        assert (await cursor.fetchone())[0] == "[]"
    finally:
        await conn.close()
