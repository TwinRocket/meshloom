"""``/api/auth/tokens``: API and device tokens (create, list, revoke).

Included by ``app/routers/auth.py``. Each route declares ``own_account()``: a
signed-in account, never a token (a scoped principal cannot mint, list or
revoke tokens), then holds ``TOKEN_ROLES`` (``member`` and ``admin``, else 403
``auth_forbidden``). A member sees and revokes its own tokens; an admin sees
and revokes everyone's (404 ``auth_token_not_found`` for a token the caller may
not see). The secret is in the creation answer only. Every creation and
revocation writes a ``token.created`` / ``token.revoked`` row, refusals
included. Rules: ``app/auth/providers/token.py``, ``docs/dev/auth.md``, "API and
device tokens".
"""

from __future__ import annotations

import time

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field, SecretStr, field_validator

from app.auth import audit
from app.auth.models import ApiToken
from app.auth.permissions import FORBIDDEN_CODE, Permission, own_account
from app.auth.principal import Principal, get_principal
from app.auth.providers import token as token_provider
from app.repository.auth import (
    MAX_TOKENS_PER_USER,
    ApiTokenRepository,
    TokenLimitError,
    UserRepository,
)

router = APIRouter(prefix="/tokens")

TOKEN_NAME_MAX_LENGTH = 64

_MESSAGES = {
    "auth_required": "Authentication required",
    FORBIDDEN_CODE: "Not allowed for this account",
    "auth_token_scope": "A scope is beyond what this account's role allows",
    "auth_token_expiry_invalid": "The expiry must be in the future",
    "auth_token_limit": "This account already holds the maximum number of tokens",
    "auth_token_not_found": "Token not found",
}


def _error(status: int, code: str, params: dict | None = None) -> HTTPException:
    detail: dict = {"code": code, "message": _MESSAGES.get(code, code)}
    if params:
        detail["params"] = params
    return HTTPException(status_code=status, detail=detail)


class TokenCreate(BaseModel):
    name: str = Field(min_length=1, max_length=TOKEN_NAME_MAX_LENGTH)
    scopes: list[Permission] = Field(min_length=1, max_length=5)
    # Unix seconds; None never expires.
    expires_at: int | None = Field(default=None, ge=0, le=2**53)

    @field_validator("name")
    @classmethod
    def _strip(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("empty name")
        return value


class TokenInfo(BaseModel):
    id: str
    name: str
    scopes: list[str]
    created_at: int
    last_used_at: int | None = None
    expires_at: int | None = None
    origin: str
    user_id: int
    username: str | None = None


class TokenCreated(TokenInfo):
    """The creation answer: the only one that carries the secret. Built from
    ``reveal()`` into a ``JSONResponse``, never a ``response_model`` (which
    would mask it)."""

    model_config = ConfigDict(hide_input_in_errors=True)

    token: SecretStr

    def reveal(self) -> dict:
        data = self.model_dump()
        data["token"] = self.token.get_secret_value()
        return data


class TokenCreatedDoc(TokenInfo):
    """The creation answer as the OpenAPI page shows it."""

    token: str = Field(description="mlt_…: shown once, never again")


def _holder(request: Request) -> Principal:
    principal = get_principal(request)
    if principal is None:
        raise _error(401, "auth_required")
    if principal.role not in token_provider.TOKEN_ROLES:
        raise _error(403, FORBIDDEN_CODE, {"permission": "account"})
    return principal


async def _info(rows: list[ApiToken]) -> list[TokenInfo]:
    names: dict[int, str | None] = {}
    for user_id in {row.user_id for row in rows}:
        user = await UserRepository.get(user_id)
        names[user_id] = user.username if user is not None else None
    return [
        TokenInfo(**row.model_dump(exclude={"revoked_at"}), username=names.get(row.user_id))
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


@router.get("", response_model=list[TokenInfo], dependencies=[Depends(own_account())])
async def list_tokens(request: Request) -> list[TokenInfo]:
    """The caller's live tokens, newest first; every user's for an admin."""
    principal = _holder(request)
    owner = None if principal.role == "admin" else principal.user_id
    return await _info(await ApiTokenRepository.list_live(owner))


@router.post(
    "",
    status_code=201,
    response_model=None,
    responses={201: {"model": TokenCreatedDoc, "description": "The token, with its secret"}},
    dependencies=[Depends(own_account())],
)
async def create_token(body: TokenCreate, request: Request) -> JSONResponse:
    """Create a token for the caller. The answer holds the secret, once."""
    try:
        principal = _holder(request)
        violation = token_provider.scope_violation(principal.role, body.scopes)
        if violation is not None:
            raise _error(400, "auth_token_scope", {"scope": violation})
        if body.expires_at is not None and body.expires_at <= int(time.time()):
            raise _error(400, "auth_token_expiry_invalid")
        try:
            row, secret = await token_provider.issue_token(
                principal.user, body.name, list(body.scopes), expires_at=body.expires_at
            )
        except TokenLimitError:
            raise _error(409, "auth_token_limit", {"max": MAX_TOKENS_PER_USER}) from None
    except HTTPException as error:
        await _refused(request, "token.created", error, extra={"scopes": sorted(set(body.scopes))})
        raise
    await audit.record_request(
        request,
        "token.created",
        "success",
        target=f"token:{row.id}",
        details={"scopes": row.scopes, "expires_at": row.expires_at, "origin": row.origin},
    )
    (info,) = await _info([row])
    created = TokenCreated(**info.model_dump(), token=SecretStr(secret))
    return JSONResponse(status_code=201, content=created.reveal())


@router.delete("/{token_id}", status_code=204, dependencies=[Depends(own_account())])
async def revoke_token(token_id: str, request: Request) -> None:
    """Revoke a token (the caller's own, or any for an admin) and close its sockets."""
    target = f"token:{token_id[:64]}"
    try:
        principal = _holder(request)
        row = await ApiTokenRepository.get(token_id)
        if (
            row is None
            or row.revoked_at is not None
            or (principal.role != "admin" and row.user_id != principal.user_id)
        ):
            raise _error(404, "auth_token_not_found")
    except HTTPException as error:
        await _refused(request, "token.revoked", error, target=target)
        raise
    await token_provider.revoke_token(row.id)
    await audit.record_request(
        request,
        "token.revoked",
        "success",
        target=target,
        details={"owner": row.user_id} if row.user_id != principal.user_id else None,
    )
