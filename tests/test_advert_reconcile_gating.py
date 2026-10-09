"""Advert ingest only reconciles messages when something could have changed."""

import asyncio
import json
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from app.repository import ContactRepository, MessageRepository

FIXTURES = json.loads((Path(__file__).parent / "fixtures" / "websocket_events.json").read_text())
ADVERT = bytes.fromhex(FIXTURES["advertisement_chat_node"]["raw_packet_hex"])
ADVERT_KEY = FIXTURES["advertisement_chat_node"]["expected_ws_event"]["data"]["public_key"]
ADVERT_NAME = FIXTURES["advertisement_chat_node"]["expected_ws_event"]["data"]["name"]


def _with_path(raw: bytes, hop: int) -> bytes:
    """Same advert payload observed over a different 1-hop path."""
    return bytes([raw[0], 0x01, hop]) + raw[2 + (raw[1] & 0x3F) :]


async def _ingest(raw: bytes, ts: int, reconcile: AsyncMock, promote: AsyncMock) -> None:
    from app.packet_processor import process_raw_packet

    with (
        patch("app.packet_processor.broadcast_event"),
        patch("app.packet_processor.record_contact_name_and_reconcile", reconcile),
        patch("app.packet_processor.promote_prefix_contacts_for_contact", promote),
    ):
        await process_raw_packet(raw, timestamp=ts)


@pytest.mark.asyncio
async def test_duplicate_advert_observation_skips_reconcile(test_db):
    reconcile = AsyncMock(return_value=(0, 0))
    promote = AsyncMock(return_value=[])

    await _ingest(_with_path(ADVERT, 1), 1000, reconcile, promote)
    assert reconcile.await_count == 1  # new contact
    assert promote.await_count == 1

    for hop in range(2, 6):
        await _ingest(_with_path(ADVERT, hop), 1000 + hop, reconcile, promote)

    # Same payload heard over other paths: contact refreshed, no reconcile.
    assert reconcile.await_count == 1
    assert promote.await_count == 1
    contact = await ContactRepository.get_by_key(ADVERT_KEY)
    assert contact is not None and contact.last_seen == 1005


@pytest.mark.asyncio
async def test_duplicate_advert_reconciles_when_contact_was_renamed(test_db):
    reconcile = AsyncMock(return_value=(0, 0))
    promote = AsyncMock(return_value=[])
    await _ingest(_with_path(ADVERT, 1), 1000, reconcile, promote)
    await ContactRepository.upsert({"public_key": ADVERT_KEY, "name": "Renamed elsewhere"})

    await _ingest(_with_path(ADVERT, 2), 1001, reconcile, promote)
    assert reconcile.await_count == 2


@pytest.mark.asyncio
async def test_new_contact_advert_backfills_channel_sender(test_db):
    """End to end: the first advert attributes earlier channel messages."""
    from app.packet_processor import process_raw_packet

    await MessageRepository.create(
        msg_type="CHAN",
        text=f"{ADVERT_NAME}: hi",
        conversation_key="AA" * 16,
        sender_timestamp=900,
        received_at=900,
        sender_name=ADVERT_NAME,
    )
    with patch("app.packet_processor.broadcast_event"):
        await process_raw_packet(ADVERT, timestamp=1000)

    messages = await MessageRepository.get_all(msg_type="CHAN", conversation_key="AA" * 16)
    assert [m.sender_key for m in messages] == [ADVERT_KEY]


@pytest.mark.asyncio
async def test_radio_sync_reconciles_contacts_sequentially(test_db):
    import app.radio_sync as radio_sync

    running = 0
    max_running = 0
    seen: list[str] = []

    async def fake_reconcile(public_key, name):
        nonlocal running, max_running
        running += 1
        max_running = max(max_running, running)
        await asyncio.sleep(0)
        seen.append(public_key)
        running -= 1

    with patch.object(radio_sync, "_reconcile_contact_messages_background", fake_reconcile):
        radio_sync._schedule_contact_reconcile_batch([(f"{i:02x}" * 32, f"n{i}") for i in range(5)])
        # A second sync while the worker runs is folded into the same worker.
        radio_sync._schedule_contact_reconcile_batch([("ff" * 32, "late")])
        task = radio_sync._reconcile_batch_task
        assert task is not None
        await task

    assert max_running == 1
    assert len(seen) == 6 and seen[-1] == "ff" * 32
