"""Opt-in raw-packet retention and bounded incremental vacuum."""

import time

import pytest

from app.database import Database
from app.repository import AppSettingsRepository, RawPacketRepository
from app.routers.settings import AppSettingsUpdate, update_settings
from app.services.stale_contacts import prune_raw_packets


async def _packet(i: int, age_days: float) -> int:
    ts = int(time.time() - age_days * 86400)
    packet_id, _ = await RawPacketRepository.create(bytes([0x15, 0x00]) + bytes([i]) * 40, ts)
    return packet_id


@pytest.mark.asyncio
async def test_retention_defaults_to_off_and_keeps_everything(test_db):
    settings = await AppSettingsRepository.get()
    assert settings.raw_packet_retention_days == 0
    await _packet(1, 400)
    assert await prune_raw_packets() == 0
    assert await RawPacketRepository.get_undecrypted_count() == 1


@pytest.mark.asyncio
async def test_retention_prunes_only_old_undecrypted_packets(test_db):
    result = await update_settings(AppSettingsUpdate(raw_packet_retention_days=30))
    assert result.raw_packet_retention_days == 30

    old_undecrypted = [await _packet(i, 45) for i in range(1, 6)]
    recent = await _packet(50, 5)
    old_linked = await _packet(60, 45)
    async with test_db.tx() as conn:
        await conn.execute(
            "INSERT INTO messages (id, type, conversation_key, text, received_at)"
            " VALUES (999, 'CHAN', 'AA', 'x', 1)"
        )
    await RawPacketRepository.mark_decrypted(old_linked, 999)

    assert await prune_raw_packets() == len(old_undecrypted)
    assert await RawPacketRepository.get_by_id(recent) is not None
    assert await RawPacketRepository.get_by_id(old_linked) is not None
    for packet_id in old_undecrypted:
        assert await RawPacketRepository.get_by_id(packet_id) is None


@pytest.mark.asyncio
async def test_batched_prune_crosses_batch_boundaries(test_db):
    for i in range(1, 8):
        await _packet(i, 45)
    assert await RawPacketRepository.prune_old_undecrypted_batched(30, batch_size=3) == 7
    assert await RawPacketRepository.get_undecrypted_count() == 0


@pytest.mark.asyncio
async def test_incremental_vacuum_returns_free_pages(tmp_path):
    database = Database(str(tmp_path / "vac.db"))
    await database.connect()
    try:
        async with database.tx() as conn:
            await conn.execute("CREATE TABLE filler (blob BLOB)")
            await conn.executemany(
                "INSERT INTO filler VALUES (?)", [(b"x" * 4000,) for _ in range(300)]
            )
        async with database.tx() as conn:
            await conn.execute("DELETE FROM filler")

        freed = await database.incremental_vacuum(100)
        assert freed == 100
        assert await database.incremental_vacuum(10_000) > 0
        assert await database.incremental_vacuum(10_000) == 0
    finally:
        await database.disconnect()
