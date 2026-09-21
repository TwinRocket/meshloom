"""Observer-reach lookups for flood messages, keyed by firmware packet hash.

Community on: counts come from the Stats directory, one entry per stored
observation event. Community off: there is no source and the badge is off.
"""

from __future__ import annotations

import asyncio
import json
import logging
import math
import re
import time
from dataclasses import dataclass

from fastapi import HTTPException

from app import keystore
from app.models import (
    Message,
    ObserverReachEntry,
    PacketObserverReachCountsResponse,
    PacketObserverReachResponse,
)
from app.path_utils import canonical_packet_hash, corescope_packet_hash
from app.repository import AmbiguousPublicKeyPrefixError, ContactRepository, MessageRepository
from app.services.directory import _as_float, _is_valid_map_location
from app.services.radio_runtime import radio_runtime
from app.services.ttl_lru import TtlLruCache

logger = logging.getLogger(__name__)

# Sealed sets are final, but "final" is only as good as what Stats stored: a
# server-side data repair has no way to reach a client holding a sealed copy.
# One hour bounds that staleness. It costs one Stats request per viewed message
# per hour, served from its Postgres.
SEALED_REACH_TTL_SECONDS = 3600.0
# Unsealed non-empty sets still climb; 2s matches YOUNG_POLL_MS. Empty unsealed
# is never stored — that freeze is what made the badge sit at 0 then jump.
LIVE_REACH_TTL_SECONDS = 2.0
COMMUNITY_OBSERVERS_LIVE_TTL_SECONDS = 8.0
BATCH_FALLBACK_CONCURRENCY = 4
MESHLOOM_BATCH_MAX = 20

REACH_CACHE_MAX = 512
OBSERVERS_CACHE_MAX = 16

_reach_cache: TtlLruCache[tuple[str, str], ParsedReach] = TtlLruCache(REACH_CACHE_MAX)
_observers_cache: TtlLruCache[str, dict[str, ObserverGeo]] = TtlLruCache(OBSERVERS_CACHE_MAX)
_reach_inflight: dict[str, asyncio.Future[ParsedReach]] = {}
_reach_inflight_lock = asyncio.Lock()


@dataclass(frozen=True)
class ObserverGeo:
    observer_id: str
    name: str
    public_key: str | None
    lat: float | None
    lon: float | None


_HOP_TOKEN_RE = re.compile(r"^[0-9a-fA-F]{2,6}$")


@dataclass(frozen=True)
class ParsedObservation:
    observer_id: str
    observer_name: str | None
    public_key: str | None
    hops: int | None
    snr: float | None
    path: tuple[str, ...] = ()
    is_mlc: bool = False


@dataclass(frozen=True)
class ParsedReach:
    observations: list[ParsedObservation]
    sealed: bool = False


def reset_observer_reach_cache() -> None:
    _reach_cache.clear()
    _observers_cache.clear()
    pending = list(_reach_inflight.items())
    _reach_inflight.clear()
    for _hash_lower, future in pending:
        if not future.done():
            future.set_exception(RuntimeError("observer reach cache reset"))


def validate_packet_hash_param(raw: str) -> str:
    try:
        return corescope_packet_hash(raw)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


def _parse_path_json_list(path_json: object) -> list[object] | None:
    parsed: object = path_json
    if isinstance(path_json, str):
        text = path_json.strip()
        if not text:
            return []
        try:
            parsed = json.loads(text)
        except ValueError:
            return None
    if isinstance(parsed, list):
        return parsed
    return None


def _hop_token(item: object) -> str | None:
    raw: object = item
    if isinstance(item, dict):
        raw = item.get("hash") or item.get("prefix") or item.get("hop") or item.get("id")
    if not isinstance(raw, str):
        return None
    token = raw.strip()
    if not token or not _HOP_TOKEN_RE.fullmatch(token) or len(token) % 2 != 0:
        return None
    return token.lower()


def path_from_path_json(path_json: object) -> list[str] | None:
    """Hop prefixes from the directory's path_json. None if the field is not a list."""
    parsed = _parse_path_json_list(path_json)
    if parsed is None:
        return None
    hops: list[str] = []
    for item in parsed:
        token = _hop_token(item)
        if token is not None:
            hops.append(token)
    return hops


def hops_from_path_json(path_json: object) -> int | None:
    """Hop count is len(path_json). The directory does not guarantee a hop_count field."""
    parsed = _parse_path_json_list(path_json)
    if parsed is None:
        return None
    return len(parsed)


def payload_sealed(payload: object) -> bool:
    """True only when the server explicitly marks the observer set final.

    A missing ``sealed`` field is treated as not final so older Stats
    deployments keep the live cache policy.
    """
    if not isinstance(payload, dict):
        return False
    if payload.get("sealed") is True:
        return True
    nested = payload.get("packet")
    return isinstance(nested, dict) and nested.get("sealed") is True


def parse_packet_observations(payload: object) -> list[ParsedObservation]:
    """Defensive parser for one packet's stored observation events."""
    if payload is None:
        return []
    items: list[object] = []
    if isinstance(payload, list):
        items = payload
    elif isinstance(payload, dict):
        for key in ("observations", "observers", "sightings"):
            raw = payload.get(key)
            if isinstance(raw, list):
                items = raw
                break
        if not items:
            nested = payload.get("packet")
            if isinstance(nested, dict):
                return parse_packet_observations(nested)
    return [_parse_one_observation(item) for item in items if isinstance(item, dict)]


def parse_packet_reach(payload: object) -> ParsedReach:
    return ParsedReach(
        observations=parse_packet_observations(payload),
        sealed=payload_sealed(payload),
    )


def parse_batch_reach(payload: object) -> dict[str, ParsedReach] | None:
    """Return hash → observations+sealed if the body looks like a query result."""
    if not isinstance(payload, dict):
        return None
    results = payload.get("results")
    if not isinstance(results, dict):
        return None
    parsed: dict[str, ParsedReach] = {}
    for raw_hash, value in results.items():
        if not isinstance(raw_hash, str):
            continue
        stored = canonical_packet_hash(raw_hash)
        if stored is None:
            continue
        observations = parse_packet_observations(value)
        if not _usable_observations(observations):
            observations = _count_only_observations(value)
        parsed[stored] = ParsedReach(
            observations=observations,
            sealed=payload_sealed(value),
        )
    return parsed


def parse_batch_observations(payload: object) -> dict[str, list[ParsedObservation]] | None:
    """Return hash → observations if the body looks like a query result, else None."""
    parsed = parse_batch_reach(payload)
    if parsed is None:
        return None
    return {stored: item.observations for stored, item in parsed.items()}


def parse_directory_observers(payload: object) -> dict[str, ObserverGeo]:
    items: list[object] = []
    if isinstance(payload, list):
        items = payload
    elif isinstance(payload, dict):
        raw = payload.get("observers")
        if isinstance(raw, list):
            items = raw
    by_id: dict[str, ObserverGeo] = {}
    for item in items:
        if not isinstance(item, dict):
            continue
        raw_id = item.get("id") or item.get("observer_id")
        if not isinstance(raw_id, str) or not raw_id.strip():
            continue
        observer_id = raw_id.strip()
        name_raw = item.get("name") or item.get("observer_name")
        name = name_raw.strip() if isinstance(name_raw, str) and name_raw.strip() else observer_id
        pubkey_raw = item.get("public_key") or item.get("pubkey")
        public_key = None
        if isinstance(pubkey_raw, str) and len(pubkey_raw.strip()) == 64:
            public_key = pubkey_raw.strip().lower()
        lat = _as_float(item.get("lat"))
        lon = _as_float(item.get("lon"))
        if lat is not None and lon is not None and not _is_valid_map_location(lat, lon):
            lat, lon = None, None
        by_id[observer_id] = ObserverGeo(
            observer_id=observer_id,
            name=name,
            public_key=public_key,
            lat=lat,
            lon=lon,
        )
    return by_id


def _observation_id(raw_id: object) -> str:
    if isinstance(raw_id, str) and raw_id.strip():
        return raw_id.strip()
    if isinstance(raw_id, int) and raw_id >= 0:
        return str(raw_id)
    return ""


def _usable_observations(observations: list[ParsedObservation]) -> bool:
    return any(item.observer_id or item.public_key or item.observer_name for item in observations)


def _count_only_observations(value: object) -> list[ParsedObservation]:
    if not isinstance(value, dict):
        return []
    raw = value.get("observation_count") or value.get("observer_count") or value.get("count")
    if not isinstance(raw, int) or raw <= 0:
        return []
    return [
        ParsedObservation(
            observer_id=f"count-{index}",
            observer_name=None,
            public_key=None,
            hops=None,
            snr=None,
            path=(),
            is_mlc=False,
        )
        for index in range(raw)
    ]


def _parse_one_observation(item: dict[str, object]) -> ParsedObservation:
    raw_id = item.get("observer_id") or item.get("id") or item.get("observer")
    observer_id = _observation_id(raw_id)
    name_raw = item.get("observer_name") or item.get("name")
    name = name_raw.strip() if isinstance(name_raw, str) and name_raw.strip() else None
    pubkey_raw = item.get("public_key") or item.get("pubkey")
    public_key = None
    if isinstance(pubkey_raw, str) and len(pubkey_raw.strip()) == 64:
        public_key = pubkey_raw.strip().lower()
    path_field = item.get("path_json")
    if path_field is None:
        path_field = item.get("path")
    path_tokens = path_from_path_json(path_field)
    hops = hops_from_path_json(path_field)
    if hops is None:
        hops_raw = item.get("hops") or item.get("hop_count")
        if isinstance(hops_raw, int) and hops_raw >= 0:
            hops = hops_raw
    path = tuple(path_tokens) if path_tokens is not None else ()
    if hops is None and path:
        hops = len(path)
    return ParsedObservation(
        observer_id=observer_id,
        observer_name=name,
        public_key=public_key,
        hops=hops,
        snr=_as_float(item.get("snr")),
        path=path,
        is_mlc=item.get("isMLC") is True,
    )


def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    radius = 6371.0
    d_lat = math.radians(lat2 - lat1)
    d_lon = math.radians(lon2 - lon1)
    a = (
        math.sin(d_lat / 2) ** 2
        + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) * math.sin(d_lon / 2) ** 2
    )
    return radius * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))


def local_public_key_hex() -> str | None:
    key = keystore.get_public_key()
    return key.hex().lower() if key is not None else None


def drop_local_observer(entries: list[ObserverReachEntry]) -> list[ObserverReachEntry]:
    """Remove our own radio: it does not observe its own transmissions.

    This runs in the service rather than the UI so the ear badge count and the
    modal list stay derived from the same set.
    """
    local = local_public_key_hex()
    if local is None:
        return entries
    return [entry for entry in entries if entry.public_key != local]


def local_radio_origin() -> tuple[float, float] | None:
    try:
        meshcore = getattr(radio_runtime, "meshcore", None)
        info = getattr(meshcore, "self_info", None) if meshcore is not None else None
        if not isinstance(info, dict):
            return None
        lat = float(info.get("adv_lat") or 0)
        lon = float(info.get("adv_lon") or 0)
        if not _is_valid_map_location(lat, lon):
            return None
        return lat, lon
    except (TypeError, ValueError, AttributeError):
        return None


async def resolve_origin_coords(message: Message | None) -> tuple[float, float] | None:
    if message is None:
        return None
    if message.outgoing:
        return local_radio_origin()
    keys: list[str] = []
    if message.type == "CHAN" and message.sender_key:
        keys.append(message.sender_key)
    elif message.type == "PRIV":
        if message.sender_key:
            keys.append(message.sender_key)
        keys.append(message.conversation_key)
    for key in keys:
        try:
            contact = await ContactRepository.get_by_key(key)
            if contact is None:
                contact = await ContactRepository.get_by_key_or_prefix(key)
        except AmbiguousPublicKeyPrefixError:
            continue
        if contact is None or contact.lat is None or contact.lon is None:
            continue
        if not _is_valid_map_location(float(contact.lat), float(contact.lon)):
            continue
        return float(contact.lat), float(contact.lon)
    return None


def _dedup_entries(
    observations: list[ParsedObservation],
    geos: dict[str, ObserverGeo],
) -> list[ObserverReachEntry]:
    by_key: dict[str, ObserverReachEntry] = {}
    for obs in observations:
        geo = geos.get(obs.observer_id)
        public_key = obs.public_key or (geo.public_key if geo else None)
        dedup_key = public_key or obs.observer_id or obs.observer_name or ""
        if not dedup_key:
            continue
        name = (
            obs.observer_name
            or (geo.name if geo else None)
            or (public_key[:12] if public_key else obs.observer_id)
            or "observer"
        )
        lat = geo.lat if geo else None
        lon = geo.lon if geo else None
        existing = by_key.get(dedup_key)
        if existing is None:
            by_key[dedup_key] = ObserverReachEntry(
                name=name,
                public_key=public_key,
                lat=lat,
                lon=lon,
                hops=obs.hops,
                snr=obs.snr,
                path=list(obs.path),
                isMLC=obs.is_mlc,
            )
            continue
        next_hops = existing.hops
        next_path = existing.path
        if obs.hops is not None and (next_hops is None or obs.hops > next_hops):
            next_hops = obs.hops
            next_path = list(obs.path)
        elif not next_path and obs.path:
            next_path = list(obs.path)
        by_key[dedup_key] = existing.model_copy(
            update={
                "hops": next_hops,
                "path": next_path,
                "snr": obs.snr if existing.snr is None else existing.snr,
                "lat": existing.lat if existing.lat is not None else lat,
                "lon": existing.lon if existing.lon is not None else lon,
                "isMLC": existing.isMLC or obs.is_mlc,
            }
        )
    return list(by_key.values())


_COMMUNITY_ORIGIN = "community"


def _community_reach_key(hash_lower: str) -> tuple[str, str]:
    return (_COMMUNITY_ORIGIN, hash_lower)


def _community_reach_ttl(sealed: bool) -> float:
    return SEALED_REACH_TTL_SECONDS if sealed else LIVE_REACH_TTL_SECONDS


def _should_cache_community_reach(reach: ParsedReach) -> bool:
    """Sealed (even empty) is final. Unsealed empty must be refetched next poll."""
    return reach.sealed or bool(reach.observations)


def _cache_community_reach(hash_lower: str, reach: ParsedReach, now: float) -> None:
    if not _should_cache_community_reach(reach):
        return
    _reach_cache.set(
        _community_reach_key(hash_lower),
        reach,
        now + _community_reach_ttl(reach.sealed),
        now,
    )


def _cached_community_reach(hash_lower: str, now: float) -> ParsedReach | None:
    return _reach_cache.get(_community_reach_key(hash_lower), now)


async def _claim_reach_inflight(
    hashes_lower: list[str],
) -> tuple[list[str], list[tuple[str, asyncio.Future[ParsedReach]]]]:
    """One Stats fetch per hash; overlapping callers wait on the same future."""
    to_fetch: list[str] = []
    waiting: list[tuple[str, asyncio.Future[ParsedReach]]] = []
    async with _reach_inflight_lock:
        loop = asyncio.get_running_loop()
        for hash_lower in hashes_lower:
            existing = _reach_inflight.get(hash_lower)
            if existing is not None:
                waiting.append((hash_lower, existing))
                continue
            future: asyncio.Future[ParsedReach] = loop.create_future()
            _reach_inflight[hash_lower] = future
            to_fetch.append(hash_lower)
    return to_fetch, waiting


async def _finish_reach_inflight(
    hash_lower: str,
    reach: ParsedReach | None,
    error: BaseException | None,
) -> None:
    async with _reach_inflight_lock:
        future = _reach_inflight.pop(hash_lower, None)
    if future is None or future.done():
        return
    if error is not None:
        future.set_exception(error)
    else:
        future.set_result(reach if reach is not None else ParsedReach(observations=[]))


async def _community_packet_observations(hash_lower: str) -> ParsedReach:
    from app.services.directory import _community_directory_data

    now = time.time()
    cached = _cached_community_reach(hash_lower, now)
    if cached is not None:
        return cached
    payload = await _community_directory_data(f"/v1/directory/packets/{hash_lower}")
    reach = parse_packet_reach(payload)
    _cache_community_reach(hash_lower, reach, now)
    return reach


async def _community_observers() -> dict[str, ObserverGeo]:
    from app.services.directory import _community_directory_data

    now = time.time()
    cached = _observers_cache.get(_COMMUNITY_ORIGIN, now)
    if cached is not None:
        return cached
    payload = await _community_directory_data("/v1/directory/observers")
    geos = parse_directory_observers(payload)
    _observers_cache.set(_COMMUNITY_ORIGIN, geos, now + COMMUNITY_OBSERVERS_LIVE_TTL_SECONDS, now)
    return geos


async def _load_uncached_community_reaches(
    hashes_lower: list[str],
) -> dict[str, ParsedReach]:
    """Stats batch query, then per-hash GET for whatever the batch did not answer."""
    from app.services.directory import _community_directory_data

    now = time.time()
    result: dict[str, ParsedReach] = {}
    parsed: dict[str, ParsedReach] | None = None
    try:
        payload = await _community_directory_data(
            "/v1/directory/packets/observations",
            method="POST",
            body={"hashes": hashes_lower},
        )
        parsed = parse_batch_reach(payload) if payload is not None else None
    except HTTPException as exc:
        if exc.status_code == 400:
            raise
        parsed = None

    still_missing: list[str] = []
    for hash_lower in hashes_lower:
        stored = canonical_packet_hash(hash_lower) or hash_lower.upper()
        reach = (parsed or {}).get(stored)
        if reach is None or not _usable_observations(reach.observations):
            reach = (parsed or {}).get(hash_lower.upper(), reach)
        if reach is not None or parsed is not None:
            if reach is None:
                reach = ParsedReach(observations=[])
            _cache_community_reach(hash_lower, reach, now)
            result[hash_lower] = reach
        else:
            still_missing.append(hash_lower)
    if not still_missing:
        return result

    semaphore = asyncio.Semaphore(BATCH_FALLBACK_CONCURRENCY)

    async def one(hash_lower: str) -> tuple[str, ParsedReach]:
        async with semaphore:
            return hash_lower, await _community_packet_observations(hash_lower)

    fetched = await asyncio.gather(*(one(h) for h in still_missing), return_exceptions=True)
    errors: list[HTTPException] = []
    for item in fetched:
        if isinstance(item, HTTPException):
            errors.append(item)
            continue
        if isinstance(item, BaseException):
            raise item
        hash_lower, reach = item
        result[hash_lower] = reach
    if not result and errors:
        raise errors[0]
    for hash_lower in still_missing:
        result.setdefault(hash_lower, ParsedReach(observations=[]))
    return result


async def _community_batch_or_fallback(
    hashes_lower: list[str],
) -> dict[str, ParsedReach]:
    """Serve cache, coalesce in-flight hashes, then hit Stats once per miss."""
    now = time.time()
    result: dict[str, ParsedReach] = {}
    missing: list[str] = []
    for hash_lower in hashes_lower:
        cached = _cached_community_reach(hash_lower, now)
        if cached is not None:
            result[hash_lower] = cached
        else:
            missing.append(hash_lower)
    if not missing:
        return result

    to_fetch, waiting = await _claim_reach_inflight(missing)
    fetched: dict[str, ParsedReach] = {}
    pending = set(to_fetch)
    try:
        if to_fetch:
            fetched = await _load_uncached_community_reaches(to_fetch)
        for hash_lower in to_fetch:
            reach = fetched.get(hash_lower, ParsedReach(observations=[]))
            _cache_community_reach(hash_lower, reach, time.time())
            await _finish_reach_inflight(hash_lower, reach, None)
            pending.discard(hash_lower)
            result[hash_lower] = reach
    except BaseException as exc:
        for hash_lower in list(pending):
            await _finish_reach_inflight(hash_lower, None, exc)
        raise
    for hash_lower, future in waiting:
        result[hash_lower] = await future
    return result


async def get_packet_observer_reach(raw_hash: str) -> PacketObserverReachResponse:
    from app.services.meshloom_community import community_enabled

    hash_lower = validate_packet_hash_param(raw_hash)
    if not await community_enabled():
        return PacketObserverReachResponse(directory_enabled=False)
    fetched = await _community_batch_or_fallback([hash_lower])
    reach = fetched.get(hash_lower, ParsedReach(observations=[]))
    try:
        geos = await _community_observers()
    except HTTPException:
        geos = {}
    entries = _dedup_entries(reach.observations, geos)
    return await _finish_observer_reach(
        hash_lower, entries, directory_enabled=True, sealed=reach.sealed
    )


async def _finish_observer_reach(
    hash_lower: str,
    entries: list[ObserverReachEntry],
    *,
    directory_enabled: bool,
    sealed: bool = False,
) -> PacketObserverReachResponse:
    entries = drop_local_observer(entries)
    message = await MessageRepository.get_by_packet_hash(hash_lower)
    origin_coords = await resolve_origin_coords(message)
    max_hops = None
    for entry in entries:
        if entry.hops is not None:
            max_hops = entry.hops if max_hops is None else max(max_hops, entry.hops)
    max_distance_km = None
    if origin_coords is not None:
        for entry in entries:
            if entry.lat is None or entry.lon is None:
                continue
            distance = haversine_km(origin_coords[0], origin_coords[1], entry.lat, entry.lon)
            max_distance_km = (
                distance if max_distance_km is None else max(max_distance_km, distance)
            )
    origin_lat = origin_coords[0] if origin_coords is not None else None
    origin_lon = origin_coords[1] if origin_coords is not None else None
    return PacketObserverReachResponse(
        directory_enabled=directory_enabled,
        packet_hash=hash_lower.upper(),
        observer_count=len(entries),
        observers=entries,
        max_hops=max_hops,
        max_distance_km=max_distance_km,
        origin_available=origin_coords is not None,
        origin_lat=origin_lat,
        origin_lon=origin_lon,
        sealed=sealed,
    )


async def get_packet_observer_reach_counts(hashes: list[str]) -> PacketObserverReachCountsResponse:
    from app.services.meshloom_community import community_enabled

    if len(hashes) > MESHLOOM_BATCH_MAX:
        raise HTTPException(
            status_code=400,
            detail=f"At most {MESHLOOM_BATCH_MAX} hashes per request",
        )
    normalized: list[str] = []
    seen: set[str] = set()
    for raw in hashes:
        hash_lower = validate_packet_hash_param(raw)
        if hash_lower not in seen:
            seen.add(hash_lower)
            normalized.append(hash_lower)

    if not await community_enabled():
        return PacketObserverReachCountsResponse(directory_enabled=False)
    fetched = await _community_batch_or_fallback(normalized)
    geos: dict[str, ObserverGeo] = {}
    counts: dict[str, int] = {}
    sealed: dict[str, bool] = {}
    for hash_lower in normalized:
        reach = fetched.get(hash_lower, ParsedReach(observations=[]))
        entries = drop_local_observer(_dedup_entries(reach.observations, geos))
        key = hash_lower.upper()
        counts[key] = len(entries)
        sealed[key] = reach.sealed
    return PacketObserverReachCountsResponse(directory_enabled=True, counts=counts, sealed=sealed)
