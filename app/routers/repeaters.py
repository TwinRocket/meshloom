import asyncio
import logging
import time
from dataclasses import dataclass
from typing import Literal

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app.models import (
    CONTACT_TYPE_REPEATER,
    AclEntry,
    CommandRequest,
    CommandResponse,
    Contact,
    LppSensor,
    NeighborInfo,
    RepeaterAclResponse,
    RepeaterAdvertIntervalsResponse,
    RepeaterLoginRequest,
    RepeaterLoginResponse,
    RepeaterLppTelemetryResponse,
    RepeaterNeighborsResponse,
    RepeaterNodeInfoResponse,
    RepeaterOwnerInfoResponse,
    RepeaterRadioSettingsResponse,
    RepeaterRegionEntry,
    RepeaterRegionsResponse,
    RepeaterStatusResponse,
    TelemetryHistoryEntry,
)
from app.repository import (
    ContactRepository,
    RepeaterPaneCacheRepository,
    RepeaterTelemetryRepository,
)
from app.routers.server_control import (
    batch_cli_fetch,
    fetch_repeater_owner_info_binary,
    prepare_authenticated_contact_connection,
    require_server_capable_contact,
    send_contact_cli_command,
)
from app.services.contact_access import ensure_on_radio, resolve_contact_or_404
from app.services.radio_runtime import radio_runtime as radio_manager
from app.telemetry_alerts import (
    is_usable_status,
    lpp_sensors_from_radio,
    note_telemetry_poll,
    repeater_status_response_kwargs,
    status_snapshot_from_radio,
)

logger = logging.getLogger(__name__)

# ACL permission level names
ACL_PERMISSION_NAMES = {
    0: "Guest",
    1: "Read-only",
    2: "Read-write",
    3: "Admin",
}
router = APIRouter(prefix="/contacts", tags=["repeaters"])


async def _cache_pane(public_key: str, pane: str, response: BaseModel) -> BaseModel:
    """Keep a pane's answer so the dashboard can reopen on it without the radio.

    Called at the point of return rather than through a decorator: the panes each
    build their own response and this repo prefers the call you can see.
    """
    try:
        await RepeaterPaneCacheRepository.put(public_key, pane, response.model_dump())
    except Exception as exc:  # a cache miss must never fail the request
        logger.debug("Could not cache pane %s for %s: %s", pane, public_key, exc)
    return response


REPEATER_LOGIN_RESPONSE_TIMEOUT_SECONDS = 5.0


async def prepare_repeater_connection(mc, contact: Contact, password: str) -> RepeaterLoginResponse:
    return await prepare_authenticated_contact_connection(
        mc,
        contact,
        password,
        label="repeater",
        response_timeout=REPEATER_LOGIN_RESPONSE_TIMEOUT_SECONDS,
    )


def _require_repeater(contact: Contact) -> None:
    """Raise 400 if contact is not a repeater."""
    if contact.type != CONTACT_TYPE_REPEATER:
        raise HTTPException(
            status_code=400,
            detail=f"Contact is not a repeater (type={contact.type}, expected {CONTACT_TYPE_REPEATER})",
        )


# ---------------------------------------------------------------------------
# Granular repeater endpoints — one attempt, no server-side retries.
# Frontend manages retry logic for better UX control.
# ---------------------------------------------------------------------------


@router.post("/{public_key}/repeater/login", response_model=RepeaterLoginResponse)
async def repeater_login(public_key: str, request: RepeaterLoginRequest) -> RepeaterLoginResponse:
    """Attempt repeater login and report whether auth was confirmed."""
    radio_manager.require_connected()
    contact = await resolve_contact_or_404(public_key)
    _require_repeater(contact)

    async with radio_manager.radio_operation(
        "repeater_login",
        pause_polling=True,
        suspend_auto_fetch=True,
    ) as mc:
        return await prepare_repeater_connection(mc, contact, request.password)


@router.get("/{public_key}/repeater/cache")
async def repeater_pane_cache(public_key: str) -> dict[str, dict]:
    """Last known answer per pane, read from the database.

    Read-only and radio-free, like the telemetry-history endpoint. Every entry
    carries its own `fetched_at` so a client can show how old a value is rather
    than presenting it as current.
    """
    contact = await resolve_contact_or_404(public_key)
    _require_repeater(contact)
    return await RepeaterPaneCacheRepository.get_all(contact.public_key)


@router.post("/{public_key}/repeater/status", response_model=RepeaterStatusResponse)
async def repeater_status(public_key: str) -> RepeaterStatusResponse:
    """Fetch status telemetry from a repeater (single attempt, 10s timeout)."""
    radio_manager.require_connected()
    contact = await resolve_contact_or_404(public_key)
    _require_repeater(contact)

    lpp_raw = None
    lpp_ok = False
    async with radio_manager.radio_operation(
        "repeater_status", pause_polling=True, suspend_auto_fetch=True
    ) as mc:
        # Ensure contact is on radio for routing
        await ensure_on_radio(mc, contact)

        status = await mc.commands.req_status_sync(contact.public_key, timeout=10, min_timeout=5)

        # Best-effort LPP sensor fetch while we still hold the lock
        if is_usable_status(status):
            try:
                lpp_raw = await mc.commands.req_telemetry_sync(
                    contact.public_key, timeout=10, min_timeout=5
                )
                lpp_ok = isinstance(lpp_raw, list)
            except Exception as e:
                logger.debug("LPP sensor fetch failed for %s (non-fatal): %s", public_key[:12], e)

    if not is_usable_status(status):
        await note_telemetry_poll(
            public_key=contact.public_key,
            name=contact.name or "",
            outcome="miss",
        )
        raise HTTPException(status_code=422, detail="No status response from repeater")

    snapshot = status_snapshot_from_radio(status)
    response = RepeaterStatusResponse(**repeater_status_response_kwargs(snapshot))

    # Record to telemetry history as a JSON blob (best-effort)
    now = int(time.time())
    status_dict = dict(snapshot)

    if lpp_ok:
        status_dict["lpp_sensors"] = lpp_sensors_from_radio(lpp_raw)

    await note_telemetry_poll(
        public_key=contact.public_key,
        name=contact.name or "",
        outcome="success",
        snapshot=status_dict,
        allow_gps_lost=lpp_ok,
    )

    try:
        await RepeaterTelemetryRepository.record(
            public_key=contact.public_key,
            timestamp=now,
            data=status_dict,
        )

        from app.websocket import dispatch_telemetry_event

        dispatch_telemetry_event(
            {
                "public_key": contact.public_key,
                "name": contact.name or contact.public_key[:12],
                "timestamp": now,
                **status_dict,
            }
        )
    except Exception as e:
        logger.warning("Failed to record telemetry history: %s", e)

    # Fetch recent history and embed in response
    try:
        since = now - 30 * 86400  # last 30 days
        rows = await RepeaterTelemetryRepository.get_history(contact.public_key, since)
        response.telemetry_history = [TelemetryHistoryEntry(**row) for row in rows]
    except Exception as e:
        logger.warning("Failed to fetch telemetry history: %s", e)

    return response


@router.get(
    "/{public_key}/repeater/telemetry-history",
    response_model=list[TelemetryHistoryEntry],
)
async def repeater_telemetry_history(public_key: str) -> list[TelemetryHistoryEntry]:
    """Return stored telemetry history for a repeater (read-only, no radio access)."""
    contact = await resolve_contact_or_404(public_key)
    _require_repeater(contact)

    since = int(time.time()) - 30 * 86400
    rows = await RepeaterTelemetryRepository.get_history(contact.public_key, since)
    return [TelemetryHistoryEntry(**row) for row in rows]


@router.post("/{public_key}/repeater/lpp-telemetry", response_model=RepeaterLppTelemetryResponse)
async def repeater_lpp_telemetry(public_key: str) -> RepeaterLppTelemetryResponse:
    """Fetch CayenneLPP sensor telemetry from a repeater (single attempt, 10s timeout)."""
    radio_manager.require_connected()
    contact = await resolve_contact_or_404(public_key)
    _require_repeater(contact)

    async with radio_manager.radio_operation(
        "repeater_lpp_telemetry", pause_polling=True, suspend_auto_fetch=True
    ) as mc:
        await ensure_on_radio(mc, contact)

        telemetry = await mc.commands.req_telemetry_sync(
            contact.public_key, timeout=10, min_timeout=5
        )

    if telemetry is None:
        await note_telemetry_poll(
            public_key=contact.public_key,
            name=contact.name or "",
            outcome="miss",
        )
        raise HTTPException(status_code=422, detail="No telemetry response from repeater")

    sensors: list[LppSensor] = []
    for entry in telemetry:
        channel = entry.get("channel", 0)
        type_name = str(entry.get("type", "unknown"))
        value = entry.get("value", 0)
        sensors.append(LppSensor(channel=channel, type_name=type_name, value=value))

    await note_telemetry_poll(
        public_key=contact.public_key,
        name=contact.name or "",
        outcome="success",
        snapshot={"lpp_sensors": [s.model_dump() for s in sensors]},
        allow_gps_lost=True,
    )

    return await _cache_pane(
        public_key, "lpp_telemetry", RepeaterLppTelemetryResponse(sensors=sensors)
    )  # type: ignore[return-value]


@router.post("/{public_key}/repeater/neighbors", response_model=RepeaterNeighborsResponse)
async def repeater_neighbors(public_key: str) -> RepeaterNeighborsResponse:
    """Fetch neighbors from a repeater (single attempt, 10s timeout)."""
    radio_manager.require_connected()
    contact = await resolve_contact_or_404(public_key)
    _require_repeater(contact)

    async with radio_manager.radio_operation(
        "repeater_neighbors", pause_polling=True, suspend_auto_fetch=True
    ) as mc:
        # Ensure contact is on radio for routing
        await ensure_on_radio(mc, contact)

        neighbors_data = await mc.commands.fetch_all_neighbours(
            contact.public_key, timeout=10, min_timeout=5
        )

    neighbors: list[NeighborInfo] = []
    if neighbors_data and "neighbours" in neighbors_data:
        for n in neighbors_data["neighbours"]:
            pubkey_prefix = n.get("pubkey", "")
            resolved_contact = await ContactRepository.get_by_key_prefix(pubkey_prefix)
            neighbors.append(
                NeighborInfo(
                    pubkey_prefix=pubkey_prefix,
                    name=resolved_contact.name if resolved_contact else None,
                    snr=n.get("snr", 0.0),
                    last_heard_seconds=n.get("secs_ago", 0),
                )
            )

    reported_count = neighbors_data.get("neighbours_count") if neighbors_data else None
    return await _cache_pane(
        public_key,
        "neighbors",
        RepeaterNeighborsResponse(neighbors=neighbors, reported_count=reported_count),
    )  # type: ignore[return-value]


@router.post("/{public_key}/repeater/acl", response_model=RepeaterAclResponse)
async def repeater_acl(public_key: str) -> RepeaterAclResponse:
    """Fetch ACL from a repeater (single attempt, 10s timeout)."""
    radio_manager.require_connected()
    contact = await resolve_contact_or_404(public_key)
    _require_repeater(contact)

    async with radio_manager.radio_operation(
        "repeater_acl", pause_polling=True, suspend_auto_fetch=True
    ) as mc:
        # Ensure contact is on radio for routing
        await ensure_on_radio(mc, contact)

        acl_data = await mc.commands.req_acl_sync(contact.public_key, timeout=10, min_timeout=5)

    acl_entries: list[AclEntry] = []
    if acl_data and isinstance(acl_data, list):
        for entry in acl_data:
            pubkey_prefix = entry.get("key", "")
            perm = entry.get("perm", 0)
            resolved_contact = await ContactRepository.get_by_key_prefix(pubkey_prefix)
            acl_entries.append(
                AclEntry(
                    pubkey_prefix=pubkey_prefix,
                    name=resolved_contact.name if resolved_contact else None,
                    permission=perm,
                    permission_name=ACL_PERMISSION_NAMES.get(perm, f"Unknown({perm})"),
                )
            )

    return await _cache_pane(public_key, "acl", RepeaterAclResponse(acl=acl_entries))  # type: ignore[return-value]


async def _batch_cli_fetch(
    contact: Contact,
    operation_name: str,
    commands: list[tuple[str, str]],
) -> dict[str, str | None]:
    return await batch_cli_fetch(contact, operation_name, commands)


@router.post("/{public_key}/repeater/node-info", response_model=RepeaterNodeInfoResponse)
async def repeater_node_info(public_key: str) -> RepeaterNodeInfoResponse:
    """Fetch repeater identity/location info via a small CLI batch."""
    radio_manager.require_connected()
    contact = await resolve_contact_or_404(public_key)
    _require_repeater(contact)

    results = await _batch_cli_fetch(
        contact,
        "repeater_node_info",
        [
            ("get name", "name"),
            ("get lat", "lat"),
            ("get lon", "lon"),
            ("clock", "clock_utc"),
        ],
    )
    return await _cache_pane(public_key, "node_info", RepeaterNodeInfoResponse(**results))  # type: ignore[return-value]


@router.post("/{public_key}/repeater/radio-settings", response_model=RepeaterRadioSettingsResponse)
async def repeater_radio_settings(public_key: str) -> RepeaterRadioSettingsResponse:
    """Fetch radio settings from a repeater via radio/config CLI commands."""
    radio_manager.require_connected()
    contact = await resolve_contact_or_404(public_key)
    _require_repeater(contact)

    results = await _batch_cli_fetch(
        contact,
        "repeater_radio_settings",
        [
            ("ver", "firmware_version"),
            ("get radio", "radio"),
            ("get tx", "tx_power"),
            ("get af", "airtime_factor"),
            ("get dutycycle", "duty_cycle_limit"),
            ("get repeat", "repeat_enabled"),
            ("get flood.max", "flood_max"),
        ],
    )
    # `get dutycycle` only exists on firmware >= 1.15. Older nodes fall through to
    # the generic unknown-config handler and reply "??: dutycycle" (or an ERROR
    # string), which extract_response_text passes back verbatim. Drop those so the
    # field reads as unsupported rather than surfacing the sentinel to the UI.
    dc = results.get("duty_cycle_limit")
    if dc is not None:
        dc = dc.strip()
        if dc.startswith("??") or dc.lower().startswith("error"):
            results["duty_cycle_limit"] = None
    return await _cache_pane(public_key, "radio_settings", RepeaterRadioSettingsResponse(**results))  # type: ignore[return-value]


@router.post(
    "/{public_key}/repeater/advert-intervals", response_model=RepeaterAdvertIntervalsResponse
)
async def repeater_advert_intervals(public_key: str) -> RepeaterAdvertIntervalsResponse:
    """Fetch advertisement intervals from a repeater via CLI commands."""
    radio_manager.require_connected()
    contact = await resolve_contact_or_404(public_key)
    _require_repeater(contact)

    results = await _batch_cli_fetch(
        contact,
        "repeater_advert_intervals",
        [
            ("get advert.interval", "advert_interval"),
            ("get flood.advert.interval", "flood_advert_interval"),
        ],
    )
    return await _cache_pane(
        public_key, "advert_intervals", RepeaterAdvertIntervalsResponse(**results)
    )  # type: ignore[return-value]


@router.post("/{public_key}/repeater/owner-info", response_model=RepeaterOwnerInfoResponse)
async def repeater_owner_info(public_key: str) -> RepeaterOwnerInfoResponse:
    """Fetch owner info, firmware, and guest password from a repeater.

    Owner info + firmware + name come from the guest-accessible binary request
    (REQ_TYPE_GET_OWNER_INFO / 0x07), which the firmware serves to any logged-in
    client. The guest password is admin-only and still comes from the CLI, so a
    guest sees it blank. See issue #306.
    """
    radio_manager.require_connected()
    contact = await resolve_contact_or_404(public_key)
    _require_repeater(contact)

    owner = await fetch_repeater_owner_info_binary(contact) or {}

    # Guest password is admin-only; still fetched via CLI (guests get None).
    cli = await _batch_cli_fetch(
        contact,
        "repeater_owner_info",
        [("get guest.password", "guest_password")],
    )

    return RepeaterOwnerInfoResponse(
        owner_info=owner.get("owner_info"),
        firmware_version=owner.get("firmware_version"),
        name=owner.get("name"),
        guest_password=cli.get("guest_password"),
    )


# The firmware's `region` dump is written into a fixed ~160-char buffer
# (CommonCLI::handleRegionCmd -> RegionMap::exportTo(reply, 160)), so large
# region sets get truncated. Flag when the reply lands close to that ceiling.
_REGION_DUMP_CAP = 160


def _is_region_name(name: str) -> bool:
    """Return True if ``name`` is a valid region name (or the wildcard ``*``).

    Mirrors firmware ``RegionMap::is_name_char``: ``-``, ``$``, ``#``, digits, or
    any byte ``>= 'A'``. Crucially this excludes spaces, so a firmware that does
    not support regions (older than v1.10) and replies to `region` with
    ``"Unknown command"`` is rejected here rather than mis-parsed as a region —
    which lets the endpoint fall back to the anon path or an empty result.
    """
    if name == "*":
        return True
    if not name:
        return False
    return all(c in "-$#0123456789" or ord(c) >= 0x41 for c in name)


def _parse_region_dump(text: str) -> tuple[list[RepeaterRegionEntry], bool]:
    """Parse the repeater `region` CLI dump into a structured hierarchy.

    Firmware emits an indented tree (``RegionMap::printChildRegions``), one entry
    per line: ``{depth spaces}{name}{^ if home}{ F if flood-allowed}``. The root
    is the wildcard ``*``. Absence of the trailing `` F`` means flood is denied.

    Lines that are not valid region names are dropped, so an unsupported-command
    reply parses to no entries. Returns the parsed entries and a best-effort
    ``truncated`` flag (the dump is capped at ~160 chars, and a complete dump
    ends every line with a newline).
    """
    truncated = len(text) >= _REGION_DUMP_CAP - 2 or (
        text.strip() != "" and not text.endswith("\n")
    )
    entries: list[RepeaterRegionEntry] = []
    for line in text.split("\n"):
        if line.strip() == "":
            continue
        depth = len(line) - len(line.lstrip(" "))
        content = line.strip()
        flood_allowed = content.endswith(" F")
        if flood_allowed:
            content = content[:-2].rstrip()
        is_home = content.endswith("^")
        if is_home:
            content = content[:-1]
        name = content.strip()
        if not _is_region_name(name):
            continue
        entries.append(
            RepeaterRegionEntry(
                name=name, depth=depth, flood_allowed=flood_allowed, is_home=is_home
            )
        )
    return entries, truncated


def _parse_anon_region_names(names: str) -> list[RepeaterRegionEntry]:
    """Parse the anon regions request's comma-separated flood-allowed names.

    ``req_regions_sync`` returns the firmware's ``exportNamesTo(REGION_DENY_FLOOD)``
    output: a flat, comma-separated list of the region names where flood is
    *allowed* (``*`` is the wildcard). There is no hierarchy or blocked-region
    information in this guest-accessible view, so every entry is depth 0 and
    flood-allowed.
    """
    entries: list[RepeaterRegionEntry] = []
    for raw_name in names.split(","):
        name = raw_name.strip().strip("\x00")
        if not name:
            continue
        entries.append(RepeaterRegionEntry(name=name, depth=0, flood_allowed=True, is_home=False))
    return entries


# One extra try, because a lost packet and an unreachable repeater are the same
# silence and only one of them is worth waiting out twice.
ANON_REGION_ATTEMPTS = 2

# The request travels to the repeater and the answer travels back, so a distant
# repeater needs longer than a neighbour. The floor is what a direct neighbour
# needs; the ceiling stops one unreachable node from holding a sweep open.
_ANON_REGION_TIMEOUT_BASE = 10
_ANON_REGION_TIMEOUT_PER_HOP = 6
_ANON_REGION_TIMEOUT_MAX = 40


def _anon_region_timeout(contact: Contact) -> int:
    """Seconds to wait for this particular repeater."""
    hops = contact.direct_path_len if contact.direct_path_len > 0 else 0
    return min(
        _ANON_REGION_TIMEOUT_MAX,
        _ANON_REGION_TIMEOUT_BASE + hops * _ANON_REGION_TIMEOUT_PER_HOP,
    )


@dataclass(frozen=True)
class AnonRegionResult:
    """What came back from one repeater, and why when nothing did.

    The previous version collapsed every failure into ``None``. A repeater out of
    range, a radio whose contact list is full, and a firmware that does not know
    the request all read as "did not answer", which leaves an operator staring at
    "0/8 answered" with nothing to act on. These are the distinctions the code can
    actually make; it does not invent the ones it cannot.
    """

    names: list[str] | None
    outcome: Literal["answered", "no_reply", "contact_add_failed", "error"]
    detail: str | None = None
    attempts: int = 1


async def request_anon_region_names_detailed(
    mc, contact: Contact, attempts: int = ANON_REGION_ATTEMPTS
) -> AnonRegionResult:
    """Ask one repeater for its flood-allowed region names, and say how it went.

    Ensures the contact is on the radio, settles, then requests the names. The
    caller must already hold ``radio_operation``. Retries only silence: an error
    the radio reported once will report itself again.
    """
    try:
        await ensure_on_radio(mc, contact)
    except HTTPException as exc:
        # Most often a full radio contact list, which fails every repeater in the
        # sweep for a reason that has nothing to do with any of them.
        return AnonRegionResult(
            names=None, outcome="contact_add_failed", detail=str(exc.detail), attempts=0
        )
    except Exception as exc:
        return AnonRegionResult(names=None, outcome="error", detail=str(exc), attempts=0)

    await asyncio.sleep(1.0)  # settle after add_contact
    timeout = _anon_region_timeout(contact)
    last_detail: str | None = None
    for attempt in range(1, max(1, attempts) + 1):
        try:
            names = await mc.commands.req_regions_sync(
                contact.public_key, timeout=timeout, min_timeout=min(5, timeout)
            )
        except Exception as exc:
            last_detail = str(exc)
            logger.debug(
                "anon regions request failed for %s (attempt %d): %s",
                contact.public_key[:12],
                attempt,
                exc,
            )
            continue
        if names:
            return AnonRegionResult(
                names=[entry.name for entry in _parse_anon_region_names(names)],
                outcome="answered",
                attempts=attempt,
            )
    return AnonRegionResult(
        names=None, outcome="no_reply", detail=last_detail, attempts=max(1, attempts)
    )


async def request_anon_region_names(mc, contact: Contact) -> list[str] | None:
    """The names only, for callers that have nothing to do with the reason."""
    return (await request_anon_region_names_detailed(mc, contact)).names


async def _fetch_anon_flood_allowed_regions(contact: Contact) -> list[RepeaterRegionEntry] | None:
    """Guest-accessible fallback: fetch flood-allowed region names via anon request.

    Returns ``None`` when the repeater does not answer (older firmware, out of
    range) so the caller can leave the pane empty.
    """
    async with radio_manager.radio_operation(
        "repeater_regions_anon", pause_polling=True, suspend_auto_fetch=True
    ) as mc:
        names = await request_anon_region_names(mc, contact)

    if names is None:
        return None
    return [
        RepeaterRegionEntry(name=name, depth=0, flood_allowed=True, is_home=False) for name in names
    ]


@router.post("/{public_key}/repeater/regions", response_model=RepeaterRegionsResponse)
async def repeater_regions(public_key: str) -> RepeaterRegionsResponse:
    """Fetch the repeater's region hierarchy and flood permissions.

    Primary path is the admin CLI `region` dump (full hierarchy + allowed/blocked
    + home; may be truncated by the firmware's ~160-char cap). When the CLI
    returns nothing — e.g. guest access, which cannot run CLI commands — it falls
    back to the guest-accessible anon regions request, which only yields a flat
    list of flood-allowed region names. See issue #309.
    """
    radio_manager.require_connected()
    contact = await resolve_contact_or_404(public_key)
    _require_repeater(contact)

    results = await _batch_cli_fetch(contact, "repeater_regions", [("region", "regions")])
    raw = results.get("regions")
    entries, truncated = _parse_region_dump(raw or "")
    # The CLI dump always includes the wildcard root, so a non-empty result means
    # the CLI answered. Empty means no CLI reply (guest / timeout) -> try anon.
    if entries:
        return RepeaterRegionsResponse(regions=entries, raw=raw, truncated=truncated, source="cli")

    anon_entries = await _fetch_anon_flood_allowed_regions(contact)
    if anon_entries is not None:
        return RepeaterRegionsResponse(
            regions=anon_entries, raw=raw, truncated=False, source="anon"
        )

    # Nothing usable from either path (unsupported firmware, guest with no anon
    # support, or out of range) -> empty, not a truncated dump.
    return RepeaterRegionsResponse(regions=[], raw=raw, truncated=False, source="cli")


@router.post("/{public_key}/command", response_model=CommandResponse)
async def send_repeater_command(public_key: str, request: CommandRequest) -> CommandResponse:
    """Send a CLI command to a repeater or room server."""
    radio_manager.require_connected()

    contact = await resolve_contact_or_404(public_key)
    require_server_capable_contact(contact)
    return await send_contact_cli_command(
        contact,
        request.command,
        operation_name="send_repeater_command",
    )
