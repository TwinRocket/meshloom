"""Tests for Phase 5: Multi-radio outbound send, ACK tracking, bot and fanout scoping."""

import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.fanout.bot import BotModule
from app.fanout.bot_exec import process_bot_response
from app.fanout.mqtt import _build_message_topic, _build_raw_packet_topic
from app.fanout.mqtt_ha import _device_payload
from app.repository import MessageRepository
from app.services.dm_ack_apply import apply_dm_ack_code
from app.services.dm_ack_tracker import (
    RadioScopedAckDict,
    clear_pending_acks_for_message,
    pop_pending_ack,
    track_pending_ack,
)


class TestRadioScopedAckTracker:
    """Test ACK tracking isolation across multiple radios."""

    def test_scoped_dict_isolation(self):
        d = RadioScopedAckDict()
        # Radio 1 and Radio 2 track same ack code
        d["radio_1", 12345] = 101
        d["radio_2", 12345] = 202

        assert d["radio_1", 12345] == 101
        assert d["radio_2", 12345] == 202
        assert len(d) == 2

        # Backward compatibility with string keys (defaults to "default")
        d["54321"] = 999
        assert d["default", 54321] == 999
        assert d["54321"] == 999

    def test_track_and_pop_isolation(self):
        # Reset and track for two radios
        track_pending_ack(message_id=1, expected_ack="1000", timeout_ms=5000, radio_id="radio_a")
        track_pending_ack(message_id=2, expected_ack="1000", timeout_ms=5000, radio_id="radio_b")

        # Pop from radio_a should not affect radio_b
        assert pop_pending_ack("1000", radio_id="radio_a") == 1
        assert pop_pending_ack("1000", radio_id="radio_a") is None
        assert pop_pending_ack("1000", radio_id="radio_b") == 2

    def test_clear_pending_acks_for_message_isolation(self):
        track_pending_ack(message_id=10, expected_ack="1111", timeout_ms=5000, radio_id="radio_a")
        track_pending_ack(message_id=10, expected_ack="2222", timeout_ms=5000, radio_id="radio_a")
        track_pending_ack(message_id=10, expected_ack="1111", timeout_ms=5000, radio_id="radio_b")

        clear_pending_acks_for_message(10, radio_id="radio_a")

        assert pop_pending_ack("1111", radio_id="radio_a") is None
        assert pop_pending_ack("2222", radio_id="radio_a") is None
        # radio_b still has its pending ack
        assert pop_pending_ack("1111", radio_id="radio_b") == 10

    @pytest.mark.asyncio
    async def test_apply_dm_ack_code_isolation(self, test_db):
        """Applying ACK on one radio does not mark message on another radio as ACKed."""
        # Insert outgoing DMs for two radios with the same ack_code
        ack_code = 9999
        msg_a_id = await MessageRepository.create(
            msg_type="PRIV",
            conversation_key="contact_a",
            text="Hello from A",
            received_at=int(time.time()),
            outgoing=True,
            radio_id="radio_a",
        )
        assert msg_a_id is not None

        msg_b_id = await MessageRepository.create(
            msg_type="PRIV",
            conversation_key="contact_b",
            text="Hello from B",
            received_at=int(time.time()),
            outgoing=True,
            radio_id="radio_b",
        )
        assert msg_b_id is not None

        track_pending_ack(message_id=msg_a_id, expected_ack=str(ack_code), timeout_ms=5000, radio_id="radio_a")
        track_pending_ack(message_id=msg_b_id, expected_ack=str(ack_code), timeout_ms=5000, radio_id="radio_b")

        # Apply ACK on radio_a
        mock_broadcast = MagicMock()
        matched = await apply_dm_ack_code(str(ack_code), broadcast_fn=mock_broadcast, radio_id="radio_a")
        assert matched is True

        # Check statuses
        refreshed_a = await MessageRepository.get_by_id(msg_a_id)
        refreshed_b = await MessageRepository.get_by_id(msg_b_id)

        assert refreshed_a.acked == 1
        assert refreshed_b.acked == 0

        # Now apply ACK on radio_b
        matched_b = await apply_dm_ack_code(str(ack_code), broadcast_fn=mock_broadcast, radio_id="radio_b")
        assert matched_b is True

        refreshed_b2 = await MessageRepository.get_by_id(msg_b_id)
        assert refreshed_b2.acked == 1


class TestMultiRadioBotRouting:
    """Test that bot triggers target the originating radio."""

    @pytest.mark.asyncio
    async def test_process_bot_response_propagates_radio_id(self):
        with (
            patch("app.routers.messages.send_direct_message", new_callable=AsyncMock) as mock_send_dm,
            patch("app.routers.messages.send_channel_message", new_callable=AsyncMock) as mock_send_chan,
            patch("app.websocket.broadcast_event"),
        ):
            mock_send_dm.return_value = MagicMock(model_dump=lambda: {})
            mock_send_chan.return_value = MagicMock(model_dump=lambda: {})

            # DM response on secondary radio
            await process_bot_response(
                response="Reply text",
                is_dm=True,
                sender_key="contact_pubkey",
                channel_key=None,
                radio_id="radio_secondary",
            )
            mock_send_dm.assert_awaited_once()
            _, kwargs = mock_send_dm.await_args
            assert kwargs.get("radio_id") == "radio_secondary"

            # Channel response on secondary radio
            await process_bot_response(
                response="Channel reply",
                is_dm=False,
                sender_key="",
                channel_key="chan_key_1",
                radio_id="radio_secondary",
            )
            mock_send_chan.assert_awaited_once()
            _, chan_kwargs = mock_send_chan.await_args
            assert chan_kwargs.get("radio_id") == "radio_secondary"

    @pytest.mark.asyncio
    async def test_bot_module_extracts_and_routes_radio_id(self, test_db):
        bot = BotModule(
            config_id="bot1",
            config={"code": "def bot(*args, **kwargs): return 'bot reply'"},
            name="TestBot",
        )

        msg = {
            "type": "PRIV",
            "conversation_key": "contact_xyz",
            "text": "ping",
            "sender": "Alice",
            "sender_name": "Alice",
            "sender_key": "alice_key",
            "sender_timestamp": 12345,
            "path": None,
            "outgoing": False,
            "radio_id": "radio_mesh_2",
        }

        with (
            patch("asyncio.sleep", new_callable=AsyncMock),
            patch("app.fanout.bot_exec.process_bot_response", new_callable=AsyncMock) as mock_process,
        ):
            await bot._run_for_message(msg)
            mock_process.assert_awaited_once()
            _, kwargs = mock_process.await_args
            assert kwargs.get("radio_id") == "radio_mesh_2"


class TestMultiRadioMqttScoping:
    """Test MQTT topic scoping between default and secondary radios."""

    def test_build_message_topic_default_preserves_legacy(self):
        dm_data = {"type": "PRIV", "conversation_key": "11223344", "radio_id": "default"}
        chan_data = {"type": "CHAN", "conversation_key": "aabbcc", "radio_id": "default"}

        assert _build_message_topic("meshcore", dm_data) == "meshcore/dm:11223344"
        assert _build_message_topic("meshcore", chan_data) == "meshcore/gm:aabbcc"

    def test_build_message_topic_secondary_uses_scoped_topic(self):
        dm_data = {"type": "PRIV", "conversation_key": "11223344", "radio_id": "radio_2"}
        chan_data = {"type": "CHAN", "conversation_key": "aabbcc", "radio_id": "radio_2"}

        assert _build_message_topic("meshcore", dm_data) == "meshcore/radio_2/dm:11223344"
        assert _build_message_topic("meshcore", chan_data) == "meshcore/radio_2/gm:aabbcc"

    def test_build_raw_packet_topic_scoping(self):
        data_default = {"radio_id": "default"}
        data_sec = {"radio_id": "radio_remote"}
        assert _build_raw_packet_topic("meshcore", data_default) == "meshcore/raw/unrouted"
        assert _build_raw_packet_topic("meshcore", data_sec) == "meshcore/radio_remote/raw/unrouted"

        data_routed = {"radio_id": "radio_remote", "decrypted_info": {"contact_key": "contact_abc"}}
        assert _build_raw_packet_topic("meshcore", data_routed) == "meshcore/radio_remote/raw/dm:contact_abc"

    def test_home_assistant_device_payload_scoping(self):
        payload_default = _device_payload(
            "1122334455667788", "Alice", "Station G2", radio_id="default"
        )
        assert payload_default["identifiers"] == ["meshcore_112233445566"]

        payload_sec = _device_payload(
            "1122334455667788", "Alice", "Station G2", radio_id="radio_secondary"
        )
        assert payload_sec["identifiers"] == ["meshcore_radio_secondary_112233445566"]
