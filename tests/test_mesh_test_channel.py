"""Built-in #meshloom-testing channel: send path and the filters that hide it."""

from __future__ import annotations

import time
from hashlib import sha256
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException
from meshcore import EventType

from app.data.meshcore_channels import channel_key_hash_byte, hashtag_key_from_name
from app.decoder import encrypt_group_text, outgoing_group_text_packet_hash
from app.radio import radio_manager
from app.repository import (
    AppSettingsRepository,
    ChannelRepository,
    MessageRepository,
    RawPacketRepository,
)
from app.routers.channels import CreateChannelRequest, create_channel
from app.routers.packets import get_undecrypted_group_text_samples
from app.routers.tools import MESH_TEST_RADIO_SLOT, MeshTestRequest, send_mesh_test
from app.services.hashtag_catalogue import _apply_matched_name, _unknown_samples
from app.services.meshloom_community import update_community
from app.services.observer_reach import _dedup_entries, parse_packet_observations
from app.services.test_channel import (
    TEST_CHANNEL_HASH_BYTE,
    TEST_CHANNEL_KEY,
    TEST_CHANNEL_KEY_HEX,
    TEST_CHANNEL_NAME,
    is_test_channel_key,
    is_test_channel_name,
)

REGION = "nl-gr"


def _group_text_packet(channel_key: bytes, text: str, timestamp: int = 1_700_000_000) -> bytes:
    return bytes([0x15, 0x00]) + encrypt_group_text(channel_key, timestamp, text, 0)


def _hash_byte_twin_key() -> bytes:
    """A foreign channel key whose one-byte channel hash equals the test channel's."""
    for index in range(100_000):
        key = hashtag_key_from_name(f"twin-{index}")
        if channel_key_hash_byte(key) == TEST_CHANNEL_HASH_BYTE:
            return key
    raise AssertionError("no colliding hash byte found")


def _make_radio_result(payload=None):
    result = MagicMock()
    result.type = EventType.MSG_SENT
    result.payload = payload or {}
    return result


def _make_mc(name="TestNode"):
    mc = MagicMock()
    mc.self_info = {"name": name}
    mc.commands = MagicMock()
    mc.commands.set_flood_scope = AsyncMock(return_value=_make_radio_result())
    mc.commands.send = AsyncMock(return_value=_make_radio_result())
    mc.commands.set_channel = AsyncMock(return_value=_make_radio_result())
    mc.commands.send_chan_msg = AsyncMock(return_value=_make_radio_result())
    return mc


async def _enable_mesh_test(regions: list[str] | None = None) -> None:
    await update_community(enabled=True, iata="LYS")
    await AppSettingsRepository.update(known_regions=regions if regions is not None else [REGION])


class TestTestChannelIdentity:
    def test_key_matches_the_hashtag_derivation(self):
        assert hashtag_key_from_name(TEST_CHANNEL_NAME) == TEST_CHANNEL_KEY
        assert sha256(TEST_CHANNEL_NAME.encode()).digest()[:16] == TEST_CHANNEL_KEY
        assert TEST_CHANNEL_KEY.hex().upper() == TEST_CHANNEL_KEY_HEX
        assert sha256(TEST_CHANNEL_KEY).digest()[:1].hex() == TEST_CHANNEL_HASH_BYTE

    def test_key_and_name_predicates(self):
        assert is_test_channel_key(TEST_CHANNEL_KEY.hex()) is True
        assert is_test_channel_key(TEST_CHANNEL_KEY_HEX.lower()) is True
        assert is_test_channel_key(hashtag_key_from_name("fr").hex()) is False
        assert is_test_channel_name(TEST_CHANNEL_NAME) is True
        assert is_test_channel_name(" meshloom-testing ") is True
        assert is_test_channel_name("#meshloom-testing-2") is False
        assert is_test_channel_name("") is False


class TestSampleFiltering:
    @pytest.mark.asyncio
    async def test_catalogue_samples_drop_only_the_mac_verified_packets(self, test_db):
        twin_key = _hash_byte_twin_key()
        now = int(time.time())
        await RawPacketRepository.create(
            _group_text_packet(TEST_CHANNEL_KEY, "TestNode: meshloom-test 1"), now
        )
        twin_packet = _group_text_packet(twin_key, "Alice: hello")
        await RawPacketRepository.create(twin_packet, now)

        samples = await _unknown_samples()

        assert samples == {TEST_CHANNEL_HASH_BYTE: [twin_packet]}

    @pytest.mark.asyncio
    async def test_hash_byte_disappears_when_only_test_packets_remain(self, test_db):
        now = int(time.time())
        await RawPacketRepository.create(
            _group_text_packet(TEST_CHANNEL_KEY, "TestNode: meshloom-test 1"), now
        )
        await RawPacketRepository.create(
            _group_text_packet(TEST_CHANNEL_KEY, "TestNode: meshloom-test 2"), now + 1
        )

        assert await _unknown_samples() == {}

    @pytest.mark.asyncio
    async def test_cracker_endpoint_hides_test_packets(self, test_db):
        twin_key = _hash_byte_twin_key()
        now = int(time.time())
        await RawPacketRepository.create(
            _group_text_packet(TEST_CHANNEL_KEY, "TestNode: meshloom-test 1"), now
        )
        twin_packet = _group_text_packet(twin_key, "Alice: hello")
        await RawPacketRepository.create(twin_packet, now + 1)

        response = await get_undecrypted_group_text_samples()

        assert [sample.data for sample in response.samples] == [twin_packet.hex()]
        assert response.hash_count == 1


class TestCatalogueNeverOpensTestChannel:
    @pytest.mark.asyncio
    async def test_apply_matched_name_refuses_the_test_channel(self, test_db):
        packet = _group_text_packet(TEST_CHANNEL_KEY, "TestNode: meshloom-test 1")

        with (
            patch("app.websocket.broadcast_event"),
            patch(
                "app.services.hashtag_catalogue.schedule_hashtag_names_publish",
                new_callable=AsyncMock,
            ) as publish,
        ):
            opened = await _apply_matched_name("meshloom-testing", [packet])

        assert opened is False
        publish.assert_not_awaited()
        assert await ChannelRepository.get_by_key(TEST_CHANNEL_KEY_HEX) is None

    @pytest.mark.asyncio
    async def test_a_regular_name_still_opens(self, test_db):
        key = hashtag_key_from_name("fr")
        packet = _group_text_packet(key, "Alice: hello")

        with (
            patch("app.websocket.broadcast_event"),
            patch("app.push.manager.push_manager.dispatch_event", new_callable=AsyncMock),
            patch(
                "app.services.hashtag_catalogue.schedule_hashtag_names_publish",
                new_callable=AsyncMock,
            ) as publish,
        ):
            opened = await _apply_matched_name("fr", [packet])

        assert opened is True
        publish.assert_awaited_once()


class TestManualAddDoesNotPublish:
    @pytest.mark.asyncio
    async def test_creating_the_test_channel_skips_the_publish(self, test_db):
        with (
            patch("app.routers.channels.broadcast_event"),
            patch(
                "app.routers.channels.schedule_hashtag_names_publish", new_callable=AsyncMock
            ) as publish,
        ):
            stored = await create_channel(CreateChannelRequest(name=TEST_CHANNEL_NAME))

        # The channel is adopted so it shows in chat; only the publish is skipped.
        assert stored.key == TEST_CHANNEL_KEY_HEX
        assert stored.membership == "adopted"
        publish.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_creating_a_regular_hashtag_still_publishes(self, test_db):
        with (
            patch("app.routers.channels.broadcast_event"),
            patch(
                "app.routers.channels.schedule_hashtag_names_publish", new_callable=AsyncMock
            ) as publish,
        ):
            await create_channel(CreateChannelRequest(name="#fr"))

        publish.assert_awaited_once()


class TestMeshTestEndpoint:
    @pytest.mark.asyncio
    async def test_community_off_is_404(self, test_db):
        with pytest.raises(HTTPException) as exc_info:
            await send_mesh_test(MeshTestRequest(flood_scope=REGION))

        assert exc_info.value.status_code == 404

    @pytest.mark.asyncio
    async def test_unknown_region_is_400(self, test_db):
        await _enable_mesh_test()

        with pytest.raises(HTTPException) as exc_info:
            await send_mesh_test(MeshTestRequest(flood_scope="de-by"))

        assert exc_info.value.status_code == 400

    @pytest.mark.asyncio
    async def test_unscoped_marker_is_rejected_unless_listed(self, test_db):
        await _enable_mesh_test()

        with pytest.raises(HTTPException) as exc_info:
            await send_mesh_test(MeshTestRequest(flood_scope="*"))

        assert exc_info.value.status_code == 400

    @pytest.mark.asyncio
    async def test_send_hashes_the_name_prefixed_text(self, test_db):
        await _enable_mesh_test()
        mc = _make_mc()

        with (
            patch("app.routers.tools.radio_manager.require_connected", return_value=mc),
            patch.object(radio_manager, "_meshcore", mc),
        ):
            response = await send_mesh_test(MeshTestRequest(flood_scope=REGION))

        sent = mc.commands.send_chan_msg.call_args.kwargs
        body = sent["msg"]
        sender_timestamp = int.from_bytes(sent["timestamp"], "little")
        assert body.startswith("meshloom-test")
        assert len(body.encode("utf-8")) < 160
        assert response.flood_scope == REGION
        assert response.packet_hash == outgoing_group_text_packet_hash(
            TEST_CHANNEL_KEY_HEX, sender_timestamp, f"TestNode: {body}"
        )
        assert response.packet_hash != outgoing_group_text_packet_hash(
            TEST_CHANNEL_KEY_HEX, sender_timestamp, body
        )

    @pytest.mark.asyncio
    async def test_send_clears_the_slot_and_stores_nothing(self, test_db):
        await _enable_mesh_test()
        mc = _make_mc()

        with (
            patch("app.routers.tools.radio_manager.require_connected", return_value=mc),
            patch.object(radio_manager, "_meshcore", mc),
        ):
            await send_mesh_test(MeshTestRequest(flood_scope=REGION))

        loaded, cleared = mc.commands.set_channel.call_args_list
        assert loaded.kwargs == {
            "channel_idx": MESH_TEST_RADIO_SLOT,
            "channel_name": TEST_CHANNEL_NAME,
            "channel_secret": TEST_CHANNEL_KEY,
        }
        assert cleared.kwargs == {
            "channel_idx": MESH_TEST_RADIO_SLOT,
            "channel_name": "",
            "channel_secret": bytes(16),
        }
        assert radio_manager.get_channel_send_cache_snapshot() == []
        assert await ChannelRepository.get_by_key(TEST_CHANNEL_KEY_HEX) is None
        assert (
            await MessageRepository.get_all(
                msg_type="CHAN", conversation_key=TEST_CHANNEL_KEY_HEX, limit=10
            )
            == []
        )

    @pytest.mark.asyncio
    async def test_scope_is_applied_then_restored(self, test_db):
        await _enable_mesh_test()
        mc = _make_mc()

        with (
            patch("app.routers.tools.radio_manager.require_connected", return_value=mc),
            patch.object(radio_manager, "_meshcore", mc),
        ):
            await send_mesh_test(MeshTestRequest(flood_scope=REGION))

        scopes = [call.args[0] for call in mc.commands.set_flood_scope.call_args_list]
        assert scopes == [f"#{REGION}", ""]

    @pytest.mark.asyncio
    async def test_slot_is_cleared_when_the_send_fails(self, test_db):
        await _enable_mesh_test()
        mc = _make_mc()
        mc.commands.send_chan_msg = AsyncMock(
            return_value=MagicMock(type=EventType.ERROR, payload="boom")
        )

        with (
            patch("app.routers.tools.radio_manager.require_connected", return_value=mc),
            patch.object(radio_manager, "_meshcore", mc),
            pytest.raises(HTTPException) as exc_info,
        ):
            await send_mesh_test(MeshTestRequest(flood_scope=REGION))

        assert exc_info.value.status_code == 422
        assert mc.commands.set_channel.call_args.kwargs["channel_secret"] == bytes(16)
        assert radio_manager.get_channel_send_cache_snapshot() == []

    @pytest.mark.asyncio
    async def test_borrowed_slot_is_evicted_from_the_send_cache(self, test_db):
        await _enable_mesh_test()
        mc = _make_mc()
        other_key = hashtag_key_from_name("fr").hex().upper()
        radio_manager.note_channel_slot_loaded(other_key, MESH_TEST_RADIO_SLOT)

        with (
            patch("app.routers.tools.radio_manager.require_connected", return_value=mc),
            patch.object(radio_manager, "_meshcore", mc),
        ):
            await send_mesh_test(MeshTestRequest(flood_scope=REGION))

        assert radio_manager.get_channel_send_cache_snapshot() == []


class TestObserverRoleAndRssi:
    def test_parsed_through_dedup(self):
        observations = parse_packet_observations(
            {
                "observations": [
                    {"observer_id": "obs-1", "role": "repeater", "rssi": -91.5, "snr": 4.5},
                    {"observer_id": "obs-1", "hops": 2, "path_json": ["aa", "bb"]},
                ]
            }
        )

        entries = _dedup_entries(observations, {})

        assert len(entries) == 1
        assert entries[0].role == "repeater"
        assert entries[0].rssi == -91.5
        assert entries[0].snr == 4.5
        assert entries[0].hops == 2

    def test_later_values_fill_a_missing_first_one(self):
        observations = parse_packet_observations(
            {
                "observations": [
                    {"observer_id": "obs-1"},
                    {"observer_id": "obs-1", "role": "chat", "rssi": -80},
                ]
            }
        )

        entries = _dedup_entries(observations, {})

        assert entries[0].role == "chat"
        assert entries[0].rssi == -80.0

    def test_blank_role_and_unparsable_rssi_are_null(self):
        observations = parse_packet_observations(
            {"observations": [{"observer_id": "obs-1", "role": "   ", "rssi": "n/a"}]}
        )

        entries = _dedup_entries(observations, {})

        assert entries[0].role is None
        assert entries[0].rssi is None


class TestRadioSyncIgnoresTestChannel:
    @pytest.mark.asyncio
    async def test_a_resident_test_slot_is_not_adopted_or_published(self, test_db):
        from app.radio_sync import upsert_channel_from_radio_slot

        with patch(
            "app.services.meshloom_community.schedule_hashtag_names_publish",
            new=AsyncMock(),
        ) as publish:
            key = await upsert_channel_from_radio_slot(
                {
                    "channel_name": "#meshloom-testing",
                    "channel_secret": TEST_CHANNEL_KEY,
                },
                on_radio=True,
            )

        assert key is None
        assert await ChannelRepository.get_by_key(TEST_CHANNEL_KEY_HEX) is None
        publish.assert_not_awaited()
