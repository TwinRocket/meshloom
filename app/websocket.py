"""WebSocket manager for real-time updates."""

import asyncio
import logging
import math
import time
from collections import Counter
from collections.abc import Callable, Mapping
from typing import Any
from urllib.parse import urlsplit

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
CLIENT_QUEUE_MAX = 2048

# Best-effort frames are dropped for a lagging client instead of queued once its
# backlog reaches the type's limit (drop-newest: the queue keeps what it holds).
# Everything else (health, message, contact, message_acked, channel, *_deleted,
# community_live status, toasts) carries state and is never dropped: it fills
# the headroom up to CLIENT_QUEUE_MAX, then the client is evicted.
#
# ``raw_packet`` feeds the local packet feed and arrives in bursts. Shedding it
# past this backlog keeps headroom for state-bearing frames and avoids
# evict/reconnect loops during RF bursts.
CLIENT_RAW_PACKET_BACKLOG_MAX = 1024

# ``community_packet`` is the Community live feed (other regions included). It
# only drives the Live map and live "heard by" bumps, which REST reconciles, so
# it is shed first, at a smaller backlog. The upstream relay queue
# (``community_live.FANOUT_QUEUE_MAX``) is drained at once into these per-client
# queues, so this is the bound that protects a slow reader.
CLIENT_COMMUNITY_PACKET_BACKLOG_MAX = 256

# Event types that may be dropped (never cause eviction) under backpressure.
_DROPPABLE_EVENTS = frozenset({"raw_packet", "community_packet"})

# A lagging client logs its dropped-frame counts at most once per interval: the
# first drop logs at once, later ones are summed and logged when the interval is
# up (on the next frame for that client), or when the client leaves.
DROP_LOG_INTERVAL_SECONDS = 60.0


def _backlog_limit(event_type: str) -> int:
    """Backlog at which a droppable frame is shed (read at call time for tests)."""
    if event_type == "community_packet":
        return CLIENT_COMMUNITY_PACKET_BACKLOG_MAX
    return CLIENT_RAW_PACKET_BACKLOG_MAX


# Close code sent to an evicted client: 1013 "Try Again Later". The frontend
# reconnects on any close and refetches state, so nothing is lost.
EVICT_CLOSE_CODE = 1013


_DEFAULT_PORTS = {"http": 80, "https": 443}

# Close code for a refused upgrade: 1008 "Policy Violation". Sent before
# accept(), it reaches the client as a plain HTTP 403 (uvicorn), not a frame.
# A denial response (websocket.http.response) would carry a body, but uvicorn
# then logs "ASGI callable returned without completing handshake" at ERROR.
FOREIGN_ORIGIN_CLOSE_CODE = 1008


def _host_and_port(netloc: str, default_port: int) -> tuple[str, int] | None:
    try:
        parts = urlsplit(f"//{netloc.strip()}")
        hostname = parts.hostname
        port = parts.port or default_port
    except ValueError:
        return None
    if not hostname:
        return None
    return hostname.lower(), port


def origin_allowed(headers: Mapping[str, str]) -> bool:
    """Whether a WebSocket upgrade's ``Origin`` is absent or same-origin.

    Browsers always send ``Origin`` on a WebSocket upgrade and never let a page
    set ``Host`` or ``X-Forwarded-Host``, so a page on another site is refused
    (cross-site WebSocket hijacking) while scripts and native clients, which
    send no ``Origin``, keep working. The origin's host and port must equal
    the request's ``Host``, or the first ``X-Forwarded-Host`` set by a reverse
    proxy that rewrites ``Host`` (the Home Assistant ingress sets it). A
    missing port means the scheme's default; ``Origin: null`` is refused.
    """
    origin = headers.get("origin")
    if origin is None:
        return True
    try:
        parts = urlsplit(origin.strip())
    except ValueError:
        return False
    default_port = _DEFAULT_PORTS.get(parts.scheme.lower())
    if default_port is None or parts.path or parts.query or parts.username:
        return False
    origin_target = _host_and_port(parts.netloc, default_port)
    if origin_target is None:
        return False
    candidates = [headers.get("host", "")]
    forwarded_host = headers.get("x-forwarded-host")
    if forwarded_host:
        candidates.append(forwarded_host.split(",", 1)[0])
    return any(
        _host_and_port(candidate, default_port) == origin_target
        for candidate in candidates
        if candidate
    )


async def reject_foreign_origin(websocket: WebSocket) -> bool:
    """Refuse an upgrade from a foreign ``Origin`` before it is accepted.

    Returns True when the upgrade was refused: the caller must return without
    calling ``ws_manager.connect()``. The socket is never accepted, so no
    frame is sent; the client gets an HTTP 403.
    """
    if origin_allowed(websocket.headers):
        return False
    logger.warning(
        "Refused a WebSocket upgrade from a foreign origin %r (host %r)",
        websocket.headers.get("origin"),
        websocket.headers.get("host"),
    )
    await websocket.close(code=FOREIGN_ORIGIN_CLOSE_CODE)
    return True


class _Client:
    """One connected socket: a bounded outbound queue drained by a single writer.

    A single writer per client keeps frames in broadcast order (concurrent
    ``send_text`` calls on one socket do not), and the bound means one stuck
    client costs at most ``CLIENT_QUEUE_MAX`` frames of memory.
    """

    __slots__ = ("websocket", "queue", "writer", "unreported", "last_drop_log")

    def __init__(self, websocket: WebSocket):
        self.websocket = websocket
        self.queue: asyncio.Queue[str] = asyncio.Queue(maxsize=CLIENT_QUEUE_MAX)
        self.writer: asyncio.Task[None] | None = None
        # Dropped frames per event type not yet logged.
        self.unreported: Counter[str] = Counter()
        self.last_drop_log = -math.inf


class WebSocketManager:
    """Manages WebSocket connections and broadcasts events."""

    def __init__(self, clock: Callable[[], float] = time.monotonic):
        self.active_connections: dict[WebSocket, _Client] = {}
        self._lock = asyncio.Lock()
        self._clock = clock
        # Process-wide count of dropped best-effort frames, per event type.
        self.dropped_frames: Counter[str] = Counter()

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
            self._log_drops(client, final=True)
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
        self._log_drops(client, final=True)
        spawn(self._close_quietly(client.websocket), name="ws-close-evicted")

    @staticmethod
    async def _close_quietly(websocket: WebSocket) -> None:
        try:
            await asyncio.wait_for(
                websocket.close(code=EVICT_CLOSE_CODE), timeout=SEND_TIMEOUT_SECONDS
            )
        except Exception as e:
            logger.debug("Closing evicted WebSocket failed: %s", e)

    def _log_drops(self, client: _Client, *, final: bool = False) -> None:
        """Log a client's unreported drops, at most once per interval unless final."""
        if not client.unreported:
            return
        now = self._clock()
        if not final and now - client.last_drop_log < DROP_LOG_INTERVAL_SECONDS:
            return
        counts = ", ".join(f"{n} {t}" for t, n in sorted(client.unreported.items()))
        logger.info(
            "WebSocket client lagging: dropped %s frame(s) (backlog %d)",
            counts,
            client.queue.qsize(),
        )
        client.unreported.clear()
        client.last_drop_log = now

    def _enqueue(self, client: _Client, message: str, *, event_type: str | None = None) -> None:
        if event_type in _DROPPABLE_EVENTS:
            limit = _backlog_limit(event_type)
            if client.queue.qsize() >= limit:
                client.unreported[event_type] += 1
                self.dropped_frames[event_type] += 1
                self._log_drops(client)
                return
        try:
            client.queue.put_nowait(message)
        except asyncio.QueueFull:
            self._evict(client, f"outbound queue full ({CLIENT_QUEUE_MAX} frames)")
            return
        if client.unreported:
            # Drops still unreported (rate limit): log them once the interval is up.
            self._log_drops(client)

    def broadcast_nowait(self, event_type: str, data: Any) -> None:
        """Queue an event for every connected client, in call order."""
        if not self.active_connections:
            return
        message = dump_ws_event(event_type, data)
        for client in list(self.active_connections.values()):
            self._enqueue(client, message, event_type=event_type)

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
