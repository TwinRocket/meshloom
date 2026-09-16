from fastapi import APIRouter, HTTPException

from app.models import (
    CONTACT_TYPE_ROOM,
    AclEntry,
    LppSensor,
    RepeaterAclResponse,
    RepeaterLoginRequest,
    RepeaterLoginResponse,
    RepeaterLppTelemetryResponse,
    RepeaterStatusResponse,
)
from app.routers.server_control import (
    prepare_authenticated_contact_connection,
    require_server_capable_contact,
)
from app.services.contact_access import ensure_on_radio, resolve_contact_or_404
from app.services.radio_runtime import radio_runtime as radio_manager
from app.telemetry_alerts import (
    is_usable_status,
    note_telemetry_poll,
    repeater_status_response_kwargs,
    status_snapshot_from_radio,
)

router = APIRouter(prefix="/contacts", tags=["rooms"])


def _require_room(contact) -> None:
    require_server_capable_contact(contact, allowed_types=(CONTACT_TYPE_ROOM,))


@router.post("/{public_key}/room/login", response_model=RepeaterLoginResponse)
async def room_login(public_key: str, request: RepeaterLoginRequest) -> RepeaterLoginResponse:
    """Attempt room-server login and report whether auth was confirmed."""
    radio_manager.require_connected()
    contact = await resolve_contact_or_404(public_key)
    _require_room(contact)

    async with radio_manager.radio_operation(
        "room_login",
        pause_polling=True,
        suspend_auto_fetch=True,
    ) as mc:
        return await prepare_authenticated_contact_connection(
            mc,
            contact,
            request.password,
            label="room server",
        )


@router.post("/{public_key}/room/status", response_model=RepeaterStatusResponse)
async def room_status(public_key: str) -> RepeaterStatusResponse:
    """Fetch status telemetry from a room server."""
    radio_manager.require_connected()
    contact = await resolve_contact_or_404(public_key)
    _require_room(contact)

    async with radio_manager.radio_operation(
        "room_status", pause_polling=True, suspend_auto_fetch=True
    ) as mc:
        await ensure_on_radio(mc, contact)
        status = await mc.commands.req_status_sync(contact.public_key, timeout=10, min_timeout=5)

    if not is_usable_status(status):
        await note_telemetry_poll(
            public_key=contact.public_key,
            name=contact.name or "",
            outcome="miss",
        )
        raise HTTPException(status_code=422, detail="No status response from room server")

    snapshot = status_snapshot_from_radio(status)
    await note_telemetry_poll(
        public_key=contact.public_key,
        name=contact.name or "",
        outcome="success",
        snapshot=snapshot,
        allow_gps_lost=False,
    )
    return RepeaterStatusResponse(**repeater_status_response_kwargs(snapshot))


@router.post("/{public_key}/room/lpp-telemetry", response_model=RepeaterLppTelemetryResponse)
async def room_lpp_telemetry(public_key: str) -> RepeaterLppTelemetryResponse:
    """Fetch CayenneLPP telemetry from a room server."""
    radio_manager.require_connected()
    contact = await resolve_contact_or_404(public_key)
    _require_room(contact)

    async with radio_manager.radio_operation(
        "room_lpp_telemetry", pause_polling=True, suspend_auto_fetch=True
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
            allow_gps_lost=True,
        )
        raise HTTPException(status_code=422, detail="No telemetry response from room server")

    sensors = [
        LppSensor(
            channel=entry.get("channel", 0),
            type_name=str(entry.get("type", "unknown")),
            value=entry.get("value", 0),
        )
        for entry in telemetry
    ]
    await note_telemetry_poll(
        public_key=contact.public_key,
        name=contact.name or "",
        outcome="success",
        snapshot={"lpp_sensors": [s.model_dump() for s in sensors]},
        allow_gps_lost=True,
    )
    return RepeaterLppTelemetryResponse(sensors=sensors)


@router.post("/{public_key}/room/acl", response_model=RepeaterAclResponse)
async def room_acl(public_key: str) -> RepeaterAclResponse:
    """Fetch ACL entries from a room server."""
    radio_manager.require_connected()
    contact = await resolve_contact_or_404(public_key)
    _require_room(contact)

    async with radio_manager.radio_operation(
        "room_acl", pause_polling=True, suspend_auto_fetch=True
    ) as mc:
        await ensure_on_radio(mc, contact)
        acl_data = await mc.commands.req_acl_sync(contact.public_key, timeout=10, min_timeout=5)

    acl_entries = []
    if acl_data and isinstance(acl_data, list):
        from app.repository import ContactRepository
        from app.routers.repeaters import ACL_PERMISSION_NAMES

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

    return RepeaterAclResponse(acl=acl_entries)
