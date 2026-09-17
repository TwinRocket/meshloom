"""Meshloom Stats community state, JWT mint, and HTTP client.

Community is on for new installs and one opt-out: off means no Stats MQTT
publish and no Stats HTTP. Env names are MESHLOOM_* only — do not invent
MESHCORE_COMMUNITY aliases.
"""

from __future__ import annotations

import asyncio
import logging
import os
import re
import time
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlsplit

import httpx
from fastapi import HTTPException

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
_IATA_CHANGE_CAP_DETAIL = "Stats IATA change cap reached"
_SAMPLE_QUOTA_DETAIL = "Stats hashtag sample quota reached"
_HASHTAG_WRITE_QUOTA_DETAIL = "Stats hashtag write quota reached"
_GENERIC_RATE_LIMIT_DETAIL = "Stats rate limit reached"
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


def _env_raw(name: str) -> str:
    return os.environ.get(name, "").strip()


def community_locked() -> bool:
    return _env_raw("MESHLOOM_COMMUNITY_LOCKED") == "1"


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
    locked: bool
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
        locked=community_locked(),
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
    if enabled is True and community_locked():
        raise HTTPException(
            status_code=403, detail="Community enable is locked by MESHLOOM_COMMUNITY_LOCKED"
        )
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
    await _reload_system_publisher()
    from app.services.community_live import sync_community_live

    await sync_community_live(state.enabled)
    return state


async def _reload_system_publisher() -> None:
    from app.fanout.manager import fanout_manager

    await fanout_manager.reload_system_module(SYSTEM_MESHLOOM_STATS_ID)


def hashtag_publish_name(name: str) -> str:
    """Strip a leading ``#`` so Stats receives names only, matching the OSS PUT."""
    text = (name or "").strip()
    if text.startswith("#"):
        text = text[1:].strip()
    return text


def _hashtag_publish_names(names: list[str]) -> list[str]:
    cleaned: list[str] = []
    seen: set[str] = set()
    for raw in names:
        name = hashtag_publish_name(raw)
        if not name or name in seen:
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
    global _sample_quota_until
    _sample_quota_until = 0.0


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
        if not name or not _HASH_BYTE_RE.fullmatch(hb) or name in seen:
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
    asyncio.create_task(_put_hashtag_names(cleaned))


def mint_stats_jwt(*, audience: str, iata: str = "", require_iata: bool = False) -> str:
    from app.fanout.community_mqtt import _generate_jwt_token
    from app.keystore import get_private_key, get_public_key

    private_key = get_private_key()
    public_key = get_public_key()
    if private_key is None or public_key is None:
        raise HTTPException(status_code=503, detail="Radio key is not available")
    if require_iata and not iata:
        raise HTTPException(status_code=400, detail="IATA is required for Meshloom Stats tokens")
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
        locked=state.locked,
        iata=state.iata,
        broker_host=state.broker_host,
        api_base=state.api_base,
        publisher_configured=state.publisher_configured,
        publisher_connected=publisher_connected(),
        env_seeded=env_community_opt_in(),
    )


def _require_enabled(state: CommunityEffective) -> None:
    if not state.enabled:
        raise HTTPException(status_code=403, detail="Community is disabled")


async def fetch_meshloom_latest() -> dict[str, Any] | None:
    """GET Stats OSS catalogue. No JWT; works when Community is opted out.

    Do not route this through ``stats_request`` / ``stats_json`` — those call
    ``_require_enabled`` and would 403 opted-out nodes.
    """
    state = await get_community_effective()
    url = f"{state.api_base}/v1/meshloom/latest"
    try:
        async with httpx.AsyncClient(
            follow_redirects=False, timeout=_STATS_TIMEOUT_SECONDS
        ) as client:
            response = await client.get(url)
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
    """HTTP to Meshloom Stats. Raises 403 when community is off (never calls)."""
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
    try:
        async with httpx.AsyncClient(follow_redirects=False, timeout=timeout) as client:
            return await client.request(method, url, params=params, json=json_body, headers=headers)
    except httpx.RequestError as exc:
        logger.warning("Meshloom Stats %s %s failed: %s", method, path, exc)
        raise HTTPException(status_code=500, detail="Stats request failed") from exc


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


def _json_or_500(response: httpx.Response, *, path: str) -> object:
    if response.status_code == 429:
        raise HTTPException(status_code=429, detail=_stats_429_message(path, response))
    if response.status_code == 401:
        raise HTTPException(status_code=502, detail="Stats rejected the radio token")
    if response.status_code == 400:
        raise HTTPException(status_code=400, detail="Stats rejected the request")
    if response.status_code != 200:
        logger.warning("Meshloom Stats %s HTTP %s", path, response.status_code)
        raise HTTPException(
            status_code=500,
            detail=f"Stats request failed (HTTP {response.status_code})",
        )
    try:
        return response.json()
    except ValueError as exc:
        logger.warning("Meshloom Stats %s returned non-JSON", path)
        raise HTTPException(status_code=500, detail="Stats returned non-JSON") from exc


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
    return _json_or_500(response, path=path)


def unwrap_directory_envelope(payload: object) -> object:
    """Map Stats directory envelope. unavailable / invalid → HTTP 500, never []."""
    if not isinstance(payload, dict):
        raise HTTPException(status_code=500, detail="Stats directory unavailable")
    status = payload.get("status")
    if status == "unavailable":
        raise HTTPException(status_code=500, detail="Stats directory unavailable")
    if status not in {"complete", "partial"}:
        raise HTTPException(status_code=500, detail="Stats directory unavailable")
    data = payload.get("data")
    if not isinstance(data, dict):
        raise HTTPException(status_code=500, detail="Stats directory unavailable")
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
    return CommunityAirportHit(
        iata=iata,
        name=name.strip() if isinstance(name, str) and name.strip() else iata,
        city=city.strip() if isinstance(city, str) else "",
        country=country.strip() if isinstance(country, str) else "",
        label=label,
    )


async def search_community_airports(query: str, *, locale: str = "en") -> list[CommunityAirportHit]:
    """Open FX-Port airport autocomplete. Empty on failure — this is convenience, not directory."""
    text = (query or "").strip()
    if len(text) < _AIRPORT_QUERY_MIN:
        return []
    text = text[:_AIRPORT_QUERY_MAX]
    try:
        async with httpx.AsyncClient(
            follow_redirects=True, timeout=AIRPORT_SEARCH_TIMEOUT_SECONDS
        ) as client:
            response = await client.get(
                AIRPORT_SEARCH_URL,
                params={"query": text, "locale": _airport_locale(locale)},
            )
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
