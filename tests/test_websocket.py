"""Tests for WebSocket manager functionality."""

import asyncio
import json
import logging
from unittest.mock import AsyncMock, patch

import pytest

from app.websocket import EVICT_CLOSE_CODE, WebSocketManager


@pytest.fixture
def ws_manager():
    """Create a fresh WebSocketManager for each test."""
    return WebSocketManager()


@pytest.fixture
def mock_websocket():
    """Create a mock WebSocket connection."""
    ws = AsyncMock()
    ws.send_text = AsyncMock()
    return ws


async def _flush(rounds: int = 20) -> None:
    """Let per-client writer tasks drain their queues.

    Eviction -> close goes through asyncio.wait_for, which takes more loop
    iterations on Python 3.11 than on 3.12+, so 5 yields were not always enough.
    """
    for _ in range(rounds):
        await asyncio.sleep(0)


def _client_ws() -> AsyncMock:
    ws = AsyncMock()
    ws.accept = AsyncMock()
    ws.close = AsyncMock()
    return ws


class TestWebSocketBroadcast:
    """Tests for the broadcast functionality."""

    @pytest.mark.asyncio
    async def test_broadcast_sends_to_all_clients(self, ws_manager: WebSocketManager):
        ws1, ws2 = _client_ws(), _client_ws()
        await ws_manager.connect(ws1)
        await ws_manager.connect(ws2)

        await ws_manager.broadcast("test", {"key": "value"})
        await _flush()

        expected = json.dumps({"type": "test", "data": {"key": "value"}})
        ws1.send_text.assert_called_once_with(expected)
        ws2.send_text.assert_called_once_with(expected)

    @pytest.mark.asyncio
    async def test_failed_client_is_evicted_and_closed(self, ws_manager: WebSocketManager):
        good_ws, bad_ws = _client_ws(), _client_ws()
        bad_ws.send_text.side_effect = Exception("Connection closed")
        await ws_manager.connect(good_ws)
        await ws_manager.connect(bad_ws)

        await ws_manager.broadcast("test", {})
        await _flush()

        assert list(ws_manager.active_connections) == [good_ws]
        bad_ws.close.assert_awaited_once_with(code=EVICT_CLOSE_CODE)
        good_ws.close.assert_not_called()

    @pytest.mark.asyncio
    async def test_timed_out_client_is_evicted_and_closed(self, ws_manager: WebSocketManager):
        """A stuck socket is closed, not merely forgotten (no zombie sessions)."""
        good_ws, slow_ws = _client_ws(), _client_ws()

        async def hang(_):
            await asyncio.sleep(3600)

        closed = asyncio.Event()

        async def record_close(**_):
            closed.set()

        slow_ws.send_text.side_effect = hang
        slow_ws.close.side_effect = record_close
        await ws_manager.connect(good_ws)
        await ws_manager.connect(slow_ws)

        with patch("app.websocket.SEND_TIMEOUT_SECONDS", 0.05):
            await ws_manager.broadcast("test", {})
            # Wait for the eviction's close instead of a fixed sleep; the bound
            # only matters if the code is broken, a healthy run returns at once.
            await asyncio.wait_for(closed.wait(), timeout=10)
            await _flush()

        assert list(ws_manager.active_connections) == [good_ws]
        slow_ws.close.assert_awaited_once_with(code=EVICT_CLOSE_CODE)

    @pytest.mark.asyncio
    async def test_broadcast_never_waits_on_client_io(self, ws_manager: WebSocketManager):
        """broadcast() only enqueues; a slow client cannot block the caller or others."""
        fast_ws, slow_ws = _client_ws(), _client_ws()
        fast_received_at = None

        async def fast_send(_):
            nonlocal fast_received_at
            fast_received_at = asyncio.get_running_loop().time()

        async def slow_send(_):
            await asyncio.sleep(0.2)

        fast_ws.send_text.side_effect = fast_send
        slow_ws.send_text.side_effect = slow_send
        await ws_manager.connect(slow_ws)
        await ws_manager.connect(fast_ws)

        start = asyncio.get_running_loop().time()
        await ws_manager.broadcast("test", {})
        assert asyncio.get_running_loop().time() - start < 0.05
        await _flush()
        assert fast_received_at is not None
        assert fast_received_at - start < 0.1, "Fast client was blocked by slow client"

    @pytest.mark.asyncio
    async def test_per_client_order_is_preserved(self, ws_manager: WebSocketManager):
        received: list[str] = []

        async def jittery_send(message):
            # Earlier frames take longer; order must still hold.
            await asyncio.sleep(0.01 if "first" in message else 0)
            received.append(json.loads(message)["type"])

        ws = _client_ws()
        ws.send_text.side_effect = jittery_send
        await ws_manager.connect(ws)

        await ws_manager.send_personal(ws, "first", {})
        ws_manager.broadcast_nowait("second", {})
        ws_manager.broadcast_nowait("third", {})
        await asyncio.sleep(0.05)

        assert received == ["first", "second", "third"]

    @pytest.mark.asyncio
    async def test_queue_overflow_evicts_and_closes_client(self, ws_manager: WebSocketManager):
        ws = _client_ws()
        gate = asyncio.Event()

        async def blocked_send(_):
            await gate.wait()

        ws.send_text.side_effect = blocked_send
        with patch("app.websocket.CLIENT_QUEUE_MAX", 3):
            await ws_manager.connect(ws)
        await _flush()  # writer picks the first frame and blocks on it

        for i in range(10):
            ws_manager.broadcast_nowait("test", {"i": i})
        await _flush()

        assert ws not in ws_manager.active_connections
        ws.close.assert_awaited_once_with(code=EVICT_CLOSE_CODE)
        gate.set()

    @pytest.mark.asyncio
    async def test_raw_packet_burst_is_dropped_not_evicted(self, ws_manager: WebSocketManager):
        """A burst of raw_packet frames past the backlog is shed; the client stays."""
        ws = _client_ws()
        gate = asyncio.Event()
        sent: list[str] = []

        async def gated_send(message):
            await gate.wait()
            sent.append(json.loads(message)["type"])

        ws.send_text.side_effect = gated_send
        with (
            patch("app.websocket.CLIENT_QUEUE_MAX", 6),
            patch("app.websocket.CLIENT_RAW_PACKET_BACKLOG_MAX", 3),
        ):
            await ws_manager.connect(ws)
            await _flush()
            for i in range(50):
                ws_manager.broadcast_nowait("raw_packet", {"i": i})
            # State-bearing frames still fit in the headroom above the raw backlog.
            ws_manager.broadcast_nowait("message", {"id": 1})
            ws_manager.broadcast_nowait("contact", {"k": 1})
            await _flush()

            assert ws in ws_manager.active_connections
            ws.close.assert_not_called()
            gate.set()
            await _flush(20)

        assert sent.count("raw_packet") <= 4
        assert sent[-2:] == ["message", "contact"]

    @pytest.mark.asyncio
    async def test_community_packet_is_shed_before_raw_packet(self, ws_manager: WebSocketManager):
        """community_packet drops at its own smaller backlog; raw_packet and state still fit."""
        ws = _client_ws()
        gate = asyncio.Event()
        sent: list[str] = []

        async def gated_send(message):
            await gate.wait()
            sent.append(json.loads(message)["type"])

        ws.send_text.side_effect = gated_send
        with (
            patch("app.websocket.CLIENT_QUEUE_MAX", 12),
            patch("app.websocket.CLIENT_RAW_PACKET_BACKLOG_MAX", 6),
            patch("app.websocket.CLIENT_COMMUNITY_PACKET_BACKLOG_MAX", 3),
        ):
            await ws_manager.connect(ws)
            await _flush()  # writer parked on the empty queue; bursts below never yield
            for i in range(100):
                ws_manager.broadcast_nowait("community_packet", {"i": i})
            assert ws_manager.active_connections[ws].queue.qsize() == 3
            for i in range(10):
                ws_manager.broadcast_nowait("raw_packet", {"i": i})
            assert ws_manager.active_connections[ws].queue.qsize() == 6
            for i in range(20):
                ws_manager.broadcast_nowait("community_packet", {"i": i})
            ws_manager.broadcast_nowait("message", {"id": 1})
            ws_manager.broadcast_nowait("message_acked", {"message_id": 1, "ack_count": 1})
            ws_manager.broadcast_nowait("community_live", {"state": "connected"})

            assert ws in ws_manager.active_connections
            ws.close.assert_not_called()
            gate.set()
            await _flush(40)

        assert sent == ["community_packet"] * 3 + ["raw_packet"] * 3 + [
            "message",
            "message_acked",
            "community_live",
        ]
        assert ws_manager.dropped_frames == {"community_packet": 117, "raw_packet": 7}

    @pytest.mark.asyncio
    async def test_state_frames_are_never_dropped_and_overflow_evicts(
        self, ws_manager: WebSocketManager
    ):
        """Past the hard bound only eviction remains: state frames are never shed."""
        ws = _client_ws()
        gate = asyncio.Event()

        async def blocked_send(_):
            await gate.wait()

        ws.send_text.side_effect = blocked_send
        with (
            patch("app.websocket.CLIENT_QUEUE_MAX", 4),
            patch("app.websocket.CLIENT_COMMUNITY_PACKET_BACKLOG_MAX", 2),
        ):
            await ws_manager.connect(ws)
            await _flush()
            ws_manager.broadcast_nowait("community_packet", {})  # taken by the writer
            await _flush()
            for i in range(10):
                ws_manager.broadcast_nowait("community_packet", {"i": i})
            assert ws in ws_manager.active_connections
            for i in range(5):
                ws_manager.broadcast_nowait("message", {"id": i})
            await _flush()

        assert ws not in ws_manager.active_connections
        ws.close.assert_awaited_once_with(code=EVICT_CLOSE_CODE)
        assert ws_manager.dropped_frames == {"community_packet": 8}
        gate.set()

    @pytest.mark.asyncio
    async def test_dropped_frames_are_logged_at_a_rate_limited_interval(self, caplog):
        now = 1000.0
        manager = WebSocketManager(clock=lambda: now)
        ws = _client_ws()
        gate = asyncio.Event()

        async def blocked_send(_):
            await gate.wait()

        ws.send_text.side_effect = blocked_send

        def drop_logs() -> list[str]:
            return [r.getMessage() for r in caplog.records if "dropped" in r.getMessage()]

        caplog.set_level(logging.INFO, logger="app.websocket")
        with (
            patch("app.websocket.CLIENT_QUEUE_MAX", 8),
            patch("app.websocket.CLIENT_COMMUNITY_PACKET_BACKLOG_MAX", 2),
            patch("app.websocket.DROP_LOG_INTERVAL_SECONDS", 60.0),
        ):
            await manager.connect(ws)
            await _flush()
            manager.broadcast_nowait("message", {"id": 0})  # taken by the writer
            await _flush()
            for i in range(2):
                manager.broadcast_nowait("message", {"id": i + 1})
            # First drop logs at once.
            manager.broadcast_nowait("community_packet", {})
            assert drop_logs() == [
                "WebSocket client lagging: dropped 1 community_packet frame(s) (backlog 2)"
            ]

            # Further drops inside the interval are only counted.
            now += 30
            for _ in range(5):
                manager.broadcast_nowait("community_packet", {})
            manager.broadcast_nowait("contact", {"k": 1})
            assert len(drop_logs()) == 1

            # Once the interval is up, the next frame for that client reports them.
            now += 31
            manager.broadcast_nowait("contact", {"k": 2})
            assert drop_logs()[1:] == [
                "WebSocket client lagging: dropped 5 community_packet frame(s) (backlog 4)"
            ]

            # Unreported drops are flushed when the client leaves.
            manager.broadcast_nowait("community_packet", {})
            assert len(drop_logs()) == 2
            await manager.disconnect(ws)
            assert drop_logs()[2:] == [
                "WebSocket client lagging: dropped 1 community_packet frame(s) (backlog 4)"
            ]
        assert manager.dropped_frames["community_packet"] == 7
        gate.set()

    @pytest.mark.asyncio
    async def test_broadcast_empty_connections(self, ws_manager: WebSocketManager):
        """Broadcast should handle empty connection list gracefully."""
        await ws_manager.broadcast("test", {"data": "value"})


class TestWebSocketConnectionManagement:
    """Tests for connection/disconnection."""

    @pytest.mark.asyncio
    async def test_connect_adds_to_list(self, ws_manager: WebSocketManager, mock_websocket):
        """Connect should add websocket to active connections."""
        assert len(ws_manager.active_connections) == 0

        await ws_manager.connect(mock_websocket)

        assert len(ws_manager.active_connections) == 1
        assert mock_websocket in ws_manager.active_connections

    @pytest.mark.asyncio
    async def test_disconnect_removes_from_list(self, ws_manager: WebSocketManager, mock_websocket):
        """Disconnect should remove websocket from active connections."""
        await ws_manager.connect(mock_websocket)
        assert len(ws_manager.active_connections) == 1

        await ws_manager.disconnect(mock_websocket)

        assert len(ws_manager.active_connections) == 0

    @pytest.mark.asyncio
    async def test_disconnect_nonexistent_is_safe(
        self, ws_manager: WebSocketManager, mock_websocket
    ):
        """Disconnecting a non-connected websocket should not raise."""
        # Should not raise
        await ws_manager.disconnect(mock_websocket)
        assert len(ws_manager.active_connections) == 0


class TestBroadcastEventFanout:
    """Test that broadcast_event dispatches to WS and fanout manager."""

    @pytest.mark.asyncio
    async def test_broadcast_event_dispatches_to_ws_and_fanout(self):
        """broadcast_event creates a WS task and dispatches to fanout manager."""
        from app.websocket import broadcast_event

        with (
            patch("app.websocket.ws_manager") as mock_ws,
            patch("app.fanout.manager.fanout_manager") as mock_fm,
        ):
            mock_fm.broadcast_message = AsyncMock()

            broadcast_event("message", {"id": 1, "text": "hello"})

            # Let the asyncio tasks run
            await asyncio.sleep(0)

            mock_ws.broadcast_nowait.assert_called_once_with("message", {"id": 1, "text": "hello"})
            mock_fm.broadcast_message.assert_called_once_with({"id": 1, "text": "hello"})

    @pytest.mark.asyncio
    async def test_broadcast_event_raw_packet_dispatches_to_fanout(self):
        """broadcast_event for raw_packet dispatches to fanout broadcast_raw."""
        from app.websocket import broadcast_event

        with (
            patch("app.websocket.ws_manager") as mock_ws,
            patch("app.fanout.manager.fanout_manager") as mock_fm,
        ):
            mock_fm.broadcast_raw = AsyncMock()

            broadcast_event("raw_packet", {"data": "ff00"})
            await asyncio.sleep(0)

            mock_ws.broadcast_nowait.assert_called_once()
            mock_fm.broadcast_raw.assert_called_once_with({"data": "ff00"})


class TestDispatchTelemetryEvent:
    """Telemetry fanout is fire-and-forget and is not a WebSocket event."""

    @pytest.mark.asyncio
    async def test_dispatch_telemetry_does_not_broadcast_ws(self):
        from app.websocket import dispatch_telemetry_event

        payload = {"public_key": "aabb", "battery_volts": 3.9}

        with (
            patch("app.websocket.ws_manager") as mock_ws,
            patch("app.fanout.manager.fanout_manager") as mock_fm,
        ):
            mock_fm.broadcast_telemetry = AsyncMock()

            dispatch_telemetry_event(payload)
            await asyncio.sleep(0)

            mock_ws.broadcast_nowait.assert_not_called()
            mock_fm.broadcast_telemetry.assert_called_once_with(payload)


class TestBroadcastErrorSuccessCodes:
    """Stable i18n codes on error/success toasts must not drop the English message."""

    @pytest.mark.asyncio
    async def test_broadcast_error_payload_without_code_is_unchanged(self):
        from app.websocket import broadcast_error

        with patch("app.websocket.ws_manager") as mock_ws:
            broadcast_error("Radio not connected")
            await asyncio.sleep(0)

        mock_ws.broadcast_nowait.assert_called_once_with(
            "error", {"message": "Radio not connected"}
        )

    @pytest.mark.asyncio
    async def test_broadcast_error_includes_code_and_params(self):
        from app.websocket import broadcast_error

        with patch("app.websocket.ws_manager") as mock_ws:
            broadcast_error(
                "Cannot decrypt historical DMs",
                "Private key not available.",
                code="cannot_decrypt_historical_dms",
                params={"name": "Alice"},
            )
            await asyncio.sleep(0)

        mock_ws.broadcast_nowait.assert_called_once_with(
            "error",
            {
                "message": "Cannot decrypt historical DMs",
                "details": "Private key not available.",
                "code": "cannot_decrypt_historical_dms",
                "params": {"name": "Alice"},
            },
        )

    @pytest.mark.asyncio
    async def test_broadcast_success_includes_code(self):
        from app.websocket import broadcast_success

        with patch("app.websocket.ws_manager") as mock_ws:
            broadcast_success(
                "Historical decrypt complete for Alice",
                "Decrypted 1 message",
                code="historical_decrypt_complete",
                params={"name": "Alice"},
            )
            await asyncio.sleep(0)

        mock_ws.broadcast_nowait.assert_called_once_with(
            "success",
            {
                "message": "Historical decrypt complete for Alice",
                "details": "Decrypted 1 message",
                "code": "historical_decrypt_complete",
                "params": {"name": "Alice"},
            },
        )


class TestTypedEventSerialization:
    """Tests for typed websocket event serialization."""

    def test_dump_ws_event_preserves_optional_message_acked_shape(self):
        from app.events import dump_ws_event

        serialized = dump_ws_event("message_acked", {"message_id": 7, "ack_count": 2})

        assert json.loads(serialized) == {
            "type": "message_acked",
            "data": {"message_id": 7, "ack_count": 2},
        }

    def test_dump_ws_event_serializes_message_deleted(self):
        from app.events import dump_ws_event

        serialized = dump_ws_event("message_deleted", {"message_id": 42})

        assert json.loads(serialized) == {
            "type": "message_deleted",
            "data": {"message_id": 42},
        }

    def test_dump_ws_event_preserves_toast_code_and_params(self):
        from app.events import dump_ws_event

        serialized = dump_ws_event(
            "error",
            {
                "message": "Radio not connected",
                "code": "radio_not_connected",
                "params": {"name": "Alice"},
            },
        )

        assert json.loads(serialized) == {
            "type": "error",
            "data": {
                "message": "Radio not connected",
                "code": "radio_not_connected",
                "params": {"name": "Alice"},
            },
        }

    def test_dump_ws_event_falls_back_to_raw_payload_when_validation_fails(self):
        from app.events import dump_ws_event

        serialized = dump_ws_event("message_acked", {"ack_count": 2})

        assert json.loads(serialized) == {
            "type": "message_acked",
            "data": {"ack_count": 2},
        }


class TestHistoricalMessageFlag:
    """`historical` is set only on realtime=False message broadcasts, absent everywhere else."""

    @staticmethod
    def _message():
        from app.models import Message

        return Message(
            id=1,
            type="CHAN",
            conversation_key="AA" * 16,
            text="Alice: hi",
            sender_timestamp=100,
            received_at=101,
        )

    @staticmethod
    def _capture(**kwargs):
        from app.services.messages import broadcast_message

        calls = []

        def fn(event_type, data, **kw):
            calls.append((event_type, data, kw))

        broadcast_message(message=TestHistoricalMessageFlag._message(), broadcast_fn=fn, **kwargs)
        return calls[0]

    def test_realtime_false_sets_historical(self):
        from app.events import dump_ws_event

        _, data, kw = self._capture(realtime=False)
        assert kw == {"realtime": False}
        assert data["historical"] is True
        assert json.loads(dump_ws_event("message", data))["data"]["historical"] is True

    @pytest.mark.parametrize("kwargs", [{"realtime": True}, {}])
    def test_live_payload_has_no_historical_key(self, kwargs):
        from app.events import dump_ws_event

        _, data, _ = self._capture(**kwargs)
        assert "historical" not in data
        assert "historical" not in json.loads(dump_ws_event("message", data))["data"]

    def test_model_dump_and_json_omit_historical_when_unset(self):
        message = self._message()
        assert "historical" not in message.model_dump()
        assert "historical" not in message.model_dump(mode="json")
        assert "historical" not in json.loads(message.model_dump_json())

    @pytest.mark.asyncio
    async def test_fanout_receives_no_historical_key_for_live_message(self):
        from app.websocket import broadcast_event

        _, data, kw = self._capture(realtime=True)
        with (
            patch("app.websocket.ws_manager"),
            patch("app.fanout.manager.fanout_manager") as mock_fm,
        ):
            mock_fm.broadcast_message = AsyncMock()
            broadcast_event("message", data, **kw)
            await asyncio.sleep(0)
        mock_fm.broadcast_message.assert_called_once()
        assert "historical" not in mock_fm.broadcast_message.call_args.args[0]
