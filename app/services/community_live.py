"""One Meshloom Stats live-packet socket per process, fanned out locally.

Browser clients never call Stats. This relay opens
``wss://{API}/v1/live/packets`` only when community is opted in and at least
one Live tab/session is present. JWT remint happens on Relancer or a new
Live subscribe (page refresh / reopen), never from the reader loop.
"""

from __future__ import annotations

import asyncio
import inspect
import json
import logging
import re
import time
import uuid
from typing import Any

logger = logging.getLogger(__name__)

LIVE_PATH = "/v1/live/packets"
FANOUT_QUEUE_MAX = 32
SESSION_TTL_S = 90.0
IDLE_CLOSE_GRACE_S = 1.5

CLOSE_JWT_EXPIRED = 4001
CLOSE_INACTIVE = 4002
CLOSE_SLOT_BUSY = 4003
CLOSE_RATE_LIMIT = 4004
LIVE_CLOSE_CODES = frozenset({CLOSE_JWT_EXPIRED, CLOSE_INACTIVE, CLOSE_SLOT_BUSY, CLOSE_RATE_LIMIT})
HANDSHAKE_TO_CLOSE = {
    401: CLOSE_JWT_EXPIRED,
    403: CLOSE_INACTIVE,
    409: CLOSE_SLOT_BUSY,
    429: CLOSE_RATE_LIMIT,
    503: CLOSE_RATE_LIMIT,
}

PACKET_TYPES = frozenset({"advert", "text", "ack", "trace", "other"})
_HASH8_RE = re.compile(r"^[0-9a-f]{8}$")
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
    if code in LIVE_CLOSE_CODES:
        return code
    return HANDSHAKE_TO_CLOSE.get(http_status) if http_status is not None else None


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


def _sanitize_hop(hop: object) -> dict[str, Any] | None:
    if not isinstance(hop, dict):
        return None
    token = hop.get("token")
    if not isinstance(token, str) or not token:
        return None
    cleaned: dict[str, Any] = {"token": token}
    if hop.get("unresolved") is True:
        cleaned["unresolved"] = True
        return cleaned
    lat, lon = hop.get("lat"), hop.get("lon")
    if (
        isinstance(lat, (int, float))
        and isinstance(lon, (int, float))
        and not isinstance(lat, bool)
        and not isinstance(lon, bool)
    ):
        cleaned["lat"] = float(lat)
        cleaned["lon"] = float(lon)
    else:
        cleaned["unresolved"] = True
    return cleaned


def sanitize_community_packet(raw: object) -> dict[str, Any] | None:
    """Keep contract fields only. Drop unknown ``v``, raw/hex, and observer identity."""
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
    if payload.get("v") != 1:
        return None
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
    packet_type = payload.get("type")
    if packet_type not in PACKET_TYPES:
        return None
    path = payload.get("path")
    if not isinstance(path, list) or not all(isinstance(item, str) for item in path):
        return None
    hops_in = payload.get("hops")
    if not isinstance(hops_in, list):
        return None
    hops: list[dict[str, Any]] = []
    for hop in hops_in:
        cleaned = _sanitize_hop(hop)
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
    out: dict[str, Any] = {
        "v": 1,
        "event_id": event_id,
        "hash8": hash8,
        "type": packet_type,
        "path": list(path),
        "hop_count": hop_count,
        "hops": hops,
        "iata": iata,
        "t": timestamp,
        "ear_id": ear_id,
    }
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
        self._generation = 0

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

    def _prune(self, now: float) -> None:
        expired = [sid for sid, seen in self._sessions.items() if now - seen > SESSION_TTL_S]
        for sid in expired:
            del self._sessions[sid]

    def _has_consumers(self) -> bool:
        self._prune(time.monotonic())
        return bool(self._sessions)

    def snapshot(self, *, session_id: str | None = None, opted_out: bool) -> dict[str, Any]:
        return {
            "session_id": session_id,
            "close_code": self._close_code,
            "opted_out": opted_out,
            "connected": self._connected,
        }

    async def subscribe(self, session_id: str | None = None) -> dict[str, Any]:
        """Register a Live session. Heartbeat (known id) never remints."""
        now = time.monotonic()
        should_open = False
        async with self._lock:
            self._prune(now)
            if session_id and session_id in self._sessions:
                self._sessions[session_id] = now
                sid = session_id
            else:
                sid = uuid.uuid4().hex
                self._sessions[sid] = now
                should_open = True
            if self._idle_close_task is not None:
                self._idle_close_task.cancel()
                self._idle_close_task = None
        from app.services.meshloom_community import community_enabled

        enabled = await community_enabled()
        if should_open:
            await self._maybe_open(enabled)
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
        """Mint a new API JWT and reconnect. Explicit only — no reader retry."""
        from app.services.meshloom_community import community_enabled

        enabled = await community_enabled()
        await self.close_stats()
        self._close_code = None
        if enabled and self._has_consumers():
            await self._start_reader()
        else:
            await self._broadcast_status(opted_out=not enabled)
        return self.snapshot(opted_out=not enabled)

    async def sync_community(self, enabled: bool) -> None:
        if not enabled:
            await self.close_stats()
            await self._broadcast_status(opted_out=True)
            return
        if self._has_consumers():
            await self._maybe_open(True)
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
        ws = self._ws
        self._ws = None
        self._connected = False
        self._generation += 1
        if ws is not None:
            try:
                await ws.close()
            except Exception:
                logger.debug("Stats live socket close failed", exc_info=True)
        task = self._reader_task
        self._reader_task = None
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

    async def _maybe_open(self, enabled: bool) -> None:
        if not enabled:
            await self.close_stats()
            await self._broadcast_status(opted_out=True)
            return
        if self._reader_task is not None and not self._reader_task.done():
            return
        if not self._has_consumers():
            return
        await self._start_reader()

    async def _start_reader(self) -> None:
        await self._ensure_fanout()
        if self._reader_task is not None and not self._reader_task.done():
            return
        self._generation += 1
        generation = self._generation
        self._reader_task = asyncio.create_task(self._run(generation))

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

        await ws_manager.broadcast(
            "community_live",
            {
                "close_code": self._close_code,
                "opted_out": opted_out,
                "connected": self._connected,
            },
        )

    async def _connect(self, url: str, token: str) -> Any:
        import websockets

        headers = {"Authorization": f"Bearer {token}"}
        return websockets.connect(url, **_connect_header_kwargs(headers))

    async def _run(self, generation: int) -> None:
        from app.services.meshloom_community import get_community_effective, mint_stats_jwt

        opted_out = False
        try:
            state = await get_community_effective()
            if not state.enabled:
                opted_out = True
                return
            url = stats_live_ws_url(state.api_base)
            if not url:
                logger.warning("Community live skipped: API base is empty")
                return
            token = mint_stats_jwt(audience=state.api_audience, iata="", require_iata=False)
            logger.info("Opening Stats live socket")
            async with await self._connect(url, token) as ws:
                if generation != self._generation:
                    return
                self._ws = ws
                self._connected = True
                self._close_code = None
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
            self._close_code = map_stats_close(
                code=_ws_close_code(exc), http_status=_http_status(exc)
            )
            logger.info("Stats live socket ended (close_code=%s)", self._close_code)
        finally:
            if generation == self._generation:
                self._connected = False
                self._ws = None
                self._reader_task = None
                try:
                    await self._broadcast_status(opted_out=opted_out)
                except Exception:
                    logger.debug("community_live status fan-out failed", exc_info=True)


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
