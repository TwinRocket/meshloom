"""Logs at the default level carry no message text and no private channel name (MSHL-459).

They end up in bug reports through the GET /api/debug snapshot.
"""

from __future__ import annotations

import logging
from hashlib import sha256

import pytest

from app.data.meshcore_channels import channel_log_label
from app.decoder import DecryptedDirectMessage
from app.models import ContactUpsert
from app.radio import radio_manager
from app.repository import ChannelRepository, ContactRepository, RawPacketRepository
from app.services.dm_ingest import ingest_decrypted_direct_message
from app.services.messages import create_message_from_decrypted
from tests.fixtures.fake_node import FakeNode, connected_node

TEXT = "meet at the old bridge"
PRIVATE_KEY = "C3" * 16
HASHTAG_NAME = "#meteo"
HASHTAG_KEY = sha256(HASHTAG_NAME.encode()).digest()[:16].hex().upper()


class _SendingNode(FakeNode):
    def _cmd_03(self, body: bytes) -> list[bytes]:
        return [b"\x00"]


@pytest.fixture(autouse=True)
def _info_logs(caplog):
    caplog.set_level(logging.INFO)


async def _store(key: str, name: str | None) -> None:
    packet_id, _ = await RawPacketRepository.create(key.encode(), 1700000000)
    stored = await create_message_from_decrypted(
        packet_id=packet_id,
        channel_key=key,
        channel_name=name,
        sender="bob",
        message_text=TEXT,
        timestamp=1700000000,
        received_at=1700000000,
        broadcast_fn=lambda *_a, **_k: None,
    )
    assert stored is not None


@pytest.mark.asyncio
async def test_a_stored_private_channel_message_logs_neither_text_nor_name(test_db, caplog):
    await _store(PRIVATE_KEY, "Team secret")
    assert "Stored channel message" in caplog.text
    assert TEXT not in caplog.text
    assert "Team secret" not in caplog.text


@pytest.mark.asyncio
async def test_a_stored_hashtag_channel_message_keeps_its_public_name(test_db, caplog):
    await _store(HASHTAG_KEY, HASHTAG_NAME)
    assert HASHTAG_NAME in caplog.text
    assert TEXT not in caplog.text


@pytest.mark.asyncio
async def test_a_stored_direct_message_does_not_log_the_text(test_db, caplog):
    key = "aa" * 32
    await ContactRepository.upsert(ContactUpsert(public_key=key, name="Alice", type=1))
    packet_id, _ = await RawPacketRepository.create(b"dm", 1700000000)
    stored = await ingest_decrypted_direct_message(
        packet_id=packet_id,
        decrypted=DecryptedDirectMessage(
            timestamp=1700000000,
            flags=0,
            message=TEXT,
            dest_hash="aa",
            src_hash="bb",
        ),
        their_public_key=key,
        received_at=1700000000,
        broadcast_fn=lambda *_a, **_k: None,
    )
    assert stored is not None
    assert "Stored direct message" in caplog.text
    assert "Alice" in caplog.text
    assert TEXT not in caplog.text


@pytest.mark.asyncio
async def test_sending_on_a_private_channel_logs_neither_text_nor_name(test_db, client, caplog):
    await ChannelRepository.upsert(key=PRIVATE_KEY, name="Team secret")
    saved = radio_manager._connection_info
    radio_manager._connection_info = "Serial: /dev/ttyUSB0"
    radio_manager.reset_channel_send_cache()
    try:
        async with connected_node(_SendingNode(), kind="serial"):
            caplog.clear()
            response = await client.post(
                "/api/messages/channel", json={"channel_key": PRIVATE_KEY, "text": TEXT}
            )
        assert response.status_code == 200, response.text
    finally:
        radio_manager.reset_channel_send_cache()
        radio_manager._connection_info = saved
    lines = [r.getMessage() for r in caplog.records if r.name.startswith("app.services.message")]
    assert any("Sending channel message" in line for line in lines)
    assert not any(TEXT in line or "Team secret" in line for line in lines)


@pytest.mark.parametrize(
    ("name", "key", "label"),
    [
        (HASHTAG_NAME, HASHTAG_KEY, HASHTAG_NAME),
        ("Public", "8B3387E9C5CDEA6AC9E5EDBAA115CD72", "Public"),
        ("#ops", PRIVATE_KEY, "a private channel"),
        ("Team secret", PRIVATE_KEY, "a private channel"),
        (None, PRIVATE_KEY, "an unnamed channel"),
    ],
)
def test_channel_log_label(name, key, label):
    assert channel_log_label(name, key) == label
