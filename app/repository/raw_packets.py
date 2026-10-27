import asyncio
import heapq
import logging
import time
from collections.abc import AsyncIterator
from hashlib import sha256

from app.database import db
from app.decoder import PayloadType, extract_payload, get_packet_payload_type, parse_packet

logger = logging.getLogger(__name__)

UNDECRYPTED_PACKET_BATCH_SIZE = 500

# ``raw_packets.payload_type`` (migration 133): the header's payload type, or this value for a
# packet whose header does not parse. Never NULL once a row is classified, so NULL means "not
# backfilled yet" and nothing else; no filter can match this value.
UNPARSEABLE_PAYLOAD_TYPE = -1


def stored_payload_type(data: bytes) -> int:
    """The value stored in ``raw_packets.payload_type`` for these packet bytes."""
    parsed = get_packet_payload_type(data)
    return UNPARSEABLE_PAYLOAD_TYPE if parsed is None else int(parsed)


class RawPacketRepository:
    @staticmethod
    async def create(data: bytes, timestamp: int | None = None) -> tuple[int, bool]:
        """
        Create a raw packet with payload-based deduplication.

        Returns (packet_id, is_new) tuple:
        - is_new=True: New packet stored, packet_id is the new row ID
        - is_new=False: Duplicate payload detected, packet_id is the existing row ID

        Deduplication is based on the SHA-256 hash of the packet payload
        (excluding routing/path information).
        """
        ts = timestamp if timestamp is not None else int(time.time())

        # Compute payload hash for deduplication
        payload = extract_payload(data)
        if payload:
            payload_hash = sha256(payload).digest()
        else:
            # For malformed packets, hash the full data
            payload_hash = sha256(data).digest()

        async with db.tx() as conn:
            async with conn.execute(
                "INSERT OR IGNORE INTO raw_packets (timestamp, data, payload_hash, payload_type) "
                "VALUES (?, ?, ?, ?)",
                (ts, data, payload_hash, stored_payload_type(data)),
            ) as cursor:
                rowcount = cursor.rowcount
                lastrowid = cursor.lastrowid

            if rowcount > 0:
                assert lastrowid is not None
                return (lastrowid, True)

            # Duplicate payload — look up the existing row (same transaction).
            async with conn.execute(
                "SELECT id FROM raw_packets WHERE payload_hash = ?", (payload_hash,)
            ) as cursor:
                existing = await cursor.fetchone()
        assert existing is not None
        return (existing["id"], False)

    @staticmethod
    async def get_undecrypted_count() -> int:
        """Get count of undecrypted packets (those without a linked message)."""
        async with db.readonly() as conn:
            async with conn.execute(
                "SELECT COUNT(*) as count FROM raw_packets WHERE message_id IS NULL"
            ) as cursor:
                row = await cursor.fetchone()
        return row["count"] if row else 0

    @staticmethod
    async def get_oldest_undecrypted() -> int | None:
        """Get timestamp of oldest undecrypted packet, or None if none exist."""
        async with db.readonly() as conn:
            async with conn.execute(
                "SELECT MIN(timestamp) as oldest FROM raw_packets WHERE message_id IS NULL"
            ) as cursor:
                row = await cursor.fetchone()
        return row["oldest"] if row and row["oldest"] is not None else None

    @staticmethod
    async def _stream_undecrypted_rows(
        batch_size: int,
    ) -> AsyncIterator[tuple[int, bytes, int]]:
        """Internal: keyset-paginated scan of every undecrypted raw packet.

        Yields ``(id, data, timestamp)`` for each row across all batches.
        Lock is acquired per batch only — concurrent writes can interleave
        at batch boundaries rather than being blocked for the full scan.
        Each batch opens a fresh cursor and consumes it fully with
        ``fetchall()`` before releasing, so no prepared statement is alive
        at a yield boundary.

        ``last_id`` advances per row, not per yield, so external filters
        (see ``stream_undecrypted_text_messages``) that drop rows do not
        cause a re-scan of skipped IDs.
        """
        last_id = -1
        while True:
            async with db.readonly() as conn:
                async with conn.execute(
                    "SELECT id, data, timestamp FROM raw_packets "
                    "WHERE message_id IS NULL AND id > ? ORDER BY id ASC LIMIT ?",
                    (last_id, batch_size),
                ) as cursor:
                    rows = await cursor.fetchall()
            if not rows:
                return
            for row in rows:
                last_id = row["id"]
                yield (row["id"], bytes(row["data"]), row["timestamp"])

    @staticmethod
    async def _stream_undecrypted_rows_newest(
        received_since: int,
        batch_size: int,
    ) -> AsyncIterator[tuple[int, bytes, int]]:
        """Keyset-scan recent undecrypted packets from newest ID to oldest."""
        last_id = 2**63 - 1
        while True:
            async with db.readonly() as conn:
                async with conn.execute(
                    "SELECT id, data, timestamp FROM raw_packets "
                    "WHERE message_id IS NULL AND timestamp >= ? AND id < ? "
                    "ORDER BY id DESC LIMIT ?",
                    (received_since, last_id, batch_size),
                ) as cursor:
                    rows = await cursor.fetchall()
            if not rows:
                return
            for row in rows:
                last_id = row["id"]
                yield (row["id"], bytes(row["data"]), row["timestamp"])

    @staticmethod
    async def recent_data(limit: int, since: int | None = None) -> list[tuple[bytes, int]]:
        """The newest stored packets, as bytes and reception time.

        Kept deliberately dumb: whether a packet is regionally routed is a
        question for the decoder, not for SQL, and the column that would answer
        it does not exist. The caller parses and discards what it does not need.
        """
        clause = "WHERE timestamp >= ?" if since is not None else ""
        params: tuple[int, ...] = (since, limit) if since is not None else (limit,)
        async with db.readonly() as conn:
            async with conn.execute(
                f"SELECT data, timestamp FROM raw_packets {clause} ORDER BY id DESC LIMIT ?",
                params,
            ) as cursor:
                rows = await cursor.fetchall()
        return [(bytes(row["data"]), row["timestamp"]) for row in rows]

    @staticmethod
    async def get_undecrypted_group_text_samples(
        *,
        max_hashes: int,
        max_per_hash: int,
        max_scan: int,
        received_since: int,
        batch_size: int = UNDECRYPTED_PACKET_BATCH_SIZE,
    ) -> tuple[int, int, list[tuple[str, int, bytes, int, str]]]:
        """Collect distinct GROUP_TEXT payload samples from recent undecrypted packets.

        Returns ``(scanned, packet_count, samples)``. ``scanned`` counts all
        undecrypted rows walked inside the received-at window, while
        ``packet_count`` counts GROUP_TEXT rows among those scanned.
        """
        scanned = 0
        packet_count = 0
        samples: list[tuple[str, int, bytes, int, str]] = []
        fingerprints: dict[str, set[bytes]] = {}

        async for packet_id, data, timestamp in RawPacketRepository._stream_undecrypted_rows_newest(
            received_since,
            min(batch_size, max_scan),
        ):
            scanned += 1
            if get_packet_payload_type(data) == PayloadType.GROUP_TEXT:
                packet = parse_packet(data)
                if packet is not None and packet.payload_type == PayloadType.GROUP_TEXT:
                    packet_count += 1
                    payload = packet.payload
                    if len(payload) >= 3:
                        channel_hash = payload[0:1].hex()
                        distinct_payload = payload[1:]
                        seen = fingerprints.get(channel_hash)
                        if seen is None:
                            if len(fingerprints) >= max_hashes:
                                if scanned >= max_scan:
                                    break
                                continue
                            seen = set()
                            fingerprints[channel_hash] = seen
                        if len(seen) < max_per_hash and distinct_payload not in seen:
                            seen.add(distinct_payload)
                            samples.append(
                                (channel_hash, packet_id, data, timestamp, payload[1:3].hex())
                            )
            if scanned >= max_scan:
                break
            if (
                fingerprints
                and len(fingerprints) >= max_hashes
                and all(len(seen) >= max_per_hash for seen in fingerprints.values())
            ):
                break

        return scanned, packet_count, samples

    @staticmethod
    async def stream_all_undecrypted(
        batch_size: int = UNDECRYPTED_PACKET_BATCH_SIZE,
    ) -> AsyncIterator[tuple[int, bytes, int]]:
        """Yield all undecrypted packets as (id, data, timestamp) in bounded batches."""
        async for row in RawPacketRepository._stream_undecrypted_rows(batch_size):
            yield row

    @staticmethod
    async def stream_undecrypted_text_messages(
        batch_size: int = UNDECRYPTED_PACKET_BATCH_SIZE,
    ) -> AsyncIterator[tuple[int, bytes, int]]:
        """Yield undecrypted TEXT_MESSAGE packets in bounded-size batches.

        Filters the shared scan to rows whose payload parses as a text
        message. Non-matching rows still advance the keyset cursor so they
        aren't re-fetched on subsequent batches.
        """
        async for packet_id, data, timestamp in RawPacketRepository._stream_undecrypted_rows(
            batch_size
        ):
            if get_packet_payload_type(data) == PayloadType.TEXT_MESSAGE:
                yield (packet_id, data, timestamp)

    @staticmethod
    async def count_undecrypted_text_messages(
        batch_size: int = UNDECRYPTED_PACKET_BATCH_SIZE,
    ) -> int:
        """Count undecrypted TEXT_MESSAGE packets without materializing them all."""
        count = 0
        async for _packet in RawPacketRepository.stream_undecrypted_text_messages(
            batch_size=batch_size
        ):
            count += 1
        return count

    @staticmethod
    async def mark_decrypted(packet_id: int, message_id: int) -> None:
        """Link a raw packet to its decrypted message."""
        async with db.tx() as conn:
            async with conn.execute(
                "UPDATE raw_packets SET message_id = ? WHERE id = ?",
                (message_id, packet_id),
            ):
                pass

    @staticmethod
    async def get_linked_message_id(packet_id: int) -> int | None:
        """Return the linked message ID for a raw packet, if any."""
        async with db.readonly() as conn:
            async with conn.execute(
                "SELECT message_id FROM raw_packets WHERE id = ?",
                (packet_id,),
            ) as cursor:
                row = await cursor.fetchone()
        if not row:
            return None
        return row["message_id"]

    @staticmethod
    async def get_by_id(packet_id: int) -> tuple[int, bytes, int, int | None] | None:
        """Return a raw packet row as (id, data, timestamp, message_id)."""
        async with db.readonly() as conn:
            async with conn.execute(
                "SELECT id, data, timestamp, message_id FROM raw_packets WHERE id = ?",
                (packet_id,),
            ) as cursor:
                row = await cursor.fetchone()
        if not row:
            return None
        return (row["id"], bytes(row["data"]), row["timestamp"], row["message_id"])

    @staticmethod
    def _history_window_sql(
        since: int | None,
        until: int | None,
        cursor_ts: int | None,
        cursor_id: int | None,
        id_before: int | None,
    ) -> tuple[str, list[int]]:
        """Build a timestamp-index window for newest-first history paging."""
        clauses: list[str] = []
        params: list[int] = []
        if since is not None:
            clauses.append("timestamp >= ?")
            params.append(since)
        if until is not None:
            clauses.append("timestamp <= ?")
            params.append(until)
        if cursor_ts is not None and cursor_id is not None:
            clauses.append("(timestamp < ? OR (timestamp = ? AND id < ?))")
            params.extend([cursor_ts, cursor_ts, cursor_id])
        elif id_before is not None:
            clauses.append("id < ?")
            params.append(id_before)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        return where, params

    @staticmethod
    async def payload_type_populated() -> bool:
        """True when no row is left without a ``payload_type`` (an index seek on NULL)."""
        async with db.readonly() as conn:
            async with conn.execute(
                "SELECT 1 FROM raw_packets WHERE payload_type IS NULL LIMIT 1"
            ) as cursor:
                return await cursor.fetchone() is None

    @staticmethod
    def _history_indexed_sql(
        since: int | None,
        until: int | None,
        cursor_ts: int | None,
        cursor_id: int | None,
        id_before: int | None,
    ) -> tuple[str, list[int]]:
        """The per-type query: ``payload_type = ?`` leads, so the index orders by timestamp."""
        window, params = RawPacketRepository._history_window_sql(
            since, until, cursor_ts, cursor_id, id_before
        )
        extra = f" AND {window[len('WHERE ') :]}" if window else ""
        sql = (
            "SELECT id, data, timestamp, message_id FROM raw_packets "
            f"WHERE payload_type = ?{extra} ORDER BY timestamp DESC, id DESC LIMIT ?"
        )
        return sql, params

    @staticmethod
    async def _list_history_indexed(
        payload_types: frozenset[PayloadType],
        since: int | None,
        until: int | None,
        cursor_ts: int | None,
        cursor_id: int | None,
        id_before: int | None,
        limit: int,
    ) -> list[tuple[int, bytes, int, int | None]]:
        sql, params = RawPacketRepository._history_indexed_sql(
            since, until, cursor_ts, cursor_id, id_before
        )
        per_type: list[list[tuple[int, bytes, int, int | None]]] = []
        async with db.readonly() as conn:
            for ptype in sorted(payload_types):
                async with conn.execute(sql, [int(ptype), *params, limit]) as cursor:
                    fetched = await cursor.fetchall()
                per_type.append(
                    [
                        (row["id"], bytes(row["data"]), row["timestamp"], row["message_id"])
                        for row in fetched
                    ]
                )
        merged = heapq.merge(*per_type, key=lambda r: (r[2], r[0]), reverse=True)
        return [row for _, row in zip(range(limit), merged, strict=False)]

    @staticmethod
    async def list_history(
        *,
        since: int | None = None,
        until: int | None = None,
        after_id: int | None = None,
        limit: int,
        max_scan: int,
        payload_types: frozenset[PayloadType] | None = None,
        batch_size: int = UNDECRYPTED_PACKET_BATCH_SIZE,
    ) -> tuple[list[tuple[int, bytes, int, int | None]], int, bool, int]:
        """Bounded newest-first history using ``idx_raw_packets_timestamp``.

        ``payload_types`` (any of the given types) is served by the
        ``(payload_type, timestamp)`` index once every row carries a
        ``payload_type`` (migration 133 and its background backfill): one
        bounded query per type, merged newest first. ``max_scan`` then bounds
        nothing (``scanned`` is the rows returned, ``truncated`` is false).
        While some row is still NULL, each header is parsed during a scan
        capped by ``max_scan``. Returns ``(rows, total, truncated, scanned)``
        where ``total`` is the SQL COUNT of the time/id window, ``scanned`` is
        rows walked, and ``truncated`` is true when the scan stopped at
        ``max_scan`` with more window rows left.
        """
        cursor_ts: int | None = None
        cursor_id: int | None = None
        id_before: int | None = None
        if after_id is not None:
            cursor_row = await RawPacketRepository.get_by_id(after_id)
            if cursor_row is not None:
                cursor_id = cursor_row[0]
                cursor_ts = cursor_row[2]
            else:
                id_before = after_id

        where, params = RawPacketRepository._history_window_sql(
            since, until, cursor_ts, cursor_id, id_before
        )
        async with db.readonly() as conn:
            async with conn.execute(
                f"SELECT COUNT(*) as count FROM raw_packets {where}",
                params,
            ) as cursor:
                count_row = await cursor.fetchone()
        total = count_row["count"] if count_row else 0

        if payload_types is not None and await RawPacketRepository.payload_type_populated():
            indexed = await RawPacketRepository._list_history_indexed(
                payload_types, since, until, cursor_ts, cursor_id, id_before, limit
            )
            return indexed, total, False, len(indexed)

        rows: list[tuple[int, bytes, int, int | None]] = []
        scanned = 0
        last_ts = cursor_ts
        last_id = cursor_id
        last_id_before = id_before

        while scanned < max_scan and len(rows) < limit:
            remaining_scan = max_scan - scanned
            batch_limit = min(batch_size, remaining_scan)
            if payload_types is None:
                batch_limit = min(batch_limit, limit - len(rows))
            if batch_limit <= 0:
                break

            batch_where, batch_params = RawPacketRepository._history_window_sql(
                since, until, last_ts, last_id, last_id_before if last_id is None else None
            )
            async with db.readonly() as conn:
                async with conn.execute(
                    "SELECT id, data, timestamp, message_id FROM raw_packets "
                    f"{batch_where} ORDER BY timestamp DESC, id DESC LIMIT ?",
                    [*batch_params, batch_limit],
                ) as cursor:
                    batch = await cursor.fetchall()
            if not batch:
                break

            last_id_before = None
            for row in batch:
                scanned += 1
                last_ts = row["timestamp"]
                last_id = row["id"]
                data = bytes(row["data"])
                if payload_types is None or get_packet_payload_type(data) in payload_types:
                    rows.append((row["id"], data, row["timestamp"], row["message_id"]))
                    if len(rows) >= limit:
                        break
                if scanned >= max_scan:
                    break

        truncated = False
        if scanned >= max_scan and last_id is not None:
            more_where, more_params = RawPacketRepository._history_window_sql(
                since, until, last_ts, last_id, None
            )
            async with db.readonly() as conn:
                async with conn.execute(
                    f"SELECT 1 FROM raw_packets {more_where} LIMIT 1",
                    more_params,
                ) as cursor:
                    truncated = await cursor.fetchone() is not None
        elif scanned >= max_scan and last_id_before is not None:
            more_where, more_params = RawPacketRepository._history_window_sql(
                since, until, None, None, last_id_before
            )
            async with db.readonly() as conn:
                async with conn.execute(
                    f"SELECT 1 FROM raw_packets {more_where} LIMIT 1",
                    more_params,
                ) as cursor:
                    truncated = await cursor.fetchone() is not None

        return rows, total, truncated, scanned

    @staticmethod
    async def prune_old_undecrypted(max_age_days: int) -> int:
        """Delete undecrypted packets older than max_age_days. Returns count deleted."""
        cutoff = int(time.time()) - (max_age_days * 86400)
        async with db.tx() as conn:
            async with conn.execute(
                "DELETE FROM raw_packets WHERE message_id IS NULL AND timestamp < ?",
                (cutoff,),
            ) as cursor:
                rowcount = cursor.rowcount
        return rowcount

    @staticmethod
    async def prune_old_undecrypted_batched(max_age_days: int, batch_size: int = 2000) -> int:
        """Like ``prune_old_undecrypted`` but in short transactions.

        Used by the periodic retention job: each batch releases the DB lock so
        packet ingest is never stalled behind one huge DELETE.
        """
        cutoff = int(time.time()) - (max_age_days * 86400)
        total = 0
        while True:
            async with db.tx() as conn:
                async with conn.execute(
                    """
                    DELETE FROM raw_packets WHERE id IN (
                        SELECT id FROM raw_packets
                        WHERE timestamp < ? AND message_id IS NULL
                        LIMIT ?
                    )
                    """,
                    (cutoff, batch_size),
                ) as cursor:
                    deleted = cursor.rowcount
            total += deleted
            if deleted < batch_size:
                return total
            await asyncio.sleep(0)

    @staticmethod
    async def purge_linked_to_messages() -> int:
        """Delete raw packets that are already linked to a stored message."""
        async with db.tx() as conn:
            async with conn.execute(
                "DELETE FROM raw_packets WHERE message_id IS NOT NULL"
            ) as cursor:
                rowcount = cursor.rowcount
        return rowcount


BACKFILL_BATCH_SIZE = 2000


async def backfill_payload_types(batch_size: int = BACKFILL_BATCH_SIZE) -> int:
    """Fill ``raw_packets.payload_type`` for the rows still NULL (migration 133). Returns the count.

    Batched and resumable: each batch reads the first header byte of the next ``batch_size``
    unclassified rows (keyset on ``id``) and writes them in its own short transaction, then yields
    the connection, so live traffic is never held behind one long write. A crash or a restart
    simply carries on from the rows still NULL. Writers classify new rows on insert, so a row
    never goes back to NULL.
    """
    last_id = -1
    done = 0
    while True:
        async with db.readonly() as conn:
            async with conn.execute(
                "SELECT id, substr(data, 1, 1) AS head FROM raw_packets "
                "WHERE id > ? AND payload_type IS NULL ORDER BY id LIMIT ?",
                (last_id, batch_size),
            ) as cursor:
                rows = list(await cursor.fetchall())
        if not rows:
            return done
        updates = [
            (stored_payload_type(bytes(row["head"]) if row["head"] is not None else b""), row["id"])
            for row in rows
        ]
        last_id = rows[-1]["id"]
        async with db.tx() as conn:
            await conn.executemany(
                "UPDATE raw_packets SET payload_type = ? WHERE id = ? AND payload_type IS NULL",
                updates,
            )
        done += len(rows)
        await asyncio.sleep(0)
