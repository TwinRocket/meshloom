"""``/api/auth/bans`` and ``/api/auth/ban-whitelist``: the built-in ban (MSHL-422).

Included by ``app/routers/auth.py``. Rules in ``app/auth/ip_ban.py``; the same
actions exist in ``meshloom-admin`` (``list-bans``, ``unban``, ``whitelist``)
for an admin locked out by a ban. Admin only.

- ``GET /auth/bans``: the running bans (``address`` is the key: an IPv4
  address or an IPv6 /64) and the whitelist.
- ``DELETE /auth/bans?address=``: lift one ban (an address or a key as
  listed). 400 ``auth_ban_address_invalid``, 404 ``auth_ban_not_found``.
- ``POST /auth/ban-whitelist`` ``{network, note}``: an address or a network
  that is never banned; a ban on it stops applying at once. 400
  ``auth_ban_whitelist_invalid``, 409 ``auth_ban_whitelist_exists`` or
  ``auth_ban_whitelist_full`` (``MAX_WHITELIST``).
- ``DELETE /auth/ban-whitelist?network=``: 404 ``auth_ban_whitelist_not_found``.

Every change writes an ``ip.unbanned``, ``ip.whitelisted`` or
``ip.unwhitelisted`` row (target ``ip:<key or network>``).
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from app.auth import audit, ip_ban
from app.auth.permissions import require
from app.repository.ip_ban import (
    MAX_WHITELIST,
    NOTE_MAX_LENGTH,
    IpBan,
    IpBanRepository,
    WhitelistEntry,
    WhitelistFullError,
    parse_ban_key,
    parse_network,
)

router = APIRouter()

_MESSAGES = {
    "auth_ban_address_invalid": "Not an IP address",
    "auth_ban_not_found": "This address is not banned",
    "auth_ban_whitelist_invalid": "Not an IP address or network",
    "auth_ban_whitelist_exists": "Already in the whitelist",
    "auth_ban_whitelist_full": "The whitelist is full",
    "auth_ban_whitelist_not_found": "Not in the whitelist",
}

_ADMIN = [Depends(require("admin"))]


def _error(status: int, code: str, params: dict | None = None) -> HTTPException:
    detail: dict = {"code": code, "message": _MESSAGES.get(code, code)}
    if params:
        detail["params"] = params
    return HTTPException(status_code=status, detail=detail)


class BansView(BaseModel):
    bans: list[IpBan] = Field(
        description=(
            "The running bans, the one that ends last first. address is an IPv4 address or "
            "an IPv6 /64; banned_at and expires_at are epoch seconds; route is the AUTH_FAIL "
            "route of the failure that crossed the threshold."
        )
    )
    whitelist: list[WhitelistEntry] = Field(
        description=f"Addresses and networks never banned (at most {MAX_WHITELIST})."
    )


class WhitelistIn(BaseModel):
    network: str = Field(max_length=64, description="An IP address or a network (CIDR).")
    note: str = Field(default="", max_length=NOTE_MAX_LENGTH)


@router.get("/bans", response_model=BansView, dependencies=_ADMIN)
async def list_bans() -> BansView:
    """The running bans and the whitelist."""
    return BansView(
        bans=await IpBanRepository.active(ip_ban.clock()),
        whitelist=await IpBanRepository.whitelist(),
    )


@router.delete("/bans", status_code=204, dependencies=_ADMIN)
async def unban(request: Request, address: str = Query(max_length=64)) -> None:
    """Lift one ban: it stops applying on the next request."""
    key = parse_ban_key(address)
    if key is None:
        raise _error(400, "auth_ban_address_invalid")
    if not await IpBanRepository.unban(key):
        raise _error(404, "auth_ban_not_found")
    ip_ban.forget(key)
    await audit.record_request(request, "ip.unbanned", "success", target=f"ip:{key}")


@router.post("/ban-whitelist", status_code=201, response_model=WhitelistEntry, dependencies=_ADMIN)
async def add_to_whitelist(body: WhitelistIn, request: Request) -> JSONResponse:
    """Never ban this address or network; a ban on it stops applying at once."""
    network = parse_network(body.network)
    if network is None:
        raise _error(400, "auth_ban_whitelist_invalid")
    try:
        added = await IpBanRepository.add_to_whitelist(network, body.note, ip_ban.clock())
    except WhitelistFullError:
        raise _error(409, "auth_ban_whitelist_full", {"max": MAX_WHITELIST}) from None
    if not added:
        raise _error(409, "auth_ban_whitelist_exists")
    await audit.record_request(request, "ip.whitelisted", "success", target=f"ip:{network}")
    entry = next(e for e in await IpBanRepository.whitelist() if e.network == str(network))
    return JSONResponse(status_code=201, content=entry.model_dump())


@router.delete("/ban-whitelist", status_code=204, dependencies=_ADMIN)
async def remove_from_whitelist(request: Request, network: str = Query(max_length=64)) -> None:
    """Remove a whitelist entry: its addresses may be banned again."""
    parsed = parse_network(network)
    if parsed is None:
        raise _error(400, "auth_ban_whitelist_invalid")
    if not await IpBanRepository.remove_from_whitelist(parsed):
        raise _error(404, "auth_ban_whitelist_not_found")
    await audit.record_request(request, "ip.unwhitelisted", "success", target=f"ip:{parsed}")
