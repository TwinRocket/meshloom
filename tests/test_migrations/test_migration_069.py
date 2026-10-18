"""Tests for migration 069: persist packet_hash and observer-reach eligibility."""

import hashlib

import aiosqlite
import pytest

from app.decoder import encrypt_group_text
from app.migrations import get_version, run_migrations, set_version
from app.path_utils import calculate_packet_hash, canonical_packet_hash
from tests.test_migrations.conftest import LATEST_SCHEMA_VERSION


def _group_text_packet(channel_key: bytes, timestamp: int, text: str) -> bytes:
    payload = encrypt_group_text(channel_key, timestamp, text, 0)
    return bytes([0x15, 0x00]) + payload


class TestMigration069:
    @pytest.mark.asyncio
    async def test_adds_columns_and_backfills_from_raw_packets(self):
        conn = await aiosqlite.connect(":memory:")
        conn.row_factory = aiosqlite.Row
        try:
            await set_version(conn, 68)
            await conn.execute(
                """
                CREATE TABLE messages (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    type TEXT NOT NULL,
                    conversation_key TEXT NOT NULL,
                    text TEXT NOT NULL,
                    sender_timestamp INTEGER,
                    received_at INTEGER NOT NULL,
                    outgoing INTEGER DEFAULT 0
                )
                """
            )
            await conn.execute(
                """
                CREATE TABLE raw_packets (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    data BLOB NOT NULL,
                    message_id INTEGER
                )
                """
            )
            channel_key = hashlib.sha256(b"#backfill").digest()[:16]
            raw = _group_text_packet(channel_key, 1_700_000_000, "Alice: hi")
            await conn.execute(
                """
                INSERT INTO messages (type, conversation_key, text, sender_timestamp, received_at)
                VALUES ('CHAN', ?, 'Alice: hi', 1700000000, 1700000000)
                """,
                (channel_key.hex().upper(),),
            )
            await conn.execute(
                "INSERT INTO raw_packets (data, message_id) VALUES (?, 1)",
                (raw,),
            )
            await conn.commit()

            applied = await run_migrations(conn)
            assert applied == LATEST_SCHEMA_VERSION - 68
            assert await get_version(conn) == LATEST_SCHEMA_VERSION

            columns = {
                row[1]
                for row in await (await conn.execute("PRAGMA table_info(messages)")).fetchall()
            }
            assert "packet_hash" in columns
            assert "observer_reach_eligible" in columns

            row = await (
                await conn.execute(
                    "SELECT packet_hash, observer_reach_eligible FROM messages WHERE id = 1"
                )
            ).fetchone()
            assert row["packet_hash"] == canonical_packet_hash(calculate_packet_hash(raw))
            assert row["observer_reach_eligible"] == 1
        finally:
            await conn.close()

    @pytest.mark.asyncio
    async def test_stub_messages_table_without_type_does_not_fail(self):
        conn = await aiosqlite.connect(":memory:")
        conn.row_factory = aiosqlite.Row
        try:
            await set_version(conn, 68)
            await conn.execute("CREATE TABLE messages (id INTEGER PRIMARY KEY, text TEXT NOT NULL)")
            await conn.execute(
                "CREATE TABLE raw_packets (id INTEGER PRIMARY KEY, data BLOB NOT NULL)"
            )
            await conn.commit()

            await run_migrations(conn)

            columns = {
                row[1]
                for row in await (await conn.execute("PRAGMA table_info(messages)")).fetchall()
            }
            assert "packet_hash" in columns
            assert "observer_reach_eligible" in columns
            assert await get_version(conn) == LATEST_SCHEMA_VERSION
        finally:
            await conn.close()
