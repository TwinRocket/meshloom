"""Migration 132: per-channel digest interval (MSHL-136)."""

import aiosqlite
import pytest

from app.migrations import _132_channel_digest as m132


@pytest.mark.asyncio
async def test_adds_the_column_off_by_default_and_is_idempotent():
    async with aiosqlite.connect(":memory:") as conn:
        await conn.execute("CREATE TABLE notification_channels (id TEXT PRIMARY KEY, name TEXT)")
        await conn.execute("INSERT INTO notification_channels VALUES ('ntfy-1', 'ntfy')")
        await m132.migrate(conn)
        await m132.migrate(conn)
        cursor = await conn.execute("SELECT digest_interval_s FROM notification_channels")
        assert await cursor.fetchall() == [(0,)]


@pytest.mark.asyncio
async def test_no_table_is_a_no_op():
    async with aiosqlite.connect(":memory:") as conn:
        await m132.migrate(conn)
        cursor = await conn.execute("SELECT name FROM sqlite_master")
        assert await cursor.fetchall() == []
