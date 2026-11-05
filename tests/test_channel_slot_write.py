"""The channel a send writes to a radio slot keeps its own secret (MSHL-459).

Goes through the real meshcore stack and the fake node: the library replaces the
secret of any ``#`` name, which a mock of ``set_channel`` cannot show.
"""

from __future__ import annotations

from hashlib import sha256

import pytest

from app.radio import radio_manager
from app.radio_sync import sync_and_offload_channels
from app.repository import ChannelRepository
from app.services.channel_slots import channel_slot_frame
from tests.fixtures.fake_node import FakeNode, connected_node

PRIVATE_KEY = "C3" * 16


class _SendingNode(FakeNode):
    """A fake node that also accepts SEND_CHANNEL_TXT."""

    def _cmd_03(self, body: bytes) -> list[bytes]:
        self.log.append(("send_chan_msg", body[1]))
        return [b"\x00"]


@pytest.fixture(autouse=True)
def _serial_link():
    saved = radio_manager._connection_info
    radio_manager.reset_channel_send_cache()
    radio_manager._connection_info = "Serial: /dev/ttyUSB0"
    yield
    radio_manager.reset_channel_send_cache()
    radio_manager._connection_info = saved


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["serial", "tcp"])
async def test_a_send_keeps_the_secret_of_a_private_hash_channel(test_db, client, kind):
    radio_manager._connection_info = "Serial: x" if kind == "serial" else "TCP: x:1"
    node = _SendingNode()
    node.channels = {0: ("#ops", bytes.fromhex(PRIVATE_KEY))}
    async with connected_node(node, kind=kind) as (loop, _):
        await sync_and_offload_channels(loop.mc)  # adopted, then the slot is emptied
        assert node.channels[0] == ("", bytes(16))

        response = await client.post(
            "/api/messages/channel", json={"channel_key": PRIVATE_KEY, "text": "hi"}
        )

    assert response.status_code == 200, response.text
    assert node.channels[0] == ("#ops", bytes.fromhex(PRIVATE_KEY))
    assert ("send_chan_msg", 0) in node.log


@pytest.mark.asyncio
async def test_a_send_does_not_adopt_a_second_channel_at_the_next_connect(test_db, client):
    node = _SendingNode()
    node.channels = {0: ("#ops", bytes.fromhex(PRIVATE_KEY))}
    async with connected_node(node, kind="serial") as (loop, _):
        await sync_and_offload_channels(loop.mc)
        await client.post("/api/messages/channel", json={"channel_key": PRIVATE_KEY, "text": "hi"})
        await sync_and_offload_channels(loop.mc)

    named = [c.key for c in await ChannelRepository.get_all() if c.name == "#ops"]
    assert named == [PRIVATE_KEY]


@pytest.mark.asyncio
async def test_a_public_hash_channel_still_sends_under_its_hash_key(test_db, client):
    public_key = sha256(b"#meshtest").digest()[:16]
    created = await client.post("/api/channels", json={"name": "#meshtest"})
    assert created.status_code == 200, created.text
    assert created.json()["key"] == public_key.hex().upper()

    node = _SendingNode()
    async with connected_node(node, kind="serial"):
        response = await client.post(
            "/api/messages/channel",
            json={"channel_key": public_key.hex().upper(), "text": "hi"},
        )

    assert response.status_code == 200, response.text
    assert node.channels[0] == ("#meshtest", public_key)


@pytest.mark.asyncio
async def test_a_radio_refusal_of_the_slot_write_still_fails_the_send(test_db, client):
    class _Refusing(_SendingNode):
        def _cmd_20(self, body: bytes) -> list[bytes]:
            return [b"\x01\x02"]

    await ChannelRepository.upsert(key=PRIVATE_KEY, name="#ops")
    node = _Refusing()
    async with connected_node(node, kind="serial"):
        response = await client.post(
            "/api/messages/channel", json={"channel_key": PRIVATE_KEY, "text": "hi"}
        )

    assert response.status_code == 422
    assert not any(name == "send_chan_msg" for name, _detail in node.log)


def test_the_frame_cuts_the_name_on_a_character_and_keeps_a_nul():
    secret = bytes(range(16))
    frame = channel_slot_frame(3, "é" * 20, secret)  # 40 bytes
    assert frame[:2] == b"\x20\x03"
    assert frame[2:34] == ("é" * 15).encode() + bytes(2)
    assert frame[34:] == secret


def test_the_frame_refuses_a_secret_of_the_wrong_size():
    with pytest.raises(ValueError):
        channel_slot_frame(0, "ops", bytes(15))
