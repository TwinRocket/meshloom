"""RF locate: conservative 0-hop coverage disks, never an inferred lat/lon pin."""

from __future__ import annotations

import re
from typing import Literal

from fastapi import HTTPException

from app.models import (
    CONTACT_TYPE_REPEATER,
    Contact,
    ContactAdvertPath,
    LocateAmbiguousDetail,
    LocateAnchor,
    LocateCandidate,
    LocateDeclaredGps,
    LocateIdentity,
    LocateResponse,
    LocateUnresolvedHop,
    MessagePath,
)
from app.repository import (
    AmbiguousPublicKeyPrefixError,
    ContactAdvertPathRepository,
    ContactRepository,
    MessageRepository,
)
from app.services.directory import directory_is_available, is_valid_map_location
from app.services.radio_runtime import radio_runtime

DEFAULT_RADIUS_KM = 20.0
MIN_RADIUS_KM = 5.0
MAX_RADIUS_KM = 50.0
_HEX_RE = re.compile(r"^[0-9A-Fa-f]+$")
PUBKEY_HEX_LEN = 64
LocateSource = Literal["local", "corescope", "mixte"]
LocateEmptyReason = Literal["insufficient_identity", "directory_off", "no_anchors"]


def clamp_radius_km(radius_km: float | None) -> float:
    if radius_km is None:
        return DEFAULT_RADIUS_KM
    return max(MIN_RADIUS_KM, min(MAX_RADIUS_KM, radius_km))


def _candidate_from_contact(contact: Contact) -> LocateCandidate:
    return LocateCandidate(
        public_key=contact.public_key,
        name=contact.name,
        type=contact.type,
        last_seen=contact.last_seen,
    )


def _raise_ambiguous(query: str, candidates: list[LocateCandidate]) -> None:
    detail = LocateAmbiguousDetail(query=query, candidates=candidates)
    raise HTTPException(status_code=409, detail=detail.model_dump())


def _local_radio_gps() -> tuple[float | None, float | None, str | None, str | None]:
    mc = getattr(radio_runtime, "meshcore", None)
    info = getattr(mc, "self_info", None) if mc is not None else None
    if not isinstance(info, dict):
        return None, None, None, None
    lat = info.get("adv_lat")
    lon = info.get("adv_lon")
    try:
        lat_f = float(lat) if lat is not None else None
        lon_f = float(lon) if lon is not None else None
    except (TypeError, ValueError):
        lat_f, lon_f = None, None
    if lat_f is None or lon_f is None or not is_valid_map_location(lat_f, lon_f):
        lat_f, lon_f = None, None
    name = info.get("name")
    pubkey = info.get("public_key")
    label = name.strip() if isinstance(name, str) and name.strip() else None
    key = pubkey.strip().lower() if isinstance(pubkey, str) and pubkey.strip() else None
    return lat_f, lon_f, label, key


def _message_path_is_zero_hop(path: MessagePath) -> bool:
    if path.path_len == 0:
        return True
    if path.path_len is None:
        return not (path.path or "").strip()
    return False


def _local_zero_hop(
    advert_paths: list[ContactAdvertPath],
    message_paths: list[MessagePath],
    radius_km: float,
    radio_lat: float | None,
    radio_lon: float | None,
    radio_name: str | None,
    radio_key: str | None,
) -> tuple[list[LocateAnchor], bool]:
    heard_direct = any(path.path_len == 0 for path in advert_paths)
    best_snr: float | None = None
    last_seen: int | None = None
    heard_count = 0
    for path in advert_paths:
        if path.path_len != 0:
            continue
        heard_count += path.heard_count
        last_seen = path.last_seen if last_seen is None else max(last_seen, path.last_seen)

    for path in message_paths:
        if not _message_path_is_zero_hop(path):
            continue
        heard_direct = True
        heard_count += 1
        last_seen = path.received_at if last_seen is None else max(last_seen, path.received_at)
        if path.snr is not None:
            best_snr = path.snr if best_snr is None else max(best_snr, path.snr)

    if not heard_direct or radio_lat is None or radio_lon is None:
        return [], heard_direct

    return (
        [
            LocateAnchor(
                kind="local_0hop",
                source="local",
                name=radio_name or "Local radio",
                public_key=radio_key,
                lat=radio_lat,
                lon=radio_lon,
                radius_km=radius_km,
                snr=best_snr,
                heard_count=heard_count or 1,
                last_seen=last_seen,
            )
        ],
        True,
    )


async def _first_hop_anchors(
    advert_paths: list[ContactAdvertPath], radius_km: float
) -> tuple[list[LocateAnchor], list[LocateUnresolvedHop]]:
    hop_stats: dict[str, dict[str, int]] = {}
    for path in advert_paths:
        prefix = path.next_hop
        if not prefix:
            continue
        stats = hop_stats.setdefault(
            prefix.upper(),
            {"heard_count": 0, "last_seen": path.last_seen},
        )
        stats["heard_count"] += path.heard_count
        stats["last_seen"] = max(stats["last_seen"], path.last_seen)

    anchors: list[LocateAnchor] = []
    unresolved: list[LocateUnresolvedHop] = []

    for prefix, stats in hop_stats.items():
        if len(prefix) == 2:
            unresolved.append(LocateUnresolvedHop(prefix=prefix, reason="one_byte"))
            continue
        try:
            contact = await ContactRepository.get_by_key_or_prefix(prefix)
        except AmbiguousPublicKeyPrefixError as err:
            matches = await ContactRepository.list_by_key_prefix(err.prefix, limit=8)
            unresolved.append(
                LocateUnresolvedHop(
                    prefix=prefix,
                    reason="ambiguous",
                    candidates=[_candidate_from_contact(item) for item in matches],
                )
            )
            continue
        if contact is None:
            unresolved.append(LocateUnresolvedHop(prefix=prefix, reason="unmatched"))
            continue
        if (
            contact.lat is None
            or contact.lon is None
            or not is_valid_map_location(contact.lat, contact.lon)
        ):
            unresolved.append(LocateUnresolvedHop(prefix=prefix, reason="no_gps"))
            continue
        anchors.append(
            LocateAnchor(
                kind="first_hop",
                source="local",
                name=contact.name or contact.public_key[:12],
                public_key=contact.public_key,
                hop_prefix=prefix.lower(),
                lat=contact.lat,
                lon=contact.lon,
                radius_km=radius_km,
                heard_count=stats["heard_count"],
                last_seen=stats["last_seen"],
                calibratable=contact.type == CONTACT_TYPE_REPEATER,
            )
        )

    return anchors, unresolved


def _identity_from_contact(contact: Contact, *, inferred: bool = False) -> LocateIdentity:
    return LocateIdentity(
        public_key=contact.public_key,
        name=contact.name,
        contact_type=contact.type,
        inferred=inferred,
        last_seen=contact.last_seen,
    )


async def _resolve_hex_query(query: str) -> LocateIdentity:
    lowered = query.lower()
    if len(lowered) == 2:
        raise HTTPException(status_code=400, detail="1-byte hop prefixes are not resolved")
    if len(lowered) == PUBKEY_HEX_LEN:
        contact = await ContactRepository.get_by_key(lowered)
        if contact:
            return _identity_from_contact(contact)
        return LocateIdentity(public_key=lowered, inferred=False)

    try:
        contact = await ContactRepository.get_by_key_or_prefix(lowered)
    except AmbiguousPublicKeyPrefixError as err:
        matches = await ContactRepository.list_by_key_prefix(err.prefix, limit=8)
        _raise_ambiguous(query, [_candidate_from_contact(item) for item in matches])

    if contact:
        return _identity_from_contact(contact)

    raise HTTPException(status_code=404, detail="No unique identity for this query")


async def _resolve_name_query(query: str) -> LocateIdentity:
    named = await ContactRepository.get_by_name(query)
    if len(named) == 1:
        return _identity_from_contact(named[0])
    if len(named) > 1:
        _raise_ambiguous(query, [_candidate_from_contact(item) for item in named])
    raise HTTPException(status_code=404, detail="Identity is insufficient to locate")


async def _resolve_identity(query: str) -> LocateIdentity:
    text = query.strip()
    if not text:
        raise HTTPException(status_code=400, detail="Query is required")
    if _HEX_RE.fullmatch(text):
        return await _resolve_hex_query(text)
    return await _resolve_name_query(text)


def _source_badge(anchors: list[LocateAnchor]) -> LocateSource | None:
    if not anchors:
        return None
    sources = {anchor.source for anchor in anchors}
    if sources == {"local"}:
        return "local"
    if sources == {"corescope"}:
        return "corescope"
    return "mixte"


async def locate_query(query: str, radius_km: float | None = None) -> LocateResponse:
    radius = clamp_radius_km(radius_km)
    directory_enabled = await directory_is_available()

    try:
        identity = await _resolve_identity(query)
    except HTTPException as exc:
        if exc.status_code == 404:
            return LocateResponse(
                query=query.strip(),
                directory_enabled=directory_enabled,
                default_radius_km=radius,
                empty_reason="insufficient_identity",
            )
        raise

    radio_lat, radio_lon, radio_name, radio_key = _local_radio_gps()
    advert_paths = await ContactAdvertPathRepository.get_recent_for_contact(identity.public_key)
    message_paths = await MessageRepository.list_recent_paths_for_contact(identity.public_key)
    local_anchors, heard_direct = _local_zero_hop(
        advert_paths,
        message_paths,
        radius,
        radio_lat,
        radio_lon,
        radio_name,
        radio_key,
    )
    first_hop_anchors, unresolved = await _first_hop_anchors(advert_paths, radius)

    declared_gps: LocateDeclaredGps | None = None
    contact = await ContactRepository.get_by_key(identity.public_key)
    if (
        contact
        and contact.lat is not None
        and contact.lon is not None
        and is_valid_map_location(contact.lat, contact.lon)
    ):
        declared_gps = LocateDeclaredGps(lat=contact.lat, lon=contact.lon, source="advert")

    anchors = _dedupe_anchors([*local_anchors, *first_hop_anchors])
    source = _source_badge(anchors)
    empty_reason_value: LocateEmptyReason | None = None
    if not anchors:
        if not directory_enabled and not heard_direct:
            empty_reason_value = "directory_off"
        else:
            empty_reason_value = "no_anchors"

    return LocateResponse(
        query=query.strip(),
        identity=identity,
        source=source,
        directory_enabled=directory_enabled,
        default_radius_km=radius,
        anchors=anchors,
        unresolved_hops=unresolved,
        declared_gps=declared_gps,
        heard_locally_0hop=heard_direct,
        radio_has_gps=radio_lat is not None and radio_lon is not None,
        empty_reason=empty_reason_value,
    )


def _dedupe_anchors(anchors: list[LocateAnchor]) -> list[LocateAnchor]:
    seen: set[tuple[str, str | None, float, float]] = set()
    out: list[LocateAnchor] = []
    for anchor in anchors:
        key = (anchor.kind, anchor.public_key, round(anchor.lat, 5), round(anchor.lon, 5))
        if key in seen:
            continue
        seen.add(key)
        out.append(anchor)
    return out
