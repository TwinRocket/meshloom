"""``/api/auth/2fa/*`` and ``POST /api/auth/login/2fa``: the TOTP second factor (Access T50).

The caller's own factor: ``GET /auth/2fa`` (state, never the secret), ``POST
/auth/2fa/enrol`` (current password; returns the secret and its ``otpauth://``
URI, the one time they leave the server), ``POST /auth/2fa/confirm`` (the first
valid code turns it on and returns the recovery codes, shown once), ``POST
/auth/2fa/disable`` (password and a code), ``POST /auth/2fa/recovery`` (password
and a TOTP code; new recovery codes). ``POST /auth/login/2fa`` is the second
step of a login whose password checked (public, ``PUBLIC_ROUTES``). Codes are
checked under the login delays (``app/auth/ratelimit.py``): a wrong code counts
as a failed login for the client's address and the account's name. Rules:
``app/auth/totp.py``, ``docs/dev/auth.md``, "Second factor". Nothing here logs a
password, a code, a secret or a pending token.
"""

from __future__ import annotations

import hmac

from fastapi import APIRouter, Depends, Request, Response
from pydantic import BaseModel, ConfigDict, Field, SecretStr

from app.auth import audit, device_cookie, ratelimit, sessions, totp
from app.auth.middleware import client_ip, second_factor_enrolment_required
from app.auth.models import User
from app.auth.passwords import MAX_PASSWORD_LENGTH
from app.auth.permissions import own_account, public
from app.repository.auth import TotpRepository, UserRepository
from app.routers.auth import (
    AuthSessionResponse,
    OkResponse,
    _check_current_password,
    _error,
    _instance_name,
    _refuse_under_ingress,
    _remember_device,
    _require,
    _secure,
    _session_payload,
    _start_session,
    _via,
)

router = APIRouter(prefix="/auth", tags=["auth"])

# A TOTP code or a recovery code, with the spaces or dashes people type.
_MAX_CODE_LENGTH = 32


class TotpStatus(BaseModel):
    enabled: bool = Field(description="The second factor is on.")
    pending: bool = Field(description="Enrolment started, waiting for the first code.")
    recovery_codes_left: int = Field(description="Unused recovery codes.")
    enrolment_required: bool = Field(
        description="require_2fa_for_admins confines this account to enrolment until it enrols."
    )


class TotpEnrolRequest(BaseModel):
    model_config = ConfigDict(hide_input_in_errors=True)

    password: SecretStr = Field(max_length=MAX_PASSWORD_LENGTH)


class TotpEnrolment(BaseModel):
    """Shown once: no other route returns the secret."""

    secret: str
    otpauth_uri: str
    issuer: str
    digits: int = totp.DIGITS
    period: int = totp.PERIOD_S


class TotpCodeRequest(BaseModel):
    model_config = ConfigDict(hide_input_in_errors=True)

    code: SecretStr = Field(min_length=1, max_length=_MAX_CODE_LENGTH)


class TotpProofRequest(BaseModel):
    model_config = ConfigDict(hide_input_in_errors=True)

    password: SecretStr = Field(max_length=MAX_PASSWORD_LENGTH)
    code: SecretStr = Field(min_length=1, max_length=_MAX_CODE_LENGTH)


class RecoveryCodes(BaseModel):
    """Shown once: only their hashes are stored."""

    recovery_codes: list[str]


class LoginSecondFactorRequest(BaseModel):
    model_config = ConfigDict(hide_input_in_errors=True)

    pending_token: SecretStr = Field(min_length=1, max_length=128)
    code: SecretStr = Field(min_length=1, max_length=_MAX_CODE_LENGTH)


def _applicable(user: User) -> None:
    """Only a local account with a password, outside ``kiosk``, has a second
    factor: Home Assistant and kiosk identities sign in another way."""
    if user.provider != "local" or not user.has_password or user.role == "kiosk":
        raise _error(403, "auth_2fa_not_applicable")


def _rate_limited(delayed: ratelimit.LoginDelayed):
    return _error(
        429,
        "auth_rate_limited",
        {"retry_after": delayed.retry_after_s},
        headers={"Retry-After": str(delayed.retry_after_s)},
    )


async def _check_code(
    request: Request,
    user: User,
    code: SecretStr,
    action: audit.AuditAction,
    *,
    allow_recovery: bool,
    status: int = 403,
    device: str | None = None,
) -> totp.FactorKind:
    """A code checked like a password: the login delays first, then the code;
    a wrong one is a failed login for (address, name) and an audit row."""
    limiter = ratelimit.login_limiter
    address = limiter.address_key(request.scope)
    target = audit.user_target(user.id)
    try:
        limiter.check(address, user.username, device)
    except ratelimit.LoginDelayed as delayed:
        if delayed.first:
            await audit.record_request(
                request,
                action,
                "refused" if delayed.reason == "ip" else "delayed",
                target=target,
                details={"reason": delayed.reason, "retry_after": delayed.retry_after_s},
            )
        raise _rate_limited(delayed) from None
    kind = await totp.verify_code(user.id, code.get_secret_value(), allow_recovery=allow_recovery)
    if kind is None:
        await limiter.record_failure(address, user.username, device)
        await audit.record_request(
            request, action, "failure", target=target, details={"reason": "second_factor_invalid"}
        )
        raise _error(status, "auth_2fa_invalid")
    limiter.record_success(address, user.username, device)
    return kind


@router.get("/2fa", response_model=TotpStatus, dependencies=[Depends(own_account())])
async def get_totp_status(request: Request) -> TotpStatus:
    """The caller's second factor: on, pending, recovery codes left. Never the secret."""
    principal = _require(request)
    state = await TotpRepository.get_state(principal.user_id)
    enabled = state is not None and state.enabled_at is not None
    return TotpStatus(
        enabled=enabled,
        pending=state is not None and not enabled and state.secret is not None,
        recovery_codes_left=await TotpRepository.codes_left(principal.user_id) if enabled else 0,
        enrolment_required=await second_factor_enrolment_required(principal),
    )


@router.post("/2fa/enrol", response_model=TotpEnrolment, dependencies=[Depends(own_account())])
async def enrol_totp(body: TotpEnrolRequest, request: Request) -> TotpEnrolment:
    """Start enrolment: a new secret, pending until ``/2fa/confirm``. Needs the
    current password (checked like a login); 409 ``auth_2fa_already_enabled``
    while a factor is on (disable it first). Enrolling again replaces a pending
    secret."""
    principal = _require(request)
    user = principal.user
    _applicable(user)
    if user.totp_enabled_at is not None:
        raise _error(409, "auth_2fa_already_enabled")
    await _check_current_password(request, user, body.password, "totp.enrolled")
    secret = totp.new_secret()
    if not await TotpRepository.set_pending_secret(user.id, secret):
        raise _error(409, "auth_2fa_already_enabled")
    issuer = _instance_name() or totp.ISSUER_FALLBACK
    return TotpEnrolment(
        secret=secret,
        otpauth_uri=totp.provisioning_uri(secret, user.username, issuer),
        issuer=issuer,
    )


@router.post("/2fa/confirm", response_model=RecoveryCodes, dependencies=[Depends(own_account())])
async def confirm_totp(body: TotpCodeRequest, request: Request) -> RecoveryCodes:
    """The first valid code turns the factor on: returns the recovery codes
    (once) and signs the account out everywhere else."""
    principal = _require(request)
    user = principal.user
    _applicable(user)
    state = await TotpRepository.get_state(user.id)
    if state is not None and state.enabled_at is not None:
        raise _error(409, "auth_2fa_already_enabled")
    if state is None or state.secret is None:
        raise _error(409, "auth_2fa_not_pending")
    secret = state.secret.get_secret_value()
    counter = totp.matching_counter(secret, body.code.get_secret_value())
    target = audit.user_target(user.id)
    if counter is None:
        await audit.record_request(
            request,
            "totp.enrolled",
            "failure",
            target=target,
            details={"reason": "second_factor_invalid"},
        )
        raise _error(400, "auth_2fa_invalid")
    codes = totp.new_recovery_codes()
    if not await TotpRepository.enable(
        user.id, secret, counter, await totp.hash_recovery_codes(codes)
    ):
        raise _error(409, "auth_2fa_not_pending")
    revoked = await sessions.sign_out_user(user.id, except_session_id=principal.session_id)
    await audit.record_request(
        request, "totp.enrolled", "success", target=target, details={"sessions_revoked": revoked}
    )
    return RecoveryCodes(recovery_codes=codes)


@router.post("/2fa/disable", response_model=OkResponse, dependencies=[Depends(own_account())])
async def disable_totp(body: TotpProofRequest, request: Request) -> OkResponse:
    """Turn the factor off: the current password and a TOTP or recovery code."""
    principal = _require(request)
    user = principal.user
    _applicable(user)
    if user.totp_enabled_at is None:
        raise _error(409, "auth_2fa_not_enabled")
    await _check_current_password(
        request, user, body.password, "totp.disabled", record_success=False
    )
    await _check_code(request, user, body.code, "totp.disabled", allow_recovery=True)
    await TotpRepository.disable(user.id)
    await audit.record_request(
        request, "totp.disabled", "success", target=audit.user_target(user.id)
    )
    return OkResponse()


@router.post("/2fa/recovery", response_model=RecoveryCodes, dependencies=[Depends(own_account())])
async def regenerate_recovery_codes(body: TotpProofRequest, request: Request) -> RecoveryCodes:
    """New recovery codes (the old ones stop working): the current password and
    a TOTP code (not a recovery code)."""
    principal = _require(request)
    user = principal.user
    _applicable(user)
    if user.totp_enabled_at is None:
        raise _error(409, "auth_2fa_not_enabled")
    await _check_current_password(
        request, user, body.password, "totp.recovery_regenerated", record_success=False
    )
    await _check_code(request, user, body.code, "totp.recovery_regenerated", allow_recovery=False)
    codes = totp.new_recovery_codes()
    if not await TotpRepository.replace_codes(user.id, await totp.hash_recovery_codes(codes)):
        raise _error(409, "auth_2fa_not_enabled")
    await audit.record_request(
        request, "totp.recovery_regenerated", "success", target=audit.user_target(user.id)
    )
    return RecoveryCodes(recovery_codes=codes)


@router.post(
    "/login/2fa",
    response_model=AuthSessionResponse,
    response_model_exclude_none=True,
    dependencies=[Depends(public())],
)
async def login_second_factor(
    body: LoginSecondFactorRequest, request: Request, response: Response
) -> AuthSessionResponse:
    """The second step of a login: the pending-login token from ``POST
    /auth/login`` and a TOTP or recovery code open the session. Public.

    401 ``auth_2fa_pending_invalid`` when the token is unknown, expired, used up
    (``PENDING_MAX_ATTEMPTS`` wrong codes), in use by a concurrent request, sent
    by another client than the password step (address or User-Agent: the token
    is then discarded), or the account changed since: enter the password again.
    401 ``auth_2fa_invalid`` for a wrong or replayed code. 429
    ``auth_rate_limited`` while a login delay runs (the attempt is not counted
    against the token).
    """
    _refuse_under_ingress(request)
    token = body.pending_token.get_secret_value()
    # Taken out of the table while the code is checked: single use under concurrency.
    entry = totp.pending_logins.take(token)
    user = await UserRepository.get(entry.user_id) if entry is not None else None
    if (
        entry is None
        or user is None
        or user.disabled
        or user.updated_at != entry.generation
        or user.totp_enabled_at is None
    ):
        raise _error(401, "auth_2fa_pending_invalid")
    client = totp.client_binding(client_ip(request.scope), request.headers.get("user-agent"))
    if not hmac.compare_digest(client, entry.client):
        await audit.record_request(
            request,
            "login",
            "refused",
            target=audit.user_target(user.id),
            details={"reason": "pending_client_mismatch"},
        )
        raise _error(401, "auth_2fa_pending_invalid")
    secure = _secure(request)
    presented = device_cookie.read(request.headers.get("cookie"), secure=secure)
    device = await device_cookie.known_device(presented, user.username)
    try:
        kind = await _check_code(
            request, user, body.code, "login", allow_recovery=True, status=401, device=device
        )
    except BaseException as exc:
        totp.pending_logins.put_back(token, entry, failed=getattr(exc, "status_code", None) == 401)
        raise
    principal = await _start_session(request, response, user)
    await _remember_device(
        response, user, device or (presented.device_id if presented else None), secure
    )
    details: dict[str, object] = {"factor": kind}
    if kind == "recovery":
        details["remaining"] = await TotpRepository.codes_left(user.id)
    await audit.record_request(
        request,
        "login",
        "success",
        principal=principal,
        target=audit.user_target(user.id),
        details=details,
    )
    return await _session_payload(principal, _via(request), request)
