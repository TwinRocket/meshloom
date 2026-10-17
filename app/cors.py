"""CORS: same-origin by default, plus the origins listed in ``app_settings``.

The frontend and the API docs only use relative URLs, so a browser never needs
CORS to use Meshloom from Meshloom's own address (Vite's dev proxy and Home
Assistant ingress are same-origin too). Another site calling the API with the
user's credentials is a cross-origin request: it gets no
``Access-Control-Allow-Origin`` unless its origin is in
``app_settings.auth_allowed_origins`` (Settings → Local Configuration → Allowed
origins, ``PATCH /api/settings``), empty by default.

The CORS logic itself is Starlette's ``CORSMiddleware``; this module only keeps
the list in memory and rebuilds that middleware when the list changes.
``load()`` fills it at startup and ``AppSettingsRepository.update()`` refreshes
it after every write, like ``app/radio_workarounds.py``. Nothing here imports the
app at load time, so the repository and ``app.models`` can both import it.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from pydantic import AnyHttpUrl, TypeAdapter, ValidationError
from starlette.middleware.cors import CORSMiddleware
from starlette.types import ASGIApp, Receive, Scope, Send

if TYPE_CHECKING:
    from app.models import AppSettings

logger = logging.getLogger(__name__)

MAX_ALLOWED_ORIGINS = 32
MAX_ORIGIN_LENGTH = 255

# Every method a router uses. OPTIONS is answered by the middleware itself.
ALLOWED_METHODS = ("GET", "POST", "PUT", "PATCH", "DELETE")
# Content-Type for JSON bodies, Authorization for the optional Basic auth.
ALLOWED_HEADERS = ("Authorization", "Content-Type")

_DEFAULT_PORTS = {"http": 80, "https": 443}
_url_adapter = TypeAdapter(AnyHttpUrl)


def normalize_origin(raw: str) -> str:
    """Return ``raw`` in the form a browser sends in ``Origin``, or raise ``ValueError``.

    ``scheme://host[:port]``: http or https, lowercase, punycode host, no default
    port, no path, query, fragment or credentials. ``*`` is refused: with
    credentials it would let every site in again.
    """
    text = raw.strip().rstrip("/")
    if not text:
        raise ValueError("empty origin")
    if len(text) > MAX_ORIGIN_LENGTH:
        raise ValueError("origin too long")
    if "*" in text:
        raise ValueError("wildcards are not allowed")
    try:
        url = _url_adapter.validate_python(text)
    except ValidationError as exc:
        raise ValueError("not an http(s) origin") from exc
    if url.username or url.password:
        raise ValueError("an origin has no credentials")
    if url.path not in (None, "", "/") or url.query is not None or url.fragment is not None:
        raise ValueError("an origin has no path, query or fragment")
    if not url.host:
        raise ValueError("an origin needs a host")
    origin = f"{url.scheme}://{url.host}"
    if url.port is not None and url.port != _DEFAULT_PORTS.get(url.scheme):
        origin += f":{url.port}"
    return origin


def normalize_origins(raw_origins: list[str]) -> list[str]:
    """Normalize every origin, drop blanks and duplicates, keep the order."""
    origins: list[str] = []
    for raw in raw_origins:
        if not raw.strip():
            continue
        origin = normalize_origin(raw)
        if origin not in origins:
            origins.append(origin)
    if len(origins) > MAX_ALLOWED_ORIGINS:
        raise ValueError(f"at most {MAX_ALLOWED_ORIGINS} allowed origins")
    return origins


_current: tuple[str, ...] = ()


def current() -> tuple[str, ...]:
    """The allowed origins as last read from the database."""
    return _current


def _valid_stored_origins(stored: list[str]) -> tuple[str, ...]:
    """Keep the entries that pass the PATCH validation, drop and log the rest.

    The column can be written outside the API (manual SQL, a restore): a ``*``
    reaching Starlette together with credentials would reflect every Origin.
    """
    origins: list[str] = []
    for raw in stored:
        try:
            origin = normalize_origin(raw)
        except ValueError as exc:
            logger.warning("Ignoring invalid allowed origin %r: %s", raw, exc)
            continue
        if origin in origins:
            continue
        if len(origins) >= MAX_ALLOWED_ORIGINS:
            logger.warning(
                "Ignoring allowed origin %r: over the %d limit", raw, MAX_ALLOWED_ORIGINS
            )
            continue
        origins.append(origin)
    return tuple(origins)


def apply(app_settings: AppSettings) -> tuple[str, ...]:
    """Replace the in-memory list with the valid ``auth_allowed_origins``."""
    global _current
    _current = _valid_stored_origins(app_settings.auth_allowed_origins)
    return _current


async def load() -> tuple[str, ...]:
    """Read the allowed origins from the database. Call at startup."""
    from app.repository.settings import AppSettingsRepository

    return apply(await AppSettingsRepository.get())


def reset() -> None:
    """Back to same-origin only (tests, and a fresh process)."""
    global _current
    _current = ()


class AllowedOriginsCORSMiddleware:
    """Starlette's ``CORSMiddleware`` for the origins in ``current()``.

    A listed origin gets ``Access-Control-Allow-Origin`` and
    ``Access-Control-Allow-Credentials``; any other origin gets no CORS header on
    a simple request and a 400 on a preflight. Same-origin requests need none.
    """

    def __init__(self, app: ASGIApp) -> None:
        self.app = app
        self._origins: tuple[str, ...] | None = None
        self._cors: CORSMiddleware | None = None

    def _middleware(self) -> CORSMiddleware:
        origins = current()
        if self._cors is None or origins != self._origins:
            self._cors = CORSMiddleware(
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
