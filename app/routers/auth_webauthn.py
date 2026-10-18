"""``/api/auth/webauthn/*``: passkeys (Access T52).

The caller's own passkeys, from a session only (never a token, Basic, a
trusted network or ingress): ``POST /auth/webauthn/register/options`` (current
password; the creation options), ``POST .../register/verify`` (the browser's
answer and a name; stores the passkey), ``GET /auth/webauthn/credentials``,
``DELETE /auth/webauthn/credentials/{id}`` (current password). The sign-in:
``POST /auth/webauthn/login/options`` (public, per-address limit) without a
pending token for a passwordless sign-in (discoverable credentials, user
verification required), or with the pending-login token of ``POST
/auth/login`` for a second factor that ``POST /auth/login/2fa`` then checks
(``app/routers/auth_totp.py``); ``POST /auth/webauthn/login/verify`` (public)
opens the session of a passwordless sign-in. Assertions are checked under the
login delays (``app/auth/ratelimit.py``): a failed one counts as a failed login
for the client's address and the account's name. Rules: ``app/auth/webauthn.py``,
``docs/dev/auth.md``, "Passkeys". Nothing here logs a password, a challenge, a
credential or a pending token.
"""

from __future__ import annotations

import hmac
import json
import logging
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, ConfigDict, Field, SecretStr, field_validator

from app.auth import audit, ratelimit, totp, webauthn
from app.auth.middleware import client_ip
from app.auth.models import User
from app.auth.passwords import MAX_PASSWORD_LENGTH
from app.auth.permissions import own_account, public
from app.auth.principal import Principal
from app.auth.tokens import hash_token
from app.repository.auth import (
    MAX_PASSKEYS_PER_USER,
    PasskeyLimitError,
    PasskeyRepository,
    StoredPasskey,
    UserRepository,
)
from app.routers.auth import (
    AuthSessionResponse,
    OkResponse,
    _check_current_password,
    _instance_name,
    _refuse_under_ingress,
    _require,
    _session_payload,
    _start_session,
    _via,
)

router = APIRouter(prefix="/auth/webauthn", tags=["auth"])
logger = logging.getLogger(__name__)

# A browser's PublicKeyCredential JSON is a few hundred bytes to a few KiB.
MAX_CREDENTIAL_JSON = 16 * 1024
NAME_MAX_LENGTH = 64

_MESSAGES = {
    "auth_passkey_unsupported": (
        "Passkeys need https on a declared public address, the Remote address or localhost"
    ),
    "auth_passkey_not_applicable": (
        "Passkeys are for local accounts with a password, signed in with a session"
    ),
    "auth_passkey_invalid": "The passkey could not be verified: try again",
    "auth_passkey_not_found": "Passkey not found",
    "auth_passkey_exists": "This passkey is already registered",
    "auth_passkey_limit": "Too many passkeys on this account: remove one first",
    "auth_2fa_pending_invalid": "This sign-in expired: enter your password again",
    "auth_invalid_credentials": "Invalid credentials",
    "auth_rate_limited": "Too many attempts: retry later",
    "auth_2fa_required": "Enter a code from your authenticator app or a recovery code",
}


def _error(
    status: int, code: str, params: dict | None = None, headers: dict[str, str] | None = None
) -> HTTPException:
    detail: dict = {"code": code, "message": _MESSAGES.get(code, code)}
    if params:
        detail["params"] = params
    return HTTPException(status_code=status, detail=detail, headers=headers)


def _bounded_credential(value: dict[str, Any]) -> dict[str, Any]:
    if len(json.dumps(value, separators=(",", ":"))) > MAX_CREDENTIAL_JSON:
        raise ValueError("credential too large")
    return value


class PasswordProof(BaseModel):
    model_config = ConfigDict(hide_input_in_errors=True)

    password: SecretStr = Field(max_length=MAX_PASSWORD_LENGTH)


class RegisterOptionsRequest(PasswordProof):
    # A TOTP or recovery code: required when the account's TOTP factor is on.
    code: SecretStr | None = Field(default=None, min_length=1, max_length=32)


class RegisterVerifyRequest(BaseModel):
    model_config = ConfigDict(hide_input_in_errors=True)

    credential: dict[str, Any]
    name: str = Field(min_length=1, max_length=NAME_MAX_LENGTH)

    _credential_size = field_validator("credential")(_bounded_credential)

    @field_validator("name")
    @classmethod
    def _name(cls, value: str) -> str:
        value = " ".join(value.split())
        if not value:
            raise ValueError("empty name")
        return value


class LoginOptionsRequest(BaseModel):
    model_config = ConfigDict(hide_input_in_errors=True)

    # The pending-login token of ``POST /auth/login``: a second factor. None: passwordless.
    pending_token: SecretStr | None = Field(default=None, min_length=1, max_length=128)


class LoginVerifyRequest(BaseModel):
    model_config = ConfigDict(hide_input_in_errors=True)

    credential: dict[str, Any]

    _credential_size = field_validator("credential")(_bounded_credential)


class PasskeyView(BaseModel):
    id: int
    name: str
    created_at: int
    last_used_at: int | None = None
    transports: list[str]
    aaguid: str
    backup_eligible: bool = Field(description="A synced (multi-device) credential.")
    backed_up: bool


def _view(passkey: StoredPasskey) -> PasskeyView:
    return PasskeyView(
        id=passkey.id,
        name=passkey.name,
        created_at=passkey.created_at,
        last_used_at=passkey.last_used_at,
        transports=passkey.transports,
        aaguid=passkey.aaguid,
        backup_eligible=passkey.backup_eligible,
        backed_up=passkey.backed_up,
    )


def _own_session(request: Request) -> Principal:
    """The caller's passkeys are managed from a session of an applicable account."""
    principal = _require(request)
    if (
        principal.credential != "session"
        or principal.via == "ingress"
        or not webauthn.applicable(principal.user)
    ):
        raise _error(403, "auth_passkey_not_applicable")
    return principal


async def _relying_party(request: Request) -> webauthn.RelyingParty:
    try:
        return await webauthn.relying_party(request.scope)
    except webauthn.PasskeyUnavailable as exc:
        raise _error(400, "auth_passkey_unsupported", {"reason": exc.reason}) from None


def _client(request: Request) -> str:
    return totp.client_binding(client_ip(request.scope), request.headers.get("user-agent"))


def _rate_limited(retry_after: int) -> HTTPException:
    return _error(
        429,
        "auth_rate_limited",
        {"retry_after": retry_after},
        headers={"Retry-After": str(retry_after)},
    )


# --- The caller's passkeys --------------------------------------------------------


@router.post("/register/options", dependencies=[Depends(own_account())])
async def register_options(body: RegisterOptionsRequest, request: Request) -> dict[str, Any]:
    """Creation options for a new passkey (``navigator.credentials.create``).
    Needs the current password, checked like a login, and, when the account's
    TOTP factor is on, a TOTP or recovery code too (the proof ``/2fa/disable``
    asks: a passkey signs in without the code, so the password alone must not
    add one; 403 ``auth_2fa_required`` without it). Valid ``CEREMONY_TTL_S``,
    once, from this session only."""
    from app.routers.auth_totp import _check_code

    principal = _own_session(request)
    user = principal.user
    rp = await _relying_party(request)
    if user.totp_enabled_at is None:
        await _check_current_password(request, user, body.password, "passkey.added")
    else:
        await _check_current_password(
            request, user, body.password, "passkey.added", record_success=False
        )
        if body.code is None:
            await audit.record_request(
                request,
                "passkey.added",
                "failure",
                target=audit.user_target(user.id),
                details={"reason": "second_factor_missing"},
            )
            raise _error(403, "auth_2fa_required")
        await _check_code(request, user, body.code, "passkey.added", allow_recovery=True)
    existing = await PasskeyRepository.list_for_user(user.id)
    if len(existing) >= MAX_PASSKEYS_PER_USER:
        raise _error(409, "auth_passkey_limit")
    challenge = webauthn.ceremonies.issue(
        "register", rp.rp_id, user.id, webauthn.registration_binding(principal.session_id)
    )
    return webauthn.registration_options(rp, user, existing, challenge, _instance_name())


@router.post("/register/verify", response_model=PasskeyView, dependencies=[Depends(own_account())])
async def register_verify(body: RegisterVerifyRequest, request: Request) -> PasskeyView:
    """Store the passkey the browser created for the options of this session."""
    principal = _own_session(request)
    user = principal.user
    rp = await _relying_party(request)
    target = audit.user_target(user.id)
    try:
        challenge = webauthn.credential_challenge(body.credential)
        ceremony = webauthn.ceremonies.take(
            challenge, "register", webauthn.registration_binding(principal.session_id), rp.rp_id
        )
        if ceremony.user_id != user.id:
            raise webauthn.PasskeyInvalid("challenge_mismatch")
        verified, transports = webauthn.verify_registration(rp, body.credential, ceremony)
    except webauthn.PasskeyInvalid as exc:
        await audit.record_request(
            request, "passkey.added", "failure", target=target, details={"reason": exc.reason}
        )
        raise _error(400, "auth_passkey_invalid") from None
    try:
        stored = await PasskeyRepository.add(
            user.id,
            credential_id=verified.credential_id,
            public_key=verified.credential_public_key,
            sign_count=verified.sign_count,
            transports=transports,
            aaguid=verified.aaguid,
            name=body.name,
            backup_eligible=verified.credential_device_type.value == "multi_device",
            backed_up=verified.credential_backed_up,
        )
    except PasskeyLimitError:
        raise _error(409, "auth_passkey_limit") from None
    if stored is None:
        raise _error(409, "auth_passkey_exists")
    await audit.record_request(
        request, "passkey.added", "success", target=target, details={"passkey_id": stored.id}
    )
    return _view(stored)


@router.get("/credentials", response_model=list[PasskeyView], dependencies=[Depends(own_account())])
async def list_passkeys(request: Request) -> list[PasskeyView]:
    """The caller's passkeys: names and dates, never a key. Session only."""
    principal = _own_session(request)
    return [_view(p) for p in await PasskeyRepository.list_for_user(principal.user_id)]


@router.delete(
    "/credentials/{passkey_id}", response_model=OkResponse, dependencies=[Depends(own_account())]
)
async def delete_passkey(passkey_id: int, body: PasswordProof, request: Request) -> OkResponse:
    """Remove one of the caller's passkeys: the current password first. The
    password keeps working, so removing the last passkey locks nobody out."""
    principal = _own_session(request)
    user = principal.user
    await _check_current_password(request, user, body.password, "passkey.removed")
    if not await PasskeyRepository.delete(user.id, passkey_id):
        raise _error(404, "auth_passkey_not_found")
    await audit.record_request(
        request,
        "passkey.removed",
        "success",
        target=audit.user_target(user.id),
        details={"passkey_id": passkey_id},
    )
    return OkResponse()


# --- Sign-in ----------------------------------------------------------------------


async def _pending_user(request: Request, token: str) -> User:
    """The account of a live pending login from this client (not taken: the
    code or passkey is checked by ``POST /auth/login/2fa``)."""
    entry = totp.pending_logins.get(token)
    user = await UserRepository.get(entry.user_id) if entry is not None else None
    if (
        entry is None
        or user is None
        or user.disabled
        or user.updated_at != entry.generation
        or not hmac.compare_digest(_client(request), entry.client)
    ):
        raise _error(401, "auth_2fa_pending_invalid")
    return user


@router.post("/login/options", dependencies=[Depends(public())])
async def login_options(body: LoginOptionsRequest, request: Request) -> dict[str, Any]:
    """Request options (``navigator.credentials.get``). Public.

    Without ``pending_token``: a passwordless sign-in, any discoverable
    passkey, user verification required. With it: the second factor of that
    pending login, the account's passkeys only (404 ``auth_passkey_not_found``
    when it has none). 429 ``auth_rate_limited`` past ``OPTIONS_PER_MINUTE``
    per address."""
    _refuse_under_ingress(request)
    rp = await _relying_party(request)
    limiter = ratelimit.login_limiter
    address = limiter.address_key(request.scope)
    try:
        # The login limiter's address rules first: an address refused for its
        # failed sign-ins gets no challenge either.
        limiter.check(address, webauthn.OPTIONS_LIMITER_NAME)
    except ratelimit.LoginDelayed as delayed:
        raise _rate_limited(delayed.retry_after_s) from None
    # Per client: the address key, else (shared address) the client address as seen.
    bucket = address or client_ip(request.scope)
    if not webauthn.options_limiter.allow(bucket):
        raise _rate_limited(webauthn.options_limiter.retry_after(bucket))
    client = _client(request)
    if body.pending_token is None:
        try:
            challenge = webauthn.ceremonies.issue(
                "login", rp.rp_id, None, webauthn.login_binding(client)
            )
        except webauthn.CeremoniesFull:
            # Never a challenge, address or token: only that the table is full.
            logger.warning("Passkey sign-in refused: the table of live challenges is full")
            raise _rate_limited(int(webauthn.CEREMONY_TTL_S)) from None
        return webauthn.authentication_options(rp, challenge, None, require_user_verification=True)
    token = body.pending_token.get_secret_value()
    user = await _pending_user(request, token)
    passkeys = await PasskeyRepository.list_for_user(user.id) if webauthn.applicable(user) else []
    if not passkeys:
        raise _error(404, "auth_passkey_not_found")
    challenge = webauthn.ceremonies.issue(
        "second_factor",
        rp.rp_id,
        user.id,
        webauthn.second_factor_binding(hash_token(token), client),
    )
    return webauthn.authentication_options(rp, challenge, passkeys, require_user_verification=False)


async def check_assertion(
    request: Request,
    rp: webauthn.RelyingParty,
    credential: dict[str, Any],
    ceremony: webauthn.Ceremony,
    *,
    require_user_verification: bool,
    expected_user: User | None = None,
    device: str | None = None,
) -> tuple[User, StoredPasskey]:
    """An assertion checked like a password: the passkey's owner, the login
    delays for (address, owner), then the signature and counter. A failure is a
    failed login and a ``login`` audit row; raises ``PasskeyInvalid`` (or
    ``LoginDelayed``) for the caller to answer."""
    limiter = ratelimit.login_limiter
    address = limiter.address_key(request.scope)
    credential_id, _ = webauthn.assertion_credential_id(credential)
    passkey = await PasskeyRepository.get_by_credential_id(credential_id)
    owner = await UserRepository.get(passkey.user_id) if passkey is not None else None
    if (
        passkey is None
        or owner is None
        or owner.disabled
        or not webauthn.applicable(owner)
        or (expected_user is not None and owner.id != expected_user.id)
        or (ceremony.user_id is not None and owner.id != ceremony.user_id)
    ):
        target = audit.user_target(expected_user.id) if expected_user is not None else None
        await audit.record_request(
            request, "login", "failure", target=target, details={"reason": "passkey_unknown"}
        )
        raise webauthn.PasskeyInvalid("passkey_unknown")
    target = audit.user_target(owner.id)
    try:
        limiter.check(address, owner.username, device)
    except ratelimit.LoginDelayed as delayed:
        if delayed.first:
            await audit.record_request(
                request,
                "login",
                "refused" if delayed.reason == "ip" else "delayed",
                target=target,
                details={"reason": delayed.reason, "retry_after": delayed.retry_after_s},
            )
        raise
    try:
        verified = webauthn.verify_assertion(
            rp,
            credential,
            ceremony,
            passkey,
            owner,
            require_user_verification=require_user_verification,
        )
        if not await PasskeyRepository.record_use(
            passkey.id, passkey.sign_count, verified.new_sign_count, verified.credential_backed_up
        ):
            raise webauthn.PasskeyInvalid("concurrent_use")
    except webauthn.PasskeyInvalid as exc:
        await limiter.record_failure(address, owner.username, device)
        await audit.record_request(
            request,
            "login",
            "failure",
            target=target,
            details={"reason": f"passkey_{exc.reason}", "passkey_id": passkey.id},
        )
        raise
    limiter.record_success(address, owner.username, device)
    return owner, passkey


@router.post(
    "/login/verify",
    response_model=AuthSessionResponse,
    response_model_exclude_none=True,
    dependencies=[Depends(public())],
)
async def login_verify(
    body: LoginVerifyRequest, request: Request, response: Response
) -> AuthSessionResponse:
    """A passwordless sign-in: the assertion for options of this client opens
    the session. Public. 401 ``auth_passkey_invalid`` for an unknown, expired
    or used challenge, an unknown passkey, a wrong signature, origin or RP id,
    no user verification or a counter that did not grow; 429
    ``auth_rate_limited`` while a login delay runs."""
    _refuse_under_ingress(request)
    rp = await _relying_party(request)
    try:
        challenge = webauthn.credential_challenge(body.credential)
        ceremony = webauthn.ceremonies.take(
            challenge, "login", webauthn.login_binding(_client(request)), rp.rp_id
        )
    except webauthn.PasskeyInvalid as exc:
        await audit.record_request(
            request, "login", "failure", details={"reason": f"passkey_{exc.reason}"}
        )
        raise _error(401, "auth_passkey_invalid") from None
    try:
        user, passkey = await check_assertion(
            request, rp, body.credential, ceremony, require_user_verification=True
        )
    except webauthn.PasskeyInvalid:
        raise _error(401, "auth_passkey_invalid") from None
    except ratelimit.LoginDelayed as delayed:
        raise _rate_limited(delayed.retry_after_s) from None
    principal = await _start_session(request, response, user)
    await audit.record_request(
        request,
        "login",
        "success",
        principal=principal,
        target=audit.user_target(user.id),
        details={"method": "passkey", "passkey_id": passkey.id},
    )
    return await _session_payload(principal, _via(request), request)
