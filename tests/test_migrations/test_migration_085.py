import aiosqlite
import pytest

from app.migrations import get_version, run_migrations, set_version

from .conftest import LATEST_SCHEMA_VERSION


class TestMigration085:
    @pytest.mark.asyncio
    async def test_backfills_eligible_when_hash_exists(self):
        conn = await aiosqlite.connect(":memory:")
        conn.row_factory = aiosqlite.Row
        try:
            await set_version(conn, 84)
            await conn.execute(
                """
                CREATE TABLE messages (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    type TEXT NOT NULL,
                    conversation_key TEXT NOT NULL,
                    text TEXT NOT NULL,
                    sender_timestamp INTEGER,
                    received_at INTEGER NOT NULL,
                    outgoing INTEGER DEFAULT 0,
                    packet_hash TEXT,
                    observer_reach_eligible INTEGER
                )
                """
            )
            await conn.execute(
                """
                INSERT INTO messages (
                    type, conversation_key, text, sender_timestamp, received_at,
                    outgoing, packet_hash, observer_reach_eligible
                )
                VALUES
                    ('PRIV', ?, 'hello', 1700000000, 1700000000, 0, 'AABBCCDDEEFF0011', 0),
                    ('PRIV', ?, 'nohash', 1700000001, 1700000001, 0, NULL, 0),
                    ('PRIV', ?, 'already', 1700000002, 1700000002, 0, 'BBBBCCDDEEFF0011', 1)
                """,
                ("aa" * 32, "bb" * 32, "cc" * 32),
            )
            await conn.commit()

            applied = await run_migrations(conn)
            assert applied == LATEST_SCHEMA_VERSION - 84
            assert await get_version(conn) == LATEST_SCHEMA_VERSION

            rows = await (
                await conn.execute(
                    """
                    SELECT text, observer_reach_eligible
                    FROM messages
                    ORDER BY id
                    """
                )
            ).fetchall()
            assert rows[0]["observer_reach_eligible"] == 1
            assert rows[1]["observer_reach_eligible"] == 0
            assert rows[2]["observer_reach_eligible"] == 1
        finally:
            await conn.close()
