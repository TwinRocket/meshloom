"""Meshloom Remote relay client: the instance's one outbound WebSocket to its relay
(docs/dev/remote.md, "Relay client"; contract meshloom-community
``docs/contracts/remote-relay.md``).

- **Egress.** Nothing connects unless ``remote_state.enabled``, Community is on
  (``remote.community_on()``, MSHL-445; ``link_status`` is ``community_off`` otherwise)
  and the instance is registered (a slug). The only destination is
  ``wss://<RELAY_HOST>/v1/connect`` (``connect_url``), with a fresh instance JWT per
  attempt (``mint_remote_jwt``) and ``X-Meshloom-Relay-Protocol: 1``. No system proxy.
  Turning Community off restarts the loop, which closes the socket
  (``tests/test_remote_off_no_egress.py``, ``tests/test_community_off_no_egress.py``).
- **Handshake.** The instance sends its HELLO first (version, install kind,
  ``plugins: {enabled, isolation}``, the gate state, the limits it wants), then waits
  10 s for the relay's HELLO and its effective limits. Liveness is the library's
  RFC 6455 ping (every 30 s, dropped after 60 s without a pong); there is no
  application ping.
- **Frames** (``app/services/remote_frames.py``). The relay opens odd streams; each
  ``REQ_HEAD`` or ``WS_OPEN`` is dispatched **in process** to the ASGI app
  (``await app(scope, receive, send)``), never over a local socket. The scope is
  ``https``/``wss`` with the slug ``Host``, ``client = (client_ip, 0)`` and
  ``state["via"] = "relay"`` (so the auth gate never treats it as LAN, the trusted
  networks and ingress never apply and the host guard defers to the check here).
  Hop-by-hop, forwarding (``X-Forwarded-*``, ``Forwarded``...), ingress and relay
  headers are dropped. Before dispatch the device pass in ``x-meshloom-pass`` is
  verified again (``remote.verify_device_pass``, the third check of the edge lock):
  without a valid one the answer is 401 ``remote_pass_required``.
- **Pass renewal.** The ``RESP_HEAD`` of a 2xx answer to a signed-in ``/api/*`` request
  with a browser pass may carry ``x-meshloom-set-pass`` (``remote.renew_browser_pass``,
  at most once a day per device); the relay turns it into ``__Host-mlgate``. Only this
  module adds the header: the same name from the app is dropped, and the relay reads it
  on HTTP answers only, so a WebSocket never has it.
- **Flow control.** Per stream and direction credits (grant once half the window is
  consumed, ``CANCEL credit_exceeded`` on overrun), at most ``max_streams`` streams,
  bodies and messages cut at ``max_chunk``. Outbound frames are batched over a 50 ms
  window into one message (at most 1 MiB); a final ``RESP_HEAD``, ``WS_ACCEPT``,
  ``CANCEL`` and ``WS_CLOSE`` flush at once.
- **Close codes.** 4001 mints a new token and reconnects (backoff if it repeats);
  4003 registers again and reconnects once, then ``not_registered``; 4004
  ``quota_exceeded`` when that is the close reason, else ``paused`` (retry after 15 to
  20 min either way); 4005 reconnects once, then ``replaced``, the count starting again
  after a connection that lasted 15 min; 4009 ``banned`` and 4010 ``update_required``
  stop; 1013 and unknown 4xxx
  ``relay_paused`` (5 to 15 min); anything else is an exponential backoff from 1 s to
  5 min with full jitter, reset after 60 s connected. A stopped state stays until
  Remote changes (enable, disable, register, rotate, restore) or Community is turned on
  or off, which restarts the loop.

Never log a pass, a token or a request header value.
"""

from __future__ import annotations

import asyncio
import ipaddress
import json
import logging
import random
import re
import time
from collections import OrderedDict
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any
from urllib.parse import unquote

from app.auth.principal import principal_from_scope
from app.services import remote
from app.services import remote_frames as rf
from app.services.remote_passes import MAX_PASS_BYTES, PassSlot, decode_pass

logger = logging.getLogger(__name__)

CONNECT_PATH = "/v1/connect"
PROTOCOL_HEADER = "X-Meshloom-Relay-Protocol"
SET_PASS_HEADER = "x-meshloom-set-pass"
OFFERED_PROTOCOLS = "1"

OPEN_TIMEOUT_S = 15.0
HELLO_TIMEOUT_S = 10.0
PING_INTERVAL_S = 30.0
PING_TIMEOUT_S = 60.0
CLOSE_TIMEOUT_S = 5.0
BATCH_WINDOW_S = 0.05
WS_CLOSE_LINGER_S = 5.0
TEARDOWN_GRACE_S = 5.0
RECENTLY_CLOSED_MAX = 256

BACKOFF_INITIAL_S = 1.0
BACKOFF_MAX_S = 300.0
STABLE_AFTER_S = 60.0
PAUSED_RETRY_S = (15 * 60.0, 20 * 60.0)
CLOSE_QUOTA_EXCEEDED = "quota_exceeded"
# Above the longest wait of a peer that cannot connect at once (BACKOFF_MAX_S plus the
# open and HELLO timeouts), so a connection kicked by a slow peer does not reset the count.
REPLACED_RESET_AFTER_S = 15 * 60.0
RELAY_PAUSED_RETRY_S = (5 * 60.0, 15 * 60.0)
RATE_LIMITED_MAX_S = 15 * 60.0

WANTED_LIMITS = {"max_streams": 64, "max_chunk": 262144, "initial_credit": 1048576}

# link_status values (GET /api/remote), for the settings UI (MSHL-82).
CONNECTING = "connecting"
CONNECTED = "connected"
RECONNECTING = "reconnecting"
NOT_REGISTERED = "not_registered"
PAUSED = "paused"
QUOTA_EXCEEDED = "quota_exceeded"
REPLACED = "replaced"
BANNED = "banned"
UPDATE_REQUIRED = "update_required"
RELAY_PAUSED = "relay_paused"
# Remote is on but Community is off (MSHL-445): nothing connects until it is on again.
COMMUNITY_OFF = "community_off"
LINK_STATES = (
    CONNECTING,
    CONNECTED,
    RECONNECTING,
    NOT_REGISTERED,
    PAUSED,
    QUOTA_EXCEEDED,
    REPLACED,
    BANNED,
    UPDATE_REQUIRED,
    RELAY_PAUSED,
    COMMUNITY_OFF,
)

_HOP_BY_HOP = frozenset(
    {"connection", "keep-alive", "te", "trailer", "transfer-encoding", "upgrade"}
)
# Inbound: never trusted from a relayed request (the relay strips them too).
_STRIP_IN = frozenset(
    {
        "host",
        "forwarded",
        "x-real-ip",
        "true-client-ip",
        "cdn-loop",
        "x-ingress-path",
        "x-meshloom-relay",
        "x-meshloom-pass",
        SET_PASS_HEADER,
    }
)
_STRIP_IN_PREFIXES = ("x-forwarded-", "x-remote-user-", "cf-", "proxy-", "sec-websocket-")
# Outbound: the relay owns HSTS for the whole domain; a pass never comes from the app
# (a renewal is added after this filter, by HttpStream).
_STRIP_OUT = frozenset({"strict-transport-security", "x-meshloom-pass", SET_PASS_HEADER})
_METHOD_RE = re.compile(r"[A-Z]{1,16}")
_JSON = [("content-type", "application/json")]


class RelayStreamClosed(OSError):
    """``send`` on a relayed WebSocket the browser or the relay already closed (as
    uvicorn's ``ClientDisconnected``)."""


# ---------------------------------------------------------------- pure helpers


def connect_url(state: remote.RemoteState) -> str | None:
    """The connect URL: always ``wss://<RELAY_HOST>/v1/connect`` (the contract's), never
    a host read from storage; the registered ``relay_url`` is informative only."""
    return f"wss://{remote.RELAY_HOST}{CONNECT_PATH}"


def _connection_listed(pairs: list[list[str]]) -> set[str]:
    out: set[str] = set()
    for name, value in pairs:
        if name == "connection":
            out.update(token.strip().lower() for token in value.split(",") if token.strip())
    return out


def inbound_headers(pairs: Any, host: str) -> list[tuple[bytes, bytes]] | None:
    """The ASGI headers of a relayed request: ``host`` first (the slug host), then the
    relay's pairs minus hop-by-hop, forwarding, ingress and relay headers. None when
    a pair is malformed or not Latin-1."""
    if not isinstance(pairs, list) or not all(
        isinstance(p, list) and len(p) == 2 and all(isinstance(x, str) for x in p) for p in pairs
    ):
        return None
    listed = _connection_listed(pairs)
    out = [(b"host", host.encode("ascii"))]
    for name, value in pairs:
        name = name.lower()
        if (
            name in _HOP_BY_HOP
            or name in listed
            or name in _STRIP_IN
            or name.startswith(_STRIP_IN_PREFIXES)
        ):
            continue
        try:
            out.append((name.encode("latin-1"), value.encode("latin-1")))
        except UnicodeEncodeError:
            return None
    return out


def outbound_headers(raw: Any) -> list[list[str]]:
    """``RESP_HEAD`` / ``WS_ACCEPT`` headers from ASGI ones, hop-by-hop and HSTS dropped."""
    pairs: list[list[str]] = []
    for name, value in raw or ():
        key = bytes(name).decode("latin-1").lower()
        pairs.append([key, bytes(value).decode("latin-1")])
    listed = _connection_listed(pairs)
    return [
        p
        for p in pairs
        if p[0] not in _HOP_BY_HOP
        and p[0] not in listed
        and p[0] not in _STRIP_OUT
        and not p[0].startswith("proxy-")
    ]


def _coded(code: str, message: str) -> bytes:
    return json.dumps({"detail": {"code": code, "message": message}}).encode()


_REFUSALS = {
    "remote_pass_required": (401, "A valid Meshloom Remote device pass is required"),
    "remote_host_mismatch": (421, "This request is not for this instance's Remote name"),
    "remote_request_invalid": (400, "The relay sent an invalid request head"),
}


async def build_hello() -> dict[str, Any]:
    """The instance HELLO (contract § HELLO)."""
    return {
        "protocol": rf.PROTOCOL,
        "role": "instance",
        "instance": remote.instance_info(),
        "plugins": remote.plugins_info(),
        "gate": await remote.gate_state(),
        "limits": dict(WANTED_LIMITS),
        "features": [],
    }


def parse_relay_hello(value: Any) -> dict[str, Any] | None:
    """The relay HELLO when it validates (protocol 1, role relay, limits, instance,
    server_time), else None."""
    if not isinstance(value, dict):
        return None
    instance, server_time = value.get("instance"), value.get("server_time")
    if (
        value.get("protocol") != rf.PROTOCOL
        or value.get("role") != "relay"
        or not isinstance(value.get("limits"), dict)
        or not isinstance(instance, dict)
        or not isinstance(server_time, int)
        or isinstance(server_time, bool)
    ):
        return None
    return value


def _pass_tokens(pairs: Any) -> list[str]:
    return [value for name, value in pairs or () if name.lower() == "x-meshloom-pass"]


async def check_pass(pairs: Any) -> dict[str, Any] | None:
    """The claims of the request's ``x-meshloom-pass`` when it is one valid browser
    (cookie slot) or client (header slot) pass of a known, unrevoked device."""
    tokens = _pass_tokens(pairs)
    if len(tokens) != 1:
        return None
    claims = decode_pass(tokens[0])
    slot: PassSlot | None = None
    if claims is not None and claims["kind"] == "browser":
        slot = "cookie"
    elif claims is not None and claims["kind"] == "client":
        slot = "header"
    if slot is None:
        return None
    verdict, checked = await remote.verify_device_pass(tokens[0], slot)
    return checked if verdict == "accept" and checked else None


async def relayed_scope(kind: str, head: Any) -> tuple[dict[str, Any] | None, str | None]:
    """The ASGI scope of a ``REQ_HEAD`` (``kind`` http) or ``WS_OPEN`` (websocket), or
    the refusal code."""
    from app.middleware import host_guard

    if not isinstance(head, dict):
        return None, "remote_request_invalid"
    host, path, query = head.get("host"), head.get("path"), head.get("query")
    client_ip, pairs = head.get("client_ip"), head.get("headers")
    method = head.get("method")
    if (
        not isinstance(host, str)
        or not isinstance(path, str)
        or not path.startswith("/")
        or not isinstance(query, str)
        or not isinstance(client_ip, str)
        or (kind == "http" and (not isinstance(method, str) or not _METHOD_RE.fullmatch(method)))
    ):
        return None, "remote_request_invalid"
    if host.lower() not in host_guard.remote_hostnames():
        return None, "remote_host_mismatch"
    try:
        ip = str(ipaddress.ip_address(client_ip))
        raw_path = path.encode("ascii")
        query_string = query.encode("ascii")
    except (ValueError, UnicodeEncodeError):
        return None, "remote_request_invalid"
    headers = inbound_headers(pairs, host.lower())
    if headers is None:
        return None, "remote_request_invalid"
    claims = await check_pass(pairs)
    if claims is None:
        return None, "remote_pass_required"
    scope: dict[str, Any] = {
        "type": kind,
        "asgi": {"version": "3.0", "spec_version": "2.4"},
        "http_version": "1.1",
        "path": unquote(path),
        "raw_path": raw_path,
        "query_string": query_string,
        "root_path": "",
        "headers": headers,
        "client": (ip, 0),
        "server": (host.lower(), 443),
        "state": {
            "via": "relay",
            "remote_device": {"id": claims["sub"], "uid": claims["uid"], "kind": claims["kind"]},
        },
    }
    if kind == "http":
        scope["method"] = method
        scope["scheme"] = "https"
    else:
        subprotocols = head.get("subprotocols")
        if not isinstance(subprotocols, list) or not all(isinstance(s, str) for s in subprotocols):
            return None, "remote_request_invalid"
        scope["scheme"] = "wss"
        scope["subprotocols"] = subprotocols
        scope["extensions"] = {"websocket.http.response": {}}
    remote.note_device_seen(claims["sub"])
    return scope, None


# ---------------------------------------------------------------- streams


class _Stream:
    def __init__(self, link: RelayLink, stream_id: int) -> None:
        self.link = link
        self.id = stream_id
        credit = link.limits["initial_credit"]
        self.window = credit
        self.send_credit = credit
        self.recv_window = credit
        self.unacked = 0
        self.credit_changed = asyncio.Event()
        self.aborted = False
        self.task: asyncio.Task[None] | None = None

    def on_credit(self, payload: bytes) -> bool:
        increment = rf.parse_credit(payload)
        if increment is None or self.send_credit + increment > rf.MAX_CREDIT_WINDOW:
            return False
        self.send_credit += increment
        self.credit_changed.set()
        return True

    async def take_credit(self, wanted: int) -> int:
        """Up to ``wanted`` bytes of send credit; 0 once the stream is aborted."""
        while self.send_credit <= 0:
            if self.aborted:
                return 0
            self.credit_changed.clear()
            await self.credit_changed.wait()
        if self.aborted:
            return 0
        granted = min(wanted, self.send_credit)
        self.send_credit -= granted
        return granted

    def accept_inbound(self, size: int) -> bool:
        if size > self.recv_window:
            return False
        self.recv_window -= size
        return True

    async def consumed(self, size: int) -> None:
        """The app consumed ``size`` inbound bytes: grant them back once half the
        window is used."""
        self.unacked += size
        if self.unacked * 2 >= self.window and not self.aborted:
            increment, self.unacked = self.unacked, 0
            self.recv_window += increment
            try:
                await self.link.emit(
                    rf.encode_frame(rf.CREDIT, self.id, 0, rf.credit_payload(increment))
                )
            except LinkClosed:
                self.abort()

    def abort(self) -> None:
        self.aborted = True
        self.credit_changed.set()

    async def on_frame(self, frame: rf.Frame) -> None:
        raise NotImplementedError

    async def _common(self, frame: rf.Frame) -> bool:
        """CANCEL and CREDIT; True when handled."""
        if frame.type == rf.CANCEL:
            self.abort()
            self.link.forget(self.id)
            return True
        if frame.type == rf.CREDIT:
            if not self.on_credit(frame.payload):
                await self.link.cancel(self.id, "credit_exceeded")
            return True
        return False


class HttpStream(_Stream):
    def __init__(self, link: RelayLink, stream_id: int) -> None:
        super().__init__(link, stream_id)
        self.inbound: asyncio.Queue[tuple[bytes, bool] | None] = asyncio.Queue()
        self.inbound_ended = False
        self.body_read = False
        self.status = 500
        self.headers: list[list[str]] = []
        self.head_sent = False
        self.done = asyncio.Event()
        # The accepted browser pass and the scope of the request it let through; kept
        # here, never in the scope the app sees.
        self.browser_pass: str | None = None
        self.scope: dict[str, Any] | None = None

    def abort(self) -> None:
        super().abort()
        self.inbound.put_nowait(None)
        self.done.set()

    async def on_frame(self, frame: rf.Frame) -> None:
        if await self._common(frame):
            return
        if frame.type != rf.REQ_BODY or self.inbound_ended:
            return
        if not self.accept_inbound(len(frame.payload)):
            await self.link.cancel(self.id, "credit_exceeded")
            return
        self.inbound_ended = frame.has(rf.END)
        self.inbound.put_nowait((frame.payload, self.inbound_ended))

    async def receive(self) -> dict[str, Any]:
        if not self.body_read and not self.aborted:
            item = await self.inbound.get()
            if item is not None:
                chunk, last = item
                self.body_read = last
                if chunk:
                    await self.consumed(len(chunk))
                return {"type": "http.request", "body": chunk, "more_body": not last}
        await self.done.wait()
        return {"type": "http.disconnect"}

    async def send(self, message: dict[str, Any]) -> None:
        """ASGI send; after a CANCEL or a lost link it drops silently, as uvicorn
        does once the client is gone."""
        try:
            await self._send(message)
        except LinkClosed:
            self.abort()

    async def _send(self, message: dict[str, Any]) -> None:
        if self.aborted or self.done.is_set():
            return
        kind = message.get("type")
        if kind == "http.response.start":
            self.status = int(message.get("status", 500))
            self.headers = outbound_headers(message.get("headers"))
            return
        if kind != "http.response.body":
            return
        body = bytes(message.get("body", b""))
        more = bool(message.get("more_body", False))
        if not self.head_sent:
            self.head_sent = True
            if not 200 <= self.status <= 599:
                await self.link.cancel(self.id, "internal")
                return
            final = not body and not more
            payload = rf.json_payload({"status": self.status, "headers": self.headers})
            if len(payload) > rf.MAX_HEADER_BYTES:
                await self.link.cancel(self.id, "frame_too_large")
                return
            renewed = await self._renewed_pass(len(payload))
            if self.aborted:
                return
            if renewed is not None:
                headers = [*self.headers, [SET_PASS_HEADER, renewed]]
                payload = rf.json_payload({"status": self.status, "headers": headers})
            await self.link.emit(
                rf.encode_frame(rf.RESP_HEAD, self.id, rf.END if final else 0, payload),
                urgent=final,
            )
            if final:
                self._finish()
                return
        await self._send_body(body, more)

    async def _send_body(self, body: bytes, more: bool) -> None:
        max_chunk = self.link.limits["max_chunk"]
        offset = 0
        if not body and not more:
            await self.link.emit(rf.encode_frame(rf.RESP_BODY, self.id, rf.END))
        while offset < len(body):
            size = await self.take_credit(min(max_chunk, len(body) - offset))
            if size == 0:
                return
            last = offset + size == len(body) and not more
            await self.link.emit(
                rf.encode_frame(
                    rf.RESP_BODY, self.id, rf.END if last else 0, body[offset : offset + size]
                )
            )
            offset += size
        if not more:
            self._finish()

    async def _renewed_pass(self, head_size: int) -> str | None:
        """A new pass for this answer's head, when the request shows the device in use:
        it succeeded, on the API (never a response a cache may keep) and signed in, by
        any account: a device approved for someone else belongs to its approver. Never
        fails the request."""
        token, scope = self.browser_pass, self.scope
        if (
            token is None
            or scope is None
            or not 200 <= self.status <= 299
            or not scope["path"].startswith("/api/")
            or principal_from_scope(scope) is None
            or head_size + len(SET_PASS_HEADER) + MAX_PASS_BYTES + 16 > rf.MAX_HEADER_BYTES
        ):
            return None
        try:
            return await remote.renew_browser_pass(token)
        except Exception as exc:
            logger.warning("Remote: browser pass renewal failed (%s)", type(exc).__name__)
            return None

    def _finish(self) -> None:
        self.done.set()
        self.link.forget(self.id)

    async def respond(self, status: int, body: bytes) -> None:
        self.status, self.headers = status, [list(p) for p in _JSON]
        await self.send({"type": "http.response.body", "body": body})

    async def run(self, head: Any, end: bool) -> None:
        if end:
            self.inbound_ended = True
            self.inbound.put_nowait((b"", True))
        scope, refusal = await relayed_scope("http", head)
        try:
            if scope is None:
                status, message = _REFUSALS[refusal or "remote_request_invalid"]
                await self.respond(status, _coded(refusal or "remote_request_invalid", message))
                return
            if scope["state"]["remote_device"]["kind"] == "browser":
                self.browser_pass, self.scope = _pass_tokens(head.get("headers"))[0], scope
            try:
                await self.link.app(scope, self.receive, self.send)
            except Exception:
                logger.exception("Relayed request failed")
                if not self.head_sent:
                    await self.respond(500, _coded("internal_error", "Internal Server Error"))
                    return
            if not self.done.is_set() and not self.aborted:
                # The app returned without finishing its response.
                if not self.head_sent:
                    await self.respond(500, _coded("internal_error", "Internal Server Error"))
                else:
                    await self.link.cancel(self.id, "internal")
        except LinkClosed:
            pass
        finally:
            self.abort()
            self.link.forget(self.id)


class WsStream(_Stream):
    def __init__(self, link: RelayLink, stream_id: int) -> None:
        super().__init__(link, stream_id)
        self.inbound: asyncio.Queue[tuple[dict[str, Any], int]] = asyncio.Queue()
        self.fragments: list[bytes] = []
        self.fragment_bytes = 0
        self.fragment_binary = False
        self.accepted = False
        self.denied = False
        self.local_closed = False
        self.remote_closed = False
        self.disconnected = False
        self.linger: asyncio.TimerHandle | None = None

    def _disconnect(self, code: int, reason: str = "") -> None:
        if not self.disconnected:
            self.disconnected = True
            message = {"type": "websocket.disconnect", "code": code, "reason": reason}
            self.inbound.put_nowait((message, 0))

    def abort(self) -> None:
        super().abort()
        self._disconnect(1006)
        if self.linger is not None:
            self.linger.cancel()

    async def on_frame(self, frame: rf.Frame) -> None:
        if await self._common(frame):
            return
        if frame.type == rf.WS_CLOSE:
            code, reason = rf.parse_close(frame.payload)
            self.remote_closed = True
            if not self.local_closed:
                self.local_closed = True
                await self.link.emit(
                    rf.encode_frame(rf.WS_CLOSE, self.id, 0, rf.close_payload(code, reason)),
                    urgent=True,
                )
            self._disconnect(code, reason)
            self.link.forget(self.id)
            return
        if frame.type != rf.WS_MSG or self.local_closed:
            return
        if not self.accept_inbound(len(frame.payload)):
            await self.link.cancel(self.id, "credit_exceeded")
            return
        if not self.fragments:
            self.fragment_binary = frame.has(rf.BINARY)
        self.fragments.append(frame.payload)
        self.fragment_bytes += len(frame.payload)
        if self.fragment_bytes > rf.MAX_WS_MESSAGE_BYTES:
            await self._close_both(1009, "message_too_big")
            return
        if frame.has(rf.MORE):
            return
        data, size = b"".join(self.fragments), self.fragment_bytes
        self.fragments, self.fragment_bytes = [], 0
        if self.fragment_binary:
            self.inbound.put_nowait(({"type": "websocket.receive", "bytes": data}, size))
            return
        try:
            text = data.decode("utf-8")
        except UnicodeDecodeError:
            await self._close_both(1007, "invalid_utf8")
            return
        self.inbound.put_nowait(({"type": "websocket.receive", "text": text}, size))

    async def _close_both(self, code: int, reason: str) -> None:
        self.local_closed = True
        await self.link.emit(
            rf.encode_frame(rf.WS_CLOSE, self.id, 0, rf.close_payload(code, reason)), urgent=True
        )
        self._disconnect(code, reason)
        self._start_linger()

    def _start_linger(self) -> None:
        if self.remote_closed:
            self.link.forget(self.id)
            return
        if self.linger is None:
            loop = asyncio.get_running_loop()
            self.linger = loop.call_later(WS_CLOSE_LINGER_S, self._linger_over)

    def _linger_over(self) -> None:
        self._disconnect(1000)
        self.link.forget(self.id)

    async def receive(self) -> dict[str, Any]:
        message, size = await self.inbound.get()
        if size:
            await self.consumed(size)
        if message["type"] == "websocket.disconnect":
            self.inbound.put_nowait((message, 0))  # every later receive sees it too
        return message

    async def send(self, message: dict[str, Any]) -> None:
        try:
            await self._send(message)
        except LinkClosed:
            self.abort()
            raise RelayStreamClosed("relay link closed") from None

    async def _send(self, message: dict[str, Any]) -> None:
        kind = message.get("type")
        if self.aborted:
            raise RelayStreamClosed("relay stream closed")
        if kind == "websocket.accept" and not self.accepted and not self.denied:
            self.accepted = True
            subprotocol = message.get("subprotocol")
            await self.link.emit(
                rf.encode_json(
                    rf.WS_ACCEPT,
                    self.id,
                    {
                        "status": 101,
                        "subprotocol": subprotocol if isinstance(subprotocol, str) else None,
                        "headers": outbound_headers(message.get("headers")),
                    },
                ),
                urgent=True,
            )
            return
        if kind == "websocket.send" and self.accepted:
            if self.local_closed:
                raise RelayStreamClosed("relay stream closed")
            text = message.get("text")
            data = text.encode("utf-8") if text is not None else bytes(message.get("bytes") or b"")
            await self._send_message(data, binary=text is None)
            return
        if kind == "websocket.close":
            if not self.accepted:
                if not self.denied:
                    await self.deny(403, b"")
                return
            if not self.local_closed:
                self.local_closed = True
                code = int(message.get("code") or 1000)
                reason = str(message.get("reason") or "")
                await self.link.emit(
                    rf.encode_frame(rf.WS_CLOSE, self.id, 0, rf.close_payload(code, reason)),
                    urgent=True,
                )
                self._start_linger()
            return
        if kind == "websocket.http.response.start" and not self.accepted and not self.denied:
            self.denied = True
            await self.link.emit(
                rf.encode_json(
                    rf.WS_ACCEPT,
                    self.id,
                    {
                        "status": int(message.get("status", 403)),
                        "headers": outbound_headers(message.get("headers")),
                    },
                ),
                urgent=True,
            )
            return
        if kind == "websocket.http.response.body" and self.denied:
            body = bytes(message.get("body", b""))
            more = bool(message.get("more_body", False))
            await self._send_chunks(rf.RESP_BODY, body, final_flags=0 if more else rf.END)
            if not more:
                self.link.forget(self.id)

    async def _send_message(self, data: bytes, *, binary: bool) -> None:
        flags = rf.BINARY if binary else 0
        await self._send_chunks(rf.WS_MSG, data, final_flags=flags, more_flag=rf.MORE | flags)

    async def _send_chunks(
        self, frame_type: int, data: bytes, *, final_flags: int, more_flag: int = 0
    ) -> None:
        max_chunk = self.link.limits["max_chunk"]
        if not data:
            await self.link.emit(rf.encode_frame(frame_type, self.id, final_flags))
            return
        offset = 0
        while offset < len(data):
            size = await self.take_credit(min(max_chunk, len(data) - offset))
            if size == 0:
                raise RelayStreamClosed("relay stream closed")
            last = offset + size == len(data)
            await self.link.emit(
                rf.encode_frame(
                    frame_type,
                    self.id,
                    final_flags if last else more_flag,
                    data[offset : offset + size],
                )
            )
            offset += size

    async def deny(self, status: int, body: bytes) -> None:
        self.denied = True
        headers = [list(p) for p in _JSON] if body else []
        await self.link.emit(
            rf.encode_json(
                rf.WS_ACCEPT,
                self.id,
                {"status": status, "headers": headers},
                flags=0 if body else rf.END,
            ),
            urgent=True,
        )
        if body:
            await self._send_chunks(rf.RESP_BODY, body, final_flags=rf.END)
        self.link.forget(self.id)

    async def run(self, head: Any) -> None:
        scope, refusal = await relayed_scope("websocket", head)
        failed = False
        try:
            if scope is None:
                status, message = _REFUSALS[refusal or "remote_request_invalid"]
                await self.deny(status, _coded(refusal or "remote_request_invalid", message))
                return
            self.inbound.put_nowait(({"type": "websocket.connect"}, 0))
            try:
                await self.link.app(scope, self.receive, self.send)
            except RelayStreamClosed:
                pass
            except Exception:
                failed = True
                logger.exception("Relayed WebSocket failed")
            if self.aborted:
                return
            if not self.accepted and not self.denied:
                await self.deny(500 if failed else 403, b"")
            elif self.accepted and not self.local_closed:
                self.local_closed = True
                await self.link.emit(
                    rf.encode_frame(
                        rf.WS_CLOSE, self.id, 0, rf.close_payload(1011 if failed else 1000)
                    ),
                    urgent=True,
                )
                self._start_linger()
        except (LinkClosed, RelayStreamClosed):
            pass
        finally:
            if self.aborted or self.denied:
                self.link.forget(self.id)


# ---------------------------------------------------------------- one connection


class LinkClosed(Exception):
    """The relay connection is gone; the stream ends."""


class RelayLink:
    """One relay connection: HELLO, the stream table, batching, CONTROL ops."""

    def __init__(self, ws: Any, app: Any) -> None:
        self.ws = ws
        self.app = app
        self.limits = rf.effective_limits(None)
        self.streams: dict[int, _Stream] = {}
        self.recent: OrderedDict[int, None] = OrderedDict()
        self.tasks: set[asyncio.Task[Any]] = set()
        self.counters = {"unknown_type": 0, "unknown_stream": 0, "even_stream": 0, "text": 0}
        self.hello_ok = False
        self.connected_at: float | None = None
        self.closed = False
        self._out: list[bytes] = []
        self._out_size = 0
        self._out_lock = asyncio.Lock()
        self._timer: asyncio.TimerHandle | None = None

    def connected_for(self) -> float:
        return time.monotonic() - self.connected_at if self.connected_at else 0.0

    def _spawn(self, coro: Any) -> asyncio.Task[Any]:
        task = asyncio.get_running_loop().create_task(coro)
        self.tasks.add(task)
        task.add_done_callback(self.tasks.discard)
        return task

    # ---- outbound

    async def emit(self, frame: bytes, *, urgent: bool = False) -> None:
        """Queue one frame for the next batch; ``urgent`` flushes at once."""
        if self.closed:
            raise LinkClosed
        async with self._out_lock:
            if self._out and self._out_size + len(frame) > rf.MAX_MESSAGE_BYTES:
                await self._flush_locked()
            self._out.append(frame)
            self._out_size += len(frame)
            if urgent:
                await self._flush_locked()
            elif self._timer is None:
                self._timer = asyncio.get_running_loop().call_later(BATCH_WINDOW_S, self._on_timer)

    def _on_timer(self) -> None:
        self._timer = None
        if not self.closed:
            self._spawn(self._flush())

    async def _flush(self) -> None:
        async with self._out_lock:
            await self._flush_locked()

    async def _flush_locked(self) -> None:
        if self._timer is not None:
            self._timer.cancel()
            self._timer = None
        if not self._out:
            return
        data = b"".join(self._out)
        self._out, self._out_size = [], 0
        try:
            await self.ws.send(data)
        except Exception as exc:
            self.closed = True
            raise LinkClosed from exc

    async def send_control(self, frame: dict[str, Any]) -> bool:
        """The CONTROL sender handed to ``remote.set_relay_link``."""
        try:
            await self.emit(rf.encode_json(rf.CONTROL, 0, frame))
        except LinkClosed:
            return False
        return True

    async def cancel(self, stream_id: int, reason: str) -> None:
        stream = self.streams.get(stream_id)
        if stream is not None:
            stream.abort()
        self.forget(stream_id)
        await self.emit(rf.encode_frame(rf.CANCEL, stream_id, 0, reason.encode()), urgent=True)

    def forget(self, stream_id: int) -> None:
        if self.streams.pop(stream_id, None) is not None:
            self.recent[stream_id] = None
            while len(self.recent) > RECENTLY_CLOSED_MAX:
                self.recent.popitem(last=False)

    # ---- inbound

    async def run(self) -> None:
        await self.ws.send(rf.encode_json(rf.HELLO, 0, await build_hello()))
        try:
            first = await asyncio.wait_for(self.ws.recv(), HELLO_TIMEOUT_S)
        except TimeoutError:
            logger.warning("Remote relay: no HELLO from the relay")
            await self.ws.close(1002, "hello_timeout")
            return
        rest = await self._hello(first)
        if rest is None:
            logger.warning("Remote relay: invalid HELLO from the relay")
            await self.ws.close(1002, "hello_invalid")
            return
        self.hello_ok = True
        self.connected_at = time.monotonic()
        remote.set_relay_link(self.send_control, CONNECTED)
        logger.info("Remote relay connected")
        try:
            for frame in rest:
                await self._on_frame(frame)
            async for message in self.ws:
                if isinstance(message, str):
                    self.counters["text"] += 1
                    continue
                try:
                    decoded = rf.decode_message(message, max_chunk=self.limits["max_chunk"])
                except rf.FrameError as exc:
                    logger.warning("Remote relay: protocol violation (%s)", exc.reason)
                    await self.ws.close(1002, exc.reason)
                    return
                self.counters["unknown_type"] += decoded.unknown_types
                for stream_id in decoded.too_large:
                    await self.cancel(stream_id, "frame_too_large")
                for frame in decoded.frames:
                    await self._on_frame(frame)
        except LinkClosed:
            pass
        finally:
            remote.set_relay_link(None)
            self.closed = True
            self._teardown()

    async def _hello(self, message: Any) -> list[rf.Frame] | None:
        """The relay HELLO: effective limits, the instance's name. Returns the frames
        after it, or None when it does not validate."""
        if not isinstance(message, bytes):
            return None
        try:
            decoded = rf.decode_message(message)
        except rf.FrameError:
            return None
        if not decoded.frames:
            return None
        first, rest = decoded.frames[0], decoded.frames[1:]
        if first.type != rf.HELLO or first.stream_id != 0:
            return None
        hello = parse_relay_hello(first.json())
        if hello is None:
            return None
        self.limits = rf.effective_limits(hello["limits"])
        instance = hello["instance"]
        if "slug" in instance or "hostname" in instance:
            await remote.handle_control(
                {"op": "status", "slug": instance.get("slug"), "hostname": instance.get("hostname")}
            )
        return rest

    async def _on_frame(self, frame: rf.Frame) -> None:
        stream_id = frame.stream_id
        if stream_id == 0:
            if frame.type == rf.CONTROL:
                value = frame.json()
                if isinstance(value, dict) and isinstance(value.get("op"), str):
                    self._spawn(self._control(value))
            return
        if stream_id % 2 == 0:
            self.counters["even_stream"] += 1
            return
        stream = self.streams.get(stream_id)
        if frame.type in (rf.REQ_HEAD, rf.WS_OPEN):
            if stream is not None or stream_id in self.recent:
                return
            if len(self.streams) >= self.limits["max_streams"]:
                await self.cancel(stream_id, "internal")
                return
            if frame.type == rf.REQ_HEAD:
                http = HttpStream(self, stream_id)
                self.streams[stream_id] = http
                http.task = self._spawn(http.run(frame.json(), frame.has(rf.END)))
            else:
                ws = WsStream(self, stream_id)
                self.streams[stream_id] = ws
                ws.task = self._spawn(ws.run(frame.json()))
            return
        if stream is None:
            if stream_id not in self.recent:
                self.counters["unknown_stream"] += 1
            return
        await stream.on_frame(frame)

    async def _control(self, frame: dict[str, Any]) -> None:
        try:
            reply = await remote.handle_control(frame)
        except Exception:
            logger.exception("Remote relay: CONTROL %s failed", frame.get("op"))
            return
        if reply is not None:
            await self.send_control(reply)

    def _teardown(self) -> None:
        """Every open stream ends at once; the app tasks get a short grace."""
        if self._timer is not None:
            self._timer.cancel()
            self._timer = None
        for stream in list(self.streams.values()):
            stream.abort()
        self.streams.clear()
        tasks = [t for t in self.tasks if not t.done()]
        if tasks:
            from app.background_tasks import spawn

            spawn(_reap(tasks), name="remote-relay-teardown")


async def _reap(tasks: list[asyncio.Task[Any]]) -> None:
    _, pending = await asyncio.wait(tasks, timeout=TEARDOWN_GRACE_S)
    for task in pending:
        task.cancel()


# ---------------------------------------------------------------- the client loop


@dataclass
class _Outcome:
    code: int | None = None
    reason: str = ""
    http_status: int | None = None
    retry_after: float | None = None
    hello_ok: bool = False
    connected_s: float = 0.0


def _retry_after(headers: Any) -> float | None:
    try:
        value = headers.get("Retry-After") if headers is not None else None
        return float(value) if value is not None and str(value).isdigit() else None
    except Exception:
        return None


class RelayClient:
    """The process-wide relay connection loop (one socket at most)."""

    def __init__(self) -> None:
        self.app: Any = None
        self.connect_url: Callable[[remote.RemoteState], str | None] = connect_url
        self._task: asyncio.Task[None] | None = None
        # Serializes restart()/stop(): held across stopping the old loop, reading the
        # state and creating the new task, so two overlapping restarts (two quick
        # Remote changes; remote._notify runs outside remote's lock) never leave an
        # orphaned loop holding a second socket.
        self._lock = asyncio.Lock()
        self._own_change = False
        self.link: RelayLink | None = None

    async def start(self, app: Any) -> None:
        self.app = app
        remote.add_change_listener(self._on_change)
        await self.restart()

    async def _on_change(self) -> None:
        if not self._own_change:
            await self.restart()

    async def restart(self) -> None:
        """Stop the current loop, then start one when Remote is on and registered."""
        async with self._lock:
            await self._stop_locked()
            self._start_locked_if_wanted(
                await remote.RemoteRepository.get_state(), await remote.community_on()
            )

    def _start_locked_if_wanted(self, state: remote.RemoteState, community_on: bool) -> None:
        if self.app is None:
            return
        if not state.enabled:
            remote.set_link_status(None)
            return
        if not community_on:
            remote.set_link_status(COMMUNITY_OFF)
            return
        if not state.slug:
            remote.set_link_status(NOT_REGISTERED)
            return
        remote.set_link_status(CONNECTING)
        self._task = asyncio.get_running_loop().create_task(self._run(), name="remote-relay")

    async def stop(self) -> None:
        async with self._lock:
            await self._stop_locked()

    async def _stop_locked(self) -> None:
        task, self._task = self._task, None
        if task is not None and not task.done():
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                current = asyncio.current_task()
                if current is not None and current.cancelling():
                    raise
            except Exception:
                logger.exception("Remote relay loop failed")
        remote.set_relay_link(None)

    def running(self) -> bool:
        return self._task is not None and not self._task.done()

    async def _connect_once(self, url: str) -> _Outcome:
        import websockets
        from websockets.exceptions import InvalidStatus

        from app.services.instance_identity import mint_remote_jwt
        from app.version_info import get_app_build_info

        out = _Outcome()
        token = await mint_remote_jwt()
        headers = {
            "Authorization": f"Bearer {token}",
            PROTOCOL_HEADER: OFFERED_PROTOCOLS,
            "User-Agent": f"Meshloom/{get_app_build_info().version}",
        }
        ws_ref: Any = None
        link: RelayLink | None = None
        try:
            async with websockets.connect(
                url,
                additional_headers=headers,
                user_agent_header=None,
                proxy=None,
                compression=None,
                open_timeout=OPEN_TIMEOUT_S,
                ping_interval=PING_INTERVAL_S,
                ping_timeout=PING_TIMEOUT_S,
                close_timeout=CLOSE_TIMEOUT_S,
                max_size=rf.MAX_MESSAGE_BYTES,
            ) as ws:
                ws_ref = ws
                link = RelayLink(ws, self.app)
                self.link = link
                await link.run()
        except InvalidStatus as exc:
            out.http_status = exc.response.status_code
            out.retry_after = _retry_after(exc.response.headers)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            if ws_ref is None:
                logger.info("Remote relay: connect failed (%s)", type(exc).__name__)
            elif not _is_closed(exc):
                logger.exception("Remote relay connection failed")
        finally:
            self.link = None
        if ws_ref is not None:
            out.code = getattr(ws_ref, "close_code", None)
            out.reason = getattr(ws_ref, "close_reason", None) or ""
        if link is not None:
            out.hello_ok = link.hello_ok
            out.connected_s = link.connected_for()
        return out

    async def _register_again(self) -> bool:
        from app.auth.audit import SYSTEM_ACTOR

        self._own_change = True
        try:
            await remote.register(SYSTEM_ACTOR)
        except remote.RemoteError:
            return False
        finally:
            self._own_change = False
        return True

    async def _run(self) -> None:
        delay = BACKOFF_INITIAL_S
        token_refusals = 0
        registered_again = False
        replaced = 0
        while True:
            state = await remote.RemoteRepository.get_state()
            if not state.enabled:
                remote.set_link_status(None)
                return
            if not await remote.community_on():
                remote.set_link_status(COMMUNITY_OFF)
                return
            if not state.slug:
                remote.set_link_status(NOT_REGISTERED)
                return
            url = self.connect_url(state)
            if url is None:
                remote.set_link_status(NOT_REGISTERED)
                return
            outcome = await self._connect_once(url)
            if outcome.hello_ok:
                token_refusals = 0
                registered_again = False
            if outcome.connected_s >= STABLE_AFTER_S:
                delay = BACKOFF_INITIAL_S
            # Not hello_ok: in a replace fight each install gets its HELLO, then is kicked
            # when the other one is back, seconds later or one backoff later.
            if outcome.connected_s >= REPLACED_RESET_AFTER_S:
                replaced = 0
            code = outcome.code if outcome.http_status is None else None
            if code is not None and code != 1000:
                logger.info("Remote relay closed (%s %s)", code, outcome.reason[:64])
            if code == 4001:
                token_refusals += 1
                if token_refusals == 1:
                    remote.set_link_status(RECONNECTING)
                    continue
            elif code == 4003:
                if registered_again or not await self._register_again():
                    remote.set_link_status(NOT_REGISTERED)
                    return
                registered_again = True
                remote.set_link_status(RECONNECTING)
                continue
            elif code == 4004:
                # The relay's reason is the source of truth, at connect and on a live
                # socket: no registry lookup, so nothing here can fail or guess.
                remote.set_link_status(
                    QUOTA_EXCEEDED if outcome.reason == CLOSE_QUOTA_EXCEEDED else PAUSED
                )
                await asyncio.sleep(random.uniform(*PAUSED_RETRY_S))
                continue
            elif code == 4005:
                replaced += 1
                if replaced >= 2:
                    remote.set_link_status(REPLACED)
                    return
                remote.set_link_status(RECONNECTING)
                continue
            elif code == 4009:
                remote.set_link_status(BANNED)
                return
            elif code == 4010:
                remote.set_link_status(UPDATE_REQUIRED)
                return
            elif code == 1013 or (code is not None and 4000 <= code <= 4999):
                remote.set_link_status(RELAY_PAUSED)
                await asyncio.sleep(random.uniform(*RELAY_PAUSED_RETRY_S))
                continue
            elif code in (1002, 1009):
                logger.warning("Remote relay: protocol error (%s %s)", code, outcome.reason[:64])
            remote.set_link_status(RECONNECTING)
            wait = random.uniform(0, delay)
            if outcome.http_status == 429 and outcome.retry_after is not None:
                wait = max(wait, min(outcome.retry_after, RATE_LIMITED_MAX_S))
            await asyncio.sleep(wait)
            delay = min(BACKOFF_MAX_S, delay * 2)


def _is_closed(exc: BaseException) -> bool:
    from websockets.exceptions import ConnectionClosed

    return isinstance(exc, (ConnectionClosed, LinkClosed, OSError, TimeoutError))


_client = RelayClient()


def get_relay_client() -> RelayClient:
    return _client


async def start(app: Any) -> None:
    """At startup (lifespan): listen to Remote changes and connect when on."""
    await _client.start(app)


async def shutdown() -> None:
    await _client.stop()
