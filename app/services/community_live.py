"""One Meshloom Stats live-packet socket per process, fanned out locally.

Browser clients never call Stats. This relay opens
``wss://{API}/v1/live/packets`` only when community is opted in, a local
IATA is set, and at least one Live session is present.

Upstream lifetime is independent of any one browser session: a page reload
that cannot run React cleanup must not close or reopen the Stats socket.
The reader loop reconnects itself (capped exponential backoff) on every
close except reserved 4002 (unused; not a product 24h sesame). JWT remint
is local and happens on each connect attempt. The live JWT includes the
local IATA when set; Stats still treats the claim as optional.
``X-Live-Instance`` identifies this process so a v2 Stats server can treat
our reconnect as a silent same-relay takeover.
"""

from __future__ import annotations

import asyncio
import inspect
import json
import logging
import re
import time
import uuid
from typing import Any, Literal

logger = logging.getLogger(__name__)

LIVE_PATH = "/v1/live/packets"
FANOUT_QUEUE_MAX = 32
SESSION_TTL_S = 90.0
IDLE_CLOSE_GRACE_S = 1.5
RECONNECT_INITIAL_S = 0.5
RECONNECT_MAX_S = 30.0
RECONNECT_FACTOR = 2.0

CLOSE_JWT_EXPIRED = 4001
CLOSE_INACTIVE = 4002
CLOSE_SLOT_BUSY = 4003
CLOSE_RATE_LIMIT = 4004
CLOSE_SUPERSEDED = 4005
LIVE_CLOSE_CODES = frozenset(
    {CLOSE_JWT_EXPIRED, CLOSE_INACTIVE, CLOSE_SLOT_BUSY, CLOSE_RATE_LIMIT, CLOSE_SUPERSEDED}
)
USER_CLOSE_CODES = frozenset({CLOSE_JWT_EXPIRED, CLOSE_INACTIVE})
HANDSHAKE_TO_CLOSE = {
    401: CLOSE_JWT_EXPIRED,
    403: CLOSE_INACTIVE,
    409: CLOSE_SUPERSEDED,
    429: CLOSE_RATE_LIMIT,
    503: CLOSE_RATE_LIMIT,
}

# Known MeshCore live tokens. Sanitize accepts any [a-z][a-z0-9_]* token so a
# future Stats type still rains instead of blanking the frame.
KNOWN_PACKET_TYPES = frozenset(
    {
        "req",
        "response",
        "text",
        "ack",
        "advert",
        "grp_txt",
        "grp_data",
        "anon_req",
        "path",
        "trace",
        "multipart",
        "control",
        "raw_custom",
        "other",
    }
)
PACKET_TYPES = KNOWN_PACKET_TYPES
_PACKET_TYPE_RE = re.compile(r"^[a-z][a-z0-9_]*$")
HOP_CONFIDENCES = frozenset({"exact", "probable", "unresolved"})
EAR_SOURCES = frozenset({"advert", "iata"})
LiveState = Literal["connected", "reconnecting", "gate", "opted_out", "idle"]
_HASH8_RE = re.compile(r"^[0-9a-f]{8}$")
_PACKET_HASH_RE = re.compile(r"^[0-9a-f]{16}$")
_HEX_RE = re.compile(r"^[0-9a-fA-F]+$")
_IATA_RE = re.compile(r"^[A-Z]{3}$")


def stats_live_ws_url(api_base: str) -> str:
    base = (api_base or "").strip().rstrip("/")
    if base.startswith("https://"):
        return f"wss://{base[len('https://') :]}{LIVE_PATH}"
    if base.startswith("http://"):
        return f"ws://{base[len('http://') :]}{LIVE_PATH}"
    return f"wss://{base}{LIVE_PATH}" if base else ""


def map_stats_close(*, code: int | None = None, http_status: int | None = None) -> int | None:
    mapped = code if code in LIVE_CLOSE_CODES else None
    if mapped is None and http_status is not None:
        mapped = HANDSHAKE_TO_CLOSE.get(http_status)
    if mapped == CLOSE_SLOT_BUSY:
        return CLOSE_SUPERSEDED
    return mapped


def user_visible_close_code(code: int | None) -> int | None:
    """4003/4005/4004 are never a user-facing error. 4001 is discreet status."""
    return code if code in USER_CLOSE_CODES else None


def _ws_close_code(exc: BaseException) -> int | None:
    code = getattr(exc, "code", None)
    if isinstance(code, int):
        return code
    rcvd = getattr(exc, "rcvd", None)
    if rcvd is not None:
        nested = getattr(rcvd, "code", None)
        if isinstance(nested, int):
            return nested
    return None


def _http_status(exc: BaseException) -> int | None:
    status = getattr(exc, "status_code", None)
    if isinstance(status, int):
        return status
    response = getattr(exc, "response", None)
    if response is not None:
        nested = getattr(response, "status_code", None)
        if isinstance(nested, int):
            return nested
    return None


def _finite_coord(value: object) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value)


def _optional_text(value: object) -> str | None:
    if isinstance(value, str) and value:
        return value
    return None


def _sanitize_hop(hop: object, *, allow_v1: bool) -> dict[str, Any] | None:
    if not isinstance(hop, dict):
        return None
    token = hop.get("token")
    if not isinstance(token, str) or not token:
        return None
    confidence = hop.get("confidence")
    if confidence in HOP_CONFIDENCES:
        reason = _optional_text(hop.get("reason"))
        lat = _finite_coord(hop.get("lat"))
        lon = _finite_coord(hop.get("lon"))
        cleaned: dict[str, Any] = {"token": token, "confidence": confidence}
        if confidence == "unresolved":
            if lat is not None or lon is not None or reason is None:
                return None
            cleaned["reason"] = reason
            return cleaned
        if lat is None or lon is None:
            return None
        if confidence == "probable" and reason is None:
            return None
        cleaned["lat"] = lat
        cleaned["lon"] = lon
        if reason is not None:
            cleaned["reason"] = reason
        if confidence == "exact":
            pubkey = _optional_text(hop.get("pubkey"))
            name = _optional_text(hop.get("name"))
            if pubkey is not None:
                cleaned["pubkey"] = pubkey
            if name is not None:
                cleaned["name"] = name
        return cleaned
    if not allow_v1:
        return None
    if hop.get("unresolved") is True:
        return {"token": token, "confidence": "unresolved", "reason": "unresolved"}
    lat = _finite_coord(hop.get("lat"))
    lon = _finite_coord(hop.get("lon"))
    if lat is None or lon is None:
        return {"token": token, "confidence": "unresolved", "reason": "unresolved"}
    return {"token": token, "confidence": "exact", "lat": lat, "lon": lon}


_PUBKEY_RE = re.compile(r"^[0-9a-fA-F]{64}$")


def _sanitize_origin(raw: object) -> dict[str, Any] | None:
    """Advertiser hop. Bad shape is dropped; the rest of the frame still rains."""
    if raw is None:
        return None
    cleaned = _sanitize_hop(raw, allow_v1=False)
    if cleaned is None or not isinstance(raw, dict):
        return None
    pubkey = _optional_text(raw.get("pubkey"))
    if pubkey is None or not _PUBKEY_RE.fullmatch(pubkey):
        if cleaned.get("confidence") == "unresolved":
            return None
        return cleaned
    cleaned["pubkey"] = pubkey.lower()
    return cleaned


def _sanitize_ear(raw: object) -> tuple[bool, dict[str, Any] | None]:
    if raw is None:
        return True, None
    if not isinstance(raw, dict):
        return False, None
    lat = _finite_coord(raw.get("lat"))
    lon = _finite_coord(raw.get("lon"))
    source = raw.get("source")
    if lat is None or lon is None or source not in EAR_SOURCES:
        return False, None
    return True, {"lat": lat, "lon": lon, "source": source}


def sanitize_community_packet(raw: object) -> dict[str, Any] | None:
    """Keep contract fields only. Drop unknown ``v``, raw/hex, and observer identity.

    v2 is the native shape. v1 frames are accepted when they can be normalized
    without guessing (``unresolved: true`` becomes ``confidence: unresolved``).
    ``packet_hash`` (16 hex) is relayed when present and consistent with ``hash8``.
    Absent ``packet_hash`` keeps the older hash8-only accept path.
    """
    payload = raw
    if isinstance(payload, (bytes, bytearray)):
        try:
            payload = payload.decode()
        except UnicodeDecodeError:
            return None
    if isinstance(payload, str):
        try:
            payload = json.loads(payload)
        except ValueError:
            return None
    if not isinstance(payload, dict):
        return None
    version = payload.get("v")
    if version not in (1, 2):
        return None
    allow_v1 = version == 1
    event_id = payload.get("event_id")
    if not isinstance(event_id, str) or not event_id:
        return None
    hash_raw = payload.get("hash8")
    if not isinstance(hash_raw, str):
        return None
    hash8 = hash_raw.lower()
    if _HEX_RE.fullmatch(hash8) and len(hash8) >= 8:
        hash8 = hash8[:8]
    if not _HASH8_RE.fullmatch(hash8):
        return None
    packet_hash_out: str | None = None
    packet_hash_raw = payload.get("packet_hash")
    if packet_hash_raw is not None:
        if not isinstance(packet_hash_raw, str):
            return None
        packet_hash = packet_hash_raw.lower()
        if not _PACKET_HASH_RE.fullmatch(packet_hash):
            return None
        if hash8 != packet_hash[:8]:
            return None
        packet_hash_out = packet_hash
    packet_type = payload.get("type")
    if not isinstance(packet_type, str) or not _PACKET_TYPE_RE.fullmatch(packet_type):
        return None
    path = payload.get("path")
    if not isinstance(path, list) or not all(isinstance(item, str) for item in path):
        return None
    hops_in = payload.get("hops")
    if not isinstance(hops_in, list) or len(hops_in) != len(path):
        return None
    hops: list[dict[str, Any]] = []
    for hop in hops_in:
        cleaned = _sanitize_hop(hop, allow_v1=allow_v1)
        if cleaned is None:
            return None
        hops.append(cleaned)
    iata_raw = payload.get("iata")
    if not isinstance(iata_raw, str):
        return None
    iata = iata_raw.upper()
    if iata and not _IATA_RE.fullmatch(iata):
        return None
    timestamp = payload.get("t")
    if not isinstance(timestamp, int) or isinstance(timestamp, bool):
        return None
    ear_id = payload.get("ear_id")
    if not isinstance(ear_id, str) or not ear_id:
        return None
    hop_count = payload.get("hop_count")
    if not isinstance(hop_count, int) or isinstance(hop_count, bool):
        hop_count = len(path)
    if version == 2:
        ear_ok, ear = _sanitize_ear(payload.get("ear"))
        if not ear_ok:
            return None
    else:
        ear_ok, ear = _sanitize_ear(payload.get("ear")) if "ear" in payload else (True, None)
        if not ear_ok:
            return None
    origin = _sanitize_origin(payload.get("origin"))
    out: dict[str, Any] = {
        "v": 2,
        "event_id": event_id,
        "hash8": hash8,
        "type": packet_type,
        "path": list(path),
        "hop_count": hop_count,
        "hops": hops,
        "ear": ear,
        "iata": iata,
        "t": timestamp,
        "ear_id": ear_id,
    }
    if packet_hash_out is not None:
        out["packet_hash"] = packet_hash_out
    if origin is not None:
        out["origin"] = origin
    route_kind = payload.get("route_kind")
    if route_kind in ("flood", "direct", "unknown"):
        out["route_kind"] = route_kind
    snr = payload.get("snr")
    if isinstance(snr, (int, float)) and not isinstance(snr, bool):
        out["snr"] = float(snr)
    return out


def _connect_header_kwargs(headers: dict[str, str]) -> dict[str, Any]:
    try:
        import websockets
    except ImportError as exc:
        raise RuntimeError("the websockets package is required for community live") from exc
    params = inspect.signature(websockets.connect).parameters
    if "additional_headers" in params:
        return {"additional_headers": headers}
    if "extra_headers" in params:
        return {"extra_headers": headers}
    return {}


class CommunityLiveRelay:
    """Process-wide Stats live viewer. One upstream socket, bounded local fan-out."""

    def __init__(self) -> None:
        self._lock = asyncio.Lock()
        self._sessions: dict[str, float] = {}
        self._reader_task: asyncio.Task[None] | None = None
        self._fanout_task: asyncio.Task[None] | None = None
        self._idle_close_task: asyncio.Task[None] | None = None
        self._queue: asyncio.Queue[dict[str, Any] | None] = asyncio.Queue(maxsize=FANOUT_QUEUE_MAX)
        self._ws: Any = None
        self._connected = False
        self._close_code: int | None = None
        self._gate_blocked = False
        self._generation = 0
        self._instance_id = uuid.uuid4().hex

    @property
    def connected(self) -> bool:
        return self._connected

    @property
    def close_code(self) -> int | None:
        return self._close_code

    @property
    def consumer_count(self) -> int:
        self._prune(time.monotonic())
        return len(self._sessions)

    @property
    def instance_id(self) -> str:
        return self._instance_id

    def _prune(self, now: float) -> None:
        expired = [sid for sid, seen in self._sessions.items() if now - seen > SESSION_TTL_S]
        for sid in expired:
            del self._sessions[sid]

    def _has_consumers(self) -> bool:
        self._prune(time.monotonic())
        return bool(self._sessions)

    def _live_state(self, *, opted_out: bool) -> LiveState:
        if opted_out:
            return "opted_out"
        if self._gate_blocked:
            return "gate"
        if self._connected:
            return "connected"
        if self._reader_task is not None and not self._reader_task.done():
            return "reconnecting"
        if self._has_consumers():
            return "reconnecting"
        return "idle"

    def snapshot(self, *, session_id: str | None = None, opted_out: bool) -> dict[str, Any]:
        return {
            "session_id": session_id,
            "close_code": self._close_code,
            "opted_out": opted_out,
            "connected": self._connected,
            "state": self._live_state(opted_out=opted_out),
        }

    async def _can_open_live(self, enabled: bool) -> bool:
        if not enabled:
            return False
        from app.services.meshloom_community import get_community_effective

        state = await get_community_effective()
        return bool(state.iata)

    def _ensure_reader_locked(self) -> None:
        # create_task is synchronous. Any await between "we need a socket" and
        # assigning _reader_task lets a second subscribe open a second Stats
        # socket against our own pubkey slot (measured production deadlock).
        if not self._lock.locked():
            raise RuntimeError("reader claim requires the relay lock")
        if self._gate_blocked:
            return
        if self._reader_task is not None and not self._reader_task.done():
            return
        if not self._sessions:
            return
        self._generation += 1
        generation = self._generation
        self._reader_task = asyncio.create_task(self._run(generation))

    async def subscribe(self, session_id: str | None = None) -> dict[str, Any]:
        """Register a Live session. Heartbeat (known id) never remints."""
        from app.services.meshloom_community import community_enabled

        enabled = await community_enabled()
        can_open = await self._can_open_live(enabled)
        now = time.monotonic()
        async with self._lock:
            self._prune(now)
            if session_id and session_id in self._sessions:
                self._sessions[session_id] = now
                sid = session_id
            else:
                sid = uuid.uuid4().hex
                self._sessions[sid] = now
            if self._idle_close_task is not None:
                self._idle_close_task.cancel()
                self._idle_close_task = None
            if can_open:
                self._ensure_reader_locked()
        if not can_open:
            await self.close_stats()
        return self.snapshot(session_id=sid, opted_out=not enabled)

    async def unsubscribe(self, session_id: str) -> dict[str, Any]:
        async with self._lock:
            self._sessions.pop(session_id, None)
            self._prune(time.monotonic())
            empty = not self._sessions
            if empty and self._idle_close_task is None:
                self._idle_close_task = asyncio.create_task(self._idle_close())
        from app.services.meshloom_community import community_enabled

        return self.snapshot(opted_out=not await community_enabled())

    async def relancer(self) -> dict[str, Any]:
        """Mint a new API JWT and reconnect. Clears leftover 4002/_gate_blocked."""
        from app.services.meshloom_community import community_enabled

        enabled = await community_enabled()
        can_open = await self._can_open_live(enabled)
        self._gate_blocked = False
        self._close_code = None
        await self.close_stats()
        if can_open:
            async with self._lock:
                self._prune(time.monotonic())
                self._ensure_reader_locked()
        else:
            await self._broadcast_status(opted_out=not enabled)
        return self.snapshot(opted_out=not enabled)

    async def sync_community(self, enabled: bool) -> None:
        if not enabled:
            self._gate_blocked = False
            await self.close_stats()
            await self._broadcast_status(opted_out=True)
            return
        if not await self._can_open_live(True):
            await self.close_stats()
            await self._broadcast_status(opted_out=False)
            return
        async with self._lock:
            self._prune(time.monotonic())
            self._ensure_reader_locked()
        await self._broadcast_status(opted_out=False)

    async def release_all_consumers(self) -> None:
        async with self._lock:
            self._sessions.clear()
            if self._idle_close_task is not None:
                self._idle_close_task.cancel()
                self._idle_close_task = None
        await self.close_stats()

    async def shutdown(self) -> None:
        await self.release_all_consumers()
        await self._stop_fanout()

    async def close_stats(self) -> None:
        """Close the upstream Stats socket so the pubkey slot is freed."""
        async with self._lock:
            ws = self._ws
            self._ws = None
            self._connected = False
            self._generation += 1
            task = self._reader_task
            self._reader_task = None
        if ws is not None:
            try:
                await ws.close()
            except Exception:
                logger.debug("Stats live socket close failed", exc_info=True)
        if task is not None and not task.done():
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

    async def _idle_close(self) -> None:
        try:
            await asyncio.sleep(IDLE_CLOSE_GRACE_S)
        except asyncio.CancelledError:
            return
        async with self._lock:
            self._prune(time.monotonic())
            if self._sessions:
                return
            self._idle_close_task = None
        await self.close_stats()

    async def _ensure_fanout(self) -> None:
        if self._fanout_task is not None and not self._fanout_task.done():
            return
        self._fanout_task = asyncio.create_task(self._fanout_loop())

    async def _stop_fanout(self) -> None:
        task = self._fanout_task
        self._fanout_task = None
        if task is None:
            return
        try:
            self._queue.put_nowait(None)
        except asyncio.QueueFull:
            try:
                self._queue.get_nowait()
            except asyncio.QueueEmpty:
                pass
            try:
                self._queue.put_nowait(None)
            except asyncio.QueueFull:
                pass
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

    async def _fanout_loop(self) -> None:
        from app.websocket import ws_manager

        while True:
            item = await self._queue.get()
            if item is None:
                return
            try:
                await ws_manager.broadcast("community_packet", item)
            except Exception:
                logger.debug("community_packet fan-out failed", exc_info=True)

    async def _broadcast_status(self, *, opted_out: bool) -> None:
        from app.websocket import ws_manager

        await ws_manager.broadcast("community_live", self.snapshot(opted_out=opted_out))

    async def _connect(self, url: str, token: str) -> Any:
        import websockets

        headers = {
            "Authorization": f"Bearer {token}",
            "X-Live-Instance": self._instance_id,
        }
        return websockets.connect(url, **_connect_header_kwargs(headers))

    async def _run(self, generation: int) -> None:
        from app.services.meshloom_community import get_community_effective, mint_stats_jwt

        await self._ensure_fanout()
        delay = RECONNECT_INITIAL_S
        opted_out = False
        try:
            while generation == self._generation:
                if not self._has_consumers() or self._gate_blocked:
                    return
                try:
                    state = await get_community_effective()
                except Exception:
                    if generation != self._generation:
                        return
                    logger.info("Community live config probe failed; retrying")
                    await self._sleep_backoff(generation, delay)
                    delay = min(RECONNECT_MAX_S, delay * RECONNECT_FACTOR)
                    continue
                if not state.enabled:
                    opted_out = True
                    return
                if not state.iata:
                    logger.info("Community live skipped: IATA is not set")
                    return
                url = stats_live_ws_url(state.api_base)
                if not url:
                    logger.warning("Community live skipped: API base is empty")
                    return
                try:
                    token = mint_stats_jwt(
                        audience=state.api_audience, iata=state.iata, require_iata=False
                    )
                except Exception:
                    if generation != self._generation:
                        return
                    logger.info("Community live JWT mint failed; retrying")
                    await self._sleep_backoff(generation, delay)
                    delay = min(RECONNECT_MAX_S, delay * RECONNECT_FACTOR)
                    continue
                logger.info("Opening Stats live socket")
                close_code: int | None = None
                try:
                    async with await self._connect(url, token) as ws:
                        if generation != self._generation:
                            return
                        self._ws = ws
                        self._connected = True
                        self._close_code = None
                        delay = RECONNECT_INITIAL_S
                        await self._broadcast_status(opted_out=False)
                        async for message in ws:
                            if generation != self._generation:
                                return
                            packet = sanitize_community_packet(message)
                            if packet is None:
                                continue
                            try:
                                self._queue.put_nowait(packet)
                            except asyncio.QueueFull:
                                logger.debug("Dropping community live frame (fan-out full)")
                except asyncio.CancelledError:
                    raise
                except Exception as exc:
                    if generation != self._generation:
                        return
                    close_code = map_stats_close(
                        code=_ws_close_code(exc), http_status=_http_status(exc)
                    )
                    logger.info("Stats live socket ended (close_code=%s)", close_code)
                self._connected = False
                self._ws = None
                if generation != self._generation or not self._has_consumers():
                    return
                if close_code == CLOSE_INACTIVE:
                    # 4002 is reserved unused. Do not reconnect this generation.
                    # Do not set _gate_blocked: 4002 is not a product 24h sesame.
                    self._close_code = user_visible_close_code(close_code)
                    await self._broadcast_status(opted_out=False)
                    return
                self._close_code = user_visible_close_code(close_code)
                await self._broadcast_status(opted_out=False)
                sleep_for = 0.0 if close_code == CLOSE_JWT_EXPIRED else delay
                await self._sleep_backoff(generation, sleep_for)
                if close_code != CLOSE_JWT_EXPIRED:
                    delay = min(RECONNECT_MAX_S, max(RECONNECT_INITIAL_S, delay) * RECONNECT_FACTOR)
        finally:
            if generation == self._generation:
                self._connected = False
                self._ws = None
                self._reader_task = None
                try:
                    await self._broadcast_status(opted_out=opted_out)
                except Exception:
                    logger.debug("community_live status fan-out failed", exc_info=True)

    async def _sleep_backoff(self, generation: int, seconds: float) -> None:
        if seconds <= 0:
            await asyncio.sleep(0)
            return
        try:
            await asyncio.sleep(seconds)
        except asyncio.CancelledError:
            raise
        if generation != self._generation:
            return


_relay: CommunityLiveRelay | None = None


def get_live_relay() -> CommunityLiveRelay:
    global _relay
    if _relay is None:
        _relay = CommunityLiveRelay()
    return _relay


async def subscribe_live(session_id: str | None = None) -> dict[str, Any]:
    return await get_live_relay().subscribe(session_id)


async def unsubscribe_live(session_id: str) -> dict[str, Any]:
    return await get_live_relay().unsubscribe(session_id)


async def relancer_live() -> dict[str, Any]:
    return await get_live_relay().relancer()


async def sync_community_live(enabled: bool) -> None:
    await get_live_relay().sync_community(enabled)


async def release_live_on_app_ws_empty() -> None:
    await get_live_relay().release_all_consumers()


async def shutdown_community_live() -> None:
    global _relay
    if _relay is None:
        return
    await _relay.shutdown()
    _relay = None


def reset_community_live_for_tests() -> None:
    global _relay
    relay = _relay
    _relay = CommunityLiveRelay()
    if relay is None:
        return
    task = relay._reader_task
    if task is not None and not task.done():
        task.cancel()
    fanout = relay._fanout_task
    if fanout is not None and not fanout.done():
        fanout.cancel()
    idle = relay._idle_close_task
    if idle is not None and not idle.done():
        idle.cancel()
