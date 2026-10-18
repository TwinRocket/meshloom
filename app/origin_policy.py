"""One origin policy for CORS (``app/cors.py``), the WebSocket upgrade (``app/websocket.py``)
and the CSRF rule of the auth middleware (``app/auth/middleware.py``).

It is also the one place that reads proxy headers: ``X-Forwarded-Host`` for
same-origin, ``X-Forwarded-Proto`` for the cookie flavour
(``request_is_secure``). Nothing else in the app interprets them.

Both rules read the same allowlist, ``app_settings.auth_allowed_origins``
(Settings → Local Configuration → Allowed origins, ``PATCH /api/settings``),
and parse an origin the same way: ``scheme://host[:port]``, http or https,
lowercase, punycode host (IDN), no default port. The parser is pydantic's
``AnyHttpUrl`` (the WHATWG URL rules browsers use), so a stored entry, a
request's ``Origin`` and its ``Host`` all end up in one form.

- **A stored entry** (``normalize_origin``) is user input: surrounding spaces
  and trailing slashes are dropped, ``*``, paths and credentials are refused.
- **A request's** ``Origin`` (``parse_request_origin``) is what a browser sent:
  nothing is dropped, a path (even ``/``), query, fragment, credentials or
  ``null`` make it unusable, which refuses it everywhere.
- **Listed** (``origin_listed``): the request origin, normalised, is in the
  allowlist. CORS answers only listed origins; the WebSocket accepts them too.
- **Same-origin** (``same_origin``, WebSocket only: CORS never sees a
  same-origin request as one to answer): the origin's host and port equal the
  request's ``Host``, or the first ``X-Forwarded-Host``. The scheme is not
  compared and ``X-Forwarded-Proto`` is not read: behind a TLS terminator the
  backend sees http while the browser's ``Origin`` is https. A browser page
  cannot set ``Host`` or ``X-Forwarded-Host``, so this is safe against
  cross-site WebSocket hijacking; DNS rebinding is a ``Host`` guard's job.

The allowlist is kept in memory: ``load()`` fills it at startup and
``AppSettingsRepository.update()`` calls ``apply()`` after every write, like
``app/radio_workarounds.py``. Nothing here imports the app at load time, so the
repository and ``app.models`` can import it.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping
from typing import TYPE_CHECKING, NamedTuple
from urllib.parse import urlsplit

from pydantic import AnyHttpUrl, TypeAdapter, ValidationError

if TYPE_CHECKING:
    from app.models import AppSettings

logger = logging.getLogger(__name__)

MAX_ALLOWED_ORIGINS = 32
MAX_ORIGIN_LENGTH = 255

_DEFAULT_PORTS = {"http": 80, "https": 443}
_url_adapter = TypeAdapter(AnyHttpUrl)


class Origin(NamedTuple):
    """A parsed http(s) origin: lowercase scheme, punycode host, explicit port."""

    scheme: str
    host: str
    port: int

    def serialize(self) -> str:
        """The form a browser sends in ``Origin``: no default port, no slash."""
        if self.port == _DEFAULT_PORTS[self.scheme]:
            return f"{self.scheme}://{self.host}"
        return f"{self.scheme}://{self.host}:{self.port}"


def _parse(text: str) -> Origin:
    """Parse ``scheme://host[:port]`` with nothing after it, or raise ``ValueError``."""
    if len(text) > MAX_ORIGIN_LENGTH:
        raise ValueError("origin too long")
    if "*" in text:
        raise ValueError("wildcards are not allowed")
    # The WHATWG parser reads "\\" as "/" and drops tabs and newlines, so
    # "http://a\\b" would come out as the origin of host "a".
    if "\\" in text or any(char.isspace() for char in text):
        raise ValueError("not an http(s) origin")
    try:
        parts = urlsplit(text)
    except ValueError as exc:
        raise ValueError("not an http(s) origin") from exc
    # urlsplit first, for the structure: the WHATWG parser alone would also read
    # "http:host" or "http://host/" as an origin.
    if parts.scheme.lower() not in _DEFAULT_PORTS or not parts.netloc:
        raise ValueError("not an http(s) origin")
    if "@" in parts.netloc:
        raise ValueError("an origin has no credentials")
    if parts.path or parts.query or parts.fragment or text.endswith(("?", "#")):
        raise ValueError("an origin has no path, query or fragment")
    try:
        url = _url_adapter.validate_python(text)
    except ValidationError as exc:
        raise ValueError("not an http(s) origin") from exc
    if not url.host:
        raise ValueError("an origin needs a host")
    return Origin(url.scheme, url.host, url.port or _DEFAULT_PORTS[url.scheme])


def normalize_origin(raw: str) -> str:
    """Return a stored or typed origin in the form a browser sends, or raise ``ValueError``.

    Surrounding spaces and trailing slashes are dropped. ``*`` is refused:
    with credentials it would let every site in again.
    """
    text = raw.strip().rstrip("/")
    if not text:
        raise ValueError("empty origin")
    return _parse(text).serialize()


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


def parse_request_origin(raw: str) -> Origin | None:
    """A request's ``Origin`` header, parsed; None when it is unusable (``null``, a path...)."""
    try:
        return _parse(raw.strip())
    except ValueError:
        return None


_current: tuple[str, ...] = ()


def current() -> tuple[str, ...]:
    """The allowed origins as last read from the database, normalised."""
    return _current


def origin_listed(raw_origin: str) -> bool:
    """Whether a request's ``Origin`` is in the allowlist."""
    origin = parse_request_origin(raw_origin)
    return origin is not None and origin.serialize() in _current


def _host_target(raw_host: str, scheme: str) -> tuple[str, int] | None:
    """``Host`` or ``X-Forwarded-Host`` as (host, port), the port defaulting to ``scheme``'s."""
    origin = parse_request_origin(f"{scheme}://{raw_host.strip()}")
    return None if origin is None else (origin.host, origin.port)


def same_origin(origin: Origin, headers: Mapping[str, str]) -> bool:
    """Whether ``origin``'s host and port are the request's ``Host`` or first ``X-Forwarded-Host``."""
    candidates = [headers.get("host", "")]
    forwarded_host = headers.get("x-forwarded-host")
    if forwarded_host:
        candidates.append(forwarded_host.split(",", 1)[0])
    target = (origin.host, origin.port)
    return any(
        _host_target(candidate, origin.scheme) == target for candidate in candidates if candidate
    )


def websocket_origin_allowed(headers: Mapping[str, str]) -> bool:
    """Whether a WebSocket upgrade may be accepted: no ``Origin``, same-origin, or listed.

    Browsers always send ``Origin`` on an upgrade; scripts and native clients
    (meshenger) send none and keep working. ``Origin: null`` is refused.
    """
    raw = headers.get("origin")
    if raw is None:
        return True
    origin = parse_request_origin(raw)
    if origin is None:
        return False
    return same_origin(origin, headers) or origin.serialize() in _current


# Sec-Fetch-Site values a browser sends for a request this site made itself
# ("same-origin") or that the user started (typed URL, bookmark: "none").
_SAFE_FETCH_SITES = frozenset({"same-origin", "none"})


def unsafe_request_allowed(headers: Mapping[str, str]) -> bool:
    """The CSRF rule for a POST/PUT/PATCH/DELETE (``app/auth/middleware.py``).

    The algorithm of Go's ``net/http.CrossOriginProtection``, plus the allowlist:

    1. ``Sec-Fetch-Site: same-origin`` or ``none``: allowed.
    2. No ``Sec-Fetch-Site`` and no ``Origin``: not a browser (curl, scripts,
       Home Assistant sensors), allowed. Every browser sends ``Origin`` on a
       cross-origin unsafe request.
    3. Otherwise ``Origin`` must be listed or same-origin (``same_origin``:
       ``Host`` or the first ``X-Forwarded-Host``, as for the WebSocket).
       ``Origin: null``, a missing ``Origin`` with a cross-site
       ``Sec-Fetch-Site``, or an unusable value are refused.
    """
    fetch_site = (headers.get("sec-fetch-site") or "").strip().lower()
    if fetch_site in _SAFE_FETCH_SITES:
        return True
    raw = headers.get("origin")
    if raw is None:
        return not fetch_site
    origin = parse_request_origin(raw)
    if origin is None:
        return False
    return origin.serialize() in _current or same_origin(origin, headers)


def request_is_secure(scope_scheme: str, headers: Mapping[str, str]) -> bool:
    """Whether the browser reached this instance over https.

    The ASGI scheme (``https``/``wss``), or the first ``X-Forwarded-Proto``
    from a TLS terminator. Read from any peer: it only picks the session
    cookie's flavour (``__Host-`` and ``Secure``), and a client that lies about
    it only gets a cookie its own browser refuses. Nothing grants access from it.
    """
    if scope_scheme in ("https", "wss"):
        return True
    forwarded = headers.get("x-forwarded-proto")
    return bool(forwarded) and forwarded.split(",", 1)[0].strip().lower() == "https"


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
