"""One Meshloom Stats live-packet socket per process, fanned out locally.

Browser clients never call Stats. This relay opens
``wss://{API}/v1/live/packets`` only when community is opted in, a local
IATA is set, and at least one Live session is present.

Upstream lifetime is independent of any one browser session: a page reload
that cannot run React cleanup must not close or reopen the Stats socket.
The reader loop reconnects itself (capped exponential backoff) on every
close except reserved 4002 (never sent by Community). JWT remint
is local and happens on each connect attempt. A close 4001 on a socket that
stayed up remints at once; a handshake 401 never does: it backs off, and the
relay gives up after ``AUTH_REJECT_MAX_ATTEMPTS`` consecutive refusals (at
once for refusals no retry can fix) with state ``auth_rejected`` and an
``auth_error`` reason the UI can explain (``clock_skew`` / ``token_rejected``). The live JWT includes the
local IATA when set; Stats still treats the claim as optional.
``X-Live-Instance`` identifies this process so a v2 Stats server can treat
our reconnect as a silent same-relay takeover.

Region filter: each Live session says which IATA codes it shows (``None`` =
every region). The socket asks Community for the union of the sessions'
codes **plus the radio's own IATA** (``?iata=A,B``), or for the global feed
when one session wants every region or the union exceeds
``UPSTREAM_IATA_MAX``. A change of union reconnects after
``FILTER_DEBOUNCE_S``, with the same ``X-Live-Instance``. A Community that
refuses the parameter (handshake 400) gets the global feed for
``FILTER_RETRY_S``; one that ignores it sends the global feed anyway. Either
way the browser filters again, so the result is the same.

Feed paused: when the Community operator pauses the public Live
(``KILL_LIVE_PUBLIC``), open sockets are closed with 1013 and handshakes are
refused with 503 ``{"code": "live_disabled"}``. The relay then reports state
``paused`` (a discreet status, not an error) and retries every
``PAUSED_RETRY_MIN_S`` then ``PAUSED_RETRY_MAX_S`` instead of the short
backoff, until a socket opens again. Every backoff sleep carries
``±BACKOFF_JITTER`` so instances do not reconnect in step.

Banned key: a handshake 403 ``{"code": "banned"}`` (or a ban answered on an
HTTP route, ``services/community_ban.py``) stops the relay with state
``banned``. It opens nothing while the ban state holds; a restart, a settings
change or ``POST /community/live/restart`` ends it.
"""

from __future__ import annotations

import asyncio
import inspect
import json
import logging
import random
import re
import time
import uuid
from typing import Any, Literal
from urllib.parse import urlencode

from app.models import LIVE_PLACEHOLDER_IATAS as PLACEHOLDER_IATAS

logger = logging.getLogger(__name__)

LIVE_PATH = "/v1/live/packets"
FANOUT_QUEUE_MAX = 32
SESSION_TTL_S = 90.0
IDLE_CLOSE_GRACE_S = 1.5
RECONNECT_INITIAL_S = 0.5
RECONNECT_MAX_S = 30.0
RECONNECT_FACTOR = 2.0
# Every backoff sleep is spread by +/- this fraction, so the instances that
# lost the feed at the same moment do not all come back at the same moment.
BACKOFF_JITTER = 0.2
# Feed paused by the operator (1013 / 503 live_disabled): first retry after
# PAUSED_RETRY_MIN_S, then every PAUSED_RETRY_MAX_S (before jitter).
PAUSED_RETRY_MIN_S = 30.0
PAUSED_RETRY_MAX_S = 60.0
# Handshake 401: first retry after AUTH_RETRY_INITIAL_S, doubled each time;
# stop after AUTH_REJECT_MAX_ATTEMPTS consecutive refusals.
AUTH_RETRY_INITIAL_S = 2.0
AUTH_REJECT_MAX_ATTEMPTS = 5
# A socket must stay up this long to count as healthy: only then does a 4001
# remint at once and the reconnect backoff start over.
JWT_EXPIRY_MIN_UPTIME_S = 10.0
# 401 codes no retry can fix without a human (community errors.md).
AUTH_FATAL_CODES = frozenset(
    {"token_audience", "token_signature", "token_malformed", "token_missing", "token_in_query"}
)

# Community /v1/live/packets accepts at most this many codes in ?iata=.
UPSTREAM_IATA_MAX = 16
# A filter change waits this long so a burst of tab changes reconnects once.
FILTER_DEBOUNCE_S = 2.0
# After a handshake 400 on a filtered URL, stay on the global feed this long.
FILTER_RETRY_S = 3600.0
IataFilter = frozenset[str] | None

CLOSE_JWT_EXPIRED = 4001
CLOSE_INACTIVE = 4002
# close_stats(): longest wait for the upstream close handshake.
CLOSE_TIMEOUT_S = 5.0
CLOSE_SLOT_BUSY = 4003
CLOSE_RATE_LIMIT = 4004
CLOSE_SUPERSEDED = 4005
# Standard "Try Again Later": Community paused the public feed (KILL_LIVE_PUBLIC).
CLOSE_FEED_PAUSED = 1013
LIVE_CLOSE_CODES = frozenset(
    {
        CLOSE_JWT_EXPIRED,
        CLOSE_INACTIVE,
        CLOSE_SLOT_BUSY,
        CLOSE_RATE_LIMIT,
        CLOSE_SUPERSEDED,
        CLOSE_FEED_PAUSED,
    }
)
USER_CLOSE_CODES = frozenset({CLOSE_JWT_EXPIRED, CLOSE_INACTIVE})
# 401 is deliberately absent: a handshake refusal is not a 4001 (see _run).
# A 503 whose body says live_disabled is the paused feed, not a 4004 (see
# is_feed_paused). A 403 ``banned`` stops the relay (community_ban); any other
# refusal (403 included) has no close code and reconnects with backoff.
HANDSHAKE_TO_CLOSE = {
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
LiveState = Literal[
    "connected", "reconnecting", "paused", "opted_out", "idle", "auth_rejected", "banned"
]
# Handshake body code of a 503 while the operator paused the public feed.
LIVE_DISABLED_CODE = "live_disabled"
LiveAuthError = Literal["clock_skew", "token_rejected"]
_HASH8_RE = re.compile(r"^[0-9a-f]{8}$")
_PACKET_HASH_RE = re.compile(r"^[0-9a-f]{16}$")
_HEX_RE = re.compile(r"^[0-9a-fA-F]+$")
_IATA_RE = re.compile(r"^[A-Z]{3}$")


def stats_live_ws_url(api_base: str, iata: IataFilter = None) -> str:
    """Live socket URL. ``iata`` adds ``?iata=A,B`` (sorted); ``None`` is the global feed."""
    base = (api_base or "").strip().rstrip("/")
    if base.startswith("https://"):
        url = f"wss://{base[len('https://') :]}{LIVE_PATH}"
    elif base.startswith("http://"):
        url = f"ws://{base[len('http://') :]}{LIVE_PATH}"
    else:
        url = f"wss://{base}{LIVE_PATH}" if base else ""
    if url and iata:
        url = f"{url}?{urlencode({'iata': ','.join(sorted(iata))}, safe=',')}"
    return url


def normalize_iata_selection(codes: object) -> IataFilter:
    """A session's regions: ``None`` (or nothing valid) means every region."""
    if not isinstance(codes, (list, tuple, set, frozenset)):
        return None
    out: set[str] = set()
    for raw in codes:
        if not isinstance(raw, str):
            continue
        code = raw.strip().upper()
        if _IATA_RE.fullmatch(code) and code not in PLACEHOLDER_IATAS:
            out.add(code)
    return frozenset(out) or None


def upstream_iata_filter(selections: list[IataFilter], radio_iata: str) -> IataFilter:
    """Union of the sessions' regions plus the radio's IATA; ``None`` = global feed.

    The radio's IATA is always in, so what this radio's own region hears
    stays complete whatever the tabs pick. One session that wants every
    region, no session at all, or more codes than Community accepts, means
    the global feed.
    """
    if not selections or any(selection is None for selection in selections):
        return None
    union: set[str] = set()
    for selection in selections:
        union.update(selection or ())
    radio = (radio_iata or "").strip().upper()
    if _IATA_RE.fullmatch(radio) and radio not in PLACEHOLDER_IATAS:
        union.add(radio)
    if not union or len(union) > UPSTREAM_IATA_MAX:
        return None
    return frozenset(union)


def map_stats_close(*, code: int | None = None, http_status: int | None = None) -> int | None:
    mapped = code if code in LIVE_CLOSE_CODES else None
    if mapped is None and http_status is not None:
        mapped = HANDSHAKE_TO_CLOSE.get(http_status)
    if mapped == CLOSE_SLOT_BUSY:
        return CLOSE_SUPERSEDED
    return mapped


def is_feed_paused(
    *, code: int | None = None, http_status: int | None = None, body: dict[str, Any] | None = None
) -> bool:
    """1013 close, or handshake 503 ``live_disabled``: the operator paused the feed.

    A 503 with another code (``live_viewer_cap``) or no body is a full server,
    not a pause: it keeps the short backoff.
    """
    if code == CLOSE_FEED_PAUSED:
        return True
    return http_status == 503 and (body or {}).get("code") == LIVE_DISABLED_CODE


def jittered(seconds: float, *, ratio: float | None = None) -> float:
    """``seconds`` spread uniformly by +/- ``ratio`` (default ``BACKOFF_JITTER``)."""
    if seconds <= 0:
        return 0.0
    spread = BACKOFF_JITTER if ratio is None else ratio
    return seconds * random.uniform(1.0 - spread, 1.0 + spread)


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


def _handshake_body(exc: BaseException) -> dict[str, Any] | None:
    """JSON body of a refused handshake (websockets ``InvalidStatus.response.body``)."""
    response = getattr(exc, "response", None)
    body = getattr(response, "body", None)
    if not isinstance(body, (bytes, bytearray)) or not body:
        return None
    try:
        payload = json.loads(bytes(body).decode())
    except (UnicodeDecodeError, ValueError):
        return None
    return payload if isinstance(payload, dict) else None


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
        # session id -> (last heartbeat, regions shown; None = every region)
        self._sessions: dict[str, tuple[float, IataFilter]] = {}
        # Filter the open (or opening) socket asked for; None = global feed.
        self._active_filter: IataFilter = None
        self._filter_rejected_at: float | None = None
        self._filter_task: asyncio.Task[None] | None = None
        self._reader_task: asyncio.Task[None] | None = None
        self._fanout_task: asyncio.Task[None] | None = None
        self._idle_close_task: asyncio.Task[None] | None = None
        self._queue: asyncio.Queue[dict[str, Any] | None] = asyncio.Queue(maxsize=FANOUT_QUEUE_MAX)
        self._ws: Any = None
        self._connected = False
        self._close_code: int | None = None
        self._auth_error: LiveAuthError | None = None
        self._auth_code: str | None = None
        self._clock_skew_s: int | None = None
        # Community paused the public feed; cleared by the next open socket.
        self._paused = False
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

    def _prune(self, now: float) -> bool:
        """Drop sessions without a heartbeat. True when one was dropped."""
        expired = [
            sid for sid, (seen, _filter) in self._sessions.items() if now - seen > SESSION_TTL_S
        ]
        for sid in expired:
            del self._sessions[sid]
        return bool(expired)

    def _has_consumers(self) -> bool:
        # Only reached while not connected (see _live_state and _run): the
        # next connect attempt reads the pruned union itself.
        self._prune(time.monotonic())
        return bool(self._sessions)

    def _live_state(self, *, opted_out: bool) -> LiveState:
        from app.services.community_ban import community_banned

        if opted_out:
            return "opted_out"
        if community_banned():
            return "banned"
        if self._auth_error is not None:
            return "auth_rejected"
        if self._connected:
            return "connected"
        waiting: LiveState = "paused" if self._paused else "reconnecting"
        if self._reader_task is not None and not self._reader_task.done():
            return waiting
        if self._has_consumers():
            return waiting
        return "idle"

    def snapshot(self, *, session_id: str | None = None, opted_out: bool) -> dict[str, Any]:
        return {
            "session_id": session_id,
            "close_code": self._close_code,
            "opted_out": opted_out,
            "connected": self._connected,
            "state": self._live_state(opted_out=opted_out),
            "auth_error": None if opted_out else self._auth_error,
            "auth_code": None if opted_out else self._auth_code,
            "clock_skew_s": None if opted_out else self._clock_skew_s,
            "upstream_iata": (
                sorted(self._active_filter)
                if self._active_filter is not None and self._connected and not opted_out
                else None
            ),
        }

    def _desired_filter(self, radio_iata: str, *, now: float | None = None) -> IataFilter:
        if self._filter_rejected_at is not None:
            moment = time.monotonic() if now is None else now
            if moment - self._filter_rejected_at < FILTER_RETRY_S:
                return None
            self._filter_rejected_at = None
        return upstream_iata_filter([sel for _seen, sel in self._sessions.values()], radio_iata)

    def _schedule_filter_check(self) -> None:
        """Debounced: reconnect once the union of the sessions' regions settles."""
        if self._filter_task is not None and not self._filter_task.done():
            self._filter_task.cancel()
        self._filter_task = asyncio.create_task(self._apply_filter_change())

    def _cancel_filter_check(self) -> None:
        task = self._filter_task
        self._filter_task = None
        if task is not None and not task.done():
            task.cancel()

    async def _apply_filter_change(self) -> None:
        try:
            await asyncio.sleep(FILTER_DEBOUNCE_S)
        except asyncio.CancelledError:
            return
        # Past the debounce this task is no longer cancellable by a newer
        # change (a cancel inside close_stats() could orphan the reader): a
        # newer change schedules its own check instead.
        if self._filter_task is asyncio.current_task():
            self._filter_task = None
        from app.services.meshloom_community import community_egress_state

        # The same guard as every other egress: opted out, nothing reopens.
        state = await community_egress_state()
        if state is None or not state.iata:
            return
        async with self._lock:
            self._prune(time.monotonic())
            if not self._sessions or self._auth_error is not None or not self._connected:
                # Not connected: the next connect attempt reads the new union
                # itself, and a handshake already in flight checks it again
                # once open (see _run).
                return
            if self._desired_filter(state.iata) == self._active_filter:
                return
        logger.info("Community live region filter changed; reconnecting")
        await self.close_stats()
        if await community_egress_state() is None:
            return
        async with self._lock:
            self._prune(time.monotonic())
            self._ensure_reader_locked()

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
        from app.services.community_ban import community_banned

        if self._auth_error is not None or community_banned():
            return
        if self._reader_task is not None and not self._reader_task.done():
            return
        if not self._sessions:
            return
        self._generation += 1
        generation = self._generation
        self._reader_task = asyncio.create_task(self._run(generation))

    async def subscribe(self, session_id: str | None = None, iata: object = None) -> dict[str, Any]:
        """Register a Live session. Heartbeat (known id) never remints.

        ``iata`` is the regions this session shows (``None`` = every region);
        a heartbeat carries it again, so a change reaches the upstream filter.
        """
        from app.services.meshloom_community import community_active

        enabled = await community_active()
        can_open = await self._can_open_live(enabled)
        now = time.monotonic()
        selection = normalize_iata_selection(iata)
        async with self._lock:
            expired = self._prune(now)
            if session_id and session_id in self._sessions:
                previous = self._sessions[session_id][1]
                self._sessions[session_id] = (now, selection)
                sid = session_id
                changed = previous != selection
            else:
                sid = uuid.uuid4().hex
                self._sessions[sid] = (now, selection)
                changed = True
            if self._idle_close_task is not None:
                self._idle_close_task.cancel()
                self._idle_close_task = None
            if can_open:
                self._ensure_reader_locked()
                if changed or expired:
                    self._schedule_filter_check()
        if not can_open:
            await self.close_stats()
        return self.snapshot(session_id=sid, opted_out=not enabled)

    async def unsubscribe(self, session_id: str) -> dict[str, Any]:
        async with self._lock:
            removed = self._sessions.pop(session_id, None) is not None
            expired = self._prune(time.monotonic())
            empty = not self._sessions
            if empty and self._idle_close_task is None:
                self._idle_close_task = asyncio.create_task(self._idle_close())
            elif (removed or expired) and not empty:
                self._schedule_filter_check()
        from app.services.meshloom_community import community_active

        return self.snapshot(opted_out=not await community_active())

    async def restart(self) -> dict[str, Any]:
        """Mint a new API JWT and reconnect. Clears a 4002 stop, a given-up 401 and a ban state."""
        from app.services.community_ban import clear_ban
        from app.services.meshloom_community import community_active

        # A user action: the next handshake checks whether the ban was lifted.
        clear_ban()
        enabled = await community_active()
        can_open = await self._can_open_live(enabled)
        self._clear_auth_error()
        self._filter_rejected_at = None
        self._close_code = None
        await self.close_stats()
        if can_open:
            async with self._lock:
                self._prune(time.monotonic())
                self._ensure_reader_locked()
        else:
            await self._broadcast_status(opted_out=not enabled)
        return self.snapshot(opted_out=not enabled)

    def _clear_auth_error(self) -> None:
        self._auth_error = None
        self._auth_code = None
        self._clock_skew_s = None

    async def sync_community(self, enabled: bool) -> None:
        # A settings change (IATA, API base, opt-in) is a fresh start for auth
        # and for the region filter (a new Community may accept it).
        self._clear_auth_error()
        self._filter_rejected_at = None
        if not enabled:
            self._paused = False
            self._cancel_filter_check()
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

    async def stop_for_ban(self) -> None:
        """Community banned this key: close the socket and say so to every tab."""
        self._paused = False
        self._cancel_filter_check()
        await self.close_stats()
        await self._broadcast_status(opted_out=False)

    async def release_all_consumers(self) -> None:
        async with self._lock:
            self._sessions.clear()
            if self._idle_close_task is not None:
                self._idle_close_task.cancel()
                self._idle_close_task = None
            self._cancel_filter_check()
        await self.close_stats()

    def drain_queue(self) -> int:
        """Drop frames waiting for fan-out (opt-out). Returns how many were dropped."""
        dropped = 0
        while True:
            try:
                item = self._queue.get_nowait()
            except asyncio.QueueEmpty:
                return dropped
            if item is None:
                # The fan-out stop sentinel is not a frame: keep it.
                self._queue.put_nowait(None)
                return dropped
            dropped += 1

    async def shutdown(self) -> None:
        await self.release_all_consumers()
        await self._stop_fanout()

    async def close_stats(self) -> None:
        """Close the upstream Stats socket so the pubkey slot is freed."""
        async with self._lock:
            ws = self._ws
            self._ws = None
            self._connected = False
            # No reader left to retry: nothing is paused any more.
            was_paused = self._paused
            self._paused = False
            self._generation += 1
            task = self._reader_task
            self._reader_task = None
        if ws is not None:
            try:
                # Bounded: a close handshake that hangs must not keep the
                # reader below from being cancelled (opt-out teardown).
                await asyncio.wait_for(ws.close(), timeout=CLOSE_TIMEOUT_S)
            except Exception:  # TimeoutError included
                logger.debug("Community live socket close failed", exc_info=True)
        if task is not None and not task.done():
            task.cancel()
            # Bounded too: the reader's own socket exit may wait on the peer.
            done, _pending = await asyncio.wait({task}, timeout=CLOSE_TIMEOUT_S)
            if not done:
                logger.warning("Community live reader did not stop in time; abandoning it")
        if was_paused:
            # The stopped reader no longer reports anything: say the pause is over.
            from app.services.meshloom_community import community_egress_state

            try:
                opted_out = await community_egress_state() is None
                await self._broadcast_status(opted_out=opted_out)
            except Exception:
                logger.debug("community_live status fan-out failed", exc_info=True)

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
        from app.services.community_ban import community_banned, is_banned_answer, note_banned
        from app.services.meshloom_community import (
            classify_auth_rejection,
            community_egress_state,
            mint_stats_jwt,
        )

        await self._ensure_fanout()
        delay = RECONNECT_INITIAL_S
        paused_delay = PAUSED_RETRY_MIN_S
        auth_failures = 0
        opted_out = False
        try:
            while generation == self._generation:
                if not self._has_consumers() or self._auth_error is not None:
                    return
                if community_banned():
                    return
                try:
                    state = await community_egress_state()
                except Exception:
                    if generation != self._generation:
                        return
                    logger.info("Community live config probe failed; retrying")
                    await self._sleep_backoff(generation, delay)
                    delay = min(RECONNECT_MAX_S, delay * RECONNECT_FACTOR)
                    continue
                if state is None:
                    opted_out = True
                    return
                if not state.iata:
                    logger.info("Community live skipped: IATA is not set")
                    return
                wanted = self._desired_filter(state.iata)
                url = stats_live_ws_url(state.api_base, wanted)
                if not url:
                    logger.warning("Community live skipped: API base is empty")
                    return
                self._active_filter = wanted
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
                logger.info(
                    "Opening Community live socket (%s)",
                    ",".join(sorted(wanted)) if wanted else "global",
                )
                close_code: int | None = None
                http_status: int | None = None
                auth_payload: dict[str, Any] | None = None
                auth_refused = False
                feed_paused = False
                banned = False
                opened_at: float | None = None
                try:
                    async with await self._connect(url, token) as ws:
                        if generation != self._generation:
                            return
                        self._ws = ws
                        self._connected = True
                        self._close_code = None
                        self._paused = False
                        paused_delay = PAUSED_RETRY_MIN_S
                        opened_at = time.monotonic()
                        auth_failures = 0
                        # A region change that landed during the handshake was
                        # dropped by its debounced check (not connected yet):
                        # this socket opened with the older union, check again.
                        if self._desired_filter(state.iata) != wanted:
                            self._schedule_filter_check()
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
                    http_status = _http_status(exc)
                    if http_status == 401:
                        auth_refused = True
                        auth_payload = _handshake_body(exc)
                    elif is_banned_answer(http_status, _handshake_body(exc)):
                        banned = True
                    elif is_feed_paused(http_status=http_status, body=_handshake_body(exc)):
                        feed_paused = True
                    else:
                        close_code = map_stats_close(
                            code=_ws_close_code(exc), http_status=http_status
                        )
                        feed_paused = is_feed_paused(code=close_code)
                    logger.info(
                        "Community live socket ended (close_code=%s, http=%s)",
                        close_code,
                        http_status,
                    )
                self._connected = False
                self._ws = None
                pause_ended = False
                if not feed_paused and self._paused:
                    # Any other answer (401, 409, 429, 502, live_viewer_cap...)
                    # means the feed is no longer known to be paused: the state
                    # must not stay "paused" through another kind of failure.
                    self._paused = False
                    pause_ended = True
                # Only a socket that stayed up earns a fresh backoff; one that is
                # closed right after opening keeps growing the delay.
                stayed_up = (
                    opened_at is not None
                    and time.monotonic() - opened_at >= JWT_EXPIRY_MIN_UPTIME_S
                )
                if stayed_up:
                    delay = RECONNECT_INITIAL_S
                if banned:
                    # No retry can lift a ban: stop until the ban state ends
                    # (the finally below reports state ``banned``).
                    note_banned()
                    self._close_code = None
                    return
                if generation != self._generation or not self._has_consumers():
                    return
                if wanted is not None and http_status == 400:
                    # A Community that knows ?iata= but refuses this value
                    # (live_bad_filter): fall back to the global feed at once.
                    # The browser filters the global feed the same way.
                    logger.warning(
                        "Community refused the live region filter; using the global feed"
                    )
                    self._filter_rejected_at = time.monotonic()
                    self._active_filter = None
                    if pause_ended:
                        await self._broadcast_status(opted_out=False)
                    await self._sleep_backoff(generation, 0)
                    continue
                if auth_refused:
                    auth_failures += 1
                    reason, skew = classify_auth_rejection(auth_payload)
                    code = (auth_payload or {}).get("code")
                    give_up = (
                        reason in AUTH_FATAL_CODES or auth_failures >= AUTH_REJECT_MAX_ATTEMPTS
                    )
                    logger.warning(
                        "Community refused the live token (%s, attempt %d/%d)%s",
                        reason,
                        auth_failures,
                        AUTH_REJECT_MAX_ATTEMPTS,
                        "; giving up until a restart or a settings change" if give_up else "",
                    )
                    self._close_code = None
                    if give_up:
                        self._auth_error = (
                            "clock_skew" if reason == "clock_skew" else "token_rejected"
                        )
                        self._auth_code = code if isinstance(code, str) and code else None
                        self._clock_skew_s = skew
                        await self._broadcast_status(opted_out=False)
                        return
                    await self._broadcast_status(opted_out=False)
                    await self._sleep_backoff(
                        generation,
                        min(RECONNECT_MAX_S, AUTH_RETRY_INITIAL_S * 2 ** (auth_failures - 1)),
                    )
                    continue
                if feed_paused:
                    # The operator paused the public feed: a discreet status and
                    # a slow, jittered retry, whatever the socket's uptime.
                    if not self._paused:
                        logger.info("Community paused the public Live feed; retrying slowly")
                    self._paused = True
                    self._close_code = None
                    await self._broadcast_status(opted_out=False)
                    await self._sleep_backoff(generation, paused_delay)
                    paused_delay = min(PAUSED_RETRY_MAX_S, paused_delay * RECONNECT_FACTOR)
                    delay = RECONNECT_INITIAL_S
                    continue
                if close_code == CLOSE_INACTIVE:
                    # 4002 is reserved and never sent (contract live-events.md):
                    # if it ever arrives, stop this generation until a restart.
                    self._close_code = user_visible_close_code(close_code)
                    await self._broadcast_status(opted_out=False)
                    return
                self._close_code = user_visible_close_code(close_code)
                await self._broadcast_status(opted_out=False)
                # A natural JWT expiry (socket stayed up) remints at once. A 4001
                # right after opening means the server keeps rejecting fresh
                # tokens: that is a loop, so back off like any other close.
                natural_expiry = close_code == CLOSE_JWT_EXPIRED and stayed_up
                sleep_for = 0.0 if natural_expiry else delay
                await self._sleep_backoff(generation, sleep_for)
                if not natural_expiry:
                    delay = min(RECONNECT_MAX_S, max(RECONNECT_INITIAL_S, delay) * RECONNECT_FACTOR)
        finally:
            if generation == self._generation:
                self._connected = False
                self._ws = None
                self._reader_task = None
                self._active_filter = None
                self._paused = False
                try:
                    await self._broadcast_status(opted_out=opted_out)
                except Exception:
                    logger.debug("community_live status fan-out failed", exc_info=True)

    async def _sleep_backoff(self, generation: int, seconds: float) -> None:
        """Sleep ``seconds`` +/- ``BACKOFF_JITTER``; 0 only yields."""
        if seconds <= 0:
            await asyncio.sleep(0)
            return
        try:
            await asyncio.sleep(jittered(seconds))
        except asyncio.CancelledError:
            raise
        if generation != self._generation:
            return


ACTIVE_IATA_PATH = "/v1/iata/active"
ACTIVE_IATA_CACHE_S = 60.0
# Community statuses that mean "this server does not serve the route yet".
ACTIVE_IATA_MISSING_STATUSES = frozenset({404, 405})
_active_iata_cache: dict[str, tuple[float, dict[str, Any]]] = {}


def parse_active_iata(payload: object) -> list[dict[str, Any]]:
    """``[{code, unique_hashes_24h}]``, busiest first. Bad rows are skipped.

    Accepts the bare list from the contract and, defensively, an object that
    wraps it under ``iata``, ``codes`` or ``data``.
    """
    rows = payload
    if isinstance(rows, dict):
        for key in ("iata", "codes", "data"):
            if isinstance(rows.get(key), list):
                rows = rows[key]
                break
    if not isinstance(rows, list):
        return []
    seen: dict[str, int] = {}
    for row in rows:
        if not isinstance(row, dict):
            continue
        raw = row.get("code")
        code = raw.strip().upper() if isinstance(raw, str) else ""
        if not _IATA_RE.fullmatch(code) or code in PLACEHOLDER_IATAS:
            continue
        count = row.get("unique_hashes_24h")
        hashes = count if isinstance(count, int) and not isinstance(count, bool) else 0
        seen[code] = max(seen.get(code, 0), max(hashes, 0))
    ordered = sorted(seen.items(), key=lambda item: (-item[1], item[0]))
    return [{"code": code, "unique_hashes_24h": hashes} for code, hashes in ordered]


async def active_iata_codes() -> dict[str, Any]:
    """Regions Community saw in the last 24 h, for the Live picker (cached 60 s).

    Opted out: nothing is sent, ``available`` is false. A Community without
    the route (404/405) is cached as unavailable too; any other failure is
    raised so the browser keeps the regions it has seen.
    """
    from app.services.meshloom_community import (
        CommunityUpstreamError,
        community_egress_state,
        stats_json,
    )

    state = await community_egress_state()
    if state is None:
        return {"available": False, "codes": []}
    now = time.monotonic()
    cached = _active_iata_cache.get(state.api_base)
    if cached is not None and cached[0] > now:
        return cached[1]
    try:
        payload = await stats_json("GET", ACTIVE_IATA_PATH, auth=False)
        result: dict[str, Any] = {"available": True, "codes": parse_active_iata(payload)}
    except CommunityUpstreamError as exc:
        if exc.upstream_status not in ACTIVE_IATA_MISSING_STATUSES:
            raise
        result = {"available": False, "codes": []}
    _active_iata_cache.clear()
    _active_iata_cache[state.api_base] = (now + ACTIVE_IATA_CACHE_S, result)
    return result


def reset_active_iata_cache() -> None:
    _active_iata_cache.clear()


_relay: CommunityLiveRelay | None = None


def get_live_relay() -> CommunityLiveRelay:
    global _relay
    if _relay is None:
        _relay = CommunityLiveRelay()
    return _relay


async def subscribe_live(
    session_id: str | None = None, iata: list[str] | None = None
) -> dict[str, Any]:
    return await get_live_relay().subscribe(session_id, iata)


async def unsubscribe_live(session_id: str) -> dict[str, Any]:
    return await get_live_relay().unsubscribe(session_id)


async def restart_live() -> dict[str, Any]:
    return await get_live_relay().restart()


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
    reset_active_iata_cache()
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
    relay._cancel_filter_check()
