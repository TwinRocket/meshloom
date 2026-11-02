"""Local API of Meshloom Remote (``/api/remote``, docs/dev/remote.md).

The browser never talks to the registry or the relay: these routes do, through
``app.services.remote`` (opt-in, egress guard, coded errors). ``GET /remote`` makes
no request.

Roles (``docs/dev/auth.md``, "Permissions"): turning Remote on or off, unregistering,
replacing the key, revoking every device and testing a custom domain are ``admin``. Every account role but
``kiosk`` (``own_account``) reads the state, lists and revokes its own devices, issues
an enrolment QR code for itself (never through the relay: ``remote_lan_only``), issues
a client pass, and approves (the new device is the approver's) or denies an access
request. An administrator sees and revokes every device.
Every write is audited (``remote.*``). Responses are ``Cache-Control: no-store``
(``app/middleware/security_headers.py``); a pass or an enrolment URL is returned once
and never logged.
"""

from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, Field

from app.auth.audit import SYSTEM_ACTOR, RequestActor
from app.auth.middleware import request_via
from app.auth.permissions import own_account, require
from app.auth.principal import Principal, get_principal
from app.repository.remote import RemoteDevice, RemoteRepository
from app.services import remote
from app.services.remote import RemoteError

router = APIRouter(prefix="/remote", tags=["remote"])

RemoteStateName = Literal["disabled", "unregistered", "registered"]


class RemoteDeviceItem(BaseModel):
    id: str
    user_id: int
    kind: Literal["browser", "client"]
    label: str | None = None
    origin: Literal["qr", "approval", "client"]
    created_at: int
    pass_expires_at: int
    last_seen_at: int | None = None
    revoked_at: int | None = None
    active: bool = Field(description="Not revoked and in the current pass generation")


class RemoteGate(BaseModel):
    gen: int
    revoked: int = Field(description="How many device ids the relay holds as revoked")


class RemoteStatus(BaseModel):
    enabled: bool
    state: RemoteStateName
    key_fingerprint: str | None = None
    slug: str | None = None
    hostname: str | None = None
    relay_url: str | None = None
    registered_at: int | None = None
    last_error: str | None = None
    relay_connected: bool = False
    link_status: str | None = Field(
        default=None,
        description=(
            "The relay client's state (app/services/remote_relay.py LINK_STATES): "
            "connecting, connected, reconnecting, not_registered, paused, replaced, "
            "banned, update_required or relay_paused; null while Remote is off"
        ),
    )
    gate: RemoteGate
    devices: list[RemoteDeviceItem]


class RemoteEnrollResponse(BaseModel):
    url: str = Field(description="The QR code's content; the token is in the fragment")
    expires_at: int


class RemoteClientPassRequest(BaseModel):
    label: str | None = Field(default=None, max_length=remote.LABEL_MAX)


class RemoteClientPassResponse(BaseModel):
    device: RemoteDeviceItem
    client_pass: str = Field(description="Shown once: the X-Meshloom-Pass header value")


class RemoteAccessRequestItem(BaseModel):
    request_id: str
    code: str
    user_agent: str
    created_at: int
    expires_at: int


class RemoteAccessApproveRequest(BaseModel):
    code: str = Field(min_length=1, max_length=32)
    label: str | None = Field(default=None, max_length=remote.LABEL_MAX)


class RemoteDomainTestRequest(BaseModel):
    domain: str = Field(min_length=1, max_length=300, description="The personal domain to test")


class RemoteDomainTestResponse(BaseModel):
    result: Literal["ok", "no_redirect", "wrong_target", "invalid_domain", "unreachable"]
    status_code: int | None = Field(default=None, description="The domain's own HTTP status")
    target_host: str | None = Field(
        default=None, description="Host the domain redirects to (untrusted text)"
    )
    detail: str | None = Field(
        default=None, description="With unreachable: an egress/transport error code"
    )


class RemoteGateResponse(BaseModel):
    gate_pushed: bool = Field(description="The relay link got the new pass state now")


def _principal(request: Request) -> Principal:
    principal = get_principal(request)
    assert principal is not None  # the declared access rule refused anonymous callers
    return principal


def _item(device: RemoteDevice, gen: int) -> RemoteDeviceItem:
    return RemoteDeviceItem(
        id=device.id,
        user_id=device.user_id,
        kind=device.kind,  # type: ignore[arg-type]
        label=device.label,
        origin=device.origin,  # type: ignore[arg-type]
        created_at=device.created_at,
        pass_expires_at=device.pass_expires_at,
        last_seen_at=device.last_seen_at,
        revoked_at=device.revoked_at,
        active=device.revoked_at is None and device.gen == gen,
    )


async def _status(principal: Principal | None) -> RemoteStatus:
    state = await RemoteRepository.get_state()
    owner = None if principal is None or principal.role == "admin" else principal.user_id
    devices = await RemoteRepository.list_devices(owner)
    gate = await remote.gate_state()
    if not state.enabled:
        name: RemoteStateName = "disabled"
    elif not state.slug:
        name = "unregistered"
    else:
        name = "registered"
    return RemoteStatus(
        enabled=state.enabled,
        state=name,
        key_fingerprint=await remote.key_fingerprint(),
        slug=state.slug or None,
        hostname=state.hostname or None,
        relay_url=state.relay_url or None,
        registered_at=state.registered_at,
        last_error=state.last_error,
        relay_connected=remote.relay_connected(),
        link_status=remote.link_status(),
        gate=RemoteGate(gen=gate["gen"], revoked=len(gate["revoked"])),
        devices=[_item(device, state.gate_gen) for device in devices],
    )


@router.get("", response_model=RemoteStatus, dependencies=[Depends(own_account())])
async def get_remote(request: Request) -> RemoteStatus:
    """State, key fingerprint, hostname and devices (an administrator sees every
    device, anyone else their own). No request leaves."""
    return await _status(get_principal(request))


@router.post("/enable", response_model=RemoteStatus, dependencies=[Depends(require("admin"))])
async def enable_remote(request: Request, actor: RequestActor = SYSTEM_ACTOR) -> RemoteStatus:
    """Turn Remote on and register (``POST /v1/instances``). A registry failure leaves
    the state ``unregistered`` with ``last_error``; calling it again retries."""
    await remote.enable(actor)
    return await _status(get_principal(request))


@router.post("/disable", response_model=RemoteStatus, dependencies=[Depends(require("admin"))])
async def disable_remote(request: Request, actor: RequestActor = SYSTEM_ACTOR) -> RemoteStatus:
    """Turn Remote off; the registration and the devices are kept."""
    await remote.disable(actor)
    return await _status(get_principal(request))


@router.delete("", response_model=RemoteStatus, dependencies=[Depends(require("admin"))])
async def unregister_remote(request: Request, actor: RequestActor = SYSTEM_ACTOR) -> RemoteStatus:
    """Delete the registration (the key is kept) and turn Remote off. 409
    ``remote_disabled`` while off: nothing may leave then."""
    await remote.unregister(actor)
    return await _status(get_principal(request))


@router.post("/rotate", response_model=RemoteStatus, dependencies=[Depends(require("admin"))])
async def rotate_remote(request: Request, actor: RequestActor = SYSTEM_ACTOR) -> RemoteStatus:
    """A new Remote key: every device is forgotten and enrols again; registered again
    (a new slug) when Remote is on."""
    await remote.rotate(actor)
    return await _status(get_principal(request))


@router.post(
    "/domain/test",
    response_model=RemoteDomainTestResponse,
    dependencies=[Depends(require("admin"))],
)
async def test_custom_domain(body: RemoteDomainTestRequest) -> RemoteDomainTestResponse:
    """Check that a personal domain redirects to this instance's ``<slug>.meshloom.link``
    (MSHL-428). One GET to the domain through the egress guard, no redirect followed.
    409 ``remote_disabled`` / ``remote_not_registered``; every other outcome is a 200
    with a ``result``."""
    check = await remote.check_custom_domain(body.domain)
    return RemoteDomainTestResponse(
        result=check.result,  # type: ignore[arg-type]
        status_code=check.status_code,
        target_host=check.target_host,
        detail=check.detail,
    )


@router.post("/enroll", response_model=RemoteEnrollResponse, dependencies=[Depends(own_account())])
async def issue_enroll(
    request: Request, actor: RequestActor = SYSTEM_ACTOR
) -> RemoteEnrollResponse:
    """A 15 min enrolment QR code for the caller's next device. Refused through the
    relay (403 ``remote_lan_only``)."""
    if request_via(request.scope) == "relay":
        raise RemoteError("remote_lan_only")
    ticket = await remote.issue_enroll_token(_principal(request).user, actor)
    return RemoteEnrollResponse(url=ticket.url, expires_at=ticket.expires_at)


@router.get(
    "/devices", response_model=list[RemoteDeviceItem], dependencies=[Depends(own_account())]
)
async def list_devices(request: Request) -> list[RemoteDeviceItem]:
    return (await _status(get_principal(request))).devices


@router.post(
    "/devices/client",
    response_model=RemoteClientPassResponse,
    dependencies=[Depends(own_account())],
)
async def issue_client_pass(
    body: RemoteClientPassRequest, request: Request, actor: RequestActor = SYSTEM_ACTOR
) -> RemoteClientPassResponse:
    """A 365-day client pass for a script or meshenger, shown once."""
    device, token = await remote.issue_client_pass(_principal(request).user, body.label, actor)
    state = await RemoteRepository.get_state()
    return RemoteClientPassResponse(device=_item(device, state.gate_gen), client_pass=token)


@router.post(
    "/devices/revoke-all",
    response_model=RemoteGateResponse,
    dependencies=[Depends(require("admin"))],
)
async def revoke_all(actor: RequestActor = SYSTEM_ACTOR) -> RemoteGateResponse:
    """Revoke every device: a new pass generation, pushed to the relay."""
    return RemoteGateResponse(gate_pushed=await remote.revoke_all_devices(actor))


@router.delete(
    "/devices/{device_id}",
    response_model=RemoteGateResponse,
    dependencies=[Depends(own_account())],
)
async def revoke_device(
    device_id: str, request: Request, actor: RequestActor = SYSTEM_ACTOR
) -> RemoteGateResponse:
    """Revoke one device (one's own; an administrator any). 404 otherwise."""
    principal = _principal(request)
    owner = None if principal.role == "admin" else principal.user_id
    return RemoteGateResponse(
        gate_pushed=await remote.revoke_device(device_id, actor, owner_id=owner)
    )


@router.get(
    "/access-requests",
    response_model=list[RemoteAccessRequestItem],
    dependencies=[Depends(own_account())],
)
async def list_access_requests() -> list[RemoteAccessRequestItem]:
    """Pending requests from unknown devices (in memory, 10 min each)."""
    return [
        RemoteAccessRequestItem(
            request_id=req.request_id,
            code=req.code,
            user_agent=req.user_agent,
            created_at=req.created_at,
            expires_at=req.expires_at,
        )
        for req in remote.pending_access_requests()
    ]


@router.post(
    "/access-requests/approve",
    response_model=RemoteDeviceItem,
    dependencies=[Depends(own_account())],
)
async def approve_access_request(
    body: RemoteAccessApproveRequest, request: Request, actor: RequestActor = SYSTEM_ACTOR
) -> RemoteDeviceItem:
    """Grant the request whose code the new device shows: the device becomes the
    caller's, its pass goes to the relay (``access_grant``). 503 ``remote_offline``
    without a relay link."""
    device = await remote.approve_access(body.code, _principal(request).user, body.label, actor)
    state = await RemoteRepository.get_state()
    return _item(device, state.gate_gen)


@router.post(
    "/access-requests/{request_id}/deny",
    status_code=204,
    dependencies=[Depends(own_account())],
)
async def deny_access_request(request_id: str, actor: RequestActor = SYSTEM_ACTOR) -> None:
    await remote.deny_access(request_id, actor)
