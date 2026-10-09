"""GroupText trial-decrypt index: hash byte -> channels, invalidated on channel writes."""

import hashlib
from unittest.mock import patch

import pytest

from app.decoder import encrypt_group_text
from app.repository import ChannelRepository, MessageRepository

KEY_A = hashlib.sha256(b"#alpha").digest()[:16]
KEY_B = hashlib.sha256(b"#bravo").digest()[:16]


def _packet(key: bytes, ts: int, text: str) -> bytes:
    return bytes([0x15, 0x00]) + encrypt_group_text(key, ts, text)


async def _ingest(raw: bytes, ts: int) -> dict:
    from app.packet_processor import process_raw_packet

    with (
        patch("app.packet_processor.broadcast_event"),
        patch("app.services.hashtag_catalogue.schedule_unknown_group_text_resolve"),
    ):
        return await process_raw_packet(raw, timestamp=ts)


@pytest.mark.asyncio
async def test_candidates_are_bucketed_by_key_hash_byte(test_db):
    await ChannelRepository.upsert(KEY_A.hex().upper(), "#alpha")
    await ChannelRepository.upsert(KEY_B.hex().upper(), "#bravo")

    hash_a = hashlib.sha256(KEY_A).digest()[0]
    candidates = await ChannelRepository.get_decrypt_candidates(hash_a)
    assert [(k, n) for k, n, _ in candidates] == [(KEY_A.hex().upper(), "#alpha")]
    assert candidates[0][2] == KEY_A


@pytest.mark.asyncio
async def test_new_renamed_and_deleted_channels_take_effect_immediately(test_db):
    # Unknown channel: index built without it.
    result = await _ingest(_packet(KEY_A, 1000, "Al: one"), 1000)
    assert not result["decrypted"]

    await ChannelRepository.upsert(KEY_A.hex().upper(), "#alpha")
    result = await _ingest(_packet(KEY_A, 1001, "Al: two"), 1001)
    assert result["decrypted"] and result["channel_name"] == "#alpha"

    await ChannelRepository.upsert_name(KEY_A.hex().upper(), "#alpha-renamed")
    result = await _ingest(_packet(KEY_A, 1002, "Al: three"), 1002)
    assert result["channel_name"] == "#alpha-renamed"

    await ChannelRepository.delete(KEY_A.hex().upper())
    result = await _ingest(_packet(KEY_A, 1003, "Al: four"), 1003)
    assert not result["decrypted"]

    stored = await MessageRepository.get_all(msg_type="CHAN", conversation_key=KEY_A.hex().upper())
    assert sorted(m.text for m in stored) == ["Al: three", "Al: two"]


@pytest.mark.asyncio
async def test_insert_if_absent_invalidates_index(test_db):
    hash_b = hashlib.sha256(KEY_B).digest()[0]
    assert await ChannelRepository.get_decrypt_candidates(hash_b) == []
    await ChannelRepository.insert_if_absent(KEY_B.hex().upper(), "#bravo")
    assert len(await ChannelRepository.get_decrypt_candidates(hash_b)) == 1
