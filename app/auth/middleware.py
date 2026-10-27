"""The auth gate: resolves a principal for every HTTP request and WebSocket upgrade.

Replaces the 4.x ``BasicAuthMiddleware``. For each ``http`` and ``websocket``
scope it:

0. refuses a banned address (``app/auth/ip_ban.py``, outside ingress) on the
   ``/api`` routes, the API docs and their WebSocket upgrades, before reading
   any credential (no argon2id work): 429 ``auth_ip_banned`` with
   ``Retry-After`` and ``params.retry_after`` (seconds), or a refused upgrade;
1. resolves the principal (``Principal``, ``app/auth/principal.py``) from, in
   order, the Home Assistant identity on an ingress request
   (``app/auth/providers/ingress.py``), ``Authorization: Bearer`` (API and
   device tokens, ``app/auth/providers/token.py``: from the header only, never
   a query string; refused under ingress),
   ``Authorization: Basic`` (the compatibility credential,
   ``app/security.py``), then the session cookie (``app/auth/sessions.py``,
   never under ingress), and last, for a request that sent no credential or
   only a cookie that did not resolve, a trusted network rule
   (``app/auth/providers/trusted_network.py``: ``via`` becomes ``trusted``,
   never through a proxy, the relay or ingress, strict ``Host`` guard); it
   stores it in ``scope["state"]``; when the argon2id queue is full
   (``VerificationBusy``) it answers 503 ``auth_busy``;
2. applies the CSRF rule to unsafe methods (``origin_policy.unsafe_request_allowed``):
   403 ``auth_origin_rejected`` unless a bearer token resolved;
3. refuses any ``/api`` route outside ``PUBLIC_ROUTES``, and the API docs
   (``AUTHENTICATED_NON_API_PATHS``), without a principal: 401
   ``auth_setup_required`` while no account exists, else 401
   ``auth_required`` (or ``auth_invalid_credentials`` when a credential was
   sent and did not check). A WebSocket upgrade is refused **before**
   ``accept()``, with close code 1008 (uvicorn answers HTTP 403, no frame).

4. while the account has ``must_change_password``, refuses every ``/api``
   route outside ``PASSWORD_CHANGE_ROUTES`` (403
   ``auth_password_change_required``) and every WebSocket upgrade;
5. otherwise, while ``auth_settings.require_2fa_for_admins`` is on and the
   principal is an admin with a password and no second factor (never under
   ingress), refuses every ``/api`` route outside
   ``SECOND_FACTOR_ENROLMENT_ROUTES`` (403 ``auth_2fa_enrolment_required``) and
   every WebSocket upgrade. An unreadable setting counts as on (fail closed).

``Authorization: Basic`` never signs in an account whose second factor is on:
the header carries no code (``auth_invalid_credentials``).

A bearer token or a Basic credential that does not check writes one
``AUTH_FAIL`` line (``app/auth/fail_log.py``, ``docs/dev/auth-fail-log.md``).

Which role may call which route is the permission registry's decision
(``app/auth/permissions.py``), once a principal is known. The other paths
outside ``/api`` (the frontend) stay public: the login page must load.

``/api/auth/*`` answers carry ``Cache-Control: no-store``; a session whose
expiry slid on this request gets its cookie again. An ingress answer never
carries ``Set-Cookie``: any is dropped on the way out.

The Basic challenge (``WWW-Authenticate``) is sent on 401 only while the
instance came from 4.x Basic (``auth_settings.basic_imported``), so browsers
that used the native dialog keep working; never under ingress.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from starlette.datastructures import Headers
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app import origin_policy
from app.auth import ip_ban, sessions
from app.auth.fail_log import log_auth_failure
from app.auth.principal import Principal, Via, set_principal
from app.auth.providers import ingress, trusted_network
from app.auth.providers import token as token_provider
from app.auth.providers.password import VerificationBusy
from app.repository.auth import AuthSettingsRepository
from app.security import (
    BASIC_CHALLENGE,
    authenticate_basic,
    basic_compat_active,
    parse_basic_authorization,
)

logger = logging.getLogger(__name__)

API_PREFIX = "/api"
AUTH_PREFIX = "/api/auth/"

# Reachable without a principal. The routes themselves decide what an
# anonymous caller sees (``/api/health`` answers only ``{"ok": true}``).
PUBLIC_ROUTES: frozenset[tuple[str, str]] = frozenset(
    {
        ("GET", "/api/health"),
        ("GET", "/api/auth/session"),
        ("POST", "/api/auth/setup"),
        ("POST", "/api/auth/login"),
        ("POST", "/api/auth/login/2fa"),
        ("POST", "/api/auth/webauthn/login/options"),
        ("POST", "/api/auth/webauthn/login/verify"),
        ("POST", "/api/auth/logout"),
        # The kiosk's one-time code exchange (``app/routers/auth_kiosk.py``).
        ("GET", "/api/auth/kiosk-session"),
        # A device trades its pairing code for a token (``app/routers/auth_pairing.py``).
        ("POST", "/api/auth/pair"),
    }
)

# All a principal whose account has ``must_change_password`` may call until the
# password is changed (``PATCH /api/auth/me``): anything else under ``/api`` is
# 403 ``auth_password_change_required``, and a WebSocket upgrade is refused.
PASSWORD_CHANGE_ROUTES: frozenset[tuple[str, str]] = frozenset(
    {
        ("GET", "/api/auth/session"),
        ("POST", "/api/auth/logout"),
        ("GET", "/api/auth/me"),
        ("PATCH", "/api/auth/me"),
    }
)

# All an admin who owes a second factor (``require_2fa_for_admins``) may call
# until it is enrolled (``app/auth/totp.py``): anything else under ``/api`` is
# 403 ``auth_2fa_enrolment_required``, and a WebSocket upgrade is refused.
SECOND_FACTOR_ENROLMENT_ROUTES: frozenset[tuple[str, str]] = frozenset(
    {
        ("GET", "/api/auth/session"),
        ("POST", "/api/auth/logout"),
        ("GET", "/api/auth/me"),
        ("GET", "/api/auth/2fa"),
        ("POST", "/api/auth/2fa/enrol"),
        ("POST", "/api/auth/2fa/confirm"),
    }
)

# Every route outside ``/api``, by its path template: ``public`` (the frontend,
# so the login page loads) or ``authenticated`` (any signed-in principal).
# ``tests/test_auth_route_coverage.py`` fails on a route missing here.
NON_API_ACCESS: dict[str, str] = {
    "/docs": "authenticated",
    "/redoc": "authenticated",
    "/openapi.json": "authenticated",
    "/": "public",
    "/assets": "public",
    "/site.webmanifest": "public",
    "/{path:path}": "public",
}
AUTHENTICATED_NON_API_PATHS: frozenset[str] = frozenset(
    path for path, access in NON_API_ACCESS.items() if access == "authenticated"
)

UNSAFE_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})

WS_REFUSED_CLOSE_CODE = 1008

_MESSAGES = {
    "auth_required": "Authentication required",
    "auth_invalid_credentials": "Invalid credentials",
    "auth_setup_required": "No account exists yet: create the administrator first",
    "auth_origin_rejected": "Cross-origin request refused",
    "auth_busy": "Too many sign-in checks at once: retry in a moment",
    "auth_password_change_required": "Change your password first",
    "auth_2fa_enrolment_required": "Set up two-factor authentication first",
    ip_ban.BANNED_CODE: "Too many failed sign-ins from this address: retry later",
}


def is_api_path(path: str) -> bool:
    return path == API_PREFIX or path.startswith(API_PREFIX + "/")


def must_change_password(principal: Principal | None) -> bool:
    return principal is not None and principal.user.must_change_password


def owes_second_factor_shape(principal: Principal | None) -> bool:
    """An admin with a password and no enabled second factor, outside ingress
    (Home Assistant already authenticated it). Kiosk accounts have no password."""
    if principal is None or principal.via == "ingress" or principal.credential == "ingress":
        return False
    user = principal.user
    return (
        user.role == "admin"
        and user.provider == "local"
        and user.has_password
        and user.totp_enabled_at is None
    )


async def second_factor_enrolment_required(principal: Principal | None) -> bool:
    """Whether ``require_2fa_for_admins`` confines this principal to the
    enrolment routes. An unreadable setting counts as on (fail closed)."""
    if not owes_second_factor_shape(principal):
        return False
    try:
        return await AuthSettingsRepository.require_2fa_for_admins()
    except Exception:
        logger.exception("Could not read require_2fa_for_admins: confining the admin")
        return True


def request_via(scope: Scope) -> Via:
    """How the request reached the instance: ``relay`` when the relay dispatched
    it (``scope["state"]["via"]``, a later Access ticket), ``ingress`` for the
    Supervisor's proxy (``ingress.is_ingress_request``), else ``lan``."""
    state = scope.get("state")
    if isinstance(state, dict) and state.get("via") == "relay":
        return "relay"
    if ingress.is_ingress_request(scope):
        return "ingress"
    return "lan"


def client_ip(scope: Scope) -> str | None:
    """The client's address: the TCP peer, or the one a trusted proxy forwarded
    (``scope["client"]`` as rewritten by ``app/middleware/proxy_headers.py``)."""
    client = scope.get("client")
    return client[0] if client else None


def coded_body(code: str, params: dict[str, Any] | None = None) -> bytes:
    detail: dict[str, Any] = {"code": code, "message": _MESSAGES.get(code, code)}
    if params:
        detail["params"] = params
    return json.dumps({"detail": detail}).encode()


async def users_exist() -> bool:
    from app.repository.auth import UserRepository

    return await UserRepository.any_exist()


class _Resolution:
    __slots__ = ("principal", "bearer_sent", "credential_failed", "busy", "refreshed_cookie")

    def __init__(self) -> None:
        self.principal: Principal | None = None
        # An ``Authorization: Bearer`` header was sent (valid or not).
        self.bearer_sent = False
        self.credential_failed = False
        # The argon2id queue was full: answer 503 ``auth_busy``.
        self.busy = False
        self.refreshed_cookie: str | None = None


KIOSK_BOOTSTRAP_ROUTE = ("POST", "/api/auth/kiosk-bootstrap")


async def resolve_bearer_token(
    token: str, via: Via, ip: str | None, *, kiosk_bootstrap: bool = False
) -> Principal | None:
    """The principal of an API or device token (``app/auth/providers/token.py``)."""
    return await token_provider.resolve_token(token, via, ip, kiosk_bootstrap=kiosk_bootstrap)


def is_kiosk_bootstrap(scope: Scope) -> bool:
    """The one request the kiosk's device token may sign in."""
    return (
        scope.get("type") == "http"
        and (scope.get("method", "").upper(), scope.get("path", "").rstrip("/"))
        == KIOSK_BOOTSTRAP_ROUTE
    )


async def resolve(scope: Scope, headers: Headers, via: Via, secure: bool) -> _Resolution:
    """Find the principal of a request, without deciding anything."""
    result = _Resolution()
    if via == "ingress":
        user = await ingress.resolve_ingress_user(headers, client_ip(scope))
        if user is not None:
            result.principal = Principal(user=user, via=via, credential="ingress")
            return result
        if headers.get(ingress.USER_ID_HEADER):
            # The Supervisor named a user we cannot map (disabled, malformed).
            result.credential_failed = True
            return result
    authorization = headers.get("authorization")
    if authorization:
        scheme = authorization.split(" ", 1)[0].lower()
        if scheme == "bearer":
            result.bearer_sent = True
            token = authorization.split(" ", 1)[1].strip() if " " in authorization else ""
            principal = (
                await resolve_bearer_token(
                    token, via, client_ip(scope), kiosk_bootstrap=is_kiosk_bootstrap(scope)
                )
                if token
                else None
            )
            if principal is None:
                result.credential_failed = True
                log_auth_failure(scope, "bearer", "invalid_token")
            result.principal = principal
            return result
        if scheme == "basic":
            credentials = parse_basic_authorization(authorization)
            try:
                user = (
                    await authenticate_basic(credentials, client_ip(scope)) if credentials else None
                )
            except VerificationBusy:
                result.busy = True
                return result
            if user is None or user.totp_enabled_at is not None:
                # Basic carries no second factor: an account with one never
                # signs in this way.
                result.credential_failed = True
                log_auth_failure(
                    scope,
                    "basic",
                    "invalid_credentials",
                    username=credentials.username if credentials else None,
                )
                return result
            result.principal = Principal(user=user, via=via, credential="basic")
            return result
    if via == "ingress":
        # No cookie under ingress: the Home Assistant origin is shared by every add-on.
        return result
    token = sessions.read_session_cookie(headers.get("cookie"), secure=secure)
    if token is None:
        return await _resolve_trusted_network(scope, headers, via, result)
    resolved = await sessions.resolve_session(token)
    if resolved is None:
        result.credential_failed = True
        # An expired cookie on a trusted network: the rule still signs it in.
        return await _resolve_trusted_network(scope, headers, via, result)
    result.principal = Principal(
        user=resolved.user, via=via, credential="session", session_id=resolved.session.id
    )
    if resolved.refreshed_max_age is not None:
        result.refreshed_cookie = sessions.session_cookie_header(
            token, secure=secure, max_age=resolved.refreshed_max_age
        )
    return result


async def _resolve_trusted_network(
    scope: Scope, headers: Headers, via: Via, result: _Resolution
) -> _Resolution:
    """The last resort of ``resolve()``: a trusted network rule, if one applies."""
    match = await trusted_network.resolve_trusted_user(scope, headers, via)
    if match is not None:
        result.principal = Principal(user=match[0], via="trusted", credential="trusted_network")
        result.credential_failed = False
    return result


class AuthMiddleware:
    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] not in ("http", "websocket"):
            await self.app(scope, receive, send)
            return
        path: str = scope.get("path", "")
        headers = Headers(scope=scope)
        via = request_via(scope)
        secure = origin_policy.request_is_secure(scope.get("scheme", "http"))
        scope["auth_secure"] = secure
        scope["auth_via"] = via

        # A banned address (app/auth/ip_ban.py) is refused before any credential
        # is read: no argon2id work for it. Never under ingress (never banned).
        if via != "ingress" and (
            is_api_path(path) or (path.rstrip("/") or "/") in AUTHENTICATED_NON_API_PATHS
        ):
            banned_for = await ip_ban.check(client_ip(scope))
            if banned_for is not None:
                if scope["type"] == "websocket":
                    await self._refuse_websocket(receive, send)
                else:
                    await self._respond(send, 429, ip_ban.BANNED_CODE, path, retry_after=banned_for)
                return

        try:
            result = await resolve(scope, headers, via, secure)
        except Exception:
            # Fail closed: a database error never lets a request through.
            logger.exception("Could not resolve the principal of a request")
            result = _Resolution()
            result.credential_failed = True
        set_principal(scope, result.principal)
        if result.principal is not None and result.principal.via != via:
            # A trusted network rule signed the request in: audit rows say so.
            via = result.principal.via
            scope["auth_via"] = via

        if scope["type"] == "websocket":
            if is_api_path(path) and (
                result.principal is None
                or must_change_password(result.principal)
                or await second_factor_enrolment_required(result.principal)
            ):
                await self._refuse_websocket(receive, send)
                return
            await self.app(scope, receive, send)
            return

        if result.busy:
            await self._respond(send, 503, "auth_busy", path)
            return

        method = scope.get("method", "GET").upper()
        # Only a bearer token that resolved exempts from the Origin rule.
        token_principal = result.principal is not None and result.principal.credential == "token"
        if (
            method in UNSAFE_METHODS
            and not token_principal
            and not origin_policy.unsafe_request_allowed(headers)
        ):
            await self._respond(send, 403, "auth_origin_rejected", path)
            return

        if result.principal is None and (
            (is_api_path(path) and (method, path.rstrip("/") or "/") not in PUBLIC_ROUTES)
            or (path.rstrip("/") or "/") in AUTHENTICATED_NON_API_PATHS
        ):
            if not await users_exist():
                code = "auth_setup_required"
            elif result.credential_failed:
                code = "auth_invalid_credentials"
            else:
                code = "auth_required"
            # No Basic challenge under ingress: the browser would prompt for a
            # password inside the Home Assistant panel.
            challenge = not result.bearer_sent and via != "ingress"
            await self._respond(send, 401, code, path, challenge=challenge)
            return

        if (
            must_change_password(result.principal)
            and is_api_path(path)
            and (method, path.rstrip("/") or "/") not in PASSWORD_CHANGE_ROUTES
        ):
            await self._respond(send, 403, "auth_password_change_required", path)
            return

        if (
            is_api_path(path)
            and not must_change_password(result.principal)
            and (method, path.rstrip("/") or "/") not in SECOND_FACTOR_ENROLMENT_ROUTES
            and await second_factor_enrolment_required(result.principal)
        ):
            await self._respond(send, 403, "auth_2fa_enrolment_required", path)
            return

        await self.app(
            scope,
            receive,
            self._wrap_send(send, path, result.refreshed_cookie, no_cookie=via == "ingress"),
        )

    @staticmethod
    def _wrap_send(
        send: Send, path: str, refreshed_cookie: str | None, *, no_cookie: bool = False
    ) -> Send:
        no_store = path.startswith(AUTH_PREFIX)
        if not no_store and refreshed_cookie is None and not no_cookie:
            return send

        async def wrapped(message: Message) -> None:
            if message["type"] == "http.response.start":
                raw = list(message.get("headers", []))
                if no_store:
                    raw = [(k, v) for k, v in raw if k.lower() != b"cache-control"]
                    raw.append((b"cache-control", b"no-store"))
                if no_cookie:
                    # Under ingress the origin is Home Assistant's, shared by every add-on.
                    raw = [(k, v) for k, v in raw if k.lower() != b"set-cookie"]
                already_sets_cookie = any(k.lower() == b"set-cookie" for k, _ in raw)
                if refreshed_cookie is not None and not already_sets_cookie and not no_cookie:
                    raw.append((b"set-cookie", refreshed_cookie.encode("latin-1")))
                message = {**message, "headers": raw}
            await send(message)

        return wrapped

    @staticmethod
    async def _respond(
        send: Send,
        status: int,
        code: str,
        path: str,
        *,
        challenge: bool = False,
        retry_after: int | None = None,
    ) -> None:
        body = coded_body(code, {"retry_after": retry_after} if retry_after else None)
        headers: list[tuple[bytes, bytes]] = [
            (b"content-type", b"application/json"),
            (b"cache-control", b"no-store"),
            (b"content-length", str(len(body)).encode("ascii")),
        ]
        if (
            status == 401
            and challenge
            and not path.startswith(AUTH_PREFIX)
            and await basic_compat_active()
        ):
            headers.append((b"www-authenticate", BASIC_CHALLENGE.encode("latin-1")))
        if status == 503:
            headers.append((b"retry-after", b"1"))
        elif retry_after:
            headers.append((b"retry-after", str(retry_after).encode("ascii")))
        await send({"type": "http.response.start", "status": status, "headers": headers})
        await send({"type": "http.response.body", "body": body})

    @staticmethod
    async def _refuse_websocket(receive: Receive, send: Send) -> None:
        message: dict[str, Any] = dict(await receive())
        if message.get("type") == "websocket.connect":
            await send({"type": "websocket.close", "code": WS_REFUSED_CLOSE_CODE})
