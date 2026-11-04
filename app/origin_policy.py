"""Which browser pages may act on this instance, and from which address bots may change.

Three rules share this module:

- **Cross-site requests** (``request_allowed``, used by ``CrossSiteGuardMiddleware`` for
  every non-safe HTTP method and by ``/api/ws`` before it accepts):

  1. ``Sec-Fetch-Site: same-origin`` or ``none``: allowed. Browsers only send this
     header on https and localhost, and a page cannot set it.
  2. No ``Sec-Fetch-Site`` and no ``Origin``: not a browser (curl, scripts, Home
     Assistant sensors), allowed.
  3. Otherwise ``Origin`` must have the host and port of ``Host`` or of the first
     ``X-Forwarded-Host``. ``Origin: null`` and anything unparseable are refused.

- **CORS**: there is none. ``app/main.py`` installs no CORS middleware, so a preflight
  fails in the browser and another site can neither read a response nor send a request
  with a custom header.

- **Bot edits** (``bot_edit_allowed``): DNS rebinding makes a hostile page same-origin
  (its ``Origin`` and ``Host`` both carry the attacker's name), so the cross-site rule
  cannot see it. Creating or changing a bot therefore also needs a raw ``Host`` that a
  public DNS name cannot be, see ``host_allowed``, and the same of ``X-Forwarded-Host``
  when present. A proxy that rewrites ``Host`` and says nothing cannot be told apart.

``X-Forwarded-Host`` is trusted for rule 3 because a browser page cannot send it to
another origin: it is a custom header, which needs the preflight that fails above.
``X-Forwarded-Proto`` is not read: the scheme is not compared, only host and port, with
the port defaulting to that of the ``Origin``'s scheme (a TLS terminator talks http to
this backend while the browser's ``Origin`` is https).

Home Assistant ingress is exempt from the cross-site and the bot rule: the request comes
from the Supervisor's address (a TCP peer, which no client can spoof) and Home Assistant
has authenticated it. The Host it carries is Home Assistant's, often a public name.
"""

from __future__ import annotations

import ipaddress
import json
import logging
import socket
import time
from collections.abc import Mapping
from functools import lru_cache
from typing import NamedTuple
from urllib.parse import urlsplit

from pydantic import AnyHttpUrl, TypeAdapter, ValidationError
from starlette.datastructures import Headers
from starlette.types import ASGIApp, Receive, Scope, Send

logger = logging.getLogger(__name__)

# The Supervisor's address on the ``hassio`` network, where every ingress request comes
# from (Home Assistant developer docs, "Ingress").
SUPERVISOR_IP = "172.30.32.2"

# Names a public DNS name cannot be: a rebinding page never has them.
LOCAL_HOST_SUFFIXES = (".local", ".home.arpa", ".lan", ".internal")

SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})
CROSS_SITE_REFUSED_CODE = "cross_site_refused"
CROSS_SITE_REFUSED_MESSAGE = "Cross-site request refused"
WS_REFUSED_CLOSE_CODE = 1008

MAX_ORIGIN_LENGTH = 255
_DEFAULT_PORTS = {"http": 80, "https": 443}
_SAFE_FETCH_SITES = frozenset({"same-origin", "none"})
_REFUSAL_LOG_INTERVAL_S = 60.0
_url_adapter = TypeAdapter(AnyHttpUrl)
_last_refusal_log = 0.0


class Origin(NamedTuple):
    """A parsed http(s) origin: lowercase scheme, punycode host, explicit port."""

    scheme: str
    host: str
    port: int


def parse_origin(raw: str) -> Origin | None:
    """Parse ``scheme://host[:port]`` with nothing after it; None when it is anything else."""
    text = raw.strip()
    # The WHATWG parser reads "\\" as "/" and drops tabs and newlines, so "http://a\\b"
    # would come out as the origin of host "a".
    if len(text) > MAX_ORIGIN_LENGTH or "\\" in text or any(char.isspace() for char in text):
        return None
    try:
        parts = urlsplit(text)
    except ValueError:
        return None
    if parts.scheme.lower() not in _DEFAULT_PORTS or not parts.netloc or "@" in parts.netloc:
        return None
    if parts.path or parts.query or parts.fragment or text.endswith(("?", "#")):
        return None
    try:
        url = _url_adapter.validate_python(text)
    except ValidationError:
        return None
    if not url.host:
        return None
    return Origin(url.scheme, url.host, url.port or _DEFAULT_PORTS[url.scheme])


def _same_origin(origin: Origin, headers: Mapping[str, str]) -> bool:
    """Whether ``origin``'s host and port are ``Host`` or the first ``X-Forwarded-Host``."""
    candidates = [headers.get("host", ""), headers.get("x-forwarded-host", "").split(",", 1)[0]]
    for candidate in candidates:
        candidate = candidate.strip()
        target = parse_origin(f"{origin.scheme}://{candidate}") if candidate else None
        if target is not None and (target.host, target.port) == (origin.host, origin.port):
            return True
    return False


def browser_request_allowed(headers: Mapping[str, str]) -> bool:
    """The cross-site rule, on headers alone (see the module docstring)."""
    fetch_site = (headers.get("sec-fetch-site") or "").strip().lower()
    if fetch_site in _SAFE_FETCH_SITES:
        return True
    raw_origin = headers.get("origin")
    if raw_origin is None:
        return not fetch_site
    origin = parse_origin(raw_origin)
    return origin is not None and _same_origin(origin, headers)


def is_ingress_request(scope: Scope) -> bool:
    """Whether the request is the Supervisor's ingress proxy: the add-on and its TCP peer.

    ``scope["client"]`` is the peer: uvicorn only rewrites it for a proxy on 127.0.0.1,
    and the port an add-on may publish arrives from the Docker gateway, another address.
    """
    from app.services.install_kind import detect_install_kind

    client = scope.get("client")
    return bool(client) and client[0] == SUPERVISOR_IP and detect_install_kind()[0] == "addon"


def request_allowed(scope: Scope) -> bool:
    """The cross-site rule for an HTTP request or WebSocket upgrade; logs a refusal."""
    headers = Headers(scope=scope)
    if browser_request_allowed(headers) or is_ingress_request(scope):
        return True
    _log_refusal(headers)
    return False


def _log_refusal(headers: Headers) -> None:
    global _last_refusal_log
    now = time.monotonic()
    if now - _last_refusal_log < _REFUSAL_LOG_INTERVAL_S and _last_refusal_log:
        return
    _last_refusal_log = now
    logger.warning(
        "Refused a cross-site request: Origin %r, Host %r, X-Forwarded-Host %r, Sec-Fetch-Site %r "
        "(behind a reverse proxy, forward Host or X-Forwarded-Host; next refusals not logged for "
        "%.0f s)",
        headers.get("origin"),
        headers.get("host"),
        headers.get("x-forwarded-host"),
        headers.get("sec-fetch-site"),
        _REFUSAL_LOG_INTERVAL_S,
    )


class CrossSiteGuardMiddleware:
    """Answer 403 to a non-safe HTTP method from another site, before anything else runs."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if (
            scope["type"] != "http"
            or scope["method"].upper() in SAFE_METHODS
            or request_allowed(scope)
        ):
            await self.app(scope, receive, send)
            return
        body = json.dumps(
            {"detail": {"code": CROSS_SITE_REFUSED_CODE, "message": CROSS_SITE_REFUSED_MESSAGE}}
        ).encode()
        await send(
            {
                "type": "http.response.start",
                "status": 403,
                "headers": [
                    (b"content-type", b"application/json"),
                    (b"cache-control", b"no-store"),
                    (b"content-length", str(len(body)).encode("ascii")),
                ],
            }
        )
        await send({"type": "http.response.body", "body": body})


@lru_cache(maxsize=1)
def _machine_names() -> frozenset[str]:
    names: set[str] = set()
    for getter in (socket.gethostname, socket.getfqdn):
        try:
            name = getter().strip().rstrip(".").lower()
        except OSError:
            continue
        if name:
            names.add(name)
            names.add(name.split(".", 1)[0])
    return frozenset(names)


def _request_host(raw: str) -> str | None:
    """The host part of a ``Host`` header, lowercase, without port or brackets."""
    raw = raw.strip()
    if not raw or any(char in raw for char in "/\\@?# \t"):
        return None
    try:
        parsed = urlsplit(f"http://{raw}")
        host = parsed.hostname
        parsed.port  # noqa: B018 - raises ValueError on a malformed port
    except ValueError:
        return None
    return host.rstrip(".").lower() if host else None


def host_allowed(raw_host: str | None) -> bool:
    """Whether ``Host`` is an IP literal, localhost, a local suffix or this machine's name."""
    host = _request_host(raw_host or "")
    if not host:
        return False
    try:
        ipaddress.ip_address(host)
        return True
    except ValueError:
        pass
    if host == "localhost" or host.endswith(LOCAL_HOST_SUFFIXES):
        return True
    return host in _machine_names()


def bot_edit_allowed(scope: Scope) -> bool:
    """Whether this request may create or change a bot.

    Both names must be local: a proxy that rewrites ``Host`` for its upstream may still say
    which name the browser asked for. Trusting ``X-Forwarded-Host`` here can only refuse.
    """
    headers = Headers(scope=scope)
    forwarded = headers.get("x-forwarded-host")
    names_allowed = host_allowed(headers.get("host")) and (
        forwarded is None or host_allowed(forwarded.split(",", 1)[0])
    )
    return names_allowed or is_ingress_request(scope)
