"""Meshloom Community state, JWT mint, and HTTP client (server code name: Stats).

Community is an explicit opt-in from the UI (a new database starts off). Off
means no connection at all to the Community hosts (HTTP API, Live WS, MQTT
broker, release mirror). Four exceptions, each bounded: if the opt-out could
not clear the retained MQTT status, ``community_status_clear`` reconnects to
the broker only to clear it, then never again; a join sends its
``PUT /v1/me/iata`` before the database says on (``_bind_state``); an
opt-out Community did not confirm can be sent again, by hand only, with
``retry_opt_out()`` (``_opt_out_state``, ``POST /v1/me/opt-out`` alone); and
the user's click on "Suggest codes" in the IATA form asks ``suggest_iata()``
for codes to propose (``_iata_suggest_state``, ``GET /v1/iata/suggest`` alone,
the radio's position rounded to 2 decimals, never during an opt-out, never fed
to the breaker). Placing an IATA code (``lookup_airport``) reads the
bundled airport list and sends nothing. This
module is the egress guard: every one of those connections checks
``community_egress_state()`` first, and HTTP goes through
``_community_http()`` so ``community_teardown()`` can cancel what is in
flight.

Waiting for a position: Community on, but the radio has no position it may
publish (``observer_position.publishable_position`` is None). The egress guard
then answers None too (no Live, no HTTP, no release mirror). Only the Community
MQTT publisher (its retained status says ``state: waiting_for_position``, and
it sends no packets) and the ``PUT /v1/me/iata`` binds go through the
enrolled-level check, ``community_enrolled_state()``, so a position chosen
later can still be bound.

The Community addresses are fixed: ``DEFAULT_BROKER_HOST`` and
``DEFAULT_API_BASE`` are the only source, read when the state is built (never
copied by value elsewhere), and are neither settable nor shown. No environment
variable steers Community any more; the old ``MESHLOOM_COMMUNITY*`` ones are
handled by ``services/legacy_env_settings.py`` (one-shot IATA import into an
existing database, then a startup warning).
"""

from __future__ import annotations

import asyncio
import logging
import re
import time
from collections.abc import Awaitable, Callable, Coroutine
from contextvars import ContextVar
from dataclasses import dataclass, replace
from typing import TYPE_CHECKING, Any, Literal, TypeVar
from urllib.parse import urlsplit

import httpx
from fastapi import HTTPException

from app.background_tasks import spawn
from app.data.meshcore_channels import hashtag_room_name
from app.loop_lock import LoopBoundLock
from app.models import (
    CommunityAirportHit,
    CommunityIataBindResult,
    CommunityIataSuggestion,
    CommunityOptOutPending,
    CommunityStatus,
)
from app.services.iata_airports import airport_position, nearest_airport_iata

if TYPE_CHECKING:
    from app.services.community_status_clear import StatusClearTarget

logger = logging.getLogger(__name__)
_T = TypeVar("_T")

SYSTEM_MESHLOOM_STATS_ID = "system:meshloom-stats"  # keep in sync with app.fanout.manager
# The only source of the Community addresses. Read at call time through
# get_community_effective(); tests monkeypatch these module attributes.
DEFAULT_BROKER_HOST = "mqtt.meshloom.app"
DEFAULT_API_BASE = "https://api.meshloom.app"
DEFAULT_BROKER_PORT = 443
DEFAULT_WEBSOCKET_PATH = "/mqtt"
MQTT_KEEPALIVE_SECONDS = 30
_IATA_RE = re.compile(r"^[A-Z]{3}$")
# Community's IATA suggestion (identity.md, § IATA suggestion): public, no JWT.
IATA_SUGGEST_PATH = "/v1/iata/suggest"
IATA_SUGGEST_TIMEOUT_SECONDS = 5.0
_STATS_TIMEOUT_SECONDS = 8.0
# Stats sample upserts are 30/hour/pubkey. After a 429, stop POSTing until that
# window has elapsed instead of retrying every catalogue pass.
SAMPLE_QUOTA_BACKOFF_SECONDS = 3600.0
_IATA_CHANGE_CAP_DETAIL = "Community IATA change cap reached"
_SAMPLE_QUOTA_DETAIL = "Community hashtag sample quota reached"
_HASHTAG_WRITE_QUOTA_DETAIL = "Community hashtag write quota reached"
_GENERIC_RATE_LIMIT_DETAIL = "Community rate limit reached"
COMMUNITY_UNREACHABLE_DETAIL = "Community is unreachable"
COMMUNITY_UNAVAILABLE_DETAIL = "Community directory unavailable"
COMMUNITY_DISABLED_DETAIL = "Community is disabled"
# Opt-out teardown: each step is bounded on its own. The MQTT step covers the
# publisher's own bounds (clear the retained status, then DISCONNECT).
TEARDOWN_STEP_TIMEOUT_S = 20.0
# The "last call" to Community before the teardown (see _announce_opt_out).
OPT_OUT_LAST_CALL_TIMEOUT_S = 5.0
# The manual retry: the operator is waiting for the answer, Community may be slow.
OPT_OUT_RETRY_TIMEOUT_S = 20.0
# HTTP timeouts of the opt-out POST. The connect timeout ends well before the
# bound above, so a connection that never opens (blackholed) fails as a
# ConnectTimeout: the request surely never left, the leftover status clear may
# be armed. The read timeout of the last call is a little longer than its
# bound, so a slow answer ends as a timeout (Community may have recorded it).
OPT_OUT_LAST_CALL_HTTP_TIMEOUT = httpx.Timeout(OPT_OUT_LAST_CALL_TIMEOUT_S + 1, connect=2.5)
OPT_OUT_RETRY_HTTP_TIMEOUT = httpx.Timeout(OPT_OUT_RETRY_TIMEOUT_S, connect=3.0)
OPT_OUT_PATH = "/v1/me/opt-out"
OptOutReason = Literal["privacy", "not_useful", "technical_issue", "other"]
# Circuit breaker: after this many consecutive transport failures or 5xx, stop
# calling Community for BREAKER_OPEN_SECONDS and answer 503 at once instead of
# making every page wait for the full timeout. One trial call then decides.
BREAKER_FAILURE_THRESHOLD = 3
BREAKER_OPEN_SECONDS = 30.0
# Clock drift beyond this (seconds) explains a 401 on a freshly minted token.
CLOCK_SKEW_TOLERANCE_SECONDS = 60
_STATS_429_BY_DETAIL = {
    "IATA change cap exceeded": _IATA_CHANGE_CAP_DETAIL,
    "hashtag sample quota": _SAMPLE_QUOTA_DETAIL,
    "hashtag write quota": _HASHTAG_WRITE_QUOTA_DETAIL,
}
_STATS_429_BY_PATH = {
    "/v1/me/iata": _IATA_CHANGE_CAP_DETAIL,
    "/v1/hashtags/samples": _SAMPLE_QUOTA_DETAIL,
    "/v1/me/hashtags": _HASHTAG_WRITE_QUOTA_DETAIL,
}
_sample_quota_until = 0.0
# Bumped by community_teardown(): a request that passed the enabled check
# before the opt-out must not leave afterwards.
_egress_generation = 0
_inflight: set[asyncio.Future[httpx.Response]] = set()
# One Community state change at a time: an opt-in sent while the opt-out
# teardown still runs would otherwise be undone by it (Live closed, publisher
# stopped while the database says on).
_update_lock = LoopBoundLock()
# Set only around the ``await bind()`` of a ``PUT /v1/me/iata`` (never around
# the status retire or the publisher restore, which spawn tasks that would
# inherit it): the bind of a join (``bind_community_iata`` with
# ``enable=True`` while Community is off), an IATA change, or the automatic
# re-bind. A join's bind may leave before the database says on, because the
# operator just asked to join and the bind must come first; any bind may leave
# while Community waits for a position, since it is how a position chosen
# later reaches Community. Everything else still sees Community off (or
# waiting) until the write.
_bind_state: ContextVar[CommunityEffective | None] = ContextVar(
    "community_bind_state", default=None
)

# Set only around the ``POST /v1/me/opt-out`` of an opt-out (Community still
# on, but ``_leaving`` already makes the guard answer None) or of its manual
# retry (``retry_opt_out``, Community off). ``stats_request`` honours it for
# that path alone.
_opt_out_state: ContextVar[CommunityEffective | None] = ContextVar(
    "community_opt_out_state", default=None
)
# Set only around the ``GET /v1/iata/suggest`` of ``suggest_iata()`` (the
# user clicked "Suggest codes" in the IATA form): the radio's position, rounded to 2
# decimals, may then leave while Community is off or waiting for a position, so
# the join form can propose a code. ``stats_request`` honours it for that path
# alone.
_iata_suggest_state: ContextVar[CommunityEffective | None] = ContextVar(
    "community_iata_suggest_state", default=None
)
# True from just before the opt-out's ``POST /v1/me/opt-out`` to the end of
# its teardown. Community kicks the MQTT session as it answers, before the
# local teardown stops the publisher: the guard then answers None, so the
# publisher does not reconnect (``_pre_connect``) and says nothing about the
# lost connection, and Live does not reopen.
_leaving = False

CommunityState = Literal["off", "active", "waiting_for_position"]
STATE_WAITING_FOR_POSITION: CommunityState = "waiting_for_position"
# Whether Community waits for a position (meaningful only while it is on).
# Set as soon as an accepted radio identity reports no publishable position
# (``note_radio_position``, called right after the identity gate, before the
# radio key is exported, and on every ``SELF_INFO``); cleared only once a
# bind carrying a position succeeded (``note_position_bound``), so no packet
# leaves before Community has the position. Kept while the radio is away or
# its identity is not accepted yet (no flicker), and persisted in
# ``app_settings.community_waiting_for_position`` (migration 092), loaded at
# startup by ``load_position_state()``.
_position_missing = False


class CommunityUpstreamError(HTTPException):
    """Local HTTP error that remembers the Community status it came from."""

    def __init__(self, status_code: int, detail: str, *, upstream_status: int) -> None:
        super().__init__(status_code=status_code, detail=detail)
        self.upstream_status = upstream_status


class CommunityBreaker:
    """Consecutive-failure circuit breaker for Community HTTP (closed/open/half-open)."""

    def __init__(
        self,
        *,
        threshold: int = BREAKER_FAILURE_THRESHOLD,
        open_seconds: float = BREAKER_OPEN_SECONDS,
    ) -> None:
        self.threshold = threshold
        self.open_seconds = open_seconds
        self.failures = 0
        self.open_until = 0.0
        self.trial_in_flight = False

    def allow(self, *, now: float | None = None) -> bool:
        """True when a call may go out. Half-open lets exactly one trial through."""
        current = time.monotonic() if now is None else now
        if self.failures < self.threshold:
            return True
        if current < self.open_until or self.trial_in_flight:
            return False
        self.trial_in_flight = True
        return True

    def record_success(self) -> None:
        self.failures = 0
        self.open_until = 0.0
        self.trial_in_flight = False

    def record_failure(self, *, now: float | None = None) -> None:
        current = time.monotonic() if now is None else now
        self.failures += 1
        self.trial_in_flight = False
        if self.failures >= self.threshold:
            self.open_until = current + self.open_seconds

    @property
    def is_open(self) -> bool:
        return self.failures >= self.threshold


_breaker = CommunityBreaker()


def community_breaker() -> CommunityBreaker:
    return _breaker


def _normalize_iata(raw: str) -> str:
    code = raw.upper().strip()
    return code if _IATA_RE.fullmatch(code) else ""


def normalize_iata(raw: str) -> str:
    """Three uppercase letters, or "" when ``raw`` is not an IATA code."""
    return _normalize_iata(raw)


def normalize_api_base(raw: str) -> str:
    text = (raw or "").strip().rstrip("/")
    if not text:
        return ""
    parsed = urlsplit(text if "://" in text else f"https://{text}")
    if parsed.scheme.lower() not in {"http", "https"} or not parsed.hostname:
        raise ValueError("API base must be an http(s) origin")
    host = parsed.hostname.lower()
    netloc = f"{host}:{parsed.port}" if parsed.port else host
    return f"{parsed.scheme.lower()}://{netloc}"


def host_without_scheme(url_or_host: str) -> str:
    text = (url_or_host or "").strip()
    if not text:
        return ""
    parsed = urlsplit(text if "://" in text else f"https://{text}")
    return (parsed.hostname or text).lower()


@dataclass(frozen=True)
class CommunityRecord:
    """What the database stores: the opt-in and the IATA. No address."""

    enabled: bool
    iata: str


@dataclass(frozen=True)
class CommunityEffective:
    """The state every Community connection uses. ``broker_host`` and
    ``api_base`` are always ``DEFAULT_BROKER_HOST`` / ``DEFAULT_API_BASE``."""

    enabled: bool
    iata: str
    broker_host: str
    api_base: str
    api_audience: str
    mqtt_audience: str

    @property
    def publisher_configured(self) -> bool:
        return self.enabled and bool(self.iata)


async def get_community_record() -> CommunityRecord:
    from app.repository.settings import AppSettingsRepository

    return await AppSettingsRepository.get_community()


async def get_community_effective() -> CommunityEffective:
    row = await get_community_record()
    broker = DEFAULT_BROKER_HOST
    api_base = normalize_api_base(DEFAULT_API_BASE)
    return CommunityEffective(
        enabled=row.enabled,
        iata=_normalize_iata(row.iata),
        broker_host=broker,
        api_base=api_base,
        api_audience=host_without_scheme(api_base),
        mqtt_audience=host_without_scheme(broker),
    )


async def community_enabled() -> bool:
    return (await get_community_effective()).enabled


async def update_community(
    *,
    enabled: bool | None = None,
    iata: str | None = None,
    iata_through_bind: bool = False,
    enable_through_bind: bool = False,
    opt_out_reason: OptOutReason | None = None,
) -> CommunityEffective:
    """Write the Community settings under ``_update_lock``.

    ``iata_through_bind`` (the ``PATCH /community`` route): an IATA other than
    the stored one is refused with 409 ``iata_requires_bind``, whatever the
    state and whether ``enabled`` is sent; the same IATA is accepted.
    ``enable_through_bind`` (same route): turning Community on from off is
    refused with 409 ``join_requires_bind``; a join is ``PUT /community/me/iata``
    with ``enable: true``, which binds before anything is written. Checked
    under the lock, so it cannot race a join or an opt-out.
    ``opt_out_reason`` goes with the ``POST /v1/me/opt-out`` of an opt-out.
    """
    async with _update_lock:
        if iata_through_bind and iata is not None:
            stored = (await get_community_record()).iata
            if _normalize_iata(iata) != _normalize_iata(stored):
                # Saved without a bind, the IATA would reach the publisher
                # (now, or at the next opt-in) and the automatic re-bind while
                # Community still knows the old one: an IATA change outside the
                # retire -> bind -> reconnect order.
                raise HTTPException(
                    status_code=409,
                    detail={
                        "code": "iata_requires_bind",
                        "message": "Change the IATA with PUT /api/community/me/iata "
                        "(enable: true to join)",
                    },
                )
        if enable_through_bind and enabled is True and not (await get_community_record()).enabled:
            # Turned on without a bind, the publisher would connect with the
            # stored IATA before Community knows it (or after a bind whose
            # answer was lost, with another one): ``iata_mismatch``, and after
            # an opt-out a key Community still refuses. Nothing is written.
            raise HTTPException(
                status_code=409,
                detail={
                    "code": "join_requires_bind",
                    "message": "Join with PUT /api/community/me/iata (enable: true)",
                },
            )
        return await _update_community_locked(
            enabled=enabled, iata=iata, opt_out_reason=opt_out_reason
        )


async def bind_community_iata(
    requested: str,
    bind: Callable[[], Awaitable[CommunityIataBindResult]],
    *,
    enable: bool = False,
) -> CommunityIataBindResult:
    """Change the IATA through Community (``PUT /v1/me/iata``), then save it.

    Order (Community identity contract, *Changing IATA*): Community's ``/authz``
    ties a status topic to the IATA of the MQTT session (the JWT it connected
    with), so the old retained status is cleared first, by the running session
    still bound to the old IATA; then ``bind()`` calls ``PUT /v1/me/iata``; only
    then does the publisher reconnect, with a JWT for the new IATA. If the bind
    fails or the caller is cancelled before it answers, the publisher comes back
    on the old topic.

    ``enable`` (joining, Community off): the bind goes out first, with a token
    for the requested IATA, and only a successful bind turns Community on, saves
    the IATA and starts the publisher. A failed bind leaves Community off and the
    stored IATA as it was, so the first ``online`` never meets another bind.
    """
    async with _update_lock:
        before = await get_community_effective()
        target = _normalize_iata(requested)
        joining = enable and not before.enabled
        retire_first = before.enabled and bool(before.iata) and target != before.iata
        # A join binds while still off; an IATA change also binds while
        # waiting for a position. Off without ``enable``, the guard says 403.
        bind_state = replace(before, enabled=True) if joining or before.enabled else None
        try:
            # Inside the try: a caller cancelled during the retire (held by
            # _run_to_completion until it is done) still gets the publisher back.
            if retire_first:
                await _run_to_completion(_retire_status_and_reload(before, restart=False))
            result = await _with_bind_state(bind_state, bind)
        except BaseException:
            if retire_first:
                await _run_to_completion(_restore_publisher())
            raise
        try:
            # As a tracked task: a caller cancelled between the database write
            # and the publisher reload must not leave Community on without its
            # publisher (or the Live socket on the old IATA).
            await _run_to_completion(
                _update_community_locked(
                    enabled=True if joining else None,
                    iata=result.iata,
                    status_retired=retire_first,
                )
            )
        except BaseException:
            if retire_first:
                await _run_to_completion(_restore_publisher())
            raise
        return result


async def rebind_current_iata(
    bind: Callable[[str], Awaitable[CommunityIataBindResult]],
    *,
    still_needed: Callable[[], bool] | None = None,
) -> CommunityIataBindResult | None:
    """Send ``PUT /v1/me/iata`` again with the stored IATA (position refresh).

    Same IATA, so nothing else moves: no status retire, no database write, and
    Community does not count it as an IATA change. Under ``_update_lock``, so
    it never interleaves with an IATA change, a join or an opt-out. None,
    without any request, when Community is off or has no IATA yet, or when
    ``still_needed`` (checked under the lock) says another bind already sent
    this position. Enrolled-level: it also runs while Community waits for a
    position, since this is how the chosen position reaches Community.
    """
    async with _update_lock:
        state = await community_enrolled_state()
        if state is None or not state.iata:
            return None
        if still_needed is not None and not still_needed():
            return None
        return await _with_bind_state(state, lambda: bind(state.iata))


async def _with_bind_state(
    state: CommunityEffective | None, bind: Callable[[], Awaitable[_T]]
) -> _T:
    """Run one bind with ``_bind_state`` set, and nothing else."""
    if state is None:
        return await bind()
    token = _bind_state.set(state)
    try:
        return await bind()
    finally:
        _bind_state.reset(token)


async def _restore_publisher() -> None:
    """Start the publisher again after a failed IATA bind stopped it."""
    from app.fanout.manager import fanout_manager

    if not fanout_manager.has_system_module():
        await _reload_system_publisher()
    await _forget_pending_clear(await get_community_effective())


async def _run_to_completion(coro: Coroutine[Any, Any, _T]) -> _T:
    """Await ``coro`` to its end, even if the caller is cancelled meanwhile.

    A cancelled request or a dropped HTTP client must not leave half a
    transition behind (database off, connections still up). The work runs as
    a tracked task; a cancellation of the caller is held until it is done,
    then raised. Only a cancellation of the work itself (shutdown drain) stops
    it. Every step is bounded, so this never waits forever.
    """
    task = spawn(coro, name="community-transition")
    cancelled = False
    while True:
        try:
            result = await asyncio.shield(task)
            break
        except asyncio.CancelledError:
            if task.cancelled():
                raise
            cancelled = True
    if cancelled:
        raise asyncio.CancelledError
    return result


async def _update_community_locked(
    *,
    enabled: bool | None,
    iata: str | None,
    status_retired: bool = False,
    opt_out_reason: OptOutReason | None = None,
) -> CommunityEffective:
    global _leaving
    from app.repository.settings import AppSettingsRepository

    row = await get_community_record()
    before = await get_community_effective()
    next_iata = row.iata
    if iata is not None:
        normalized = _normalize_iata(iata)
        if iata.strip() and not normalized:
            raise HTTPException(status_code=400, detail="IATA must be exactly 3 uppercase letters")
        next_iata = normalized
    opting_out = before.enabled and enabled is False
    written = False
    topic_moved = False
    # How the last call ended decides whether a leftover status clear may be
    # armed: only when Community surely did not record the opt-out.
    announced: OptOutResult = "failed"
    try:
        if opting_out:
            # From here the guard answers None: Community kicks the MQTT
            # session as it answers, and nothing may reconnect before the
            # teardown below.
            _leaving = True
            # Last call while Community is still on, before the DB flips.
            announced = await _announce_opt_out(before, opt_out_reason)
        await AppSettingsRepository.update_community(
            enabled=enabled,
            iata=next_iata if iata is not None else None,
        )
        written = True
        from app.services.directory import reset_directory_nodes_cache
        from app.services.observer_reach import reset_observer_reach_cache

        reset_directory_nodes_cache()
        reset_observer_reach_cache()
    finally:
        # Also on an exception or a cancellation above: the operator asked to
        # leave (or the database already names a new topic), so the old
        # connections must not stay up. Fail closed: an opt-out tears down
        # even when the write itself failed.
        if opting_out:
            try:
                await _run_to_completion(community_teardown(before, opt_out=announced))
            finally:
                _leaving = False
        elif written and before.enabled:
            topic_moved = await _run_to_completion(_move_status_topic(before, status_retired))
    state = await get_community_effective()
    if opting_out:
        return state
    if state.enabled and not before.enabled:
        # Joined again: Community lifts its opt-out on the join's own bind,
        # so an unconfirmed opt-out no longer matters.
        await _set_opt_out_pending(None)
    if not topic_moved:
        await _reload_system_publisher()
    await _forget_pending_clear(state)
    from app.services.community_live import get_live_relay, sync_community_live

    if before.enabled and state.enabled and _live_target_moved(before, state):
        # A running Live socket would otherwise stay on the old IATA or the
        # old API host until it drops; sessions are kept, so it reopens.
        await get_live_relay().close_stats()
    # Waiting for a position counts as off for Live.
    await sync_community_live(community_state_of(state) == "active")
    if state.enabled and not before.enabled:
        from app.services.oss_updates import nudge_oss_update_poll

        nudge_oss_update_poll()
    return state


async def _move_status_topic(before: CommunityEffective, already_retired: bool) -> bool:
    """IATA changed while on: clear the old status topic, then reconnect.

    The old meshcore/{IATA}/{PUBKEY}/status would keep its
    retained "online" forever. ``already_retired``: ``bind_community_iata``
    cleared it and stopped the publisher before ``PUT /v1/me/iata``; only the
    reconnect is left. Returns whether the topic moved (the publisher reloaded).
    """
    state = await get_community_effective()
    if not (state.enabled and _status_topic_moved(before, state)):
        return False
    if already_retired:
        await _reload_system_publisher()
    else:
        await _retire_status_and_reload(before)
    return True


# The addresses are fixed at runtime, so only the IATA can move a topic. A
# broker left over from a retired custom address is cleared once at startup
# (``community_status_clear.adopt_orphaned_status``, which still compares it).
def _live_target_moved(before: CommunityEffective, after: CommunityEffective) -> bool:
    return before.iata != after.iata


def _status_topic_moved(before: CommunityEffective, after: CommunityEffective) -> bool:
    return bool(before.iata) and before.iata != after.iata


# ``confirmed``: Community answered 2xx. ``maybe``: the call timed out after it
# left, so Community may have recorded it. ``failed``: Community surely did not
# (refused, unreachable, no radio key).
OptOutResult = Literal["confirmed", "maybe", "failed"]


def opt_out_in_progress() -> bool:
    """True while an opt-out runs (last call and teardown): nothing reconnects."""
    return _leaving


async def _post_opt_out(reason: OptOutReason | None, *, timeout: httpx.Timeout) -> None:
    """``POST /v1/me/opt-out``. Any 2xx confirms it; the body is not needed.

    Community deletes the bind's position, kicks the MQTT session, clears the
    retained status (or soon after, through its own follow-up) and refuses
    reconnections until a re-join. Raises the usual mapped ``HTTPException``
    otherwise.
    """
    response = await stats_request(
        "POST",
        OPT_OUT_PATH,
        auth=True,
        json_body={"reason": reason} if reason else {},
        timeout=timeout,
    )
    if not 200 <= response.status_code < 300:
        _json_or_http_error(response, path=OPT_OUT_PATH)
        raise CommunityUpstreamError(
            502,
            f"Community request failed (HTTP {response.status_code})",
            upstream_status=response.status_code,
        )


def _may_have_reached(exc: BaseException) -> bool:
    """A failure after which Community may still have recorded the opt-out."""
    if isinstance(exc, TimeoutError):
        return True
    cause = exc.__cause__
    return isinstance(cause, httpx.TimeoutException) and not isinstance(cause, httpx.ConnectTimeout)


async def _with_opt_out_state(state: CommunityEffective, call: Callable[[], Awaitable[_T]]) -> _T:
    """Run the opt-out call with ``_opt_out_state`` set, and nothing else."""
    token = _opt_out_state.set(state)
    try:
        return await call()
    finally:
        _opt_out_state.reset(token)


async def _announce_opt_out(state: CommunityEffective, reason: OptOutReason | None) -> OptOutResult:
    """The "last call" to Community when the operator opts out.

    Runs while the database still says on, before ``community_teardown()``,
    bounded by ``OPT_OUT_LAST_CALL_TIMEOUT_S``. The opt-out is first saved as
    pending (write-ahead), and cleared once Community confirms it. A failure
    is logged, keeps the pending record (the UI offers a manual retry, which
    is idempotent) and never blocks the opt-out.
    """
    try:
        await _set_opt_out_pending(await _pending_entry(state, reason))
        # See OPT_OUT_LAST_CALL_HTTP_TIMEOUT: a connection that never opens
        # fails first (ConnectTimeout, "failed"); a slow answer ends here, as a
        # timeout ("maybe": Community may have recorded it).
        await asyncio.wait_for(
            _with_opt_out_state(
                replace(state, enabled=True),
                lambda: _post_opt_out(reason, timeout=OPT_OUT_LAST_CALL_HTTP_TIMEOUT),
            ),
            timeout=OPT_OUT_LAST_CALL_TIMEOUT_S,
        )
    except Exception as exc:  # TimeoutError included
        logger.warning("Community opt-out: the last call failed", exc_info=True)
        return "maybe" if _may_have_reached(exc) else "failed"
    await _set_opt_out_pending(None)
    logger.info("Community confirmed the opt-out")
    return "confirmed"


def _current_pubkey_hex() -> str:
    from app.keystore import get_public_key

    key = get_public_key()
    return key.hex().upper() if key is not None else ""


async def _pending_entry(state: CommunityEffective, reason: OptOutReason | None) -> dict[str, Any]:
    from datetime import UTC, datetime

    # The radio away: the key the publisher last published with stands in.
    pubkey = _current_pubkey_hex()
    if not pubkey:
        last = await _last_published_or_none()
        pubkey = last.pubkey.upper() if last is not None else ""
    return {
        "pubkey": pubkey,
        "reason": reason,
        "requested_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "iata": state.iata,
    }


async def opt_out_pending() -> dict[str, Any] | None:
    """The opt-out Community did not confirm, if any (migration 093)."""
    from app.repository.settings import AppSettingsRepository

    try:
        return await AppSettingsRepository.get_community_opt_out_pending()
    except Exception:
        logger.warning("Could not read the pending Community opt-out", exc_info=True)
        return None


async def _set_opt_out_pending(entry: dict[str, Any] | None) -> None:
    from app.repository.settings import AppSettingsRepository

    try:
        await AppSettingsRepository.set_community_opt_out_pending(entry)
    except Exception:
        logger.warning("Could not save the pending Community opt-out", exc_info=True)


def _pending_reason(entry: dict[str, Any]) -> OptOutReason | None:
    raw = entry.get("reason")
    if raw in ("privacy", "not_useful", "technical_issue", "other"):
        return raw  # type: ignore[no-any-return]
    return None


async def retry_opt_out() -> None:
    """Send an unconfirmed opt-out again (``POST /api/community/opt-out/retry``).

    The only Community request made by hand while off, and only this one
    path (``_opt_out_state``). Manual only: nothing retries on its own. Under
    ``_update_lock``, so it never meets a join. Signed by the radio key that
    left, and only by it: another radio is refused (409
    ``opt_out_other_radio``), and so is any radio when that key is unknown
    (409 ``opt_out_key_unknown``). Bounded by ``OPT_OUT_RETRY_TIMEOUT_S``. On
    success the pending record goes, and every leftover status clear for that
    key is dropped (Community refuses the key, so its reconnects would be
    refused). Errors are the usual mapped ``HTTPException``; the record then
    stays.
    """
    from app.services import community_status_clear

    async with _update_lock:
        pending = await opt_out_pending()
        state = await get_community_effective()
        if pending is None or state.enabled:
            if pending is not None:
                await _set_opt_out_pending(None)
            raise HTTPException(
                status_code=409,
                detail={"code": "no_pending_opt_out", "message": "No opt-out to send again"},
            )
        left_with = str(pending.get("pubkey") or "").upper()
        if not left_with:
            # Never opt out whatever radio happens to be connected now.
            raise HTTPException(
                status_code=409,
                detail={
                    "code": "opt_out_key_unknown",
                    "message": "The radio that left Community is not known",
                },
            )
        current = _current_pubkey_hex()
        if not current:
            raise HTTPException(status_code=503, detail="Radio key is not available")
        if left_with != current:
            raise HTTPException(
                status_code=409,
                detail={
                    "code": "opt_out_other_radio",
                    "message": "Connect the radio that left Community to send its opt-out",
                },
            )
        await _with_opt_out_state(
            replace(state, enabled=True),
            lambda: _post_opt_out(_pending_reason(pending), timeout=OPT_OUT_RETRY_HTTP_TIMEOUT),
        )
        await _set_opt_out_pending(None)
        logger.info("Community confirmed the opt-out (retry)")
        try:
            await community_status_clear.drop_pubkey(current)
        except Exception:
            logger.warning("Could not update the pending Community status clear", exc_info=True)


async def _bounded_step(
    label: str, step: Callable[[], Awaitable[object]], timeout: float = TEARDOWN_STEP_TIMEOUT_S
) -> None:
    try:
        await asyncio.wait_for(step(), timeout=timeout)
    except Exception:  # TimeoutError included
        logger.warning("Community opt-out: %s failed", label, exc_info=True)


async def _run_steps(steps: list[tuple[str, Callable[[], Awaitable[object]]]]) -> None:
    """Run each step bounded, in order; a failed or cancelled step never skips the next."""
    if not steps:
        return
    label, step = steps[0]
    try:
        await _bounded_step(label, step)
    finally:
        await _run_steps(steps[1:])


async def _retire_status_and_reload(
    before: CommunityEffective | None,
    *,
    restart: bool = True,
    opt_out: OptOutResult = "failed",
) -> None:
    """Clear the retained status of ``before`` (before DISCONNECT), then reload.

    The topic is first saved as "clear pending"; it is forgotten once the
    publisher's empty retained payload is acknowledged. Otherwise (broker
    unreachable, publisher not connected) ``community_status_clear`` makes its
    single bounded retry, the only Community connection after an opt-out.
    Without the radio key (radio away), the topic the publisher last recorded
    stands in for it. ``restart=False`` stops the publisher without starting
    it again (``bind_community_iata`` reconnects once Community has the new
    IATA).

    ``opt_out``: how the opt-out's ``POST /v1/me/opt-out`` ended. The
    publisher still clears the status in its session, but the bounded retry is
    armed only when Community surely did not record the opt-out (``failed``,
    also for an IATA change). ``confirmed``: Community refuses the key on the
    broker a few seconds later and clears the status itself, so every clear
    pending for that key is dropped. ``maybe`` (timed out): nothing is armed;
    the manual retry of the opt-out settles it.
    """
    from app.fanout.manager import fanout_manager
    from app.services import community_status_clear

    target = None
    if before is not None:
        target = community_status_clear.target_for(before)
        if target is None:
            last = await _last_published_or_none()
            if last is not None and (last.iata, last.broker_host) == (
                before.iata,
                before.broker_host,
            ):
                target = last
    arm_retry = opt_out == "failed"
    if target is not None and arm_retry:
        try:
            await community_status_clear.mark_pending(target)
        except Exception:
            logger.warning("Could not save the pending Community status clear", exc_info=True)
    module = fanout_manager.retire_system_status()
    if restart:
        await _reload_system_publisher()
    else:
        await fanout_manager.stop_system_module()
    if target is None:
        return
    try:
        # Pending now (or cleared): no longer the publisher's to remember.
        if opt_out == "confirmed":
            # Community refuses this key on the broker now: any clear still
            # pending for it (an older IATA too) could only be refused.
            await community_status_clear.drop_pubkey(target.pubkey)
            return
        await community_status_clear.forget_published(target)
        if getattr(module, "retired_status_topic", None) == target.topic:
            await community_status_clear.resolve(target)
        elif arm_retry:
            community_status_clear.schedule()
    except Exception:
        logger.warning("Could not update the pending Community status clear", exc_info=True)


async def _last_published_or_none() -> StatusClearTarget | None:
    from app.services import community_status_clear

    try:
        return await community_status_clear.last_published()
    except Exception:
        logger.warning("Could not read the last Community status topic", exc_info=True)
        return None


async def _forget_pending_clear(state: CommunityEffective) -> None:
    """On again on a topic with a clear pending: the publisher owns it now."""
    from app.services import community_status_clear

    try:
        await community_status_clear.drop_current(state)
    except Exception:
        logger.warning("Could not update the pending Community status clear", exc_info=True)


async def _close_live() -> None:
    from app.services.community_live import sync_community_live

    await sync_community_live(False)


async def _cancel_inflight_requests() -> None:
    pending = [task for task in _inflight if not task.done()]
    for task in pending:
        task.cancel()
    if pending:
        await asyncio.wait(pending)


async def _reset_client_state() -> None:
    global _sample_quota_until
    _breaker.record_success()
    _sample_quota_until = 0.0
    from app.services.community_live import get_live_relay

    get_live_relay().drain_queue()
    from app.services.observer_position import forget_last_bind

    # Community refuses this key until it binds again: the next opt-in must
    # send PUT /v1/me/iata even if the position did not move.
    forget_last_bind()


async def community_teardown(
    before: CommunityEffective | None = None, *, opt_out: OptOutResult = "failed"
) -> None:
    """Cut every Community connection after an opt-out.

    Order: cancel Community requests in flight and close the Live relay
    first (both are quick, and nothing more comes back from Community), then
    clear the retained MQTT status (published before the DISCONNECT) and stop
    the publisher, which may wait on the broker, then reset the breaker, the
    sample quota and the Live queue. Each step is bounded and runs even when
    the previous one failed.

    ``before`` is the state that was on. If its status topic could not be
    cleared here, ``community_status_clear`` retries that clear alone, bounded
    in time and attempts: the one exception to "nothing after the opt-out"
    (besides the manual ``retry_opt_out``). It is armed only when the
    opt-out's ``POST /v1/me/opt-out`` surely did not reach Community
    (``opt_out == "failed"``). The radio's last bound position is forgotten,
    so nothing takes the key as still bound: only a join (``PUT /v1/me/iata``
    with ``enable``; ``PATCH {enabled: true}`` is refused) turns Community on
    again, and that bind is what lifts Community's refusal of the key.
    """
    global _egress_generation
    _egress_generation += 1
    await _run_steps(
        [
            ("in-flight requests", _cancel_inflight_requests),
            ("Live relay", _close_live),
            (
                "MQTT publisher",
                lambda: _retire_status_and_reload(before, opt_out=opt_out),
            ),
            ("client state reset", _reset_client_state),
        ]
    )


async def _reload_system_publisher() -> None:
    from app.fanout.manager import fanout_manager

    await fanout_manager.reload_system_module(SYSTEM_MESHLOOM_STATS_ID)


def hashtag_publish_name(name: str) -> str:
    """Drop one leading ``#`` so Stats receives names only, matching the OSS PUT.

    The rest is kept exact: Community derives the hash byte from this text, so
    a trimmed name would publish the key of another channel.
    """
    return hashtag_room_name(name)


def _hashtag_publish_names(names: list[str]) -> list[str]:
    cleaned: list[str] = []
    seen: set[str] = set()
    for raw in names:
        name = hashtag_publish_name(raw)
        if not name.strip() or name in seen:
            continue
        seen.add(name)
        cleaned.append(name)
        if len(cleaned) >= 50:
            break
    return cleaned


def _path_requires_iata(path: str) -> bool:
    if path == OPT_OUT_PATH:
        return False  # identity.md: the opt-out needs no ``iata`` claim
    return path.startswith("/v1/me/") or path == "/v1/hashtags/samples"


def sample_quota_blocked(*, now: float | None = None) -> bool:
    return (time.monotonic() if now is None else now) < _sample_quota_until


def note_sample_quota(*, now: float | None = None) -> None:
    global _sample_quota_until
    current = time.monotonic() if now is None else now
    _sample_quota_until = current + SAMPLE_QUOTA_BACKOFF_SECONDS


def reset_stats_client_for_tests() -> None:
    global _sample_quota_until, _breaker, _position_missing, _leaving
    _sample_quota_until = 0.0
    _breaker = CommunityBreaker()
    _position_missing = False
    _leaving = False


def _is_quota_http(exc: BaseException) -> bool:
    return isinstance(exc, HTTPException) and exc.status_code == 429


async def _put_hashtag_names(names: list[str]) -> None:
    try:
        await stats_json("PUT", "/v1/me/hashtags", auth=True, json_body={"names": names})
    except HTTPException as exc:
        if _is_quota_http(exc):
            logger.info("Community hashtag name publish skipped: Stats quota")
            return
        logger.info("Community hashtag name publish skipped", exc_info=True)
    except Exception:
        logger.info("Community hashtag name publish skipped", exc_info=True)


async def upload_hashtag_sample(hash_byte: str, payload_hex: str) -> bool:
    """POST /v1/hashtags/samples. True only if stats_json succeeded. Never raises."""
    if sample_quota_blocked():
        return False
    try:
        await stats_json(
            "POST",
            "/v1/hashtags/samples",
            auth=True,
            json_body={"hash_byte": hash_byte, "payload_hex": payload_hex},
        )
    except HTTPException as exc:
        if _is_quota_http(exc):
            note_sample_quota()
            logger.info("Community hashtag sample upload skipped: Stats sample quota")
            return False
        logger.info("Community hashtag sample upload skipped", exc_info=True)
        return False
    except Exception:
        logger.info("Community hashtag sample upload skipped", exc_info=True)
        return False
    return True


_HASH_BYTE_RE = re.compile(r"^[0-9a-f]{2}$")
RESOLVE_HASH_BYTES_MAX = 32


def _normalize_resolve_hash_bytes(raw: list[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for item in raw:
        hb = (item or "").strip().lower()
        if not _HASH_BYTE_RE.fullmatch(hb) or hb in seen:
            continue
        seen.add(hb)
        out.append(hb)
        if len(out) >= RESOLVE_HASH_BYTES_MAX:
            break
    return out


async def resolve_hashtag_names(hash_bytes: list[str]) -> list[dict[str, str]]:
    """POST /v1/hashtags/resolve. Body is hash_bytes only — never ciphertext.

    Caller must have already checked Community on + IATA. ``stats_json``
    raises when Community is off (no HTTP). Distinct names, global catalogue.
    """
    cleaned = _normalize_resolve_hash_bytes(hash_bytes)
    if not cleaned:
        return []
    payload = await stats_json(
        "POST",
        "/v1/hashtags/resolve",
        auth=True,
        json_body={"hash_bytes": cleaned},
    )
    if not isinstance(payload, dict):
        return []
    rows = payload.get("hashtags")
    if not isinstance(rows, list):
        return []
    out: list[dict[str, str]] = []
    seen: set[str] = set()
    for row in rows:
        if not isinstance(row, dict):
            continue
        name = hashtag_publish_name(str(row.get("name") or ""))
        hb = str(row.get("hash_byte") or "").strip().lower()
        if not name.strip() or not _HASH_BYTE_RE.fullmatch(hb) or name in seen:
            continue
        seen.add(name)
        out.append({"name": name, "hash_byte": hb})
    return out


async def schedule_hashtag_names_publish(
    names: list[str],
    *,
    is_hashtag: bool = True,
) -> None:
    """Fire-and-forget PUT of hashtag names when Community is on and IATA is set.

    Private-key channels (``is_hashtag=0``) and later HMAC community channels
    must not call this with ``is_hashtag=True``. Never raises. No creator toast.
    """
    if not is_hashtag:
        return
    cleaned = _hashtag_publish_names(names)
    if not cleaned:
        return
    state = await community_egress_state()
    if state is None or not state.iata:
        return
    spawn(_put_hashtag_names(cleaned))


def mint_stats_jwt(*, audience: str, iata: str = "", require_iata: bool = False) -> str:
    from app.fanout.community_mqtt import _generate_jwt_token
    from app.keystore import get_private_key, get_public_key

    private_key = get_private_key()
    public_key = get_public_key()
    if private_key is None or public_key is None:
        raise HTTPException(status_code=503, detail="Radio key is not available")
    if require_iata and not iata:
        raise HTTPException(status_code=400, detail="IATA is required for Community tokens")
    return _generate_jwt_token(private_key, public_key, audience=audience, iata=iata)


def publisher_connected() -> bool:
    from app.fanout.manager import fanout_manager

    entry = fanout_manager._modules.get(SYSTEM_MESHLOOM_STATS_ID)
    if entry is None:
        return False
    return entry[0].status == "connected"


async def community_status() -> CommunityStatus:
    state = await get_community_effective()
    pending = None if state.enabled else await opt_out_pending()
    return CommunityStatus(
        enabled=state.enabled,
        iata=state.iata,
        publisher_configured=state.publisher_configured,
        publisher_connected=publisher_connected(),
        state=community_state_of(state),
        opt_out_pending=(
            CommunityOptOutPending(
                requested_at=str(pending.get("requested_at") or ""),
                reason=_pending_reason(pending),
            )
            if pending is not None
            else None
        ),
    )


def community_state_of(state: CommunityEffective) -> CommunityState:
    """``off``, ``waiting_for_position`` (on, no publishable position) or ``active``."""
    if not state.enabled:
        return "off"
    return STATE_WAITING_FOR_POSITION if _position_missing else "active"


def waiting_for_position() -> bool:
    """True when the last known radio position cannot be published.

    Only meaningful while Community is on (``community_state_of``); the Stats
    publisher, which runs only then, reads it for its status and packets.
    """
    return _position_missing


def note_radio_position(info: Any) -> None:
    """A fresh ``self_info``: enter the waiting state when it has no position.

    Cheap and synchronous. Called right after the identity gate accepts a
    radio (before its key is exported, so nothing authenticated leaves first)
    and on every ``SELF_INFO`` (through ``observer_position.note_radio_self_info``).
    Ignored when ``info`` is not a ``self_info`` or the identity is not
    accepted: the last known state stays (owner decision Q2, no flicker). A
    position does not end the wait by itself: the bind that carries it does
    (``note_position_bound``), except when this very position was already
    bound since the start.
    """
    from app.services.observer_position import position_already_bound, publishable_position
    from app.services.radio_ingest_gate import ingest_allowed

    if not isinstance(info, dict) or not ingest_allowed():
        return
    if publishable_position(info) is None:
        _set_position_missing(True)
    elif _position_missing and position_already_bound(info):
        _set_position_missing(False)


def note_position_bound() -> None:
    """A ``PUT /v1/me/iata`` carrying a position succeeded: the wait is over."""
    _set_position_missing(False)


def _set_position_missing(missing: bool) -> None:
    global _position_missing
    if missing == _position_missing:
        return
    _position_missing = missing
    logger.info(
        "Community %s",
        "waits for a radio position" if missing else "has the radio position",
    )
    spawn(_apply_position_state(), name="community-position-state")


async def load_position_state() -> None:
    """Startup: the waiting state last known before the restart (migration 092)."""
    global _position_missing
    from app.repository.settings import AppSettingsRepository

    _position_missing = await AppSettingsRepository.get_community_waiting_for_position()


async def _apply_position_state() -> None:
    """Bring the Community connections in line with the waiting state.

    Saves the state first (also while off), then, under ``_update_lock`` and
    reading the state again there, so a quick
    back-and-forth or an opt-out in between ends in the right place. Waiting:
    requests in flight are cancelled and Live closes. Active again: Live
    reopens for its sessions. Both ways the Stats publisher re-publishes its
    retained status at once, and the ``community_live`` status broadcast makes
    every tab read ``GET /api/community`` again.
    """
    global _egress_generation
    from app.repository.settings import AppSettingsRepository
    from app.services.community_live import sync_community_live

    # Spawned from inside a bind: never inherit its egress exception.
    _bind_state.set(None)
    try:
        # The latest value, whatever the order these tasks run in.
        await AppSettingsRepository.set_community_waiting_for_position(_position_missing)
    except Exception:
        logger.warning("Could not save the Community waiting state", exc_info=True)
    async with _update_lock:
        state = await community_enrolled_state()
        if state is None:
            return
        active = community_state_of(state) == "active"
        if not active:
            _egress_generation += 1
            await _bounded_step("in-flight requests", _cancel_inflight_requests)
        await _bounded_step("Live relay", lambda: sync_community_live(active))
        await _bounded_step("MQTT status", _republish_system_status)
    if active:
        from app.services.oss_updates import nudge_oss_update_poll

        nudge_oss_update_poll()


async def _republish_system_status() -> None:
    from app.fanout.manager import fanout_manager

    await fanout_manager.republish_system_status()


async def community_enrolled_state() -> CommunityEffective | None:
    """The effective config while Community is on, waiting for a position or not.

    Enrolled-level check, used only by the Stats MQTT publisher (start and
    every reconnect) and the ``PUT /v1/me/iata`` binds: while Community waits
    for a position the publisher keeps its status up and a chosen position can
    still be bound. Everything else uses ``community_egress_state()``. None
    while an opt-out runs (``opt_out_in_progress``), before the database flips.
    """
    if _leaving:
        return None
    state = await get_community_effective()
    return state if state.enabled and not _leaving else None


async def community_egress_state() -> CommunityEffective | None:
    """The effective config when Community connections are allowed, else None.

    The one check every Community egress makes: HTTP (``stats_request``), the
    Live socket and the release mirror. None while off and
    while Community waits for a position (``waiting_for_position``).
    """
    state = await community_enrolled_state()
    if state is None or community_state_of(state) != "active":
        return None
    return state


async def community_active() -> bool:
    """Community on and not waiting for a position: its features may run."""
    return await community_egress_state() is not None


async def _community_http(
    method: str,
    url: str,
    *,
    generation: int,
    timeout: float | httpx.Timeout,
    follow_redirects: bool = False,
    **kwargs: Any,
) -> httpx.Response:
    """The only place an HTTP request to Community leaves.

    The request runs as its own task so ``community_teardown()`` can cancel it;
    the caller then gets the same 403 as a request made after the opt-out.
    """
    if generation != _egress_generation:
        raise HTTPException(status_code=403, detail=COMMUNITY_DISABLED_DETAIL)

    async def _send() -> httpx.Response:
        async with httpx.AsyncClient(follow_redirects=follow_redirects, timeout=timeout) as client:
            return await client.request(method, url, **kwargs)

    task = asyncio.ensure_future(_send())
    _inflight.add(task)
    try:
        return await task
    except asyncio.CancelledError:
        current = asyncio.current_task()
        if task.cancelled() and (current is None or current.cancelling() == 0):
            raise HTTPException(status_code=403, detail=COMMUNITY_DISABLED_DETAIL) from None
        raise
    finally:
        _inflight.discard(task)


async def fetch_meshloom_latest() -> dict[str, Any] | None:
    """GET the Community release mirror. No JWT; never called when opted out.

    Only a fallback since the update check reads GitHub first
    (``app.services.oss_updates``). Skipped while the Community breaker is open.
    Not routed through ``stats_request``: the mirror needs no JWT and must not
    feed the breaker.
    """
    generation = _egress_generation
    state = await community_egress_state()
    if state is None:
        return None
    if _breaker.is_open and time.monotonic() < _breaker.open_until:
        return None
    url = f"{state.api_base}/v1/meshloom/latest"
    try:
        response = await _community_http(
            "GET", url, generation=generation, timeout=_STATS_TIMEOUT_SECONDS
        )
    except HTTPException:
        return None
    except httpx.RequestError as exc:
        logger.warning("Meshloom latest release fetch failed: %s", exc)
        return None
    if response.status_code != 200:
        logger.warning("Meshloom latest release HTTP %s", response.status_code)
        return None
    try:
        payload = response.json()
    except ValueError:
        logger.warning("Meshloom latest release returned non-JSON")
        return None
    return payload if isinstance(payload, dict) else None


async def stats_request(
    method: str,
    path: str,
    *,
    auth: bool,
    params: dict[str, str | int] | None = None,
    json_body: dict[str, Any] | None = None,
    iata: str | None = None,
    timeout: float | httpx.Timeout = _STATS_TIMEOUT_SECONDS,
) -> httpx.Response:
    """HTTP to Community. Raises 403 when community is off (never calls).

    Transport failures, timeouts and 5xx feed the circuit breaker. While it is
    open this raises 503 without touching the network.
    """
    generation = _egress_generation
    # A bind may leave while off (join) or waiting for a position (``_bind_state``);
    # the opt-out call while leaving or off (``_opt_out_state``), and the IATA
    # suggestion while off or waiting (``_iata_suggest_state``), each on its path only.
    state = _bind_state.get()
    if state is None and path == OPT_OUT_PATH:
        state = _opt_out_state.get()
    # Read again here, after the caller's awaits: once an opt-out started,
    # the suggestion never leaves.
    if state is None and path == IATA_SUGGEST_PATH and not _leaving:
        state = _iata_suggest_state.get()
    if state is None:
        state = await community_egress_state()
    if state is None:
        raise HTTPException(status_code=403, detail=COMMUNITY_DISABLED_DETAIL)
    headers: dict[str, str] = {}
    if auth:
        # jwt.md: API `iata` is required for `/v1/me/*` and sample upserts,
        # not directory reads. First IATA bind has no stored code yet:
        # mint with the requested code.
        mint_iata = _normalize_iata(iata) if iata is not None else state.iata
        headers["Authorization"] = (
            f"Bearer {mint_stats_jwt(audience=state.api_audience, iata=mint_iata, require_iata=_path_requires_iata(path))}"
        )
    url = f"{state.api_base}{path}"
    # The public IATA suggestion never touches the breaker (like the release
    # mirror): it can neither open it nor spend the half-open trial a join needs.
    uses_breaker = path != IATA_SUGGEST_PATH
    # Checked last: a half-open trial granted here must reach the network.
    if uses_breaker and not _breaker.allow():
        raise HTTPException(status_code=503, detail=COMMUNITY_UNREACHABLE_DETAIL)
    try:
        response = await _community_http(
            method,
            url,
            generation=generation,
            timeout=timeout,
            params=params,
            json=json_body,
            headers=headers,
        )
    except httpx.RequestError as exc:
        if uses_breaker:
            _breaker.record_failure()
        logger.warning("Community %s %s failed: %s", method, path, exc)
        raise HTTPException(status_code=503, detail=COMMUNITY_UNREACHABLE_DETAIL) from exc
    except BaseException:
        # Cancellation or a local bug: release a half-open trial without judging Community.
        if uses_breaker:
            _breaker.trial_in_flight = False
        raise
    if not uses_breaker:
        return response
    if response.status_code >= 500:
        _breaker.record_failure()
    else:
        _breaker.record_success()
    return response


def _response_detail(response: httpx.Response) -> str:
    try:
        payload = response.json()
    except ValueError:
        return ""
    if not isinstance(payload, dict):
        return ""
    raw = payload.get("detail")
    return raw.strip() if isinstance(raw, str) else ""


def _stats_429_message(path: str, response: httpx.Response) -> str:
    """Map Stats 429s. IATA cap is only PUT /v1/me/iata; samples are a quota."""
    mapped = _STATS_429_BY_DETAIL.get(_response_detail(response))
    if mapped:
        return mapped
    return _STATS_429_BY_PATH.get(path, _GENERIC_RATE_LIMIT_DETAIL)


def _response_payload(response: httpx.Response) -> dict[str, Any]:
    try:
        payload = response.json()
    except ValueError:
        return {}
    return payload if isinstance(payload, dict) else {}


def classify_auth_rejection(
    payload: dict[str, Any] | None, *, now: float | None = None
) -> tuple[str, int | None]:
    """Map a Community 401 body to ``(reason, skew_seconds)``.

    Contract (community docs/contracts/errors.md): ``{"detail": str, "code": str,
    "server_time": int}``; only ``code`` is stable. ``reason`` is ``clock_skew``
    when the local clock is off (``code == "clock_skew"``, or ``server_time`` is
    more than 60 s away from ours), else the server ``code`` (``unknown`` when an
    older server sent no code). Without ``server_time`` the free-text ``detail``
    is checked as a last resort.
    """
    body = payload or {}
    raw_code = body.get("code")
    code = raw_code.strip() if isinstance(raw_code, str) and raw_code.strip() else "unknown"
    server_time = body.get("server_time")
    skew: int | None = None
    if isinstance(server_time, int | float) and not isinstance(server_time, bool):
        local = time.time() if now is None else now
        skew = int(round(float(server_time) - local))
    if code == "clock_skew":
        return "clock_skew", skew
    if skew is not None and abs(skew) > CLOCK_SKEW_TOLERANCE_SECONDS:
        return "clock_skew", skew
    if code == "unknown" and skew is None:
        detail = body.get("detail")
        text = detail.lower() if isinstance(detail, str) else ""
        if any(word in text for word in ("not yet valid", "clock", "skew", "future")):
            return "clock_skew", None
        if "expired" in text:
            return "token_expired", None
    return code, skew


def _json_or_http_error(response: httpx.Response, *, path: str) -> object:
    status = response.status_code
    if status == 429:
        raise HTTPException(status_code=429, detail=_stats_429_message(path, response))
    if status == 401:
        reason, _skew = classify_auth_rejection(_response_payload(response))
        if reason == "clock_skew":
            raise HTTPException(
                status_code=502,
                detail="Community rejected the radio token: this server's clock is off",
            )
        raise HTTPException(status_code=502, detail="Community rejected the radio token")
    if status == 400:
        raise HTTPException(status_code=400, detail="Community rejected the request")
    if status >= 500:
        logger.warning("Community %s HTTP %s", path, status)
        raise HTTPException(
            status_code=503,
            detail=f"Community is unavailable (HTTP {status})",
        )
    if status != 200:
        logger.warning("Community %s HTTP %s", path, status)
        raise CommunityUpstreamError(
            502, f"Community request failed (HTTP {status})", upstream_status=status
        )
    try:
        return response.json()
    except ValueError as exc:
        logger.warning("Community %s returned non-JSON", path)
        raise HTTPException(status_code=502, detail="Community returned non-JSON") from exc


async def stats_json(
    method: str,
    path: str,
    *,
    auth: bool,
    params: dict[str, str | int] | None = None,
    json_body: dict[str, Any] | None = None,
    iata: str | None = None,
) -> object:
    response = await stats_request(
        method, path, auth=auth, params=params, json_body=json_body, iata=iata
    )
    return _json_or_http_error(response, path=path)


def unwrap_directory_envelope(payload: object) -> object:
    """Map the Community directory envelope. unavailable → 503, malformed → 502, never []."""
    if not isinstance(payload, dict):
        raise HTTPException(status_code=502, detail=COMMUNITY_UNAVAILABLE_DETAIL)
    status = payload.get("status")
    if status == "unavailable":
        raise HTTPException(status_code=503, detail=COMMUNITY_UNAVAILABLE_DETAIL)
    if status not in {"complete", "partial"}:
        raise HTTPException(status_code=502, detail=COMMUNITY_UNAVAILABLE_DETAIL)
    data = payload.get("data")
    if not isinstance(data, dict):
        raise HTTPException(status_code=502, detail=COMMUNITY_UNAVAILABLE_DETAIL)
    return data


async def stats_directory_get(
    path: str,
    *,
    params: dict[str, str | int] | None = None,
) -> object:
    payload = await stats_json("GET", path, auth=True, params=params)
    return unwrap_directory_envelope(payload)


async def stats_directory_post(path: str, json_body: dict[str, Any]) -> object:
    payload = await stats_json("POST", path, auth=True, json_body=json_body)
    return unwrap_directory_envelope(payload)


def lookup_airport(code: str) -> list[CommunityAirportHit]:
    """Where an IATA code is, from the bundled airport list. No request, ever.

    One hit for a known code (a heliport or a metropolitan area code such as
    PAR included), none otherwise.
    """
    iata = (code or "").strip().upper()
    if not _IATA_RE.fullmatch(iata):
        return []
    position = airport_position(iata)
    if position is None:
        return []
    return [CommunityAirportHit(iata=iata, lat=position[0], lon=position[1])]


def _suggested_code(value: object) -> str | None:
    if value is None:
        return None
    if isinstance(value, str) and _IATA_RE.fullmatch(value):
        return value
    raise ValueError("not an IATA code")


async def _ask_iata_suggestion(
    state: CommunityEffective, lat: float, lon: float
) -> tuple[str | None, str | None] | None:
    """Community's ``(neighbors, nearest_airport)``, or None when it did not answer one.

    The one ``GET /v1/iata/suggest`` call: no JWT, the position as 2-decimal
    text, ``_iata_suggest_state`` set around this request alone.
    """
    token = _iata_suggest_state.set(state)
    try:
        response = await stats_request(
            "GET",
            IATA_SUGGEST_PATH,
            auth=False,
            params={"lat": f"{lat:.2f}", "lon": f"{lon:.2f}"},
            timeout=IATA_SUGGEST_TIMEOUT_SECONDS,
        )
    except HTTPException as exc:
        logger.info("IATA suggestion unavailable (%s), using the local airport list", exc.detail)
        return None
    finally:
        _iata_suggest_state.reset(token)
    if response.status_code != 200:
        logger.info("IATA suggestion HTTP %s, using the local airport list", response.status_code)
        return None
    payload = _response_payload(response)
    try:
        if not {"neighbors", "nearest_airport"} <= payload.keys():
            raise ValueError("missing keys")
        return (
            _suggested_code(payload["neighbors"]),
            _suggested_code(payload["nearest_airport"]),
        )
    except ValueError:
        logger.warning("IATA suggestion: unexpected answer, using the local airport list")
        return None


async def suggest_iata() -> CommunityIataSuggestion:
    """Up to two IATA codes the join form proposes. Nothing is ever assigned.

    Called only by ``GET /api/community/iata/suggest``, which the settings ask
    only when the user clicks "Suggest codes" in the IATA form. The radio's own
    position, rounded to 2 decimals (about 1 km), goes to Community's
    ``GET /v1/iata/suggest`` (never fed to the circuit breaker), also
    while Community is off or waiting for a position (the one exception of the
    egress guard for it, ``_iata_suggest_state``). Not during an opt-out.
    Without a position nothing leaves and nothing is suggested. When Community
    does not answer (offline, 429, 503, opt-out started meanwhile...), the nearest airport
    comes from the bundled list (``iata_airports.nearest_airport_iata``, the
    same exclusion rule) and ``neighbors`` stays null.
    """
    from app.services.observer_position import radio_self_info, suggestion_position

    position = suggestion_position(radio_self_info())
    if position is None:
        return CommunityIataSuggestion()
    lat, lon = position
    answer: tuple[str | None, str | None] | None = None
    if not _leaving:
        answer = await _ask_iata_suggestion(await get_community_effective(), lat, lon)
    neighbors, nearest = answer if answer is not None else (None, None)
    if nearest is None:
        nearest = nearest_airport_iata(lat, lon)
    return CommunityIataSuggestion(
        neighbors=neighbors,
        nearest_airport=nearest,
        source="community" if answer is not None else "local",
    )
