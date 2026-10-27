"""raw_packets.payload_type: writers, resumable backfill, indexed history (MSHL-163)."""

import os
import time

import pytest

from app.decoder import PayloadType
from app.repository.raw_packets import (
    UNPARSEABLE_PAYLOAD_TYPE,
    RawPacketRepository,
    backfill_payload_types,
)

CONTROL = frozenset(
    {
        PayloadType.REQUEST,
        PayloadType.RESPONSE,
        PayloadType.ANON_REQUEST,
        PayloadType.GROUP_DATA,
    }
)
BASE = 1_700_000_000


def _packet(ptype: PayloadType, seed: int) -> bytes:
    return bytes([(int(ptype) << 2) | 1, 0x00]) + seed.to_bytes(4, "big") + os.urandom(12)


async def _insert_null(db, rows: list[tuple[int, bytes]]) -> None:
    async with db.tx() as conn:
        await conn.executemany(
            "INSERT INTO raw_packets (timestamp, data, payload_hash) VALUES (?, ?, ?)",
            [(ts, data, os.urandom(8)) for ts, data in rows],
        )


async def _types(db) -> list[int | None]:
    async with db.readonly() as conn:
        async with conn.execute("SELECT payload_type FROM raw_packets ORDER BY id") as cur:
            return [row[0] for row in await cur.fetchall()]


@pytest.mark.asyncio
async def test_writer_sets_the_type(test_db):
    await RawPacketRepository.create(_packet(PayloadType.ADVERT, 1), BASE)
    await RawPacketRepository.create(b"", BASE + 1)  # header does not parse
    await RawPacketRepository.create(bytes([0x0C << 2 | 1, 0]) + b"x" * 8, BASE + 2)  # type 12
    assert await _types(test_db) == [int(PayloadType.ADVERT), UNPARSEABLE_PAYLOAD_TYPE, -1]


@pytest.mark.asyncio
async def test_backfill_is_batched_and_resumable(test_db):
    await _insert_null(
        test_db, [(BASE + i, _packet(PayloadType.RESPONSE, i)) for i in range(25)] + [(BASE, b"")]
    )
    assert await _types(test_db) == [None] * 26
    assert await RawPacketRepository.payload_type_populated() is False
    assert await backfill_payload_types(batch_size=10) == 26
    assert set(await _types(test_db)) == {int(PayloadType.RESPONSE), UNPARSEABLE_PAYLOAD_TYPE}
    assert await RawPacketRepository.payload_type_populated() is True
    assert await backfill_payload_types(batch_size=10) == 0  # nothing left: a no-op

    # Interrupted run: a few rows done, the rest NULL; the next run finishes only those.
    await _insert_null(
        test_db, [(BASE + 100 + i, _packet(PayloadType.ADVERT, i)) for i in range(7)]
    )
    async with test_db.tx() as conn:
        await conn.execute("UPDATE raw_packets SET payload_type = 4 WHERE id IN (27, 28)")
    assert await backfill_payload_types(batch_size=3) == 5
    assert None not in await _types(test_db)


@pytest.mark.asyncio
async def test_indexed_and_parsed_paths_agree(test_db):
    types = [PayloadType.ADVERT, *sorted(CONTROL), PayloadType.TEXT_MESSAGE]
    rows = [(BASE + i // 3, _packet(types[i % len(types)], i)) for i in range(60)]
    await _insert_null(test_db, rows)

    parsed = await RawPacketRepository.list_history(
        limit=1000, max_scan=1000, payload_types=CONTROL
    )
    assert parsed[3] == 60  # NULL rows: every row walked
    await backfill_payload_types()
    indexed = await RawPacketRepository.list_history(
        limit=1000, max_scan=1000, payload_types=CONTROL
    )
    assert indexed[0] == parsed[0] and len(indexed[0]) == 40
    assert indexed[2] is False and indexed[3] == 40

    # Paging through a cursor gives the same pages on both paths.
    first = await RawPacketRepository.list_history(limit=10, max_scan=1000, payload_types=CONTROL)
    second = await RawPacketRepository.list_history(
        limit=10, max_scan=1000, payload_types=CONTROL, after_id=first[0][-1][0]
    )
    assert [r[0] for r in first[0] + second[0]] == [r[0] for r in indexed[0][:20]]


@pytest.mark.asyncio
async def test_a_null_row_keeps_the_header_parsing_path(test_db):
    await RawPacketRepository.create(_packet(PayloadType.REQUEST, 1), BASE)
    await _insert_null(test_db, [(BASE + 1, _packet(PayloadType.ADVERT, 2))])
    rows, _total, truncated, scanned = await RawPacketRepository.list_history(
        limit=10, max_scan=1, payload_types=CONTROL
    )
    assert scanned == 1 and truncated is True  # max_scan applies while a row is unclassified
    await backfill_payload_types()
    rows, _total, truncated, scanned = await RawPacketRepository.list_history(
        limit=10, max_scan=1, payload_types=CONTROL
    )
    assert len(rows) == 1 and truncated is False and scanned == 1


@pytest.mark.asyncio
async def test_plan_uses_the_payload_type_index_without_temp_sort(test_db):
    sql, params = RawPacketRepository._history_indexed_sql(BASE, BASE + 99, BASE + 50, 7, None)
    for statement, args in (
        (sql, [int(PayloadType.REQUEST), *params, 200]),
        (RawPacketRepository._history_indexed_sql(None, None, None, None, None)[0], [0, 200]),
    ):
        async with test_db.readonly() as conn:
            async with conn.execute(f"EXPLAIN QUERY PLAN {statement}", args) as cur:
                plan = " | ".join(row[3] for row in await cur.fetchall())
        assert "idx_raw_packets_payload_type_timestamp" in plan, plan
        assert "TEMP B-TREE" not in plan, plan


@pytest.mark.slow
@pytest.mark.asyncio
async def test_four_type_history_on_100000_rows_is_fast(test_db):
    """The old scan walked 20 000 rows to find a hundred; the index reads what it returns."""
    rows = []
    for i in range(100_000):
        ptype = sorted(CONTROL)[(i // 200) % 4] if i % 200 == 0 else PayloadType.ADVERT
        rows.append((BASE + i, _packet(ptype, i)))
    await _insert_null(test_db, rows)
    t0 = time.perf_counter()
    assert await backfill_payload_types() == 100_000
    backfill = time.perf_counter() - t0
    t0 = time.perf_counter()
    items, _total, truncated, scanned = await RawPacketRepository.list_history(
        limit=200, max_scan=20_000, payload_types=CONTROL
    )
    elapsed = time.perf_counter() - t0
    print(f"backfill 100k rows: {backfill:.1f} s; four-type history: {elapsed * 1000:.0f} ms")
    assert len(items) == 200 and truncated is False and scanned == 200
    assert elapsed < 1.0


@pytest.mark.asyncio
async def test_backfill_query_is_a_rowid_scan_without_sort_or_payload_type_index(test_db):
    from app.repository.raw_packets import BACKFILL_SELECT_SQL

    async with test_db.readonly() as conn:
        async with conn.execute(f"EXPLAIN QUERY PLAN {BACKFILL_SELECT_SQL}", [0, 2000]) as cur:
            plan = " | ".join(row[3] for row in await cur.fetchall())
    assert "idx_raw_packets_payload_type_timestamp" not in plan, plan
    assert "TEMP B-TREE" not in plan, plan


@pytest.mark.asyncio
async def test_merge_orders_by_timestamp_then_id_when_ids_are_out_of_time_order(test_db):
    """A clock jump: later ids carry older timestamps, across types."""
    types = sorted(CONTROL)
    stamps = [BASE + 500, BASE + 100, BASE + 400, BASE + 100, BASE + 300, BASE + 200, BASE + 500]
    rows = [(ts, _packet(types[i % 4], i)) for i, ts in enumerate(stamps)]
    await _insert_null(test_db, rows)
    await backfill_payload_types()
    items, *_ = await RawPacketRepository.list_history(
        limit=100, max_scan=100, payload_types=CONTROL
    )
    got = [(r[2], r[0]) for r in items]
    assert got == sorted(got, reverse=True)
    assert [r[0] for r in items] != sorted((r[0] for r in items), reverse=True)  # not id order
    assert len(items) == 7


@pytest.mark.asyncio
async def test_a_page_supplied_by_one_type_is_full(test_db):
    await _insert_null(
        test_db,
        [(BASE + 1000 + i, _packet(PayloadType.REQUEST, i)) for i in range(50)]
        + [(BASE + i, _packet(PayloadType.RESPONSE, i)) for i in range(5)],
    )
    await backfill_payload_types()
    first, _t, _tr, scanned = await RawPacketRepository.list_history(
        limit=20, max_scan=100, payload_types=CONTROL
    )
    assert len(first) == 20 and scanned == 20
    assert all(r[2] >= BASE + 1000 for r in first)
    second, *_ = await RawPacketRepository.list_history(
        limit=40, max_scan=100, payload_types=CONTROL, after_id=first[-1][0]
    )
    assert len(second) == 35  # the 30 REQUEST left, then the 5 RESPONSE
