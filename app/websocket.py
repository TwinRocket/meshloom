"""WebSocket manager for real-time updates."""

import asyncio
import logging
from typing import Any

from fastapi import WebSocket

from app.background_tasks import spawn
from app.events import dump_ws_event

logger = logging.getLogger(__name__)

# Timeout for individual WebSocket send operations (seconds). A client whose
# socket does not accept a frame within this delay is evicted.
SEND_TIMEOUT_SECONDS = 5.0

# Maximum number of frames buffered per client. A client that falls this far
# behind (slow link, suspended tab that still holds the TCP session) is evicted
# rather than letting its backlog grow without bound.
CLIENT_QUEUE_MAX = 512

# Close code sent to an evicted client: 1013 "Try Again Later". The frontend
# reconnects on any close and refetches state, so nothing is lost.
EVICT_CLOSE_CODE = 1013


class _Client:
    """One connected socket: a bounded outbound queue drained by a single writer.

    A single writer per client keeps frames in broadcast order (concurrent
    ``send_text`` calls on one socket do not), and the bound means one stuck
    client costs at most ``CLIENT_QUEUE_MAX`` frames of memory.
    """

    __slots__ = ("websocket", "queue", "writer")

    def __init__(self, websocket: WebSocket):
        self.websocket = websocket
        self.queue: asyncio.Queue[str] = asyncio.Queue(maxsize=CLIENT_QUEUE_MAX)
        self.writer: asyncio.Task[None] | None = None


class WebSocketManager:
    """Manages WebSocket connections and broadcasts events."""

    def __init__(self):
        self.active_connections: dict[WebSocket, _Client] = {}
        self._lock = asyncio.Lock()

    async def connect(self, websocket: WebSocket) -> None:
        await websocket.accept()
        client = _Client(websocket)
        async with self._lock:
            self.active_connections[websocket] = client
        client.writer = asyncio.get_running_loop().create_task(
            self._writer(client), name="ws-writer"
        )
        logger.info("WebSocket client connected (%d total)", len(self.active_connections))

    async def disconnect(self, websocket: WebSocket) -> None:
        async with self._lock:
            client = self.active_connections.pop(websocket, None)
            empty = not self.active_connections
        if client is not None:
            self._stop_writer(client)
        logger.info("WebSocket client disconnected (%d remaining)", len(self.active_connections))
        if empty and self is ws_manager:
            from app.services.community_live import release_live_on_app_ws_empty

            await release_live_on_app_ws_empty()

    @staticmethod
    def _stop_writer(client: _Client) -> None:
        writer = client.writer
        if writer is not None and not writer.done() and writer is not asyncio.current_task():
            writer.cancel()

    async def _writer(self, client: _Client) -> None:
        while True:
            message = await client.queue.get()
            try:
                await asyncio.wait_for(
                    client.websocket.send_text(message), timeout=SEND_TIMEOUT_SECONDS
                )
            except TimeoutError:
                self._evict(client, "send timed out")
                return
            except Exception as e:
                self._evict(client, f"send failed: {e}")
                return

    def _evict(self, client: _Client, reason: str) -> None:
        """Drop a client and actively close its socket.

        Merely forgetting the socket would leave the TCP session open and the
        browser would never learn it stopped receiving updates.
        """
        if self.active_connections.get(client.websocket) is not client:
            return
        del self.active_connections[client.websocket]
        logger.info(
            "Evicting WebSocket client (%s); %d remaining", reason, len(self.active_connections)
        )
        self._stop_writer(client)
        spawn(self._close_quietly(client.websocket), name="ws-close-evicted")

    @staticmethod
    async def _close_quietly(websocket: WebSocket) -> None:
        try:
            await asyncio.wait_for(
                websocket.close(code=EVICT_CLOSE_CODE), timeout=SEND_TIMEOUT_SECONDS
            )
        except Exception as e:
            logger.debug("Closing evicted WebSocket failed: %s", e)

    def _enqueue(self, client: _Client, message: str) -> None:
        try:
            client.queue.put_nowait(message)
        except asyncio.QueueFull:
            self._evict(client, f"outbound queue full ({CLIENT_QUEUE_MAX} frames)")

    def broadcast_nowait(self, event_type: str, data: Any) -> None:
        """Queue an event for every connected client, in call order."""
        if not self.active_connections:
            return
        message = dump_ws_event(event_type, data)
        for client in list(self.active_connections.values()):
            self._enqueue(client, message)

    async def broadcast(self, event_type: str, data: Any) -> None:
        """Queue an event for every connected client (never blocks on I/O)."""
        self.broadcast_nowait(event_type, data)

    async def send_personal(self, websocket: WebSocket, event_type: str, data: Any) -> None:
        """Queue an event for one client, ordered with its broadcasts."""
        self.send_raw(websocket, dump_ws_event(event_type, data))

    def send_raw(self, websocket: WebSocket, message: str) -> None:
        """Queue a pre-serialized frame for one client."""
        client = self.active_connections.get(websocket)
        if client is not None:
            self._enqueue(client, message)


# Global instance
ws_manager = WebSocketManager()


def broadcast_event(event_type: str, data: dict, *, realtime: bool = True) -> None:
    """Broadcast an event without blocking.

    Queues the event for every connected WebSocket client (per-client order
    follows call order) and forwards it to fanout modules.

    Args:
        event_type: Event type string (e.g. "message", "raw_packet")
        data: Event payload dict
        realtime: If False, skip fanout dispatch (used for historical decryption)
    """
    ws_manager.broadcast_nowait(event_type, data)

    if realtime:
        try:
            from app.radio_proxy.manager import radio_proxy_manager

            radio_proxy_manager.notify_broadcast(event_type, data)
        except Exception:
            logger.debug("Radio proxy fanout failed", exc_info=True)
        from app.fanout.manager import fanout_manager

        if event_type == "message":
            spawn(fanout_manager.broadcast_message(data))

            from app.push.manager import push_manager

            spawn(push_manager.dispatch_message(data))
        elif event_type == "raw_packet":
            spawn(fanout_manager.broadcast_raw(data))
        elif event_type == "contact":
            spawn(fanout_manager.broadcast_contact(data))


def dispatch_telemetry_event(data: dict) -> None:
    """Fire-and-forget fanout telemetry dispatch (not a WebSocket event)."""
    from app.fanout.manager import fanout_manager

    spawn(fanout_manager.broadcast_telemetry(data))


def broadcast_error(
    message: str,
    details: str | None = None,
    *,
    code: str | None = None,
    params: dict | None = None,
) -> None:
    """Broadcast an error notification to all connected clients.

    This appears as a toast notification in the frontend.
    ``code`` / ``params`` are optional stable i18n keys; ``message`` stays English.
    """
    data: dict[str, Any] = {"message": message}
    if details:
        data["details"] = details
    if code:
        data["code"] = code
    if params:
        data["params"] = params
    ws_manager.broadcast_nowait("error", data)


def broadcast_success(
    message: str,
    details: str | None = None,
    *,
    code: str | None = None,
    params: dict | None = None,
) -> None:
    """Broadcast a success notification to all connected clients.

    This appears as a toast notification in the frontend.
    ``code`` / ``params`` are optional stable i18n keys; ``message`` stays English.
    """
    data: dict[str, Any] = {"message": message}
    if details:
        data["details"] = details
    if code:
        data["code"] = code
    if params:
        data["params"] = params
    ws_manager.broadcast_nowait("success", data)


def broadcast_health(radio_connected: bool, connection_info: str | None = None) -> None:
    """Broadcast health status change to all connected clients."""

    async def _broadcast():
        from app.routers.health import build_health_data

        data = await build_health_data(radio_connected, connection_info)
        await ws_manager.broadcast("health", data)

    spawn(_broadcast())
