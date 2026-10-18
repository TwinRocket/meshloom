"""Trusted proxies: the one interpreter of ``X-Forwarded-For`` and ``X-Forwarded-Proto``.

Behind a reverse proxy (nginx, Caddy, Traefik, cloudflared) the TCP peer is the
proxy, and the browser's address and scheme only arrive in headers that anyone
can send. They are believed only when the peer is listed in
``auth_settings.trusted_proxies`` (Settings → Local Configuration → Trusted
proxies, ``PATCH /api/auth/settings``): addresses or CIDRs, **empty by
default**, loopback not trusted implicitly. From a listed peer:

- ``scope["client"]`` becomes the rightmost address of ``X-Forwarded-For``
  that is not itself a trusted proxy (each proxy appends the address it saw,
  so what a client wrote on the left is never picked);
- ``scope["scheme"]`` becomes ``X-Forwarded-Proto`` when it is ``http``,
  ``https``, ``ws`` or ``wss`` (``ws``/``wss`` on a WebSocket), so the session
  cookie gets ``__Host-`` and ``Secure`` (``origin_policy.request_is_secure``).

From any other peer both headers are ignored. The work is uvicorn's own
``ProxyHeadersMiddleware`` and ``_TrustedHosts`` (CIDRs, walk from the right),
fed from the database instead of uvicorn's ``--forwarded-allow-ips``: every
launcher starts uvicorn with ``--no-proxy-headers``
(``tests/test_proxy_headers.py`` checks them), so nothing rewrites the scope
before this middleware, which is the outermost one. Everything after it
(``client_ip()`` and the CSRF/cookie rules of ``app/auth/middleware.py``, the
login delays, audit, sessions) reads the rewritten scope and never a header.
``X-Forwarded-Host`` is not a client claim of this kind: only the origin
policy reads it, for the WebSocket and CSRF same-origin match
(``app/origin_policy.py``).

The TCP peer before any rewrite stays available as ``peer_address(scope)``: the
Home Assistant ingress check (``app/auth/providers/ingress.py``) needs the
Supervisor's own address, whatever the proxy list says.

The list is kept in memory like the allowed origins: ``load()`` fills it at
startup and ``AuthSettingsRepository.update()`` calls ``apply()`` after every
write. Nothing here imports the app at load time.
"""

from __future__ import annotations

import ipaddress
import logging
from collections.abc import Iterable, Mapping
from typing import TYPE_CHECKING, Any

from starlette.types import ASGIApp, Receive, Scope, Send
from uvicorn.middleware.proxy_headers import ProxyHeadersMiddleware

if TYPE_CHECKING:
    from app.auth.models import AuthSettings

logger = logging.getLogger(__name__)

MAX_TRUSTED_PROXIES = 32

# Where the TCP peer is kept once the scope may have been rewritten.
PEER_STATE_KEY = "peer"

_current: tuple[str, ...] = ()


def normalize_proxy(raw: str) -> str:
    """An address or CIDR in canonical network form (``10.0.0.1`` → ``10.0.0.1/32``).

    Raises ``ValueError`` for anything else, and for a network that covers a
    whole address family: trusting every peer would let any client pick its
    own address.
    """
    network = ipaddress.ip_network(raw.strip(), strict=False)
    if network.prefixlen == 0:
        raise ValueError("a trusted proxy cannot cover every address")
    return str(network)


def normalize_proxies(raw_proxies: Iterable[str]) -> list[str]:
    """Normalize every entry, drop blanks and duplicates, keep the order."""
    proxies: list[str] = []
    for raw in raw_proxies:
        if not raw.strip():
            continue
        proxy = normalize_proxy(raw)
        if proxy not in proxies:
            proxies.append(proxy)
    if len(proxies) > MAX_TRUSTED_PROXIES:
        raise ValueError(f"at most {MAX_TRUSTED_PROXIES} trusted proxies")
    return proxies


def valid_stored_proxies(stored: Iterable[str]) -> tuple[str, ...]:
    """Keep the entries that pass the PATCH validation, drop and log the rest."""
    proxies: list[str] = []
    for raw in stored:
        try:
            proxy = normalize_proxy(raw)
        except ValueError as exc:
            logger.warning("Ignoring invalid trusted proxy %r: %s", raw, exc)
            continue
        if proxy in proxies:
            continue
        if len(proxies) >= MAX_TRUSTED_PROXIES:
            logger.warning("Ignoring trusted proxy %r: over the %d limit", raw, MAX_TRUSTED_PROXIES)
            continue
        proxies.append(proxy)
    return tuple(proxies)


def current() -> tuple[str, ...]:
    """The trusted proxies as last read from the database, normalised."""
    return _current


def apply(auth_settings: AuthSettings) -> tuple[str, ...]:
    """Replace the in-memory list with the valid ``trusted_proxies``."""
    global _current
    _current = valid_stored_proxies(auth_settings.trusted_proxies)
    return _current


async def load() -> tuple[str, ...]:
    """Read the trusted proxies from the database. Call at startup."""
    from app.repository.auth import AuthSettingsRepository

    return apply(await AuthSettingsRepository.get())


def reset() -> None:
    """Back to no trusted proxy (tests, and a fresh process)."""
    global _current
    _current = ()


def peer_address(scope: Mapping[str, Any]) -> str | None:
    """The TCP peer's address, before any rewrite from forwarded headers."""
    state = scope.get("state")
    if isinstance(state, Mapping) and PEER_STATE_KEY in state:
        peer = state[PEER_STATE_KEY]
        return peer if isinstance(peer, str) else None
    client = scope.get("client")
    return client[0] if client else None


class TrustedProxyHeadersMiddleware:
    """uvicorn's ``ProxyHeadersMiddleware`` for the proxies in ``current()``.

    Rebuilt when the list changes, so a saved setting applies to the next
    request without a restart.
    """

    def __init__(self, app: ASGIApp) -> None:
        self.app = app
        self._proxies: tuple[str, ...] | None = None
        self._inner: ProxyHeadersMiddleware | None = None

    def _middleware(self) -> ProxyHeadersMiddleware:
        proxies = current()
        if self._inner is None or proxies != self._proxies:
            # An empty list trusts no peer at all (uvicorn's default would be loopback).
            self._inner = ProxyHeadersMiddleware(self.app, trusted_hosts=list(proxies))  # type: ignore[arg-type]
            self._proxies = proxies
        return self._inner

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] not in ("http", "websocket"):
            await self.app(scope, receive, send)
            return
        client = scope.get("client")
        state = scope.setdefault("state", {})
        if isinstance(state, dict):
            state[PEER_STATE_KEY] = client[0] if client else None
        if not current():
            await self.app(scope, receive, send)
            return
        await self._middleware()(scope, receive, send)  # type: ignore[arg-type]
