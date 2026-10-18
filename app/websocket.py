"""WebSocket manager for real-time updates."""

import asyncio
import logging
import math
import time
from collections import Counter
from collections.abc import Callable, Iterable
from typing import Any

from fastapi import WebSocket

from app.auth.permissions import (
    CONTACT_DM_ACTIVITY_FIELDS,
    Permission,
    has_permission,
    packet_decryption_is_private,
)
from app.auth.principal import Principal
from app.auth.ws_events import WsEvent, ws_event
from app.background_tasks import spawn
from app.events import dump_ws_event
from app.origin_policy import websocket_origin_allowed

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
_DROPPABLE_EVENTS = frozenset({WsEvent.RAW_PACKET, WsEvent.COMMUNITY_PACKET})

# A lagging client logs its dropped-frame counts at most once per interval: the
# first drop logs at once, later ones are summed and logged when the interval is
# up (on the next frame for that client), or when the client leaves.
DROP_LOG_INTERVAL_SECONDS = 60.0


def _reveals_direct_message(event_type: WsEvent, data: Any) -> bool:
    """A ``PRIV`` message, or a raw packet decrypted from one (``read_private``).

    A ``message`` is private unless its payload says ``CHAN`` (fail closed):
    live, historical and outgoing messages all carry their ``type``.
    """
    if event_type is WsEvent.MESSAGE:
        return not isinstance(data, dict) or data.get("type") != "CHAN"
    if not isinstance(data, dict):
        return False
    if event_type is WsEvent.RAW_PACKET:
        info = data.get("decrypted_info")
        # No decryption, nothing to hide: control packets stay readable.
        return isinstance(info, dict) and packet_decryption_is_private(
            data.get("payload_type"), info.get("channel_key")
        )
    return False


def _without_direct_message(data: dict) -> dict:
    """A raw packet as a client without ``read_private`` gets it: no decryption."""
    return {**data, "decrypted": False, "decrypted_info": None}


def _without_dm_activity(contact: Any) -> Any:
    """A contact as a client without ``read_private`` gets it (like REST)."""
    if not isinstance(contact, dict):
        return contact
    return {**contact, **dict.fromkeys(CONTACT_DM_ACTIVITY_FIELDS)}


# The permission an event needs on top of its type's when the caller names none
# (``audience``). Toasts can carry hosts, ports, radio and fanout errors, so
# they are admin-only unless the caller says otherwise; an ack is private
# unless the caller says it acks a channel message (fail closed).
_DEFAULT_AUDIENCE: dict[WsEvent, Permission] = {
    WsEvent.ERROR: "admin",
    WsEvent.SUCCESS: "admin",
    WsEvent.MESSAGE_ACKED: "read_private",
}

# What a client gets of an event besides the whole payload.
_HEALTH_WITHOUT_ADMIN_DETAILS = "health_without_admin_details"
_CONTACT_WITHOUT_DM_ACTIVITY = "contact_without_dm_activity"
_PACKET_UNDECRYPTED = "packet_undecrypted"
_FULL = "full"


def _view(
    event_type: WsEvent, data: Any, principal: Principal | None, audience: Permission
) -> str | None:
    """Which version of the event this principal gets; None for nothing."""
    if not has_permission(principal, event_type.permission):
        return None
    if not has_permission(principal, audience):
        return None
    if event_type is WsEvent.HEALTH:
        from app.routers.health import HEALTH_DETAIL_PERMISSION

        if not has_permission(principal, HEALTH_DETAIL_PERMISSION):
            return _HEALTH_WITHOUT_ADMIN_DETAILS
        return _FULL
    if has_permission(principal, "read_private"):
        return _FULL
    if event_type in (WsEvent.CONTACT, WsEvent.CONTACT_RESOLVED):
        return _CONTACT_WITHOUT_DM_ACTIVITY
    if _reveals_direct_message(event_type, data):
        return _PACKET_UNDECRYPTED if event_type is WsEvent.RAW_PACKET else None
    return _FULL


def _render(event_type: WsEvent, data: Any, view: str) -> Any:
    """The payload of one version of the event (``_view``)."""
    if view == _HEALTH_WITHOUT_ADMIN_DETAILS:
        from app.routers.health import without_admin_details

        return without_admin_details(data) if isinstance(data, dict) else data
    if view == _CONTACT_WITHOUT_DM_ACTIVITY:
        if event_type is WsEvent.CONTACT_RESOLVED and isinstance(data, dict):
            return {**data, "contact": _without_dm_activity(data.get("contact"))}
        return _without_dm_activity(data)
    if view == _PACKET_UNDECRYPTED:
        return _without_direct_message(data)
    return data


def _backlog_limit(event_type: WsEvent) -> int:
    """Backlog at which a droppable frame is shed (read at call time for tests)."""
    if event_type is WsEvent.COMMUNITY_PACKET:
        return CLIENT_COMMUNITY_PACKET_BACKLOG_MAX
    return CLIENT_RAW_PACKET_BACKLOG_MAX


# Event types that are not a ``WsEvent`` member already refused (logged once each).
_refused_event_types: set[str] = set()


def _classified(event_type: str) -> WsEvent | None:
    """The member for ``event_type``; None (and one ERROR log) for any other type.

    Fail closed: an unclassified type reaches no client, admins included.
    """
    member = ws_event(event_type)
    if member is None and event_type not in _refused_event_types:
        if len(_refused_event_types) < 64:
            _refused_event_types.add(event_type)
        logger.error("Refused to send WebSocket event %r: not a WsEvent member", event_type)
    return member


# Close code sent to an evicted client: 1013 "Try Again Later". The frontend
# reconnects on any close and refetches state, so nothing is lost.
EVICT_CLOSE_CODE = 1013


# Close code for a refused upgrade: 1008 "Policy Violation". Sent before
# accept(), it reaches the client as a plain HTTP 403 (uvicorn), not a frame.
# A denial response (websocket.http.response) would carry a body, but uvicorn
# then logs "ASGI callable returned without completing handshake" at ERROR.
FOREIGN_ORIGIN_CLOSE_CODE = 1008

# Close code for a socket whose credential stopped being valid (its session or
# token was revoked or expired, its account disabled): 1008 "Policy Violation".
# The frontend reconnects, the gate refuses the upgrade, and its next REST call
# gets the 401 that sends it to the login page.
REVOKED_CLOSE_CODE = 1008

# A socket keeps the principal it opened with, role included: a change of role
# must revoke the user's sessions (``app.auth.sessions.revoke_user_sessions``)
# so its sockets close and reconnect with the new role; the recheck below
# checks liveness only.
#
# How often the sessions and tokens of the open sockets are checked against the
# database (one query for each). A revocation in this process closes its sockets
# at once (``close_sessions``, ``close_tokens``); this catches the rest:
# ``meshloom-admin``, which writes the database from another process, an expiry,
# a disabled account.
SESSION_RECHECK_INTERVAL_SECONDS = 30.0


# Last WARNING per refused origin (monotonic seconds), so a hostile page that
# retries in a loop cannot flood the log. Bounded: the oldest entry is evicted.
_refusal_last_logged: dict[str | None, float] = {}
_REFUSAL_LOG_MAX_ORIGINS = 256


def _log_refusal(origin: str | None, host: str | None) -> None:
    """Log a refusal at WARNING once per origin per ``DROP_LOG_INTERVAL_SECONDS``."""
    now = time.monotonic()
    last = _refusal_last_logged.get(origin)
    if last is not None and now - last < DROP_LOG_INTERVAL_SECONDS:
        return
    if origin not in _refusal_last_logged and (
        len(_refusal_last_logged) >= _REFUSAL_LOG_MAX_ORIGINS
    ):
        _refusal_last_logged.pop(next(iter(_refusal_last_logged)))
    _refusal_last_logged[origin] = now
    logger.warning(
        "Refused a WebSocket upgrade from a foreign origin %r (host %r)",
        origin,
        host,
    )


async def reject_foreign_origin(websocket: WebSocket) -> bool:
    """Refuse an upgrade from a foreign ``Origin`` before it is accepted.

    Same-origin, listed in Allowed origins, or no ``Origin`` at all is accepted
    (``app/origin_policy.py``, shared with CORS).

    Returns True when the upgrade was refused: the caller must return without
    calling ``ws_manager.connect()``. The socket is never accepted, so no
    frame is sent; the client gets an HTTP 403.
    """
    if websocket_origin_allowed(websocket.headers):
        return False
    _log_refusal(websocket.headers.get("origin"), websocket.headers.get("host"))
    await websocket.close(code=FOREIGN_ORIGIN_CLOSE_CODE)
    return True


class _Client:
    """One connected socket: a bounded outbound queue drained by a single writer.

    A single writer per client keeps frames in broadcast order (concurrent
    ``send_text`` calls on one socket do not), and the bound means one stuck
    client costs at most ``CLIENT_QUEUE_MAX`` frames of memory.
    """

    __slots__ = (
        "websocket",
        "principal",
        "session_id",
        "token_id",
        "last_slide",
        "expiry_timer",
        "queue",
        "writer",
        "unreported",
        "last_drop_log",
    )

    def __init__(self, websocket: WebSocket, principal: Principal | None):
        self.websocket = websocket
        # Who opened the socket: decides which events it receives
        # (``WsEvent.permission``). None receives nothing (fail closed).
        self.principal = principal
        # The credential that opened it, for ``close_sessions``/``close_tokens``.
        self.session_id = principal.session_id if principal is not None else None
        self.token_id = principal.token_id if principal is not None else None
        # Wall-clock second of the last session slide from this socket's
        # frames (``note_activity``); the upgrade request itself just slid it.
        self.last_slide = time.time()
        # Closes a token socket at the token's expiry (``connect``).
        self.expiry_timer: asyncio.TimerHandle | None = None
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
        # Open clients by the session or token that opened them.
        self._by_session: dict[str, set[_Client]] = {}
        self._by_token: dict[str, set[_Client]] = {}
        # Runs while a session socket is open (``_recheck_sessions``).
        self._recheck_task: asyncio.Task[None] | None = None

    async def connect(self, websocket: WebSocket, principal: Principal | None) -> None:
        await websocket.accept()
        client = _Client(websocket, principal)
        async with self._lock:
            self.active_connections[websocket] = client
            self._index(client)
        loop = asyncio.get_running_loop()
        client.writer = loop.create_task(self._writer(client), name="ws-writer")
        expires_at = principal.token_expires_at if principal is not None else None
        if expires_at is not None:
            client.expiry_timer = loop.call_later(
                max(0.0, expires_at - time.time()),
                lambda: self._evict(client, "token expired", code=REVOKED_CLOSE_CODE),
            )
        if (client.session_id is not None or client.token_id is not None) and (
            self._recheck_task is None or self._recheck_task.done()
        ):
            self._recheck_task = spawn(self._recheck_sessions(), name="ws-session-recheck")
        logger.info("WebSocket client connected (%d total)", len(self.active_connections))

    async def disconnect(self, websocket: WebSocket) -> None:
        async with self._lock:
            client = self.active_connections.pop(websocket, None)
            empty = not self.active_connections
        if client is not None:
            if client.expiry_timer is not None:
                client.expiry_timer.cancel()
            self._unindex(client)
            self._stop_writer(client)
            self._log_drops(client, final=True)
        logger.info("WebSocket client disconnected (%d remaining)", len(self.active_connections))
        if empty and self is ws_manager:
            from app.services.community_live import release_live_on_app_ws_empty

            await release_live_on_app_ws_empty()

    def _index(self, client: _Client) -> None:
        if client.session_id is not None:
            self._by_session.setdefault(client.session_id, set()).add(client)
        if client.token_id is not None:
            self._by_token.setdefault(client.token_id, set()).add(client)

    def _unindex(self, client: _Client) -> None:
        for index, key in (
            (self._by_session, client.session_id),
            (self._by_token, client.token_id),
        ):
            if key is None:
                continue
            clients = index.get(key)
            if clients is not None:
                clients.discard(client)
                if not clients:
                    del index[key]

    def close_sessions(self, session_ids: Iterable[str]) -> int:
        """Close every socket opened with one of these sessions (policy close
        1008). Returns how many. Call it after revoking them."""
        return self._close_indexed(self._by_session, session_ids, "session revoked")

    def close_user(self, user_id: int, *, except_session_id: str | None = None) -> int:
        """Close every socket a user opened, whatever its credential (session,
        Basic, ingress, token), except those of ``except_session_id``: a role
        change, a disable or a deletion (a socket keeps the role it opened with).
        Returns how many."""
        clients = [
            client
            for client in self.active_connections.values()
            if client.principal is not None
            and client.principal.user_id == user_id
            and (except_session_id is None or client.session_id != except_session_id)
        ]
        for client in clients:
            self._evict(client, "account changed", code=REVOKED_CLOSE_CODE)
        return len(clients)

    def close_tokens(self, token_ids: Iterable[str]) -> int:
        """Close every socket opened with one of these API or device tokens."""
        return self._close_indexed(self._by_token, token_ids, "token revoked")

    def _close_indexed(
        self, index: dict[str, set[_Client]], keys: Iterable[str], reason: str
    ) -> int:
        clients = [client for key in set(keys) for client in index.get(key, ())]
        for client in clients:
            self._evict(client, reason, code=REVOKED_CLOSE_CODE)
        return len(clients)

    async def note_activity(self, websocket: WebSocket) -> None:
        """A frame from the client: slide its session like an HTTP request
        would, so a page that only keeps a WebSocket open (a kiosk display)
        is not signed out at expiry. At most one attempt per
        ``LAST_SEEN_WRITE_INTERVAL_S`` per socket, then ``touch()``'s own
        once-a-minute rule."""
        from app.repository.auth import LAST_SEEN_WRITE_INTERVAL_S

        client = self.active_connections.get(websocket)
        if client is None or client.session_id is None:
            return
        now = time.time()
        if now - client.last_slide < LAST_SEEN_WRITE_INTERVAL_S:
            return
        client.last_slide = now
        from app.auth.sessions import slide_session

        try:
            await slide_session(client.session_id, now=int(now))
        except Exception:
            logger.debug("Could not slide the session of a WebSocket", exc_info=True)

    async def _recheck_sessions(self) -> None:
        """Close the sockets whose session or token is no longer live, every
        ``SESSION_RECHECK_INTERVAL_SECONDS``, while such a socket is open."""
        from app.repository.auth import ApiTokenRepository, AuthSessionRepository

        while self._by_session or self._by_token:
            await asyncio.sleep(SESSION_RECHECK_INTERVAL_SECONDS)
            open_ids = list(self._by_session)
            open_tokens = list(self._by_token)
            if not open_ids and not open_tokens:
                break
            try:
                live = await AuthSessionRepository.filter_live(open_ids)
                live_tokens = await ApiTokenRepository.filter_live(open_tokens)
            except Exception:
                # Fail closed would sign everyone out on a database hiccup;
                # the gate still refuses the next upgrade and request.
                logger.exception("Could not recheck the sessions of the open WebSockets")
                continue
            if closed := self.close_sessions(set(open_ids) - live):
                logger.info("Closed %d WebSocket(s) whose session ended", closed)
            if closed := self.close_tokens(set(open_tokens) - live_tokens):
                logger.info("Closed %d WebSocket(s) whose token ended", closed)

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

    def _evict(self, client: _Client, reason: str, *, code: int = EVICT_CLOSE_CODE) -> None:
        """Drop a client and actively close its socket.

        Merely forgetting the socket would leave the TCP session open and the
        browser would never learn it stopped receiving updates.
        """
        if self.active_connections.get(client.websocket) is not client:
            return
        del self.active_connections[client.websocket]
        if client.expiry_timer is not None:
            client.expiry_timer.cancel()
        self._unindex(client)
        logger.info(
            "Evicting WebSocket client (%s); %d remaining", reason, len(self.active_connections)
        )
        self._stop_writer(client)
        self._log_drops(client, final=True)
        spawn(self._close_quietly(client.websocket, code), name="ws-close-evicted")

    @staticmethod
    async def _close_quietly(websocket: WebSocket, code: int = EVICT_CLOSE_CODE) -> None:
        try:
            await asyncio.wait_for(websocket.close(code=code), timeout=SEND_TIMEOUT_SECONDS)
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

    def _enqueue(self, client: _Client, message: str, *, event_type: WsEvent | None = None) -> None:
        if event_type is not None and event_type in _DROPPABLE_EVENTS:
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

    def broadcast_nowait(
        self, event_type: WsEvent, data: Any, *, audience: Permission | None = None
    ) -> None:
        """Queue an event for every connected client allowed to see it, in call order.

        A client needs the type's permission (``WsEvent.permission``) and the
        event's ``audience`` (``_DEFAULT_AUDIENCE`` when None). Below
        ``read_private`` a direct ``message`` is not sent, a ``raw_packet``
        decrypted from one is sent undecrypted and a contact loses its DM
        activity; below admin ``health`` loses its errors. A type that is not
        a ``WsEvent`` member is sent to no one.
        """
        if not self.active_connections:
            return
        member = _classified(event_type)
        if member is None:
            return
        audience = audience or _DEFAULT_AUDIENCE.get(member, member.permission)
        frames: dict[str, str] = {}
        for client in list(self.active_connections.values()):
            view = _view(member, data, client.principal, audience)
            if view is None:
                continue
            frame = frames.get(view)
            if frame is None:
                frame = frames[view] = dump_ws_event(member, _render(member, data, view))
            self._enqueue(client, frame, event_type=member)

    async def broadcast(
        self, event_type: WsEvent, data: Any, *, audience: Permission | None = None
    ) -> None:
        """Queue an event for every connected client (never blocks on I/O)."""
        self.broadcast_nowait(event_type, data, audience=audience)

    async def send_personal(
        self,
        websocket: WebSocket,
        event_type: WsEvent,
        data: Any,
        *,
        audience: Permission | None = None,
    ) -> None:
        """Queue an event for one client, ordered with its broadcasts (same filters)."""
        client = self.active_connections.get(websocket)
        if client is None:
            return
        member = _classified(event_type)
        if member is None:
            return
        audience = audience or _DEFAULT_AUDIENCE.get(member, member.permission)
        view = _view(member, data, client.principal, audience)
        if view is not None:
            self._enqueue(client, dump_ws_event(member, _render(member, data, view)))

    def send_raw(self, websocket: WebSocket, message: str) -> None:
        """Queue a pre-serialized frame for one client: the ``pong`` reply only,
        never an event (events go through the ``WsEvent`` filters above)."""
        client = self.active_connections.get(websocket)
        if client is not None:
            self._enqueue(client, message)


# Global instance
ws_manager = WebSocketManager()


def broadcast_event(
    event_type: WsEvent,
    data: dict,
    *,
    realtime: bool = True,
    audience: Permission | None = None,
) -> None:
    """Broadcast an event without blocking.

    Queues the event for every connected WebSocket client allowed to see it
    (per-client order follows call order) and forwards it to fanout modules.

    Args:
        event_type: A ``WsEvent`` member, never a string (``app/auth/ws_events.py``)
        data: Event payload dict
        realtime: If False, skip fanout dispatch (used for historical decryption)
        audience: The permission a WebSocket client needs to get this event, on
            top of the type's (``WebSocketManager.broadcast_nowait``)
    """
    ws_manager.broadcast_nowait(event_type, data, audience=audience)

    if realtime:
        try:
            from app.radio_proxy.manager import radio_proxy_manager

            radio_proxy_manager.notify_broadcast(event_type, data)
        except Exception:
            logger.debug("Radio proxy fanout failed", exc_info=True)
        from app.fanout.manager import fanout_manager

        if event_type == WsEvent.MESSAGE:
            spawn(fanout_manager.broadcast_message(data))

            from app.push.manager import push_manager

            spawn(push_manager.dispatch_message(data))
        elif event_type == WsEvent.RAW_PACKET:
            spawn(fanout_manager.broadcast_raw(data))
        elif event_type == WsEvent.CONTACT:
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
    audience: Permission = "admin",
) -> None:
    """Broadcast an error notification (a toast in the frontend).

    ``code`` / ``params`` are optional stable i18n keys; ``message`` stays English.
    Only admins get it unless ``audience`` names a lower permission: pass one
    only when the text holds nothing an admin-only setting, a host, a port or
    a direct message would reveal (``read_private`` for DM details).
    """
    data: dict[str, Any] = {"message": message}
    if details:
        data["details"] = details
    if code:
        data["code"] = code
    if params:
        data["params"] = params
    ws_manager.broadcast_nowait(WsEvent.ERROR, data, audience=audience)


def broadcast_success(
    message: str,
    details: str | None = None,
    *,
    code: str | None = None,
    params: dict | None = None,
    audience: Permission = "admin",
) -> None:
    """Broadcast a success notification (a toast in the frontend).

    ``code`` / ``params`` are optional stable i18n keys; ``message`` stays English.
    Only admins get it unless ``audience`` names a lower permission: pass one
    only when the text holds nothing an admin-only setting, a host, a port or
    a direct message would reveal (``read_private`` for DM details).
    """
    data: dict[str, Any] = {"message": message}
    if details:
        data["details"] = details
    if code:
        data["code"] = code
    if params:
        data["params"] = params
    ws_manager.broadcast_nowait(WsEvent.SUCCESS, data, audience=audience)


def broadcast_health(radio_connected: bool, connection_info: str | None = None) -> None:
    """Broadcast health status change to all connected clients."""

    async def _broadcast():
        from app.routers.health import build_health_data

        data = await build_health_data(radio_connected, connection_info)
        await ws_manager.broadcast(WsEvent.HEALTH, data)

    spawn(_broadcast())
