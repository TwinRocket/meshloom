"""Hop directory client. Meshloom Community (Stats) only — no upstream client here.

Community on: hops, nodes, reach, neighbors and search are answered by the Stats
directory API. Community off: every surface is empty with ``directory_enabled``
false. This process never opens a connection to a CoreScope instance.
"""

from __future__ import annotations

import logging
import re
import time
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Literal

from fastapi import HTTPException

from app.models import (
    AppSettings,
    DirectoryHopHit,
    DirectoryMapNode,
    DirectoryMapNodesResponse,
    DirectoryNeighbor,
    DirectoryNeighborsResponse,
    DirectoryNodeSearchHit,
    DirectoryNodeSearchResponse,
    DirectoryReachNode,
    DirectoryReachObserver,
    DirectoryReachResponse,
    DirectoryResolveHopsResponse,
)
from app.repository import ContactRepository
from app.repository.directory import (
    DIRECTORY_SOURCE_CORESCOPE,
    DirectoryHopCacheRepository,
)

logger = logging.getLogger(__name__)

ALLOWED_HOP_HEX_LENS = frozenset({4, 6})
CACHE_TTL_SECONDS = 86400
NODES_PAGE_SIZE = 500
NODES_MAX_PAGES = 40
NODES_CACHE_TTL_SECONDS = 600
DIRECTORY_NODE_MAX_AGE_SECONDS = 24 * 60 * 60
PUBKEY_HEX_LEN = 64
MAX_HOPS = 64
_HEX_RE = re.compile(r"^[0-9A-Fa-f]+$")
_SKIP_CONFIDENCE = frozenset({"no_match", "conflict", "ambiguous"})

MapNodeRole = Literal["repeater", "room", "client", "companion", "sensor", "observer", "unknown"]
MapNodeSource = Literal["community-db", "corescope", "local"]
MAP_NODE_ROLES: dict[str, MapNodeRole] = {
    "repeater": "repeater",
    "room": "room",
    "client": "client",
    "companion": "companion",
    "sensor": "sensor",
    "observer": "observer",
}
MAP_NODE_SOURCES: frozenset[str] = frozenset({"community-db", "corescope", "local"})
CONTACT_TYPE_TO_MAP_ROLE: dict[int, MapNodeRole] = {
    1: "companion",
    2: "repeater",
    3: "room",
    4: "sensor",
}

_nodes_cache: tuple[float, list[DirectoryMapNode], int | None] | None = None


def validate_hop_prefixes(hops: list[str]) -> list[str]:
    """Normalize 2/3-byte hex prefixes. Reject any 1-byte prefix with 400."""
    if len(hops) > MAX_HOPS:
        raise HTTPException(status_code=400, detail=f"At most {MAX_HOPS} hops per request")
    one_byte: list[str] = []
    normalized: list[str] = []
    seen: set[str] = set()
    for raw in hops:
        prefix = raw.strip().upper()
        if not prefix or not _HEX_RE.fullmatch(prefix):
            raise HTTPException(status_code=400, detail=f"Invalid hop prefix: {raw!r}")
        if len(prefix) == 2:
            one_byte.append(prefix)
            continue
        if len(prefix) not in ALLOWED_HOP_HEX_LENS:
            raise HTTPException(
                status_code=400,
                detail=f"Hop prefix must be 4 or 6 hex chars: {prefix}",
            )
        if prefix not in seen:
            seen.add(prefix)
            normalized.append(prefix)
    if one_byte:
        raise HTTPException(
            status_code=400,
            detail="1-byte hop prefixes are not resolved",
        )
    return normalized


def hash_width_for_prefix(prefix: str) -> int:
    return len(prefix) // 2


@dataclass(frozen=True)
class ParsedDirectoryHop:
    name: str | None
    public_key: str | None = None
    lat: float | None = None
    lon: float | None = None


def _normalize_pubkey(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    key = value.strip().lower()
    if len(key) != PUBKEY_HEX_LEN or not _HEX_RE.fullmatch(key):
        return None
    return key


def _gps_from_resolution(value: dict[str, object]) -> tuple[str | None, float | None, float | None]:
    pubkey = _normalize_pubkey(value.get("pubkey"))
    candidates = value.get("candidates")
    if isinstance(candidates, list) and candidates:
        first = candidates[0]
        if isinstance(first, dict):
            if pubkey is None:
                pubkey = _normalize_pubkey(first.get("pubkey"))
            lat = _as_float(first.get("lat"))
            lon = _as_float(first.get("lon"))
            if lat is not None and lon is not None and _is_valid_map_location(lat, lon):
                return pubkey, lat, lon
    return pubkey, None, None


def parse_directory_resolved_hits(payload: object) -> dict[str, ParsedDirectoryHop | None]:
    """Map prefix → hop (or None for a conclusive no-match). Ignore undocumented keys."""
    if not isinstance(payload, dict):
        return {}
    resolved = payload.get("resolved")
    if not isinstance(resolved, dict):
        return {}
    out: dict[str, ParsedDirectoryHop | None] = {}
    for key, value in resolved.items():
        if not isinstance(key, str) or not isinstance(value, dict):
            continue
        prefix = key.strip().upper()
        if len(prefix) not in ALLOWED_HOP_HEX_LENS or not _HEX_RE.fullmatch(prefix):
            continue
        confidence = value.get("confidence")
        if isinstance(confidence, str) and confidence.lower() in _SKIP_CONFIDENCE:
            out[prefix] = None
            continue
        conflicts = value.get("conflicts")
        if isinstance(conflicts, list) and conflicts:
            out[prefix] = None
            continue
        name = value.get("name")
        label = name.strip() if isinstance(name, str) and name.strip() else None
        pubkey, lat, lon = _gps_from_resolution(value)
        out[prefix] = ParsedDirectoryHop(name=label, public_key=pubkey, lat=lat, lon=lon)
    return out


def parse_directory_resolved(payload: object) -> dict[str, str | None]:
    """Map prefix → name (or None for a conclusive no-match). Ignore undocumented keys."""
    return {
        prefix: (hit.name if hit else None)
        for prefix, hit in parse_directory_resolved_hits(payload).items()
    }


def _hit_from_cache(row: object, hash_width: int) -> DirectoryHopHit | None:
    name = getattr(row, "name", None)
    source = getattr(row, "source", None)
    if not name or source != DIRECTORY_SOURCE_CORESCOPE:
        return None
    return DirectoryHopHit(
        name=name,
        source="corescope",
        hash_width=hash_width,
        public_key=getattr(row, "public_key", None),
        lat=getattr(row, "lat", None),
        lon=getattr(row, "lon", None),
    )


async def directory_is_available() -> bool:
    """True only when Meshloom Community is on. There is no manual origin."""
    from app.services.meshloom_community import community_enabled

    return await community_enabled()


async def annotate_directory_available(settings: AppSettings) -> AppSettings:
    return settings.model_copy(update={"directory_available": await directory_is_available()})


async def _community_directory_data(
    path: str,
    *,
    params: dict[str, str | int] | None = None,
    method: str = "GET",
    body: dict[str, object] | None = None,
) -> object | None:
    """Stats directory payload when Community is on, else None (directory off)."""
    from app.services.meshloom_community import (
        community_enabled,
        stats_directory_get,
        stats_directory_post,
    )

    if not await community_enabled():
        return None
    if method == "POST":
        return await stats_directory_post(path, body or {})
    return await stats_directory_get(path, params=params)


def _hits_from_directory(
    prefixes: list[str],
    fetched: dict[str, ParsedDirectoryHop | None],
) -> dict[str, DirectoryHopHit]:
    resolved: dict[str, DirectoryHopHit] = {}
    for prefix in prefixes:
        parsed = fetched.get(prefix)
        if parsed and parsed.name:
            resolved[prefix] = DirectoryHopHit(
                name=parsed.name,
                source="corescope",
                hash_width=hash_width_for_prefix(prefix),
                public_key=parsed.public_key,
                lat=parsed.lat,
                lon=parsed.lon,
            )
    return resolved


async def _write_hop_cache(
    prefixes: list[str],
    fetched: dict[str, ParsedDirectoryHop | None],
) -> None:
    expires_at = int(time.time()) + CACHE_TTL_SECONDS
    for prefix in prefixes:
        if prefix not in fetched:
            continue
        parsed = fetched[prefix]
        await DirectoryHopCacheRepository.upsert(
            prefix,
            hash_width_for_prefix(prefix),
            parsed.name if parsed else None,
            DIRECTORY_SOURCE_CORESCOPE,
            expires_at,
            public_key=parsed.public_key if parsed else None,
            lat=parsed.lat if parsed else None,
            lon=parsed.lon if parsed else None,
        )


async def resolve_directory_hops(hops: list[str]) -> DirectoryResolveHopsResponse:
    prefixes = validate_hop_prefixes(hops)
    keys = [(prefix, hash_width_for_prefix(prefix)) for prefix in prefixes]
    cached = await DirectoryHopCacheRepository.get_many(keys)
    resolved: dict[str, DirectoryHopHit] = {}
    misses: list[str] = []
    for prefix, hash_width in keys:
        row = cached.get((prefix, hash_width))
        if row is None or row.expired:
            misses.append(prefix)
            continue
        hit = _hit_from_cache(row, hash_width)
        if hit:
            resolved[prefix] = hit

    if not misses:
        return DirectoryResolveHopsResponse(resolved=resolved)

    stats_data = await _community_directory_data(
        "/v1/directory/resolve-hops",
        params={"hops": ",".join(misses)},
    )
    if stats_data is None:
        return DirectoryResolveHopsResponse(resolved=resolved)

    fetched = parse_directory_resolved_hits(stats_data)
    await _write_hop_cache(misses, fetched)
    resolved.update(_hits_from_directory(misses, fetched))
    return DirectoryResolveHopsResponse(resolved=resolved)


async def reset_directory_cache() -> int:
    reset_directory_nodes_cache()
    from app.services.observer_reach import reset_observer_reach_cache

    reset_observer_reach_cache()
    return await DirectoryHopCacheRepository.wipe()


def _is_valid_map_location(lat: float, lon: float) -> bool:
    if lat < -90 or lat > 90 or lon < -180 or lon > 180:
        return False
    return not (lat == 0.0 and lon == 0.0)


def _as_float(value: object) -> float | None:
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, int | float):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value.strip())
        except ValueError:
            return None
    return None


def _normalize_map_role(role: object) -> MapNodeRole:
    if isinstance(role, str):
        return MAP_NODE_ROLES.get(role.strip().lower(), "unknown")
    return "unknown"


def _normalize_map_source(source: object, default: MapNodeSource) -> MapNodeSource:
    if isinstance(source, str):
        raw = source.strip().lower()
        if raw in {"community", "community_db", "communitydb"}:
            return "community-db"
        if raw in MAP_NODE_SOURCES:
            return raw  # type: ignore[return-value]
    return default


def _as_unix_timestamp(value: object) -> int | None:
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, int):
        return value if value > 0 else None
    if isinstance(value, float):
        if value != value:
            return None
        parsed = int(value)
        return parsed if parsed > 0 else None
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return None
        try:
            parsed = int(float(text))
            return parsed if parsed > 0 else None
        except ValueError:
            pass
        iso = text.replace("Z", "+00:00")
        try:
            dt = datetime.fromisoformat(iso)
        except ValueError:
            return None
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=UTC)
        parsed = int(dt.timestamp())
        return parsed if parsed > 0 else None
    return None


def _parse_map_last_seen(item: Mapping[str, object]) -> int | None:
    last_seen = _as_unix_timestamp(item.get("last_seen_at"))
    if last_seen is not None:
        return last_seen
    return _as_unix_timestamp(item.get("last_seen"))


def parse_directory_map_nodes(
    payload: object,
    *,
    default_source: MapNodeSource = "community-db",
) -> tuple[list[DirectoryMapNode], int | None]:
    """Keep documented Node fields: public_key, name, role, lat, lon, source, last_seen."""
    if not isinstance(payload, dict):
        return [], None
    raw_nodes = payload.get("nodes")
    if not isinstance(raw_nodes, list):
        return [], None
    total = payload.get("total")
    total_n = total if isinstance(total, int) and total >= 0 else None
    nodes: list[DirectoryMapNode] = []
    seen: set[str] = set()
    for item in raw_nodes:
        if not isinstance(item, dict):
            continue
        public_key = item.get("public_key")
        if not isinstance(public_key, str):
            continue
        key = public_key.strip().lower()
        if len(key) != PUBKEY_HEX_LEN or not _HEX_RE.fullmatch(key) or key in seen:
            continue
        lat = _as_float(item.get("lat"))
        lon = _as_float(item.get("lon"))
        if lat is None or lon is None or not _is_valid_map_location(lat, lon):
            continue
        name = item.get("name")
        label = name.strip() if isinstance(name, str) and name.strip() else key[:12]
        seen.add(key)
        nodes.append(
            DirectoryMapNode(
                public_key=key,
                name=label,
                role=_normalize_map_role(item.get("role")),
                lat=lat,
                lon=lon,
                source=_normalize_map_source(item.get("source"), default_source),
                last_seen=_parse_map_last_seen(item),
            )
        )
    return nodes, total_n


def reset_directory_nodes_cache() -> None:
    global _nodes_cache
    _nodes_cache = None


def _cached_map_response(now: float) -> DirectoryMapNodesResponse | None:
    if _nodes_cache is None or _nodes_cache[0] <= now:
        return None
    return DirectoryMapNodesResponse(nodes=list(_nodes_cache[1]), total=_nodes_cache[2])


async def _collect_map_node_pages(
    fetch_page: Callable[[int], Awaitable[tuple[list[DirectoryMapNode], int | None]]],
) -> tuple[list[DirectoryMapNode], int | None]:
    merged: dict[str, DirectoryMapNode] = {}
    offset = 0
    total_out: int | None = None
    for _ in range(NODES_MAX_PAGES):
        page, total = await fetch_page(offset)
        if total is not None:
            total_out = total
        if not page and offset == 0:
            break
        new_on_page = 0
        for node in page:
            if node.public_key not in merged:
                merged[node.public_key] = node
                new_on_page += 1
        offset += NODES_PAGE_SIZE
        if total_out is not None:
            # A page is short because nodes without GPS were dropped, not because
            # the directory ended. Only the upstream total can say where it ends.
            if offset >= total_out:
                break
            continue
        if len(page) < NODES_PAGE_SIZE or new_on_page == 0:
            break
    return list(merged.values()), total_out


async def _fetch_community_nodes_page(
    offset: int,
) -> tuple[list[DirectoryMapNode], int | None] | None:
    stats_data = await _community_directory_data(
        "/v1/directory/nodes",
        params={"limit": NODES_PAGE_SIZE, "offset": offset},
    )
    if stats_data is None:
        return None
    return parse_directory_map_nodes(stats_data)


async def list_local_gps_map_nodes() -> list[DirectoryMapNode]:
    """Local RF contacts with a usable GPS pin, tagged source=local."""
    contacts = await ContactRepository.list_with_map_location()
    nodes: list[DirectoryMapNode] = []
    seen: set[str] = set()
    for contact in contacts:
        key = contact.public_key.strip().lower()
        if len(key) != PUBKEY_HEX_LEN or not _HEX_RE.fullmatch(key) or key in seen:
            continue
        lat = contact.lat
        lon = contact.lon
        if lat is None or lon is None or not _is_valid_map_location(lat, lon):
            continue
        raw_name = contact.name.strip() if isinstance(contact.name, str) else ""
        name = raw_name or key[:12]
        seen.add(key)
        nodes.append(
            DirectoryMapNode(
                public_key=key,
                name=name,
                role=CONTACT_TYPE_TO_MAP_ROLE.get(contact.type, "unknown"),
                lat=lat,
                lon=lon,
                source="local",
                last_seen=contact.last_seen,
            )
        )
    return nodes


def merge_directory_and_local_nodes(
    remote: list[DirectoryMapNode],
    local: list[DirectoryMapNode],
) -> list[DirectoryMapNode]:
    """Dedupe by public_key. Directory nodes win over local."""
    remote_keys = {node.public_key for node in remote}
    extra = [node for node in local if node.public_key not in remote_keys]
    return list(remote) + extra


def drop_observer_nodes(nodes: list[DirectoryMapNode]) -> list[DirectoryMapNode]:
    """Filter observer catalog rows. Live pins keep them for hop/origin geometry."""
    return [node for node in nodes if node.role != "observer"]


def drop_stale_remote_map_nodes(
    nodes: list[DirectoryMapNode],
    *,
    now: float | None = None,
    max_age_seconds: int = DIRECTORY_NODE_MAX_AGE_SECONDS,
) -> list[DirectoryMapNode]:
    """Drop remote pins whose last_seen is older than max_age. Local pins stay."""
    cutoff = (time.time() if now is None else now) - max_age_seconds
    kept: list[DirectoryMapNode] = []
    for node in nodes:
        if node.source == "local":
            kept.append(node)
            continue
        if node.last_seen is None or node.last_seen >= cutoff:
            kept.append(node)
    return kept


async def _list_remote_directory_map_nodes() -> DirectoryMapNodesResponse:
    """GPS pins from Community. Empty when Community is off."""
    global _nodes_cache
    now = time.time()
    cached = _cached_map_response(now)
    if cached is not None:
        return cached

    first = await _fetch_community_nodes_page(0)
    if first is None:
        return DirectoryMapNodesResponse()

    async def _community_page(offset: int) -> tuple[list[DirectoryMapNode], int | None]:
        if offset == 0:
            return first
        page = await _fetch_community_nodes_page(offset)
        return page if page is not None else ([], None)

    nodes, total = await _collect_map_node_pages(_community_page)
    _nodes_cache = (now + NODES_CACHE_TTL_SECONDS, nodes, total)
    return DirectoryMapNodesResponse(nodes=nodes, total=total)


async def list_directory_map_nodes(
    *,
    include_local: bool = False,
    include_observers: bool = True,
    max_remote_age_seconds: int | None = None,
) -> DirectoryMapNodesResponse:
    """GPS pins for directory roles. Local contacts only when include_local."""
    remote = await _list_remote_directory_map_nodes()
    nodes = remote.nodes if include_observers else drop_observer_nodes(remote.nodes)
    total = remote.total
    dropped = len(remote.nodes) - len(nodes)
    if max_remote_age_seconds is not None:
        fresh = drop_stale_remote_map_nodes(nodes, max_age_seconds=max_remote_age_seconds)
        dropped += len(nodes) - len(fresh)
        nodes = fresh
    if dropped and total is not None:
        total = max(0, total - dropped)
    if include_local:
        local = await list_local_gps_map_nodes()
        merged = merge_directory_and_local_nodes(nodes, local)
        extra = len(merged) - len(nodes)
        if extra:
            total = (total if total is not None else len(nodes)) + extra
        nodes = merged
    return DirectoryMapNodesResponse(nodes=nodes, total=total)


def is_valid_map_location(lat: float, lon: float) -> bool:
    return _is_valid_map_location(lat, lon)


def validate_directory_pubkey(pubkey: str) -> str:
    key = pubkey.strip().lower()
    if len(key) != PUBKEY_HEX_LEN or not _HEX_RE.fullmatch(key):
        raise HTTPException(status_code=400, detail="Public key must be 64 hex characters")
    return key


def parse_directory_reach(payload: object, pubkey: str) -> DirectoryReachResponse:
    """Keep documented reach fields: node GPS + 0-hop direct_observers."""
    if not isinstance(payload, dict):
        return DirectoryReachResponse(directory_enabled=True)
    node_payload = payload.get("node")
    node: DirectoryReachNode | None = None
    if isinstance(node_payload, dict):
        key = _normalize_pubkey(node_payload.get("pubkey") or node_payload.get("public_key"))
        name = node_payload.get("name")
        role = node_payload.get("role")
        lat = _as_float(node_payload.get("lat"))
        lon = _as_float(node_payload.get("lon"))
        if lat is not None and lon is not None and not _is_valid_map_location(lat, lon):
            lat, lon = None, None
        node = DirectoryReachNode(
            public_key=key or pubkey,
            name=name.strip() if isinstance(name, str) and name.strip() else None,
            role=role.strip() if isinstance(role, str) and role.strip() else None,
            lat=lat,
            lon=lon,
        )
    observers: list[DirectoryReachObserver] = []
    seen: set[str] = set()
    raw_observers = payload.get("direct_observers")
    if isinstance(raw_observers, list):
        for item in raw_observers:
            if not isinstance(item, dict):
                continue
            key = _normalize_pubkey(item.get("pubkey") or item.get("public_key"))
            if not key or key in seen:
                continue
            lat = _as_float(item.get("lat"))
            lon = _as_float(item.get("lon"))
            if lat is None or lon is None or not _is_valid_map_location(lat, lon):
                continue
            name = item.get("name")
            label = name.strip() if isinstance(name, str) and name.strip() else key[:12]
            count = item.get("count")
            snr = _as_float(item.get("avg_snr"))
            seen.add(key)
            observers.append(
                DirectoryReachObserver(
                    public_key=key,
                    name=label,
                    count=count if isinstance(count, int) and count >= 0 else 0,
                    avg_snr=snr,
                    lat=lat,
                    lon=lon,
                )
            )
    return DirectoryReachResponse(node=node, observers=observers, directory_enabled=True)


def parse_directory_neighbors(payload: object) -> DirectoryNeighborsResponse:
    if not isinstance(payload, dict):
        return DirectoryNeighborsResponse(directory_enabled=True)
    raw = payload.get("neighbors")
    if not isinstance(raw, list):
        return DirectoryNeighborsResponse(directory_enabled=True)
    neighbors: list[DirectoryNeighbor] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        if item.get("unresolved") is True:
            continue
        ambiguous = bool(item.get("ambiguous"))
        key = _normalize_pubkey(item.get("pubkey") or item.get("public_key"))
        prefix_raw = item.get("prefix")
        prefix = (
            prefix_raw.strip().upper()
            if isinstance(prefix_raw, str) and _HEX_RE.fullmatch(prefix_raw.strip())
            else None
        )
        name = item.get("name")
        lat = _as_float(item.get("lat"))
        lon = _as_float(item.get("lon"))
        if lat is not None and lon is not None and not _is_valid_map_location(lat, lon):
            lat, lon = None, None
        count = item.get("count")
        score = _as_float(item.get("score"))
        snr = _as_float(item.get("avg_snr"))
        neighbors.append(
            DirectoryNeighbor(
                public_key=key,
                prefix=prefix,
                name=name.strip() if isinstance(name, str) and name.strip() else None,
                count=count if isinstance(count, int) and count >= 0 else 0,
                score=score,
                avg_snr=snr,
                lat=lat,
                lon=lon,
                ambiguous=ambiguous,
            )
        )
    return DirectoryNeighborsResponse(neighbors=neighbors, directory_enabled=True)


def parse_directory_node_search(payload: object) -> DirectoryNodeSearchResponse:
    if not isinstance(payload, dict):
        return DirectoryNodeSearchResponse(directory_enabled=True)
    raw = payload.get("nodes")
    if not isinstance(raw, list):
        return DirectoryNodeSearchResponse(directory_enabled=True)
    nodes: list[DirectoryNodeSearchHit] = []
    seen: set[str] = set()
    for item in raw:
        if not isinstance(item, dict):
            continue
        key = _normalize_pubkey(item.get("public_key") or item.get("pubkey"))
        if not key or key in seen:
            continue
        lat = _as_float(item.get("lat"))
        lon = _as_float(item.get("lon"))
        if lat is not None and lon is not None and not _is_valid_map_location(lat, lon):
            lat, lon = None, None
        name = item.get("name")
        role = item.get("role")
        last_seen = item.get("last_seen")
        seen.add(key)
        nodes.append(
            DirectoryNodeSearchHit(
                public_key=key,
                name=name.strip() if isinstance(name, str) and name.strip() else None,
                role=role.strip() if isinstance(role, str) and role.strip() else None,
                lat=lat,
                lon=lon,
                last_seen=last_seen if isinstance(last_seen, str) else None,
            )
        )
    return DirectoryNodeSearchResponse(nodes=nodes, directory_enabled=True)


async def get_directory_node_reach(pubkey: str) -> DirectoryReachResponse:
    key = validate_directory_pubkey(pubkey)
    stats_data = await _community_directory_data(f"/v1/directory/nodes/{key}/reach")
    if stats_data is None:
        return DirectoryReachResponse()
    return parse_directory_reach(stats_data, key)


async def get_directory_node_neighbors(pubkey: str) -> DirectoryNeighborsResponse:
    key = validate_directory_pubkey(pubkey)
    stats_data = await _community_directory_data(f"/v1/directory/nodes/{key}/neighbors")
    if stats_data is None:
        return DirectoryNeighborsResponse()
    return parse_directory_neighbors(stats_data)


async def search_directory_nodes(query: str) -> DirectoryNodeSearchResponse:
    q = query.strip()
    if not q:
        raise HTTPException(status_code=400, detail="Search query is required")
    stats_data = await _community_directory_data("/v1/directory/nodes/search", params={"q": q})
    if stats_data is None:
        return DirectoryNodeSearchResponse()
    return parse_directory_node_search(stats_data)
