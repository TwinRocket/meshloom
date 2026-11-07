"""Migration 133: raw_packets.payload_type and its index (MSHL-163)."""

import aiosqlite
import pytest

from app.migrations import _133_raw_packet_payload_type as m133


async def _legacy(conn: aiosqlite.Connection) -> None:
    await conn.execute(
        "CREATE TABLE raw_packets (id INTEGER PRIMARY KEY AUTOINCREMENT, timestamp INTEGER NOT NULL,"
        " data BLOB NOT NULL, message_id INTEGER, payload_hash BLOB)"
    )
    await conn.executemany(
        "INSERT INTO raw_packets (timestamp, data, payload_hash) VALUES (?, ?, ?)",
        [(1, b"\x11\x00", b"a"), (2, b"\x15\x00", b"b")],
    )


@pytest.mark.asyncio
async def test_adds_the_column_null_and_the_index_and_is_idempotent():
    async with aiosqlite.connect(":memory:") as conn:
        await _legacy(conn)
        await m133.migrate(conn)
        await m133.migrate(conn)  # a second run changes nothing
        cursor = await conn.execute("SELECT id, payload_type FROM raw_packets ORDER BY id")
        assert await cursor.fetchall() == [(1, None), (2, None)]  # nothing backfilled here
        cursor = await conn.execute("PRAGMA index_info(idx_raw_packets_payload_type_timestamp)")
        assert [row[2] for row in await cursor.fetchall()] == ["payload_type", "timestamp"]


@pytest.mark.asyncio
async def test_no_table_is_a_no_op():
    async with aiosqlite.connect(":memory:") as conn:
        await m133.migrate(conn)
        cursor = await conn.execute("SELECT name FROM sqlite_master")
        assert await cursor.fetchall() == []


@pytest.mark.asyncio
async def test_fresh_and_migrated_databases_have_the_same_column_and_index(tmp_path):
    from app.database import Database
    from app.migrations import run_migrations, set_version

    from .conftest import LATEST_SCHEMA_VERSION

    async def shape(path: str):
        async with aiosqlite.connect(path) as conn:
            cols = await (await conn.execute("PRAGMA table_info(raw_packets)")).fetchall()
            idx = await (
                await conn.execute("PRAGMA index_info(idx_raw_packets_payload_type_timestamp)")
            ).fetchall()
            return [(c[1], c[2]) for c in cols], [i[2] for i in idx]

    fresh = tmp_path / "fresh.db"
    database = Database(str(fresh))
    await database.connect()
    await database.disconnect()

    migrated = tmp_path / "migrated.db"
    async with aiosqlite.connect(migrated) as conn:
        await _legacy(conn)
        await set_version(conn, 132)
        assert await run_migrations(conn) == LATEST_SCHEMA_VERSION - 132
    assert await shape(str(migrated)) == await shape(str(fresh))
