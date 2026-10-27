"""Migration 133: ``raw_packets.payload_type`` and its index (MSHL-163).

Adds ``payload_type INTEGER`` (NULL = not classified yet) and the index
``(payload_type, timestamp)`` that serves the control history of ``GET /api/packets/history``.
Nothing is backfilled here: reading the first byte of every stored packet would hold one write
transaction for as long as the table is large, on a Raspberry Pi. The rows are classified in the
background after startup (``backfill_payload_types`` in ``app/repository/raw_packets.py``), in
short batches, resumable; until the last one is done the history keeps parsing headers. Writers
set the column on insert. Idempotent: a database created from ``SCHEMA_TABLES`` already has both.
"""

import aiosqlite


async def migrate(conn: aiosqlite.Connection) -> None:
    cursor = await conn.execute("PRAGMA table_info(raw_packets)")
    existing = {row[1] for row in await cursor.fetchall()}
    if not existing:
        return
    if "payload_type" not in existing:
        await conn.execute("ALTER TABLE raw_packets ADD COLUMN payload_type INTEGER")
    if "timestamp" not in existing:  # a stub table of an old test fixture
        return
    await conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_raw_packets_payload_type_timestamp "
        "ON raw_packets(payload_type, timestamp)"
    )
