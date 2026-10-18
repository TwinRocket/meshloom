"""``/api/auth/*``: first-run setup, login, logout, sessions and the own profile.

``GET/PATCH /auth/settings`` holds the access settings an admin tunes (the
trusted proxies, the trusted networks, the audit retention, the role of new
Home Assistant users);
``/auth/users`` is the admin's account management (create, change the role,
disable, delete, set a password, sign out); ``/auth/tokens`` (API and device
tokens) lives in ``app/routers/auth_tokens.py``, the kiosk launcher's sign-in
(``/auth/kiosk-bootstrap``, ``/auth/kiosk-session``) in
``app/routers/auth_kiosk.py``. Every change writes an audit row;
no change may leave the instance without an enabled administrator (409
``auth_last_admin``). Contract and rules: ``docs/dev/auth.md``. ``GET /auth/session``,
``POST /auth/setup``, ``POST /auth/login``, ``POST /auth/logout`` and ``GET
/auth/kiosk-session`` are public
(``PUBLIC_ROUTES``, ``app/auth/middleware.py``); the others need a principal.
Errors are coded (``{"detail": {"code", "message", "params"?}}``), translated
by the frontend from ``errors.<code>``. Nothing here logs a password, a code or
a session secret.
"""

from __future__ import annotations

import asyncio
import functools
import logging
import re
from collections.abc import Awaitable, Callable
from typing import Any, Literal
from urllib.parse import urlsplit

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from pydantic import BaseModel, ConfigDict, Field, SecretStr

from app.auth import audit, device_cookie, ratelimit, sessions, totp, webauthn
from app.auth.middleware import client_ip, second_factor_enrolment_required
from app.auth.models import (
    MAX_AUDIT_RETENTION_DAYS,
    MIN_AUDIT_RETENTION_DAYS,
    USERNAME_MAX_LENGTH,
    AuthAuditEntry,
    AuthSettings,
    HostGuardMode,
    Role,
    TrustedNetworkRule,
    User,
)
from app.auth.passwords import (
    MAX_PASSWORD_LENGTH,
    MIN_PASSWORD_LENGTH,
    hash_password_async,
    password_policy_violation,
)
from app.auth.permissions import ACCESS_ATTRIBUTE, own_account, public, require
from app.auth.principal import Principal, Via, get_principal
from app.auth.providers import trusted_network
from app.auth.providers.password import (
    VerificationBusy,
    authenticate,
    authenticate_with_hash,
)
from app.auth.setup_code import (
    SetupUnavailable,
    check_setup_code,
    clear_setup_code,
    setup_code_required,
)
from app.middleware import host_guard
from app.middleware.host_guard import RejectedHost
from app.middleware.proxy_headers import MAX_TRUSTED_PROXIES, normalize_proxies
from app.repository.auth import (
    AuthAuditRepository,
    AuthSessionRepository,
    AuthSettingsRepository,
    InvalidUsernameError,
    LastAdminError,
    PasskeyRepository,
    UsernameTakenError,
    UserRepository,
)
from app.routers import auth_kiosk, auth_tokens
from app.security import basic_compat_active, invalidate_basic_cache

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/auth", tags=["auth"])

# Audit log pages (``GET /auth/audit``). ASCII digits only: ``str.isdigit()``
# accepts "²", which ``int()`` refuses.
_CURSOR = re.compile(r"[0-9]{1,19}")
AUDIT_PAGE_DEFAULT = 100
AUDIT_PAGE_MAX = 500

# Wrong installation codes are answered one at a time, this far apart.
SETUP_FAILURE_DELAY_S = 1.0

_setup_lock = asyncio.Lock()

_MESSAGES = {
    "auth_setup_done": "The administrator already exists",
    "auth_setup_code_invalid": "Invalid installation code",
    "auth_password_policy": "Password refused by the password policy",
    "auth_username_invalid": "Invalid username",
    "auth_invalid_credentials": "Invalid credentials",
    "auth_required": "Authentication required",
    "auth_forbidden": "Not allowed for this account",
    "auth_session_not_found": "Session not found",
    "auth_setup_unavailable": "Setup is not available yet: check the server log and retry",
    "auth_busy": "Too many sign-in checks at once: retry in a moment",
    "auth_rate_limited": "Too many attempts: retry later",
    "auth_ingress_only": "Home Assistant signs you in here: open Meshloom from its sidebar",
    "auth_audit_cursor_invalid": "Invalid audit cursor",
    "auth_user_not_found": "User not found",
    "auth_username_taken": "This username is already taken",
    "auth_last_admin": "This is the last enabled administrator: keep at least one",
    "auth_password_required": "This role needs an initial password",
    "auth_password_not_applicable": (
        "This account has no password: Home Assistant and kiosk accounts sign in another way"
    ),
    "auth_trusted_proxies_invalid": (
        "Not an address or network, a network wider than /8 or /16, or too many trusted proxies"
    ),
    "auth_trusted_networks_invalid": (
        "Not a network, a network wider than /8 or /16, a network listed twice, "
        "or too many trusted networks"
    ),
    "auth_trusted_network_user_invalid": "A trusted network names no enabled account",
    "auth_public_urls_invalid": (
        "Not an address of the form https://host[:port][/prefix], or too many public addresses"
    ),
    "auth_host_guard_would_lock_out": (
        "Enforcing the Host guard would reject the address you are using now: "
        "declare it as a public address first"
    ),
    "auth_trusted_network_overlaps_proxy": "A trusted network overlaps a trusted proxy",
    "auth_trusted_network_loopback": (
        "A trusted network covers loopback, where a local proxy or tunnel would bring every visitor"
    ),
    "auth_trusted_network_overlaps_gateway": (
        "A trusted network covers the Docker gateway while a public address is configured"
    ),
    "auth_2fa_invalid": "Invalid or already used code",
    "auth_2fa_pending_invalid": "This sign-in expired: enter your password again",
    "auth_2fa_already_enabled": "Two-factor authentication is already on",
    "auth_2fa_not_pending": "Start the two-factor setup first",
    "auth_2fa_not_enabled": "Two-factor authentication is off",
    "auth_2fa_not_applicable": (
        "This account has no password: Home Assistant and kiosk accounts sign in another way"
    ),
}


def _error(
    status: int, code: str, params: dict | None = None, headers: dict[str, str] | None = None
) -> HTTPException:
    detail: dict = {"code": code, "message": _MESSAGES.get(code, code)}
    if params:
        detail["params"] = params
    return HTTPException(status_code=status, detail=detail, headers=headers)


def _policy_error(reason: str) -> HTTPException:
    return _error(
        400,
        "auth_password_policy",
        {"reason": reason, "min_length": MIN_PASSWORD_LENGTH},
    )


def _require(request: Request) -> Principal:
    principal = get_principal(request)
    if principal is None:
        raise _error(401, "auth_required")
    return principal


def _via(request: Request) -> Via:
    return request.scope.get("auth_via", "lan")


def _secure(request: Request) -> bool:
    return bool(request.scope.get("auth_secure", False))


# --- Models -------------------------------------------------------------------


class AuthMethods(BaseModel):
    password: bool = True
    ingress: bool = False
    # A WebAuthn ceremony can run on this request (``app/auth/webauthn.py``:
    # https on a declared public address, the relay, or localhost).
    passkey: bool = False


class AuthCompat(BaseModel):
    basic: bool = False


class SessionUser(BaseModel):
    id: int
    username: str
    display_name: str
    role: Role
    # True after an admin set the password: every other route answers 403
    # ``auth_password_change_required`` until ``PATCH /auth/me`` changes it.
    must_change_password: bool = False
    # Whether the account's TOTP second factor is on.
    totp_enabled: bool = False
    # ``require_2fa_for_admins`` and no second factor yet: every other route
    # answers 403 ``auth_2fa_enrolment_required`` until ``/auth/2fa/confirm``.
    second_factor_enrolment_required: bool = False


class SecondFactorChallenge(BaseModel):
    """The password checked; ``POST /auth/login/2fa`` with this token and a code
    opens the session (``app/auth/totp.py``)."""

    pending_token: str
    methods: list[Literal["totp", "recovery", "passkey"]]
    expires_in: int


class InstanceInfo(BaseModel):
    name: str | None = None
    version: str


class AuthSessionResponse(BaseModel):
    """``GET /auth/session``. Anonymous: no instance name or version (the
    answer can reach the Internet through a relay)."""

    authenticated: bool
    setup_required: bool
    setup_code_required: bool
    methods: AuthMethods
    auth_compat: AuthCompat
    # Whether the server saw this request as https (``scope["scheme"]``, from a
    # trusted proxy's X-Forwarded-Proto behind one): the session cookie is the
    # ``__Host-`` Secure one only then. A browser on https that reads false sits
    # behind a proxy Meshloom does not trust.
    secure: bool = False
    # The installation code, for the kiosk screen alone (``app/services/kiosk.py``).
    kiosk_setup_code: str | None = None
    user: SessionUser | None = None
    via: Via | None = None
    instance: InstanceInfo | None = None
    # ``POST /auth/login`` only, for an account with a second factor (no session yet).
    second_factor: SecondFactorChallenge | None = None


class SetupRequest(BaseModel):
    model_config = ConfigDict(hide_input_in_errors=True)

    username: str = Field(min_length=1, max_length=USERNAME_MAX_LENGTH)
    password: SecretStr = Field(max_length=MAX_PASSWORD_LENGTH)
    display_name: str = Field(default="", max_length=128)
    setup_code: SecretStr | None = Field(default=None, max_length=64)


class LoginRequest(BaseModel):
    model_config = ConfigDict(hide_input_in_errors=True)

    username: str = Field(min_length=1, max_length=256)
    password: SecretStr = Field(min_length=1, max_length=MAX_PASSWORD_LENGTH)


class SessionInfo(BaseModel):
    id: str
    created_at: int
    last_seen_at: int
    expires_at: int
    ip: str | None = None
    user_agent: str | None = None
    via: str | None = None
    current: bool = False


class RevokedCount(BaseModel):
    revoked: int


class MeResponse(BaseModel):
    id: int
    username: str
    display_name: str
    role: Role
    provider: str
    has_password: bool
    must_change_password: bool
    totp_enabled: bool = False
    created_at: int


class MePatch(BaseModel):
    model_config = ConfigDict(hide_input_in_errors=True)

    display_name: str | None = Field(default=None, max_length=128)
    current_password: SecretStr | None = Field(default=None, max_length=MAX_PASSWORD_LENGTH)
    new_password: SecretStr | None = Field(default=None, max_length=MAX_PASSWORD_LENGTH)


class OkResponse(BaseModel):
    ok: bool = True


# --- Helpers ------------------------------------------------------------------


def _instance_name() -> str | None:
    try:
        from app.services.radio_runtime import radio_runtime

        mc = radio_runtime.meshcore
        self_info = getattr(mc, "self_info", None) if mc is not None else None
        name = self_info.get("name") if isinstance(self_info, dict) else None
        return name or None
    except Exception:
        return None


async def _session_payload(
    principal: Principal | None, via: Via, request: Request
) -> AuthSessionResponse:
    """The session state. Under ingress the only sign-in method is Home
    Assistant's: no password form (no cookie could keep its session)."""
    setup_required = not await UserRepository.any_exist()
    under_ingress = via == "ingress"
    payload = AuthSessionResponse(
        authenticated=principal is not None,
        setup_required=setup_required,
        setup_code_required=setup_required and await setup_code_required(),
        methods=AuthMethods(
            password=not under_ingress,
            ingress=under_ingress,
            passkey=await webauthn.available(request.scope),
        ),
        auth_compat=AuthCompat(basic=await basic_compat_active()),
        secure=_secure(request),
    )
    if setup_required:
        from app.services.kiosk import displayed_setup_code

        payload.kiosk_setup_code = await displayed_setup_code(request.scope, request.headers)
    if principal is not None:
        from app.version_info import get_app_build_info

        user = principal.user
        payload.user = SessionUser(
            id=user.id,
            username=user.username,
            display_name=user.display_name,
            role=user.role,
            must_change_password=user.must_change_password,
            totp_enabled=user.totp_enabled_at is not None,
            second_factor_enrolment_required=await second_factor_enrolment_required(principal),
        )
        payload.via = principal.via
        payload.instance = InstanceInfo(name=_instance_name(), version=get_app_build_info().version)
    return payload


def _refuse_under_ingress(request: Request) -> None:
    """Setup and login open a cookie session, which ingress never has: Home
    Assistant's identity is the way in there (``app/auth/providers/ingress.py``)."""
    if _via(request) == "ingress":
        raise _error(403, "auth_ingress_only")


async def _start_session(request: Request, response: Response, user: User) -> Principal:
    """Create a session and set its cookie (never under ingress: refused before)."""
    via = _via(request)
    issued = await sessions.issue_session(
        user,
        ip=client_ip(request.scope),
        user_agent=request.headers.get("user-agent"),
        via=via,
    )
    response.headers.append(
        "set-cookie",
        sessions.session_cookie_header(
            issued.token, secure=_secure(request), max_age=issued.max_age
        ),
    )
    return Principal(user=user, via=via, credential="session", session_id=issued.session.id)


async def _remember_device(
    response: Response, user: User, device_id: str | None, secure: bool
) -> None:
    """Set the known-device cookie after a password login (``app/auth/device_cookie.py``)."""
    # Read again: a rehash on this login moved updated_at after ``user`` was read.
    current = await UserRepository.get(user.id)
    if current is None or not current.has_password:
        return
    value = device_cookie.issue(current.id, current.updated_at, device_id)
    response.headers.append("set-cookie", device_cookie.cookie_header(value, secure=secure))


def _me(user: User) -> MeResponse:
    return MeResponse(
        id=user.id,
        username=user.username,
        display_name=user.display_name,
        role=user.role,
        provider=user.provider,
        has_password=user.has_password,
        must_change_password=user.must_change_password,
        totp_enabled=user.totp_enabled_at is not None,
        created_at=user.created_at,
    )


async def _check_current_password(
    request: Request,
    user: User,
    current_password: SecretStr | None,
    action: audit.AuditAction = "password.changed",
    *,
    record_success: bool = True,
) -> None:
    """The current password of a self-service change, checked like a login:
    the login delays first (keyed on the client's address and the account's
    name, ``app/auth/ratelimit.py``), then the bounded argon2id queue (503
    ``auth_busy``). A stolen session cannot guess it faster than the login page.
    Refusals are audited under ``action``. With ``record_success=False`` (a code
    check follows) a right password does not clear the login delays: the caller
    records the success once the whole check passed."""
    current = current_password.get_secret_value() if current_password else ""
    target = audit.user_target(user.id)
    if not current:
        await audit.record_request(
            request,
            action,
            "failure",
            target=target,
            details={"reason": "current_password_invalid"},
        )
        raise _error(403, "auth_invalid_credentials")
    limiter = ratelimit.login_limiter
    address = limiter.address_key(request.scope)
    try:
        limiter.check(address, user.username)
    except ratelimit.LoginDelayed as delayed:
        if delayed.first:
            await audit.record_request(
                request,
                action,
                "refused" if delayed.reason == "ip" else "delayed",
                target=target,
                details={"reason": delayed.reason, "retry_after": delayed.retry_after_s},
            )
        raise _error(
            429,
            "auth_rate_limited",
            {"retry_after": delayed.retry_after_s},
            headers={"Retry-After": str(delayed.retry_after_s)},
        ) from None
    try:
        result = await authenticate_with_hash(user.username, current)
    except VerificationBusy:
        raise _error(503, "auth_busy") from None
    if result is None or result[0].id != user.id:
        await limiter.record_failure(address, user.username)
        await audit.record_request(
            request,
            action,
            "failure",
            target=target,
            details={"reason": "current_password_invalid"},
        )
        raise _error(403, "auth_invalid_credentials")
    if record_success:
        limiter.record_success(address, user.username)


# --- Routes -------------------------------------------------------------------


@router.get(
    "/session",
    response_model=AuthSessionResponse,
    response_model_exclude_none=True,
    dependencies=[Depends(public())],
)
async def get_session(request: Request) -> AuthSessionResponse:
    """Who is signed in, and what the login page needs. Public."""
    return await _session_payload(get_principal(request), _via(request), request)


@router.post(
    "/setup",
    response_model=AuthSessionResponse,
    response_model_exclude_none=True,
    dependencies=[Depends(public())],
)
async def setup(body: SetupRequest, request: Request, response: Response) -> AuthSessionResponse:
    """Create the first administrator. Public while no account exists, 409 after.

    403 ``auth_ingress_only`` under ingress: the first Home Assistant user to
    open the sidebar becomes the administrator there."""
    _refuse_under_ingress(request)
    async with _setup_lock:
        if await UserRepository.any_exist():
            raise _error(409, "auth_setup_done")
        code = body.setup_code.get_secret_value() if body.setup_code else None
        try:
            code_ok = await check_setup_code(code)
        except SetupUnavailable:
            await audit.record_request(
                request, "setup", "error", details={"reason": "setup_unavailable"}
            )
            raise _error(503, "auth_setup_unavailable") from None
        if not code_ok:
            await audit.record_request(
                request, "setup", "failure", details={"reason": "setup_code_invalid"}
            )
            await asyncio.sleep(SETUP_FAILURE_DELAY_S)
            raise _error(403, "auth_setup_code_invalid")
        password = body.password.get_secret_value()
        if reason := password_policy_violation(password, body.username):
            raise _policy_error(reason)
        try:
            user = await UserRepository.create(
                body.username,
                "admin",
                display_name=body.display_name,
                password_hash=await hash_password_async(password),
            )
        except (InvalidUsernameError, UsernameTakenError):
            raise _error(400, "auth_username_invalid") from None
        await clear_setup_code()
    logger.info("Administrator account %r created", user.username)
    principal = await _start_session(request, response, user)
    await audit.record_request(
        request,
        "setup",
        "success",
        principal=principal,
        target=audit.user_target(user.id),
        details={"role": user.role},
    )
    return await _session_payload(principal, _via(request), request)


@router.post(
    "/login",
    response_model=AuthSessionResponse,
    response_model_exclude_none=True,
    dependencies=[Depends(public())],
)
async def login(body: LoginRequest, request: Request, response: Response) -> AuthSessionResponse:
    """Check a username and password and open a session. Public.

    One generic 401 for an unknown name, a wrong password, a disabled account
    and an account without a password (ingress, kiosk). 403
    ``auth_ingress_only`` under ingress, checked before any password work. 429
    ``auth_rate_limited`` with ``params.retry_after`` and ``Retry-After`` while
    a login delay runs (``app/auth/ratelimit.py``), checked before the argon2id
    queue so a refused attempt costs no password check.
    """
    _refuse_under_ingress(request)
    limiter = ratelimit.login_limiter
    address = limiter.address_key(request.scope)
    secure = _secure(request)
    presented = device_cookie.read(request.headers.get("cookie"), secure=secure)
    device = await device_cookie.known_device(presented, body.username)
    try:
        limiter.check(address, body.username, device)
    except ratelimit.LoginDelayed as delayed:
        # One row per delay, not per refused attempt (app/auth/ratelimit.py, "Audit").
        if delayed.first:
            await audit.record_request(
                request,
                "login",
                "refused" if delayed.reason == "ip" else "delayed",
                target=await audit.typed_username_target(body.username),
                details={"reason": delayed.reason, "retry_after": delayed.retry_after_s},
            )
        raise _error(
            429,
            "auth_rate_limited",
            {"retry_after": delayed.retry_after_s},
            headers={"Retry-After": str(delayed.retry_after_s)},
        ) from None
    try:
        user = await authenticate(body.username, body.password.get_secret_value())
    except VerificationBusy:
        raise _error(503, "auth_busy") from None
    if user is None:
        await limiter.record_failure(address, body.username, device)
        await audit.record_request(
            request,
            "login",
            "failure",
            target=await audit.typed_username_target(body.username),
        )
        raise _error(401, "auth_invalid_credentials")
    if user.totp_enabled_at is not None:
        return await _second_factor_challenge(request, user)
    limiter.record_success(address, body.username, device)
    principal = await _start_session(request, response, user)
    await _remember_device(
        response, user, device or (presented.device_id if presented else None), secure
    )
    await audit.record_request(
        request, "login", "success", principal=principal, target=audit.user_target(user.id)
    )
    return await _session_payload(principal, _via(request), request)


async def _second_factor_challenge(request: Request, user: User) -> AuthSessionResponse:
    """A password that checked for an account with a second factor: no session
    and no limiter success yet (a wrong code counts as a failed login), only a
    pending-login token for ``POST /auth/login/2fa`` (``app/auth/totp.py``)."""
    # Read again: a rehash on this login moved updated_at after ``user`` was read.
    current = await UserRepository.get(user.id)
    if current is None:
        raise _error(401, "auth_invalid_credentials")
    token = totp.pending_logins.issue(
        current.id,
        current.updated_at,
        totp.client_binding(client_ip(request.scope), request.headers.get("user-agent")),
    )
    await audit.record_request(
        request, "login", "pending", target=audit.user_target(user.id), details={"factor": "totp"}
    )
    payload = await _session_payload(None, _via(request), request)
    methods: list[Literal["totp", "recovery", "passkey"]] = ["totp", "recovery"]
    # A passkey too, when the account has one and this request can run a ceremony.
    if payload.methods.passkey and await PasskeyRepository.count_for_user(current.id):
        methods.append("passkey")
    payload.second_factor = SecondFactorChallenge(
        pending_token=token,
        methods=methods,
        expires_in=int(totp.PENDING_TTL_S),
    )
    return payload


@router.post("/logout", response_model=OkResponse, dependencies=[Depends(public())])
async def logout(request: Request, response: Response) -> OkResponse:
    """End the current session and clear the cookie. Public (idempotent)."""
    principal = get_principal(request)
    if principal is not None and principal.session_id is not None:
        await sessions.revoke_session(principal.session_id)
        await audit.record_request(
            request, "logout", "success", target=f"session:{principal.session_id}"
        )
    if _via(request) != "ingress":
        response.headers.append("set-cookie", sessions.clear_cookie_header(secure=_secure(request)))
        response.headers["Clear-Site-Data"] = '"cookies"'
    return OkResponse()


@router.get("/sessions", response_model=list[SessionInfo], dependencies=[Depends(own_account())])
async def list_sessions(request: Request) -> list[SessionInfo]:
    """The signed-in user's active sessions, newest first."""
    principal = _require(request)
    rows = await AuthSessionRepository.list_for_user(principal.user_id)
    return [
        SessionInfo(
            **row.model_dump(exclude={"user_id", "revoked_at"}),
            current=row.id == principal.session_id,
        )
        for row in rows
    ]


@router.delete("/sessions", response_model=RevokedCount, dependencies=[Depends(own_account())])
async def revoke_other_sessions(request: Request) -> RevokedCount:
    """Sign out everywhere except here."""
    principal = _require(request)
    count = await sessions.revoke_user_sessions(
        principal.user_id, except_session_id=principal.session_id
    )
    await audit.record_request(
        request,
        "session.revoked",
        "success",
        target=audit.user_target(principal.user_id),
        details={"scope": "others", "count": count},
    )
    return RevokedCount(revoked=count)


@router.delete(
    "/sessions/{session_id}", response_model=OkResponse, dependencies=[Depends(own_account())]
)
async def revoke_session(session_id: str, request: Request) -> OkResponse:
    """Revoke one of the signed-in user's sessions (404 for anyone else's)."""
    principal = _require(request)
    own = await AuthSessionRepository.list_for_user(principal.user_id)
    if not any(row.id == session_id for row in own):
        raise _error(404, "auth_session_not_found")
    await sessions.revoke_session(session_id)
    await audit.record_request(
        request, "session.revoked", "success", target=f"session:{session_id}"
    )
    return OkResponse()


@router.get("/me", response_model=MeResponse, dependencies=[Depends(own_account())])
async def get_me(request: Request) -> MeResponse:
    """The signed-in user's profile."""
    return _me(_require(request).user)


@router.patch("/me", response_model=MeResponse, dependencies=[Depends(own_account())])
async def patch_me(body: MePatch, request: Request) -> MeResponse:
    """Change the display name or the password (the current one is required).

    A password change revokes every other session of the user. Refused to
    ``kiosk`` accounts and to accounts without a password.
    """
    principal = _require(request)
    user = principal.user
    if user.role == "kiosk":
        raise _error(403, "auth_forbidden")
    if body.new_password is not None:
        target = audit.user_target(user.id)
        if not user.has_password:
            await audit.record_request(
                request,
                "password.changed",
                "refused",
                target=target,
                details={"reason": "password_not_applicable"},
            )
            raise _error(403, "auth_forbidden")
        await _check_current_password(request, user, body.current_password)
        new_password = body.new_password.get_secret_value()
        if reason := password_policy_violation(new_password, user.username):
            await audit.record_request(
                request,
                "password.changed",
                "refused",
                target=target,
                details={"reason": "password_policy", "policy": reason},
            )
            raise _policy_error(reason)
        # Clears must_change_password; moving updated_at forgets the user's
        # known-device cookies (app/auth/device_cookie.py).
        await UserRepository.set_password_hash(user.id, await hash_password_async(new_password))
        invalidate_basic_cache(user.id)
        revoked = await sessions.sign_out_user(user.id, except_session_id=principal.session_id)
        await audit.record_request(
            request,
            "password.changed",
            "success",
            target=audit.user_target(user.id),
            details={"sessions_revoked": revoked},
        )
    if body.display_name is not None and body.display_name != user.display_name:
        await UserRepository.update(user.id, display_name=body.display_name)
        await audit.record_request(
            request,
            "user.changed",
            "success",
            target=audit.user_target(user.id),
            details={"fields": ["display_name"]},
        )
    updated = await UserRepository.get(user.id)
    if updated is None:
        raise _error(401, "auth_required")
    return _me(updated)


class TrustedNetworkRuleView(BaseModel):
    cidr: str = Field(description="The network, canonical CIDR form.")
    user_id: int = Field(description="The account a request from this network acts as.")


SecurityNotice = Literal[
    "trusted_admin_reduced_plugin_isolation", "trusted_loopback_or_gateway_public"
]


class AuthSettingsView(BaseModel):
    """The access settings an admin reads and changes (``auth_settings``)."""

    trusted_proxies: list[str] = Field(
        description=(
            "Reverse proxies whose X-Forwarded-For and X-Forwarded-Proto are believed: "
            "addresses or CIDRs, canonical network form. Empty trusts no peer."
        )
    )
    trusted_networks: list[TrustedNetworkRuleView] = Field(
        description=(
            "Networks whose direct requests act as an account without a login. Never "
            "applies through a proxy, the relay or ingress. Empty by default."
        )
    )
    audit_retention_days: int = Field(
        description="Audit rows older than this many days are purged (hourly)."
    )
    ingress_default_role: Role = Field(
        description=(
            "Role of a Home Assistant user seen for the first time, after the first one "
            "(always admin)."
        )
    )
    public_urls: list[str] = Field(
        description=(
            "Declared public addresses, canonical 'https://host[:port][/prefix]'. Their hosts "
            "pass the Host guard."
        )
    )
    host_guard_mode: HostGuardMode = Field(
        description=(
            "'observe' (default in 5.0.0): a Host header that is not one of this instance's "
            "addresses is recorded in rejected_hosts and let through; 'enforce': answered 421."
        )
    )
    rejected_hosts: list[RejectedHost] = Field(
        description=(
            "Host names the guard would refuse, seen since the last restart, most recently "
            "seen first (at most 64). Read-only; declare a name as a public address."
        )
    )
    security_notices: list[SecurityNotice] = Field(
        description=(
            "The Access security notice: a trusted network signs in an administrator while "
            "plugins run with reduced isolation, or covers loopback or the Docker gateway "
            "while a public address is configured."
        )
    )
    require_2fa_for_admins: bool = Field(
        description=(
            "Admins with a password and no second factor reach only the two-factor "
            "enrolment routes until they enrol. Off by default; never applies under ingress."
        )
    )


class TrustedNetworkRuleIn(BaseModel):
    cidr: str = Field(max_length=64)
    user_id: int


class AuthSettingsPatch(BaseModel):
    trusted_proxies: list[str] | None = Field(default=None, max_length=MAX_TRUSTED_PROXIES * 4)
    trusted_networks: list[TrustedNetworkRuleIn] | None = Field(
        default=None, max_length=trusted_network.MAX_TRUSTED_NETWORKS
    )
    audit_retention_days: int | None = Field(
        default=None, ge=MIN_AUDIT_RETENTION_DAYS, le=MAX_AUDIT_RETENTION_DAYS
    )
    ingress_default_role: Role | None = None
    public_urls: list[str] | None = Field(default=None, max_length=host_guard.MAX_PUBLIC_URLS * 4)
    host_guard_mode: HostGuardMode | None = None
    require_2fa_for_admins: bool | None = None


def _plugins_isolation_reduced() -> bool:
    """Plugins on with the ``none`` level. An unreadable state counts as reduced."""
    try:
        from app.plugins import lifecycle

        fields = lifecycle.health_fields()
    except Exception:
        return True
    return fields.get("plugins_disabled_source") is None and fields.get("plugins_isolation") in (
        None,
        "none",
    )


async def _security_notices(settings: AuthSettings) -> list[SecurityNotice]:
    rules = settings.trusted_networks
    if not rules:
        return []
    notices: list[SecurityNotice] = []
    admins = {
        user.id
        for user in await UserRepository.list_all()
        if user.role == "admin" and not user.disabled
    }
    if any(rule.user_id in admins for rule in rules) and _plugins_isolation_reduced():
        notices.append("trusted_admin_reduced_plugin_isolation")
    if settings.public_urls:
        gateways = trusted_network.docker_gateways()
        if any(
            trusted_network.covers_loopback(rule.cidr)
            or trusted_network.covered_gateway(rule.cidr, gateways)
            for rule in rules
        ):
            notices.append("trusted_loopback_or_gateway_public")
    return notices


async def _settings_view(settings: AuthSettings) -> AuthSettingsView:
    return AuthSettingsView(
        trusted_proxies=settings.trusted_proxies,
        trusted_networks=[
            TrustedNetworkRuleView(cidr=rule.cidr, user_id=rule.user_id)
            for rule in settings.trusted_networks
        ],
        audit_retention_days=settings.audit_retention_days,
        ingress_default_role=settings.ingress_default_role,
        public_urls=settings.public_urls,
        host_guard_mode=settings.host_guard_mode,
        rejected_hosts=host_guard.rejected_hosts(),
        security_notices=await _security_notices(settings),
        require_2fa_for_admins=settings.require_2fa_for_admins,
    )


def _normalize_rules(raw: list[TrustedNetworkRuleIn]) -> list[TrustedNetworkRule]:
    rules: list[TrustedNetworkRule] = []
    seen: set[str] = set()
    for item in raw:
        try:
            cidr = trusted_network.normalize_cidr(item.cidr)
        except ValueError:
            raise _error(400, "auth_trusted_networks_invalid", {"cidr": item.cidr}) from None
        if cidr in seen:
            raise _error(400, "auth_trusted_networks_invalid", {"cidr": cidr})
        seen.add(cidr)
        rules.append(TrustedNetworkRule(cidr=cidr, user_id=item.user_id))
    return rules


async def _check_rules(
    rules: list[TrustedNetworkRule], proxies: list[str], public_urls: list[str]
) -> None:
    """Refuse a rule set the gate could be tricked with (coded 400s)."""
    for rule in rules:
        proxy = trusted_network.overlapping_proxy(rule.cidr, proxies)
        if proxy is not None:
            raise _error(
                400, "auth_trusted_network_overlaps_proxy", {"cidr": rule.cidr, "proxy": proxy}
            )
        if trusted_network.covers_loopback(rule.cidr) and not trusted_network.loopback_allowed():
            raise _error(400, "auth_trusted_network_loopback", {"cidr": rule.cidr})
        if public_urls:
            gateway = trusted_network.covered_gateway(rule.cidr, trusted_network.docker_gateways())
            if gateway is not None:
                raise _error(
                    400,
                    "auth_trusted_network_overlaps_gateway",
                    {"cidr": rule.cidr, "gateway": gateway},
                )
        user = await UserRepository.get(rule.user_id)
        if user is None or user.disabled:
            raise _error(400, "auth_trusted_network_user_invalid", {"cidr": rule.cidr})


@router.get("/settings", response_model=AuthSettingsView, dependencies=[Depends(require("admin"))])
async def get_auth_settings() -> AuthSettingsView:
    """The access settings."""
    return await _settings_view(await AuthSettingsRepository.get())


@router.patch(
    "/settings", response_model=AuthSettingsView, dependencies=[Depends(require("admin"))]
)
async def patch_auth_settings(body: AuthSettingsPatch, request: Request) -> AuthSettingsView:
    """Change the access settings. The trusted proxies and networks apply to the next request."""
    _require(request)
    trusted_proxies: list[str] | None = None
    if body.trusted_proxies is not None:
        try:
            trusted_proxies = normalize_proxies(body.trusted_proxies)
        except ValueError:
            raise _error(400, "auth_trusted_proxies_invalid") from None
    public_urls: list[str] | None = None
    if body.public_urls is not None:
        try:
            public_urls = host_guard.normalize_public_urls(body.public_urls)
        except host_guard.PublicUrlError:
            raise _error(400, "auth_public_urls_invalid") from None
    if public_urls is not None or body.host_guard_mode is not None:
        # Never save a guard that rejects the request being answered.
        current = await AuthSettingsRepository.get()
        new_urls = public_urls if public_urls is not None else current.public_urls
        new_mode = body.host_guard_mode or current.host_guard_mode
        if host_guard.would_lock_out(request.scope, new_mode, new_urls):
            raise _error(
                400,
                "auth_host_guard_would_lock_out",
                {"host": host_guard.request_host(request.headers.get("host", "")) or "unknown"},
            )
    trusted_networks: list[TrustedNetworkRule] | None = None
    if body.trusted_networks is not None:
        trusted_networks = _normalize_rules(body.trusted_networks)
    if trusted_proxies is not None or trusted_networks is not None:
        # Checked together: a new proxy may overlap a rule saved earlier.
        current = await AuthSettingsRepository.get()
        await _check_rules(
            trusted_networks if trusted_networks is not None else current.trusted_networks,
            trusted_proxies if trusted_proxies is not None else current.trusted_proxies,
            public_urls if public_urls is not None else current.public_urls,
        )
    settings = await AuthSettingsRepository.update(
        trusted_proxies=trusted_proxies,
        trusted_networks=trusted_networks,
        audit_retention_days=body.audit_retention_days,
        ingress_default_role=body.ingress_default_role,
        public_urls=public_urls,
        host_guard_mode=body.host_guard_mode,
        require_2fa_for_admins=body.require_2fa_for_admins,
    )
    if trusted_proxies is not None:
        logger.info("Trusted proxies updated: %d entries", len(trusted_proxies))
        await audit.record_request(
            request,
            "settings.changed",
            "success",
            target="auth_settings.trusted_proxies",
            details={"trusted_proxies": trusted_proxies},
        )
    if trusted_networks is not None:
        logger.info("Trusted networks updated: %d rules", len(trusted_networks))
        await audit.record_request(
            request,
            "settings.changed",
            "success",
            target="auth_settings.trusted_networks",
            details={"trusted_networks": [rule.model_dump() for rule in trusted_networks]},
        )
    if body.audit_retention_days is not None:
        await audit.record_request(
            request,
            "settings.changed",
            "success",
            target="auth_settings.audit_retention_days",
            details={"audit_retention_days": settings.audit_retention_days},
        )
    if body.ingress_default_role is not None:
        await audit.record_request(
            request,
            "settings.changed",
            "success",
            target="auth_settings.ingress_default_role",
            details={"ingress_default_role": settings.ingress_default_role},
        )
    if public_urls is not None:
        for url in public_urls:
            if name := urlsplit(url).hostname:
                host_guard.forget_rejected(name)
        await audit.record_request(
            request,
            "settings.changed",
            "success",
            target="auth_settings.public_urls",
            details={"public_urls": public_urls},
        )
    if body.host_guard_mode is not None:
        await audit.record_request(
            request,
            "settings.changed",
            "success",
            target="auth_settings.host_guard_mode",
            details={"host_guard_mode": settings.host_guard_mode},
        )
    if body.require_2fa_for_admins is not None:
        await audit.record_request(
            request,
            "settings.changed",
            "success",
            target="auth_settings.require_2fa_for_admins",
            details={"require_2fa_for_admins": settings.require_2fa_for_admins},
        )
    return await _settings_view(settings)


# --- Users (admin) ----------------------------------------------------------------


class UserView(BaseModel):
    """An account as the admin sees it. ``external_id`` (the Home Assistant
    user id) is never returned: ``provider`` is enough for a badge."""

    id: int
    username: str
    display_name: str
    role: Role
    provider: str
    disabled: bool
    must_change_password: bool
    has_password: bool
    totp_enabled: bool
    created_at: int
    updated_at: int


class UserCreate(BaseModel):
    model_config = ConfigDict(hide_input_in_errors=True)

    username: str = Field(min_length=1, max_length=USERNAME_MAX_LENGTH)
    role: Role
    display_name: str = Field(default="", max_length=128)
    # Required for every role but ``kiosk``, which has none.
    password: SecretStr | None = Field(default=None, max_length=MAX_PASSWORD_LENGTH)
    must_change_password: bool = True


class UserPatch(BaseModel):
    display_name: str | None = Field(default=None, max_length=128)
    role: Role | None = None
    disabled: bool | None = None
    must_change_password: bool | None = None


class UserPasswordSet(BaseModel):
    model_config = ConfigDict(hide_input_in_errors=True)

    password: SecretStr = Field(min_length=1, max_length=MAX_PASSWORD_LENGTH)
    must_change_password: bool = True


def _user_view(user: User) -> UserView:
    return UserView(
        id=user.id,
        username=user.username,
        display_name=user.display_name,
        role=user.role,
        provider=user.provider,
        disabled=user.disabled,
        must_change_password=user.must_change_password,
        has_password=user.has_password,
        totp_enabled=user.totp_enabled_at is not None,
        created_at=user.created_at,
        updated_at=user.updated_at,
    )


def _takes_password(provider: str, role: Role) -> bool:
    """Only local accounts outside ``kiosk`` sign in with a password: Home
    Assistant signs ingress identities in, a kiosk has no password (Q-A7)."""
    return provider == "local" and role != "kiosk"


async def _target_user(user_id: int) -> User:
    user = await UserRepository.get(user_id)
    if user is None:
        raise _error(404, "auth_user_not_found")
    return user


ActionFor = Callable[[dict[str, Any]], audit.AuditAction]


def _admin(action: audit.AuditAction | ActionFor) -> Callable[[Request], Awaitable[None]]:
    """``require("admin")`` that also audits a refusal (outcome ``refused``,
    ``details.reason = forbidden``) under the route's action. Declares
    ``admin`` for the permission registry like ``require("admin")``."""
    check = require("admin")

    async def dependency(request: Request) -> None:
        try:
            await check(request)
        except HTTPException as exc:
            name = action({}) if callable(action) else action
            await audit.record_request(
                request,
                name,
                "refused",
                target=_path_target(request.path_params),
                details={"reason": "forbidden", "status": exc.status_code},
            )
            raise

    dependency.__name__ = "require_admin"
    setattr(dependency, ACCESS_ATTRIBUTE, "admin")
    return dependency


def _path_target(params: dict[str, Any]) -> str | None:
    user_id = str(params.get("user_id") or "")
    return audit.user_target(int(user_id)) if user_id.isdigit() else None


def _audits_refusals(action: audit.AuditAction | ActionFor):
    """Write one ``refused`` row (``details.reason``: the error code without
    ``auth_``, plus ``status``; ``error`` for a 5xx) for any coded
    ``HTTPException`` the route raises, so every refusal of the users API is
    in the audit log. Success rows stay the route's own."""

    def decorate(route: Callable[..., Awaitable[Any]]) -> Callable[..., Awaitable[Any]]:
        @functools.wraps(route)
        async def wrapped(*args: Any, **kwargs: Any) -> Any:
            try:
                return await route(*args, **kwargs)
            except HTTPException as exc:
                detail = exc.detail if isinstance(exc.detail, dict) else {}
                code = str(detail.get("code", "error"))
                details: dict[str, Any] = {
                    "reason": code.removeprefix("auth_"),
                    "status": exc.status_code,
                }
                params = detail.get("params") or {}
                if code == "auth_password_policy" and "reason" in params:
                    details["policy"] = params["reason"]
                await audit.record_request(
                    kwargs["request"],
                    action(kwargs) if callable(action) else action,
                    "error" if exc.status_code >= 500 else "refused",
                    target=_path_target(kwargs),
                    details=details,
                )
                raise

        return wrapped

    return decorate


def _patch_action(kwargs: dict[str, Any]) -> audit.AuditAction:
    body = kwargs.get("body")
    return "user.disabled" if body is not None and body.disabled else "user.changed"


def _sign_out_scope(principal: Principal, user_id: int) -> str | None:
    """The session to keep when an admin acts on their own account: theirs."""
    return principal.session_id if principal.user_id == user_id else None


@router.get("/users", response_model=list[UserView], dependencies=[Depends(_admin("users.listed"))])
async def list_users(request: Request) -> list[UserView]:
    """Every account, local and Home Assistant, by id (one ``users.listed`` row,
    as ``meshloom-admin list-users`` writes)."""
    users = await UserRepository.list_all()
    await audit.record_request(request, "users.listed", "success", details={"count": len(users)})
    return [_user_view(user) for user in users]


@router.post(
    "/users",
    response_model=UserView,
    status_code=201,
    dependencies=[Depends(_admin("user.created"))],
)
@_audits_refusals("user.created")
async def create_user(body: UserCreate, request: Request) -> UserView:
    """Create a local account. Every role but ``kiosk`` gets an initial
    password (the policy applies), to change at first sign-in by default."""
    _require(request)
    password_hash: str | None = None
    if body.role == "kiosk":
        if body.password is not None:
            raise _error(400, "auth_password_not_applicable")
    else:
        if body.password is None:
            raise _error(400, "auth_password_required")
        password = body.password.get_secret_value()
        if reason := password_policy_violation(password, body.username):
            raise _policy_error(reason)
        password_hash = await hash_password_async(password)
    try:
        user = await UserRepository.create(
            body.username,
            body.role,
            display_name=body.display_name,
            password_hash=password_hash,
            must_change_password=body.must_change_password and password_hash is not None,
        )
    except InvalidUsernameError:
        raise _error(400, "auth_username_invalid") from None
    except UsernameTakenError:
        raise _error(409, "auth_username_taken") from None
    await audit.record_request(
        request,
        "user.created",
        "success",
        target=audit.user_target(user.id),
        details={"role": user.role, "must_change": user.must_change_password},
    )
    return _user_view(user)


@router.patch(
    "/users/{user_id}", response_model=UserView, dependencies=[Depends(_admin(_patch_action))]
)
@_audits_refusals(_patch_action)
async def patch_user(user_id: int, body: UserPatch, request: Request) -> UserView:
    """Change an account's display name, role, disabled state or
    ``must_change_password``. A role change or a disable signs the user out
    everywhere (sessions revoked, sockets closed); a role of ``kiosk`` drops
    the password. 409 ``auth_last_admin`` when no enabled admin would be left."""
    principal = _require(request)
    target = await _target_user(user_id)
    role = body.role if body.role is not None and body.role != target.role else None
    disabled = (
        body.disabled if body.disabled is not None and body.disabled != target.disabled else None
    )
    display_name = (
        body.display_name
        if body.display_name is not None and body.display_name.strip() != target.display_name
        else None
    )
    effective_role = role or target.role
    must_change = (
        body.must_change_password
        if body.must_change_password is not None
        and body.must_change_password != target.must_change_password
        else None
    )
    if must_change and not (
        _takes_password(target.provider, effective_role) and target.has_password
    ):
        raise _error(400, "auth_password_not_applicable")
    to_kiosk = role == "kiosk"
    if to_kiosk and target.must_change_password:
        must_change = False
    try:
        updated = await UserRepository.update(
            user_id,
            display_name=display_name,
            role=role,
            disabled=disabled,
            must_change_password=must_change,
            clear_password=to_kiosk and target.has_password,
            keep_an_admin=True,
        )
    except LastAdminError:
        raise _error(409, "auth_last_admin") from None
    if updated is None:
        raise _error(404, "auth_user_not_found")
    revoked = 0
    if role is not None or disabled or to_kiosk:
        invalidate_basic_cache(user_id)
        # Their own account included: the admin's socket keeps the role it opened with.
        revoked = await sessions.sign_out_user(user_id)
    elif must_change:
        # The socket would keep running while every route asks for the change.
        from app.websocket import ws_manager

        ws_manager.close_user(user_id, except_session_id=_sign_out_scope(principal, user_id))
    fields = [
        name
        for name, value in (
            ("display_name", display_name),
            ("role", role),
            ("must_change_password", must_change),
        )
        if value is not None
    ]
    if to_kiosk and target.has_password:
        fields.append("password_removed")
    target_ref = audit.user_target(user_id)
    if fields:
        details: dict = {"fields": fields, "sessions_revoked": revoked}
        if role is not None:
            details["role"] = role
            details["previous_role"] = target.role
        await audit.record_request(
            request, "user.changed", "success", target=target_ref, details=details
        )
    if disabled is not None:
        await audit.record_request(
            request,
            "user.disabled" if disabled else "user.enabled",
            "success",
            target=target_ref,
            details={"sessions_revoked": revoked} if disabled else None,
        )
    return _user_view(updated)


@router.delete(
    "/users/{user_id}", response_model=OkResponse, dependencies=[Depends(_admin("user.deleted"))]
)
@_audits_refusals("user.deleted")
async def delete_user(user_id: int, request: Request) -> OkResponse:
    """Delete an account and its sessions, closing its sockets. A Home
    Assistant identity comes back on its next visit, with the default role:
    disable it to keep it out. 409 ``auth_last_admin`` for the last enabled admin."""
    _require(request)
    target = await _target_user(user_id)
    try:
        deleted = await UserRepository.delete(user_id, keep_an_admin=True)
    except LastAdminError:
        raise _error(409, "auth_last_admin") from None
    if not deleted:
        raise _error(404, "auth_user_not_found")
    invalidate_basic_cache(user_id)
    from app.websocket import ws_manager

    ws_manager.close_user(user_id)
    await audit.record_request(
        request,
        "user.deleted",
        "success",
        target=audit.user_target(user_id),
        details={"role": target.role, "provider": target.provider},
    )
    return OkResponse()


@router.post(
    "/users/{user_id}/password",
    response_model=UserView,
    dependencies=[Depends(_admin("password.changed"))],
)
@_audits_refusals("password.changed")
async def set_user_password(user_id: int, body: UserPasswordSet, request: Request) -> UserView:
    """Set a local account's password (the policy applies), to change at the
    next sign-in by default. Signs the user out everywhere (the admin's own
    session stays when it is their account) and forgets its known devices.
    400 ``auth_password_not_applicable`` for Home Assistant and kiosk accounts."""
    principal = _require(request)
    target = await _target_user(user_id)
    if not _takes_password(target.provider, target.role):
        raise _error(400, "auth_password_not_applicable")
    password = body.password.get_secret_value()
    if reason := password_policy_violation(password, target.username):
        raise _policy_error(reason)
    if not await UserRepository.set_password_hash(
        user_id,
        await hash_password_async(password),
        must_change_password=body.must_change_password,
    ):
        raise _error(404, "auth_user_not_found")
    invalidate_basic_cache(user_id)
    revoked = await sessions.sign_out_user(
        user_id, except_session_id=_sign_out_scope(principal, user_id)
    )
    await audit.record_request(
        request,
        "password.changed",
        "success",
        target=audit.user_target(user_id),
        details={
            "reset": True,
            "must_change": body.must_change_password,
            "sessions_revoked": revoked,
        },
    )
    updated = await _target_user(user_id)
    return _user_view(updated)


@router.delete(
    "/users/{user_id}/sessions",
    response_model=RevokedCount,
    dependencies=[Depends(_admin("session.revoked"))],
)
@_audits_refusals("session.revoked")
async def revoke_user_sessions(user_id: int, request: Request) -> RevokedCount:
    """Sign a user out everywhere (the admin's own session stays when it is
    their account) and close its sockets."""
    principal = _require(request)
    await _target_user(user_id)
    count = await sessions.sign_out_user(
        user_id, except_session_id=_sign_out_scope(principal, user_id)
    )
    await audit.record_request(
        request,
        "session.revoked",
        "success",
        target=audit.user_target(user_id),
        details={"scope": "all", "count": count},
    )
    return RevokedCount(revoked=count)


# --- Audit log ------------------------------------------------------------------


class AuditPage(BaseModel):
    """One page of the audit log, newest first. ``next_cursor`` is opaque:
    send it back as ``cursor`` for the next (older) page; None on the last."""

    entries: list[AuthAuditEntry]
    next_cursor: str | None = None


@router.get("/audit", response_model=AuditPage, dependencies=[Depends(require("admin"))])
async def get_audit(
    cursor: str | None = Query(default=None, max_length=32),
    limit: int = Query(default=AUDIT_PAGE_DEFAULT, ge=1, le=AUDIT_PAGE_MAX),
) -> AuditPage:
    """The audit log, newest first, paged by cursor. Reading it writes no row."""
    before_id: int | None = None
    if cursor is not None:
        if not _CURSOR.fullmatch(cursor) or not 0 < int(cursor) < 2**63:
            raise _error(400, "auth_audit_cursor_invalid")
        before_id = int(cursor)
    rows = await AuthAuditRepository.list_recent(limit=limit + 1, before_id=before_id)
    entries = rows[:limit]
    next_cursor = str(entries[-1].id) if len(rows) > limit else None
    return AuditPage(entries=entries, next_cursor=next_cursor)


# ``/auth/tokens``: API and device tokens (``app/routers/auth_tokens.py``).
router.include_router(auth_tokens.router)
# ``/auth/kiosk-*``: the kiosk launcher's device-token sign-in (``app/routers/auth_kiosk.py``).
router.include_router(auth_kiosk.router)
