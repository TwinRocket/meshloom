"""Tests for WebSocket manager functionality."""

import asyncio
import json
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


async def _flush(rounds: int = 5) -> None:
    """Let per-client writer tasks drain their queues."""
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

        slow_ws.send_text.side_effect = hang
        await ws_manager.connect(good_ws)
        await ws_manager.connect(slow_ws)

        with patch("app.websocket.SEND_TIMEOUT_SECONDS", 0.05):
            await ws_manager.broadcast("test", {})
            await asyncio.sleep(0.15)
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
