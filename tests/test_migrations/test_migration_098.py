import aiosqlite
import pytest

from app.migrations import get_version, run_migrations, set_version

from .conftest import LATEST_SCHEMA_VERSION

FEED_INDEXES = {
    "idx_messages_feed_type",
    "idx_messages_feed_outgoing",
    "idx_messages_feed_type_outgoing",
}


async def _index_names(conn) -> set[str]:
    cursor = await conn.execute("SELECT name FROM sqlite_master WHERE type = 'index'")
    return {row[0] for row in await cursor.fetchall()}


@pytest.mark.asyncio
async def test_creates_feed_indexes():
    conn = await aiosqlite.connect(":memory:")
    try:
        await set_version(conn, 97)
        await conn.execute(
            "CREATE TABLE messages (id INTEGER PRIMARY KEY, type TEXT, conversation_key TEXT,"
            " outgoing INTEGER, received_at INTEGER)"
        )
        await conn.commit()
        await run_migrations(conn)
        assert await get_version(conn) == LATEST_SCHEMA_VERSION
        assert await _index_names(conn) >= FEED_INDEXES
    finally:
        await conn.close()


@pytest.mark.asyncio
async def test_tolerates_a_messages_stub_without_the_columns():
    conn = await aiosqlite.connect(":memory:")
    try:
        await set_version(conn, 97)
        await conn.execute("CREATE TABLE messages (id INTEGER PRIMARY KEY)")
        await conn.commit()
        await run_migrations(conn)
        assert await get_version(conn) == LATEST_SCHEMA_VERSION
        assert not FEED_INDEXES & await _index_names(conn)
    finally:
        await conn.close()
