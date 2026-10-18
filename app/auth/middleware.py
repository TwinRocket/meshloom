"""The auth gate: resolves a principal for every HTTP request and WebSocket upgrade.

Replaces the 4.x ``BasicAuthMiddleware``. For each ``http`` and ``websocket``
scope it:

1. resolves the principal (``Principal``, ``app/auth/principal.py``) from, in
   order, the Home Assistant identity on an ingress request
   (``app/auth/providers/ingress.py``), ``Authorization: Bearer`` (device
   tokens, a later Access ticket: no token is valid yet),
   ``Authorization: Basic`` (the compatibility credential,
   ``app/security.py``), then the session cookie (``app/auth/sessions.py``,
   never under ingress), and stores it in ``scope["state"]``; when the
   argon2id queue is full (``VerificationBusy``) it answers 503 ``auth_busy``;
2. applies the CSRF rule to unsafe methods (``origin_policy.unsafe_request_allowed``):
   403 ``auth_origin_rejected`` unless a bearer token resolved;
3. refuses any ``/api`` route outside ``PUBLIC_ROUTES``, and the API docs
   (``AUTHENTICATED_NON_API_PATHS``), without a principal: 401
   ``auth_setup_required`` while no account exists, else 401
   ``auth_required`` (or ``auth_invalid_credentials`` when a credential was
   sent and did not check). A WebSocket upgrade is refused **before**
   ``accept()``, with close code 1008 (uvicorn answers HTTP 403, no frame).

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
from app.auth import sessions
from app.auth.principal import Principal, Via, set_principal
from app.auth.providers import ingress
from app.auth.providers.password import VerificationBusy
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
        ("POST", "/api/auth/logout"),
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
}


def is_api_path(path: str) -> bool:
    return path == API_PREFIX or path.startswith(API_PREFIX + "/")


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


def coded_body(code: str) -> bytes:
    return json.dumps({"detail": {"code": code, "message": _MESSAGES.get(code, code)}}).encode()


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


async def resolve_bearer_token(token: str, via: Via) -> Principal | None:
    """The principal of a device or API token. None until tokens exist (a later
    Access ticket): no bearer token is valid yet."""
    return None


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
            principal = await resolve_bearer_token(token, via) if token else None
            if principal is None:
                result.credential_failed = True
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
            if user is None:
                result.credential_failed = True
                return result
            result.principal = Principal(user=user, via=via, credential="basic")
            return result
    if via == "ingress":
        # No cookie under ingress: the Home Assistant origin is shared by every add-on.
        return result
    token = sessions.read_session_cookie(headers.get("cookie"), secure=secure)
    if token is None:
        return result
    resolved = await sessions.resolve_session(token)
    if resolved is None:
        result.credential_failed = True
        return result
    result.principal = Principal(
        user=resolved.user, via=via, credential="session", session_id=resolved.session.id
    )
    if resolved.refreshed_max_age is not None:
        result.refreshed_cookie = sessions.session_cookie_header(
            token, secure=secure, max_age=resolved.refreshed_max_age
        )
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

        try:
            result = await resolve(scope, headers, via, secure)
        except Exception:
            # Fail closed: a database error never lets a request through.
            logger.exception("Could not resolve the principal of a request")
            result = _Resolution()
            result.credential_failed = True
        set_principal(scope, result.principal)

        if scope["type"] == "websocket":
            if is_api_path(path) and result.principal is None:
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
        send: Send, status: int, code: str, path: str, *, challenge: bool = False
    ) -> None:
        body = coded_body(code)
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
        await send({"type": "http.response.start", "status": status, "headers": headers})
        await send({"type": "http.response.body", "body": body})

    @staticmethod
    async def _refuse_websocket(receive: Receive, send: Send) -> None:
        message: dict[str, Any] = dict(await receive())
        if message.get("type") == "websocket.connect":
            await send({"type": "websocket.close", "code": WS_REFUSED_CLOSE_CODE})
