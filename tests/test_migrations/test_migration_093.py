import aiosqlite
import pytest

from app.migrations import run_migrations, set_version


@pytest.mark.asyncio
async def test_adds_community_opt_out_pending_empty():
    conn = await aiosqlite.connect(":memory:")
    try:
        await set_version(conn, 92)
        await conn.execute("CREATE TABLE app_settings (id INTEGER PRIMARY KEY CHECK (id = 1))")
        await conn.execute("INSERT INTO app_settings (id) VALUES (1)")
        await conn.commit()
        await run_migrations(conn)
        cursor = await conn.execute("SELECT community_opt_out_pending FROM app_settings")
        assert (await cursor.fetchone())[0] == ""
    finally:
        await conn.close()


@pytest.mark.asyncio
async def test_is_idempotent_when_the_column_exists():
    conn = await aiosqlite.connect(":memory:")
    try:
        await set_version(conn, 92)
        await conn.execute(
            "CREATE TABLE app_settings (id INTEGER PRIMARY KEY CHECK (id = 1),"
            " community_opt_out_pending TEXT DEFAULT '')"
        )
        await conn.commit()
        await run_migrations(conn)
        cursor = await conn.execute("PRAGMA table_info(app_settings)")
        names = [row[1] for row in await cursor.fetchall()]
        assert names.count("community_opt_out_pending") == 1
    finally:
        await conn.close()
