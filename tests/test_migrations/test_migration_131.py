"""Migration 131: per-channel heartbeat columns (MSHL-131)."""

import aiosqlite
import pytest

from app.migrations import _131_channel_heartbeat as m131


@pytest.mark.asyncio
async def test_adds_the_heartbeat_columns_off_by_default_and_is_idempotent():
    async with aiosqlite.connect(":memory:") as conn:
        await conn.execute("CREATE TABLE notification_channels (id TEXT PRIMARY KEY, name TEXT)")
        await conn.execute("INSERT INTO notification_channels VALUES ('ntfy-1', 'ntfy')")
        await m131.migrate(conn)
        await m131.migrate(conn)  # a second run changes nothing
        cursor = await conn.execute(
            "SELECT heartbeat_interval_s, heartbeat_method FROM notification_channels"
        )
        assert await cursor.fetchall() == [(0, "GET")]


@pytest.mark.asyncio
async def test_no_table_is_a_no_op():
    async with aiosqlite.connect(":memory:") as conn:
        await m131.migrate(conn)
        cursor = await conn.execute("SELECT name FROM sqlite_master")
        assert await cursor.fetchall() == []
