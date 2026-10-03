"""Tests for Phase 7: Remote Link Hardening and Aggregated All Radios View."""

import socket
import time
from unittest.mock import MagicMock

import pytest

from app.repository import MessageRepository, RawPacketRepository
from app.services.radio_instance import RadioInstance


class TestTcpKeepaliveTuning:
    """Test remote link TCP keepalive configuration."""

    def test_tune_tcp_socket_configures_keepalive(self):
        instance = RadioInstance(radio_id="remote_radio", name="Remote Radio")

        mock_sock = MagicMock(spec=socket.socket)
        mock_conn = MagicMock()
        mock_conn.get_extra_info.return_value = mock_sock

        mock_cm = MagicMock()
        mock_cm.connection = mock_conn

        mock_mc = MagicMock()
        mock_mc.connection_manager = mock_cm

        instance._tune_tcp_socket(mock_mc)

        mock_sock.setsockopt.assert_any_call(socket.SOL_SOCKET, socket.SO_KEEPALIVE, 1)

    def test_tune_tcp_socket_handles_missing_socket_gracefully(self):
        instance = RadioInstance(radio_id="test", name="Test")
        # Should not raise any exception even if connection has no socket
        instance._tune_tcp_socket(MagicMock(connection_manager=None))
        instance._tune_tcp_socket(None)


class TestAggregatedAllRadiosView:
    """Test read-time aggregated 'all' radio queries across messages and packets."""

    @pytest.mark.asyncio
    async def test_messages_query_all_radios(self, test_db):
        now = int(time.time())
        # Insert messages across two radios
        id_1 = await MessageRepository.create(
            msg_type="CHAN",
            conversation_key="chan_key_1",
            text="Message on radio 1",
            received_at=now,
            radio_id="radio_1",
        )
        id_2 = await MessageRepository.create(
            msg_type="CHAN",
            conversation_key="chan_key_1",
            text="Message on radio 2",
            received_at=now + 1,
            radio_id="radio_2",
        )
        assert id_1 is not None
        assert id_2 is not None

        # Query radio_1 only
        msgs_1 = await MessageRepository.get_all(radio_id="radio_1")
        assert any(m.id == id_1 for m in msgs_1)
        assert not any(m.id == id_2 for m in msgs_1)

        # Query radio_2 only
        msgs_2 = await MessageRepository.get_all(radio_id="radio_2")
        assert any(m.id == id_2 for m in msgs_2)
        assert not any(m.id == id_1 for m in msgs_2)

        # Query all radios
        msgs_all = await MessageRepository.get_all(radio_id="all")
        all_ids = {m.id for m in msgs_all}
        assert id_1 in all_ids
        assert id_2 in all_ids

    @pytest.mark.asyncio
    async def test_raw_packets_history_all_radios(self, test_db):
        now = int(time.time())
        data_1 = b"\x01\x02\x03\x04"
        data_2 = b"\x05\x06\x07\x08"

        pkt_1_id, _ = await RawPacketRepository.create(data_1, timestamp=now, radio_id="radio_1")
        pkt_2_id, _ = await RawPacketRepository.create(
            data_2, timestamp=now + 1, radio_id="radio_2"
        )

        # Query radio_1 history
        rows_1, total_1, _, _ = await RawPacketRepository.list_history(
            limit=10, max_scan=50, radio_id="radio_1"
        )
        ids_1 = {r[0] for r in rows_1}
        assert pkt_1_id in ids_1
        assert pkt_2_id not in ids_1

        # Query all radios history
        rows_all, total_all, _, _ = await RawPacketRepository.list_history(
            limit=10, max_scan=50, radio_id="all"
        )
        ids_all = {r[0] for r in rows_all}
        assert pkt_1_id in ids_all
        assert pkt_2_id in ids_all
