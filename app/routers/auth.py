"""``/api/auth/*``: first-run setup, login, logout, sessions and the own profile.

``GET/PATCH /auth/settings`` holds the access settings an admin tunes (the
trusted proxies today). Contract and rules: ``docs/dev/auth.md``. ``GET /auth/session``,
``POST /auth/setup``, ``POST /auth/login`` and ``POST /auth/logout`` are public
(``PUBLIC_ROUTES``, ``app/auth/middleware.py``); the others need a principal.
Errors are coded (``{"detail": {"code", "message", "params"?}}``), translated
by the frontend from ``errors.<code>``. Nothing here logs a password, a code or
a session secret.
"""

from __future__ import annotations

import asyncio
import logging

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, ConfigDict, Field, SecretStr

from app.auth import sessions
from app.auth.middleware import client_ip
from app.auth.models import USERNAME_MAX_LENGTH, Role, User
from app.auth.passwords import (
    MAX_PASSWORD_LENGTH,
    MIN_PASSWORD_LENGTH,
    hash_password_async,
    password_policy_violation,
    verify_password_async,
)
from app.auth.permissions import own_account, public, require
from app.auth.principal import Principal, Via, get_principal
from app.auth.providers.password import VerificationBusy, authenticate
from app.auth.setup_code import (
    SetupUnavailable,
    check_setup_code,
    clear_setup_code,
    setup_code_required,
)
from app.middleware.proxy_headers import MAX_TRUSTED_PROXIES, normalize_proxies
from app.repository.auth import (
    AuthAuditRepository,
    AuthSessionRepository,
    AuthSettingsRepository,
    InvalidUsernameError,
    UsernameTakenError,
    UserRepository,
)
from app.security import basic_compat_active, invalidate_basic_cache

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/auth", tags=["auth"])

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
    "auth_ingress_only": "Home Assistant signs you in here: open Meshloom from its sidebar",
    "auth_trusted_proxies_invalid": "Not an address or network, or too many trusted proxies",
}


def _error(status: int, code: str, params: dict | None = None) -> HTTPException:
    detail: dict = {"code": code, "message": _MESSAGES.get(code, code)}
    if params:
        detail["params"] = params
    return HTTPException(status_code=status, detail=detail)


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


async def _audit(action: str, outcome: str, request: Request, **fields) -> None:
    try:
        await AuthAuditRepository.add(
            action, outcome, ip=client_ip(request.scope), via=_via(request), **fields
        )
    except Exception:
        logger.warning("Could not write the auth audit row %s/%s", action, outcome)


# --- Models -------------------------------------------------------------------


class AuthMethods(BaseModel):
    password: bool = True
    ingress: bool = False


class AuthCompat(BaseModel):
    basic: bool = False


class SessionUser(BaseModel):
    id: int
    username: str
    display_name: str
    role: Role


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
    user: SessionUser | None = None
    via: Via | None = None
    instance: InstanceInfo | None = None


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


async def _session_payload(principal: Principal | None, via: Via) -> AuthSessionResponse:
    """The session state. Under ingress the only sign-in method is Home
    Assistant's: no password form (no cookie could keep its session)."""
    setup_required = not await UserRepository.any_exist()
    under_ingress = via == "ingress"
    payload = AuthSessionResponse(
        authenticated=principal is not None,
        setup_required=setup_required,
        setup_code_required=setup_required and await setup_code_required(),
        methods=AuthMethods(password=not under_ingress, ingress=under_ingress),
        auth_compat=AuthCompat(basic=await basic_compat_active()),
    )
    if principal is not None:
        from app.version_info import get_app_build_info

        user = principal.user
        payload.user = SessionUser(
            id=user.id, username=user.username, display_name=user.display_name, role=user.role
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


def _me(user: User) -> MeResponse:
    return MeResponse(
        id=user.id,
        username=user.username,
        display_name=user.display_name,
        role=user.role,
        provider=user.provider,
        has_password=user.has_password,
        must_change_password=user.must_change_password,
        created_at=user.created_at,
    )


# --- Routes -------------------------------------------------------------------


@router.get(
    "/session",
    response_model=AuthSessionResponse,
    response_model_exclude_none=True,
    dependencies=[Depends(public())],
)
async def get_session(request: Request) -> AuthSessionResponse:
    """Who is signed in, and what the login page needs. Public."""
    return await _session_payload(get_principal(request), _via(request))


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
            await _audit("setup", "unavailable", request)
            raise _error(503, "auth_setup_unavailable") from None
        if not code_ok:
            await _audit("setup", "setup_code_invalid", request)
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
    await _audit("setup", "success", request, actor_user_id=user.id, target=f"user:{user.id}")
    logger.info("Administrator account %r created", user.username)
    principal = await _start_session(request, response, user)
    return await _session_payload(principal, _via(request))


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
    ``auth_ingress_only`` under ingress, checked before any password work.
    """
    _refuse_under_ingress(request)
    try:
        user = await authenticate(body.username, body.password.get_secret_value())
    except VerificationBusy:
        raise _error(503, "auth_busy") from None
    if user is None:
        await _audit("login", "failure", request, target=body.username[:USERNAME_MAX_LENGTH])
        raise _error(401, "auth_invalid_credentials")
    principal = await _start_session(request, response, user)
    await _audit("login", "success", request, actor_user_id=user.id)
    return await _session_payload(principal, _via(request))


@router.post("/logout", response_model=OkResponse, dependencies=[Depends(public())])
async def logout(request: Request, response: Response) -> OkResponse:
    """End the current session and clear the cookie. Public (idempotent)."""
    principal = get_principal(request)
    if principal is not None and principal.session_id is not None:
        await sessions.revoke_session(principal.session_id)
        await _audit("logout", "success", request, actor_user_id=principal.user_id)
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
    await _audit(
        "sessions_revoke_others",
        "success",
        request,
        actor_user_id=principal.user_id,
        details={"count": count},
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
    await _audit(
        "session_revoke",
        "success",
        request,
        actor_user_id=principal.user_id,
        target=f"session:{session_id}",
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
        if not user.has_password:
            raise _error(403, "auth_forbidden")
        credentials = await UserRepository.get_credentials(user.username)
        stored = (
            credentials.password_hash.get_secret_value()
            if credentials is not None and credentials.password_hash is not None
            else None
        )
        current = body.current_password.get_secret_value() if body.current_password else ""
        if not current or not await verify_password_async(stored, current):
            await _audit("password_change", "failure", request, actor_user_id=user.id)
            raise _error(403, "auth_invalid_credentials")
        new_password = body.new_password.get_secret_value()
        if reason := password_policy_violation(new_password, user.username):
            raise _policy_error(reason)
        await UserRepository.set_password_hash(user.id, await hash_password_async(new_password))
        invalidate_basic_cache(user.id)
        await sessions.revoke_user_sessions(user.id, except_session_id=principal.session_id)
        await _audit("password_change", "success", request, actor_user_id=user.id)
    if body.display_name is not None:
        await UserRepository.update(user.id, display_name=body.display_name)
    updated = await UserRepository.get(user.id)
    if updated is None:
        raise _error(401, "auth_required")
    return _me(updated)


class AuthSettingsView(BaseModel):
    """The access settings an admin reads and changes (``auth_settings``)."""

    trusted_proxies: list[str] = Field(
        description=(
            "Reverse proxies whose X-Forwarded-For and X-Forwarded-Proto are believed: "
            "addresses or CIDRs, canonical network form. Empty trusts no peer."
        )
    )


class AuthSettingsPatch(BaseModel):
    trusted_proxies: list[str] | None = Field(default=None, max_length=MAX_TRUSTED_PROXIES * 4)


@router.get("/settings", response_model=AuthSettingsView, dependencies=[Depends(require("admin"))])
async def get_auth_settings() -> AuthSettingsView:
    """The access settings."""
    settings = await AuthSettingsRepository.get()
    return AuthSettingsView(trusted_proxies=settings.trusted_proxies)


@router.patch(
    "/settings", response_model=AuthSettingsView, dependencies=[Depends(require("admin"))]
)
async def patch_auth_settings(body: AuthSettingsPatch, request: Request) -> AuthSettingsView:
    """Change the access settings. The trusted proxies apply to the next request."""
    principal = _require(request)
    trusted_proxies: list[str] | None = None
    if body.trusted_proxies is not None:
        try:
            trusted_proxies = normalize_proxies(body.trusted_proxies)
        except ValueError:
            raise _error(400, "auth_trusted_proxies_invalid") from None
    settings = await AuthSettingsRepository.update(trusted_proxies=trusted_proxies)
    if trusted_proxies is not None:
        logger.info("Trusted proxies set to %s", trusted_proxies)
        await _audit(
            "settings_change",
            "success",
            request,
            actor_user_id=principal.user_id,
            target="auth_settings.trusted_proxies",
            details={"trusted_proxies": trusted_proxies},
        )
    return AuthSettingsView(trusted_proxies=settings.trusted_proxies)
