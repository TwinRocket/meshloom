import aiosqlite
import pytest

from app.migrations import get_version, run_migrations, set_version

from .conftest import LATEST_SCHEMA_VERSION


@pytest.mark.asyncio
async def test_creates_partial_sender_backfill_index_used_by_planner():
    conn = await aiosqlite.connect(":memory:")
    try:
        await set_version(conn, 85)
        await conn.execute(
            "CREATE TABLE messages (id INTEGER PRIMARY KEY, type TEXT, conversation_key TEXT,"
            " sender_name TEXT, sender_key TEXT)"
        )
        await conn.commit()
        await run_migrations(conn)
        assert await get_version(conn) == LATEST_SCHEMA_VERSION

        cursor = await conn.execute(
            "EXPLAIN QUERY PLAN UPDATE messages SET sender_key = 'k'"
            " WHERE type = 'CHAN' AND sender_name = 'n' AND sender_key IS NULL"
        )
        plan = " ".join(str(row[3]) for row in await cursor.fetchall())
        assert "idx_messages_chan_unattributed_sender" in plan
    finally:
        await conn.close()
