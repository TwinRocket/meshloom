"""CORS: same-origin by default, plus the origins listed in ``app_settings``.

The frontend and the API docs only use relative URLs, so a browser never needs
CORS to use Meshloom from Meshloom's own address (Vite's dev proxy and Home
Assistant ingress are same-origin too). Another site calling the API with the
user's credentials is a cross-origin request: it gets no
``Access-Control-Allow-Origin`` unless its origin is in
``app_settings.auth_allowed_origins`` (Settings → Local Configuration → Allowed
origins, ``PATCH /api/settings``), empty by default.

The CORS logic itself is Starlette's ``CORSMiddleware``; the list, its
normalisation and the match of a request's ``Origin`` are the shared origin
policy (``app/origin_policy.py``), which the WebSocket upgrade uses too. This
module only rebuilds the middleware when the list changes.
"""

from __future__ import annotations

from starlette.middleware.cors import CORSMiddleware
from starlette.types import ASGIApp, Receive, Scope, Send

from app import origin_policy

# Re-exported for the settings router, which validates the PATCH with it.
from app.origin_policy import normalize_origins as normalize_origins

# Every method a router uses. OPTIONS is answered by the middleware itself.
ALLOWED_METHODS = ("GET", "POST", "PUT", "PATCH", "DELETE")
# Content-Type for JSON bodies, Authorization for the optional Basic auth.
ALLOWED_HEADERS = ("Authorization", "Content-Type")


class _PolicyCORSMiddleware(CORSMiddleware):
    """Starlette's middleware, matching ``Origin`` with the shared policy."""

    def is_allowed_origin(self, origin: str) -> bool:
        return origin_policy.origin_listed(origin)


class AllowedOriginsCORSMiddleware:
    """Starlette's ``CORSMiddleware`` for the origins in ``origin_policy.current()``.

    A listed origin gets ``Access-Control-Allow-Origin`` and
    ``Access-Control-Allow-Credentials``; any other origin gets no CORS header on
    a simple request and a 400 on a preflight. Same-origin requests need none.
    """

    def __init__(self, app: ASGIApp) -> None:
        self.app = app
        self._origins: tuple[str, ...] | None = None
        self._cors: CORSMiddleware | None = None

    def _middleware(self) -> CORSMiddleware:
        origins = origin_policy.current()
        if self._cors is None or origins != self._origins:
            self._cors = _PolicyCORSMiddleware(
                self.app,
                allow_origins=origins,
                # Starlette sends this header on every response once set; without
                # an allowed origin a browser ignores it, but say nothing then.
                allow_credentials=bool(origins),
                allow_methods=ALLOWED_METHODS,
                allow_headers=ALLOWED_HEADERS,
            )
            self._origins = origins
        return self._cors

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        await self._middleware()(scope, receive, send)
