"""Meshloom Community state, JWT mint, and HTTP client (server code name: Stats).

Community is on for new installs and one opt-out. Off means no connection at
all to the Community hosts (HTTP API, Live WS, MQTT broker, release mirror)
nor to the airport search. This module is the egress guard: every one of
those connections checks ``community_egress_state()`` first, and HTTP goes
through ``_community_http()`` so ``community_teardown()`` can cancel what is
in flight. Env names are MESHLOOM_* only — do not invent MESHCORE_COMMUNITY
aliases.
"""

from __future__ import annotations

import asyncio
import logging
import os
import re
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlsplit

import httpx
from fastapi import HTTPException

from app.background_tasks import spawn
from app.data.meshcore_channels import hashtag_room_name
from app.models import CommunityAirportHit, CommunityStatus

logger = logging.getLogger(__name__)

SYSTEM_MESHLOOM_STATS_ID = "system:meshloom-stats"  # keep in sync with app.fanout.manager
DEFAULT_BROKER_HOST = "mqtt.meshloom.app"
DEFAULT_API_BASE = "https://api.meshloom.app"
DEFAULT_BROKER_PORT = 443
DEFAULT_WEBSOCKET_PATH = "/mqtt"
MQTT_KEEPALIVE_SECONDS = 30
_IATA_RE = re.compile(r"^[A-Z]{3}$")
AIRPORT_SEARCH_URL = "https://api.fx-port.com/api/v1/flights/airports"
AIRPORT_SEARCH_TIMEOUT_SECONDS = 5.0
_AIRPORT_QUERY_MIN = 2
_AIRPORT_QUERY_MAX = 48
_AIRPORT_HITS_MAX = 12
_STATS_TIMEOUT_SECONDS = 8.0
_PLACEHOLDER_HOST_SUFFIX = ".example.invalid"
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
# Future "last call" to Community before the teardown (see _announce_opt_out).
OPT_OUT_LAST_CALL_TIMEOUT_S = 5.0
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


def _env_raw(name: str) -> str:
    return os.environ.get(name, "").strip()


def env_community_opt_in() -> bool:
    """New installs default on. MESHLOOM_COMMUNITY=0/false/off seeds opted out."""
    raw = _env_raw("MESHLOOM_COMMUNITY").lower()
    return raw not in {"0", "false", "off", "no"}


def env_community_iata() -> str:
    return _normalize_iata(_env_raw("MESHLOOM_COMMUNITY_IATA"))


def env_community_broker_host() -> str:
    return _usable_broker_host(_env_raw("MESHLOOM_COMMUNITY_BROKER_HOST"))


def env_community_api_base() -> str:
    return _usable_api_base(_env_raw("MESHLOOM_COMMUNITY_API_BASE"))


def _is_placeholder_host(host: str) -> bool:
    return not host or host == "example.invalid" or host.endswith(_PLACEHOLDER_HOST_SUFFIX)


def _usable_broker_host(raw: str) -> str:
    text = (raw or "").strip()
    if _is_placeholder_host(host_without_scheme(text)):
        return ""
    return text


def _usable_api_base(raw: str) -> str:
    text = (raw or "").strip()
    if not text:
        return ""
    try:
        normalized = normalize_api_base(text)
    except ValueError:
        return ""
    if _is_placeholder_host(host_without_scheme(normalized)):
        return ""
    return normalized


def _normalize_iata(raw: str) -> str:
    code = raw.upper().strip()
    return code if _IATA_RE.fullmatch(code) else ""


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
    enabled: bool
    iata: str
    broker_host: str
    api_base: str


@dataclass(frozen=True)
class CommunityEffective:
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
    iata = env_community_iata() or row.iata
    broker = (
        env_community_broker_host() or _usable_broker_host(row.broker_host) or DEFAULT_BROKER_HOST
    )
    api_base = env_community_api_base() or _usable_api_base(row.api_base) or DEFAULT_API_BASE
    try:
        api_base = normalize_api_base(api_base) or DEFAULT_API_BASE
    except ValueError:
        api_base = DEFAULT_API_BASE
    return CommunityEffective(
        enabled=row.enabled,
        iata=_normalize_iata(iata),
        broker_host=broker,
        api_base=api_base,
        api_audience=host_without_scheme(api_base),
        mqtt_audience=host_without_scheme(broker),
    )


async def community_enabled() -> bool:
    return (await get_community_effective()).enabled


async def seed_community_from_env(*, new_install: bool) -> None:
    """Write env defaults only for a brand-new database. Existing DBs stay opted out."""
    from app.repository.settings import AppSettingsRepository

    if not new_install:
        return
    row = await get_community_record()
    if row.enabled or row.iata or row.broker_host or row.api_base:
        return
    enabled = env_community_opt_in()
    iata = env_community_iata()
    broker = env_community_broker_host()
    api_raw = env_community_api_base()
    api_base = ""
    if api_raw:
        try:
            api_base = normalize_api_base(api_raw)
        except ValueError:
            logger.warning("Ignoring invalid MESHLOOM_COMMUNITY_API_BASE")
    await AppSettingsRepository.update_community(
        enabled=enabled,
        iata=iata,
        broker_host=broker,
        api_base=api_base,
    )
    if enabled:
        logger.info("Seeded Meshloom Community on for new install")
    else:
        logger.info("Seeded Meshloom Community off from MESHLOOM_COMMUNITY")


async def update_community(
    *,
    enabled: bool | None = None,
    iata: str | None = None,
    broker_host: str | None = None,
    api_base: str | None = None,
) -> CommunityEffective:
    from app.repository.settings import AppSettingsRepository

    row = await get_community_record()
    before = await get_community_effective()
    next_iata = row.iata
    if iata is not None:
        normalized = _normalize_iata(iata)
        if iata.strip() and not normalized:
            raise HTTPException(status_code=400, detail="IATA must be exactly 3 uppercase letters")
        next_iata = normalized
    next_broker = row.broker_host
    if broker_host is not None:
        next_broker = broker_host.strip()
    next_api = row.api_base
    if api_base is not None:
        raw = api_base.strip()
        if raw:
            try:
                next_api = normalize_api_base(raw)
            except ValueError as exc:
                raise HTTPException(status_code=400, detail=str(exc)) from exc
        else:
            next_api = ""
    if before.enabled and enabled is False:
        # Last call while Community is still on, before the DB flips.
        await _bounded_step(
            "opt-out last call", lambda: _announce_opt_out(before), OPT_OUT_LAST_CALL_TIMEOUT_S
        )
    await AppSettingsRepository.update_community(
        enabled=enabled,
        iata=next_iata if iata is not None else None,
        broker_host=next_broker if broker_host is not None else None,
        api_base=next_api if api_base is not None else None,
    )
    from app.services.directory import reset_directory_nodes_cache
    from app.services.observer_reach import reset_observer_reach_cache

    reset_directory_nodes_cache()
    reset_observer_reach_cache()
    state = await get_community_effective()
    if before.enabled and not state.enabled:
        await community_teardown()
        return state
    if before.enabled and state.enabled and _status_topic_moved(before, state):
        # The old meshcore/{IATA}/{PUBKEY}/status (or old broker) would keep
        # its retained "online" forever: clear it on the way out.
        _retire_publisher_status()
    await _reload_system_publisher()
    from app.services.community_live import sync_community_live

    await sync_community_live(state.enabled)
    if state.enabled and not before.enabled:
        from app.services.oss_updates import nudge_oss_update_poll

        nudge_oss_update_poll()
    return state


def _status_topic_moved(before: CommunityEffective, after: CommunityEffective) -> bool:
    return bool(before.iata) and (
        before.iata != after.iata or before.broker_host != after.broker_host
    )


async def _announce_opt_out(state: CommunityEffective) -> None:
    """Hook: the "last call" to Community when the operator opts out.

    Runs while Community is still on, before the database flips and before
    ``community_teardown()``, bounded by ``OPT_OUT_LAST_CALL_TIMEOUT_S``. A
    failure is logged and never blocks the opt-out.

    Not implemented yet: Community does not serve ``POST /v1/me/opt-out``
    (separate lot). When it does, call it from here, e.g.
    ``await stats_json("POST", "/v1/me/opt-out", auth=True)``.
    """
    del state


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


def _retire_publisher_status() -> None:
    from app.fanout.manager import fanout_manager

    fanout_manager.retire_system_status()


async def _stop_publisher() -> None:
    """Clear the retained status (before DISCONNECT), then stop the publisher."""
    _retire_publisher_status()
    await _reload_system_publisher()


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


async def community_teardown() -> None:
    """Cut every Community connection after an opt-out.

    Order: clear the retained MQTT status (published before the DISCONNECT),
    stop the publisher, close the Live relay, cancel Community requests in
    flight, then reset the breaker, the sample quota and the Live queue.
    Each step is bounded and runs even when the previous one failed.
    """
    global _egress_generation
    _egress_generation += 1
    await _run_steps(
        [
            ("MQTT publisher", _stop_publisher),
            ("Live relay", _close_live),
            ("in-flight requests", _cancel_inflight_requests),
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
    return path.startswith("/v1/me/") or path == "/v1/hashtags/samples"


def sample_quota_blocked(*, now: float | None = None) -> bool:
    return (time.monotonic() if now is None else now) < _sample_quota_until


def note_sample_quota(*, now: float | None = None) -> None:
    global _sample_quota_until
    current = time.monotonic() if now is None else now
    _sample_quota_until = current + SAMPLE_QUOTA_BACKOFF_SECONDS


def reset_stats_client_for_tests() -> None:
    global _sample_quota_until, _breaker
    _sample_quota_until = 0.0
    _breaker = CommunityBreaker()


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
    raises when Community is off (no HTTP). Distinct names, all IATA.
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
    state = await get_community_effective()
    if not state.enabled or not state.iata:
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


def radio_gps_or_none() -> tuple[float | None, float | None]:
    from app.services.radio_runtime import radio_runtime as radio_manager

    try:
        info = radio_manager.meshcore.self_info if radio_manager.meshcore else None
        if not isinstance(info, dict):
            return None, None
        lat = float(info.get("adv_lat") or 0.0)
        lon = float(info.get("adv_lon") or 0.0)
        if lat == 0.0 and lon == 0.0:
            return None, None
        return lat, lon
    except (TypeError, ValueError, AttributeError):
        return None, None


def publisher_connected() -> bool:
    from app.fanout.manager import fanout_manager

    entry = fanout_manager._modules.get(SYSTEM_MESHLOOM_STATS_ID)
    if entry is None:
        return False
    return entry[0].status == "connected"


async def community_status() -> CommunityStatus:
    state = await get_community_effective()
    return CommunityStatus(
        enabled=state.enabled,
        iata=state.iata,
        broker_host=state.broker_host,
        api_base=state.api_base,
        publisher_configured=state.publisher_configured,
        publisher_connected=publisher_connected(),
        env_seeded=env_community_opt_in(),
    )


def _require_enabled(state: CommunityEffective) -> None:
    if not state.enabled:
        raise HTTPException(status_code=403, detail=COMMUNITY_DISABLED_DETAIL)


async def community_egress_state() -> CommunityEffective | None:
    """The effective config when Community connections are allowed, else None.

    The one check every Community egress makes: HTTP (``stats_request``), the
    Live socket, the MQTT publisher, the release mirror and the airport search.
    """
    state = await get_community_effective()
    return state if state.enabled else None


async def _community_http(
    method: str,
    url: str,
    *,
    generation: int,
    timeout: float,
    follow_redirects: bool = False,
    **kwargs: Any,
) -> httpx.Response:
    """The only place an HTTP request to Community (or the airport search) leaves.

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
    timeout: float = _STATS_TIMEOUT_SECONDS,
) -> httpx.Response:
    """HTTP to Community. Raises 403 when community is off (never calls).

    Transport failures, timeouts and 5xx feed the circuit breaker. While it is
    open this raises 503 without touching the network.
    """
    generation = _egress_generation
    state = await get_community_effective()
    _require_enabled(state)
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
    # Checked last: a half-open trial granted here must reach the network.
    if not _breaker.allow():
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
        _breaker.record_failure()
        logger.warning("Community %s %s failed: %s", method, path, exc)
        raise HTTPException(status_code=503, detail=COMMUNITY_UNREACHABLE_DETAIL) from exc
    except BaseException:
        # Cancellation or a local bug: release a half-open trial without judging Community.
        _breaker.trial_in_flight = False
        raise
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


def _airport_locale(locale: str) -> str:
    return "fr" if locale.lower().startswith("fr") else "en"


def _airport_coords(item: dict[str, object]) -> tuple[float | None, float | None]:
    raw_lat = item.get("lat")
    raw_lon = item.get("lng")
    if raw_lon is None:
        raw_lon = item.get("lon")
    if isinstance(raw_lat, bool) or isinstance(raw_lon, bool):
        return None, None
    try:
        lat = float(raw_lat)  # type: ignore[arg-type]
        lon = float(raw_lon)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None, None
    if not (-90.0 <= lat <= 90.0 and -180.0 <= lon <= 180.0):
        return None, None
    if lat == 0.0 and lon == 0.0:
        return None, None
    return lat, lon


def _airport_hit(item: object) -> CommunityAirportHit | None:
    if not isinstance(item, dict):
        return None
    raw_iata = item.get("ap") or item.get("iata") or item.get("id")
    if not isinstance(raw_iata, str):
        return None
    iata = raw_iata.strip().upper()
    if not _IATA_RE.fullmatch(iata):
        return None
    name = item.get("airportname") or item.get("name") or iata
    city = item.get("cityonly") or item.get("cityname") or ""
    country = item.get("country") or ""
    label_raw = item.get("shortdisplayname") or item.get("displayname")
    label = label_raw.strip() if isinstance(label_raw, str) and label_raw.strip() else iata
    lat, lon = _airport_coords(item)
    return CommunityAirportHit(
        iata=iata,
        name=name.strip() if isinstance(name, str) and name.strip() else iata,
        city=city.strip() if isinstance(city, str) else "",
        country=country.strip() if isinstance(country, str) else "",
        label=label,
        lat=lat,
        lon=lon,
    )


async def search_community_airports(query: str, *, locale: str = "en") -> list[CommunityAirportHit]:
    """Open FX-Port airport autocomplete. Empty on failure — this is convenience, not directory.

    A Community feature: empty, with no request, while Community is off.
    """
    text = (query or "").strip()
    if len(text) < _AIRPORT_QUERY_MIN:
        return []
    text = text[:_AIRPORT_QUERY_MAX]
    generation = _egress_generation
    if await community_egress_state() is None:
        return []
    try:
        response = await _community_http(
            "GET",
            AIRPORT_SEARCH_URL,
            generation=generation,
            timeout=AIRPORT_SEARCH_TIMEOUT_SECONDS,
            follow_redirects=True,
            params={"query": text, "locale": _airport_locale(locale)},
        )
    except HTTPException:
        return []
    except httpx.RequestError as exc:
        logger.warning("Airport IATA search failed: %s", exc)
        return []
    if response.status_code != 200:
        return []
    try:
        payload = response.json()
    except ValueError:
        return []
    items = payload if isinstance(payload, list) else []
    hits: list[CommunityAirportHit] = []
    seen: set[str] = set()
    for item in items:
        hit = _airport_hit(item)
        if hit is None or hit.iata in seen:
            continue
        seen.add(hit.iata)
        hits.append(hit)
        if len(hits) >= _AIRPORT_HITS_MAX:
            break
    return hits
