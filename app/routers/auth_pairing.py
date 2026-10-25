"""``/api/auth/pairings`` and ``/api/auth/pair``: device pairing codes and their redemption.

Included by ``app/routers/auth.py``. Rules in ``app/auth/pairing.py``; the
contract a device follows is ``docs/dev/device-pairing.md``.

- ``POST /auth/pairings``, ``GET /auth/pairings``, ``DELETE /auth/pairings/{id}``
  declare ``own_account()`` (a signed-in account, never a token) and are for
  an ``admin`` only (403 ``auth_forbidden`` otherwise, as the cadrage says). An
  admin sees and revokes every pending code (404 ``auth_pairing_not_found``
  for one that is unknown or no longer pending). The code is in the creation
  answer only.
- ``POST /auth/pair`` is public (``PUBLIC_ROUTES``): a device sends the code
  and gets its device token. 403 ``auth_pairing_ingress`` under Home Assistant
  ingress, 429 ``auth_rate_limited`` while a login delay runs for this client
  (both before any lookup), 410 ``auth_pairing_expired`` for every bad code, 409
  ``auth_token_limit`` when the creator holds the maximum of live tokens (the
  code is spent then).

Every creation, revocation and redemption writes a ``pairing.created`` /
``pairing.revoked`` / ``pairing.used`` row, refusals included (one row per
delay for the limiter, as for ``login``). No row carries a code or a token.
"""

from __future__ import annotations

import json
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field, SecretStr, field_validator

from app.auth import audit, pairing, ratelimit
from app.auth.models import PairingCode
from app.auth.permissions import FORBIDDEN_CODE, Permission, own_account, public
from app.auth.principal import Principal, get_principal
from app.auth.providers import token as token_provider
from app.repository.auth import (
    MAX_PENDING_PAIRINGS_PER_USER,
    MAX_TOKENS_PER_USER,
    PairingCodeRepository,
    PairingLimitError,
    UserRepository,
)

router = APIRouter()

NAME_MAX_LENGTH = 64

EXPIRED_CODE = "auth_pairing_expired"

_MESSAGES = {
    "auth_required": "Authentication required",
    FORBIDDEN_CODE: "Not allowed for this account",
    "auth_token_scope": "A scope is beyond what this account's role allows",
    "auth_token_limit": "This account already holds the maximum number of tokens",
    "auth_pairing_limit": "This account already has the maximum number of pending pairing codes",
    "auth_pairing_not_found": "Pairing code not found",
    EXPIRED_CODE: "This pairing code is used, expired or unknown",
    "auth_pairing_ingress": "Pair devices with the instance's own address, not through Home Assistant",
    "auth_rate_limited": "Too many attempts: retry later",
}


def _error(
    status: int, code: str, params: dict | None = None, headers: dict | None = None
) -> HTTPException:
    detail: dict = {"code": code, "message": _MESSAGES.get(code, code)}
    if params:
        detail["params"] = params
    return HTTPException(status_code=status, detail=detail, headers=headers)


def _label(value: str) -> str:
    value = value.strip()
    if not value:
        raise ValueError("empty name")
    return value


class PairingCreate(BaseModel):
    label: str = Field(min_length=1, max_length=NAME_MAX_LENGTH)
    scopes: list[Permission] = Field(min_length=1, max_length=5)
    ttl: int = Field(default=pairing.DEFAULT_TTL_S, ge=pairing.MIN_TTL_S, le=pairing.MAX_TTL_S)

    @field_validator("label")
    @classmethod
    def _strip(cls, value: str) -> str:
        return _label(value)


class PairingInfo(BaseModel):
    id: str
    label: str
    scopes: list[str]
    urls: list[str]
    created_at: int
    expires_at: int
    created_by: int
    username: str | None = None


class PairingCreatedDoc(PairingInfo):
    """The creation answer as the OpenAPI page shows it."""

    code: str = Field(description="mlp_…: shown once, never again")
    qr_payload: str = Field(description="meshloom://pair?d=…, the text the QR encodes")


class PairRequest(BaseModel):
    model_config = ConfigDict(hide_input_in_errors=True)

    code: SecretStr = Field(max_length=pairing.MAX_CODE_LENGTH)
    device_name: str = Field(min_length=1, max_length=NAME_MAX_LENGTH)
    # Opaque to the instance: stored with the pairing for meshenger E2.
    extensions: dict[str, Any] | None = None

    @field_validator("device_name")
    @classmethod
    def _strip(cls, value: str) -> str:
        return _label(value)

    @field_validator("extensions")
    @classmethod
    def _bounded(cls, value: dict[str, Any] | None) -> dict[str, Any] | None:
        if value is not None:
            size = len(json.dumps(value, separators=(",", ":")).encode("utf-8"))
            if size > pairing.MAX_EXTENSIONS_BYTES:
                raise ValueError("extensions too large")
        return value


class PairedUser(BaseModel):
    id: int
    username: str
    display_name: str


class PairedDoc(BaseModel):
    """The redemption answer as the OpenAPI page shows it."""

    token: str = Field(description="mlt_…: the device token, shown once")
    token_id: str
    scopes: list[str]
    user: PairedUser
    urls: list[str]


def _admin(request: Request) -> Principal:
    """The signed-in admin managing pairing codes (cadrage: ``POST
    /api/auth/pairings`` is admin; ``own_account()`` already refused tokens)."""
    principal = get_principal(request)
    if principal is None:
        raise _error(401, "auth_required")
    if principal.role != "admin":
        raise _error(403, FORBIDDEN_CODE, {"permission": "admin"})
    return principal


async def _info(rows: list[PairingCode]) -> list[PairingInfo]:
    names: dict[int, str | None] = {}
    for user_id in {row.created_by for row in rows}:
        user = await UserRepository.get(user_id)
        names[user_id] = user.username if user is not None else None
    return [
        PairingInfo(
            id=row.id,
            label=row.label,
            scopes=row.scopes,
            urls=row.urls,
            created_at=row.created_at,
            expires_at=row.expires_at,
            created_by=row.created_by,
            username=names.get(row.created_by),
        )
        for row in rows
    ]


async def _refused(
    request: Request,
    action: audit.AuditAction,
    error: HTTPException,
    *,
    target: str | None = None,
    extra: dict | None = None,
) -> None:
    detail = error.detail if isinstance(error.detail, dict) else {}
    code = str(detail.get("code", ""))
    await audit.record_request(
        request,
        action,
        "refused" if error.status_code == 403 else "failure",
        target=target,
        details={
            "reason": code.removeprefix("auth_"),
            "status": error.status_code,
            **(extra or {}),
        },
    )


@router.get("/pairings", response_model=list[PairingInfo], dependencies=[Depends(own_account())])
async def list_pairings(request: Request) -> list[PairingInfo]:
    """Every pending pairing code, newest first (admin)."""
    _admin(request)
    return await _info(await PairingCodeRepository.list_pending())


@router.post(
    "/pairings",
    status_code=201,
    response_model=None,
    responses={201: {"model": PairingCreatedDoc, "description": "The code and its QR payload"}},
    dependencies=[Depends(own_account())],
)
async def create_pairing(body: PairingCreate, request: Request) -> JSONResponse:
    """Create a single-use pairing code. The answer holds the code, once."""
    scopes: list[str] = sorted(set(body.scopes))
    try:
        principal = _admin(request)
        violation = token_provider.scope_violation(principal.role, scopes)
        if violation is not None:
            raise _error(400, "auth_token_scope", {"scope": violation})
        urls = await pairing.candidate_urls(request.scope)
        try:
            row, code = await pairing.create_pairing(
                principal.user, body.label, scopes, body.ttl, urls
            )
        except PairingLimitError:
            raise _error(
                409, "auth_pairing_limit", {"max": MAX_PENDING_PAIRINGS_PER_USER}
            ) from None
    except HTTPException as error:
        await _refused(request, "pairing.created", error, extra={"scopes": scopes})
        raise
    await audit.record_request(
        request,
        "pairing.created",
        "success",
        target=f"pairing:{row.id}",
        details={"scopes": row.scopes, "expires_at": row.expires_at, "urls": len(row.urls)},
    )
    (info,) = await _info([row])
    content = {**info.model_dump(), "code": code, "qr_payload": pairing.qr_payload(code, row.urls)}
    return JSONResponse(status_code=201, content=content)


@router.delete("/pairings/{pairing_id}", status_code=204, dependencies=[Depends(own_account())])
async def revoke_pairing(pairing_id: str, request: Request) -> None:
    """Revoke a pending code (admin): it stops working at once."""
    target = f"pairing:{pairing_id[:64]}"
    try:
        principal = _admin(request)
        row = await PairingCodeRepository.get(pairing_id)
        if row is None:
            raise _error(404, "auth_pairing_not_found")
        if not await PairingCodeRepository.revoke(row.id):
            raise _error(404, "auth_pairing_not_found")
    except HTTPException as error:
        await _refused(request, "pairing.revoked", error, target=target)
        raise
    await audit.record_request(
        request,
        "pairing.revoked",
        "success",
        target=target,
        details={"owner": row.created_by} if row.created_by != principal.user_id else None,
    )


@router.post(
    "/pair",
    status_code=201,
    response_model=None,
    responses={
        201: {"model": PairedDoc, "description": "The device token, with its secret"},
        410: {"description": EXPIRED_CODE},
    },
    dependencies=[Depends(public())],
)
async def pair(body: PairRequest, request: Request) -> JSONResponse:
    """Trade a pairing code for a device token. Public; the code works once."""
    if request.scope.get("auth_via") == "ingress":
        error = _error(403, "auth_pairing_ingress")
        await _refused(request, "pairing.used", error)
        raise error
    limiter = ratelimit.login_limiter
    address = limiter.address_key(request.scope)
    try:
        limiter.check(address, pairing.LIMIT_KEY, name_rule=False)
    except ratelimit.LoginDelayed as delayed:
        # One row per delay, not per refused attempt (as for ``login``).
        if delayed.first:
            await audit.record_request(
                request,
                "pairing.used",
                "refused" if delayed.reason == "ip" else "delayed",
                details={"reason": delayed.reason, "retry_after": delayed.retry_after_s},
            )
        raise _error(
            429,
            "auth_rate_limited",
            {"retry_after": delayed.retry_after_s},
            headers={"Retry-After": str(delayed.retry_after_s)},
        ) from None
    outcome, secret = await pairing.redeem(
        body.code.get_secret_value(), body.device_name, body.extensions
    )
    code = outcome.code
    if outcome.reason is not None or secret is None or outcome.token is None:
        reason = outcome.reason or "unknown"
        if reason == "token_limit":
            error = _error(409, "auth_token_limit", {"max": MAX_TOKENS_PER_USER})
        else:
            await limiter.record_failure(address, pairing.LIMIT_KEY, name_rule=False)
            error = _error(410, EXPIRED_CODE)
        await audit.record_request(
            request,
            "pairing.used",
            "failure",
            target=f"pairing:{code.id}" if code is not None else None,
            details={"reason": reason, "status": error.status_code},
        )
        raise error
    token, owner = outcome.token, outcome.owner
    assert owner is not None and code is not None
    limiter.record_success(address, pairing.LIMIT_KEY)
    principal = Principal(
        user=owner,
        via=request.scope.get("auth_via", "lan"),
        credential="token",
        token_id=token.id,
        scopes=frozenset(token.scopes),
    )
    await audit.record_request(
        request,
        "pairing.used",
        "success",
        principal=principal,
        target=f"token:{token.id}",
        details={"pairing": code.id, "scopes": token.scopes, "device": token.name},
    )
    content = {
        "token": secret,
        "token_id": token.id,
        "scopes": token.scopes,
        "user": PairedUser(
            id=owner.id, username=owner.username, display_name=owner.display_name
        ).model_dump(),
        "urls": code.urls,
    }
    return JSONResponse(status_code=201, content=content)
