"""Security response headers that depend on how the request arrived.

The static ones (``nosniff``, ``Referrer-Policy``, the framing rules) stay in
``app/main.py``. This module adds the three that need the request:

- ``Strict-Transport-Security`` only when the browser reached the instance over
  https directly (scheme ``https``/``wss`` after ``app/middleware/proxy_headers.py``
  and ``request_via() == "lan"``). Never on plain http (the header is ignored
  there, and a LAN address must not be pinned to https), and never under Home
  Assistant ingress or the relay: those are another origin's business (HA's own
  HSTS), and pinning it from an add-on would break its other add-ons. No
  ``includeSubDomains`` and no ``preload``: the instance does not own the domain.
- ``Cache-Control: no-store`` on ``/api/auth/*``, ``/api/remote/*`` (the auth
  gate already sets it on the first; this is a second net, and covers a response
  it did not produce) and on the routes that return credential or secret
  material (``NO_STORE_PATHS``: the radio private key, the backups, the channel
  keys). ``tests/test_security_headers.py`` fails when a route that returns such
  a field is missing from the list.
- The same headers on an unhandled exception: the 500 is written here, before
  the exception goes on to Starlette's ``ServerErrorMiddleware`` (outermost, so
  it would otherwise answer around this layer), which then skips its own response
  and re-raises as before.
- ``Permissions-Policy``: camera and microphone off, geolocation for the page
  itself (the radio position button uses it). Web Push and service workers are
  not policy-controlled features, so they are unaffected.

No CSP beyond ``frame-ancestors``: maps, WebGPU and inline styles would break.
"""

from __future__ import annotations

from starlette.datastructures import MutableHeaders
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.auth.middleware import request_via
from app.origin_policy import request_is_secure

HSTS_VALUE = "max-age=31536000"
PERMISSIONS_POLICY = "camera=(), microphone=(), geolocation=(self)"
NO_STORE_PREFIXES = ("/api/auth", "/api/remote")
# Routes outside those prefixes that return credential or secret material.
NO_STORE_PATHS = (
    "/api/radio/private-key",
    "/api/settings/backup",
    "/api/channels",
    "/api/push/subscriptions",
    "/api/debug",
)


def _wants_no_store(path: str) -> bool:
    return any(
        path == prefix or path.startswith(prefix + "/")
        for prefix in (*NO_STORE_PREFIXES, *NO_STORE_PATHS)
    )


class SecurityHeadersMiddleware:
    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        hsts = request_is_secure(scope.get("scheme", "http")) and request_via(scope) == "lan"
        no_store = _wants_no_store(scope.get("path", ""))

        started = False

        def decorate(headers: MutableHeaders) -> None:
            headers.setdefault("Permissions-Policy", PERMISSIONS_POLICY)
            if hsts:
                headers.setdefault("Strict-Transport-Security", HSTS_VALUE)
            if no_store:
                headers["Cache-Control"] = "no-store"

        async def wrapped(message: Message) -> None:
            nonlocal started
            if message["type"] == "http.response.start":
                started = True
                decorate(MutableHeaders(scope=message))
            await send(message)

        try:
            await self.app(scope, receive, wrapped)
        except Exception:
            if not started:
                start: Message = {"type": "http.response.start", "status": 500, "headers": []}
                headers = MutableHeaders(scope=start)
                headers["Content-Type"] = "text/plain; charset=utf-8"
                await wrapped(start)
                await wrapped({"type": "http.response.body", "body": b"Internal Server Error"})
            raise
