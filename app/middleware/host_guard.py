"""The ``Host`` guard: DNS-rebinding defence (docs/dev/auth.md, "Host guard").

A page on a public DNS name that is re-pointed at a LAN address reaches the
instance with ``Host: attacker.example``. ``host_allowed`` is the one rule for
which names an instance answers to:

- an IP literal, ``localhost``, a ``.local`` / ``.home.arpa`` / ``.lan`` /
  ``.internal`` name, the machine's short and full name
  (``socket.gethostname()``, ``socket.getfqdn()``),
- the host of an entry of ``auth_settings.public_urls``,
- the Remote hostname (``set_remote_hostname()``, called by the Remote client
  once it is linked).

Two users, two strictnesses:

- **Trusted-network auto-login** (``app/auth/providers/trusted_network.py``)
  calls ``host_allowed`` directly and always refuses a rejected host, whatever
  ``auth_settings.host_guard_mode`` says: it is the only case where rebinding
  yields a principal without a cookie.
- **The middleware** here follows ``host_guard_mode``. ``observe`` (the 5.0.0
  default, so an upgrade behind any reverse proxy name keeps working) lets the
  request through, logs each new name once and records it in the bounded
  ``rejected_hosts()`` list. ``enforce`` answers 421 ``auth_host_rejected``
  (HTTP) or closes the WebSocket with 1008. The default becomes ``enforce`` in
  5.1.

Exempt from the middleware: Home Assistant ingress (the Supervisor's proxy,
``ingress.is_ingress_request``, independent of any setting) and requests the
relay tagged (``scope["state"]["via"] == "relay"``, set by our own code, never
by a client). It runs inside ``TrustedProxyHeadersMiddleware`` (the ingress
check needs the TCP peer it records) and outside the auth gate.

The list of observed names is what the access settings screen reads
(``rejected_hosts`` in ``GET /api/auth/settings``): newest first, at most
``MAX_REJECTED_HOSTS``, the least recently seen dropped when full. It lives in
memory, so a restart empties it (a name is seen again on its next request).
Only a well-formed name is recorded; a header that does not parse is refused in
``enforce`` mode and ignored in ``observe`` mode.

The mode and the public URLs are kept in memory like the trusted proxies:
``AuthSettingsRepository.update()`` calls ``apply()`` after every write and the
middleware loads them from the database on first use.
"""

from __future__ import annotations

import ipaddress
import json
import logging
import socket
import time
from collections import OrderedDict
from collections.abc import Iterable
from functools import lru_cache
from typing import TYPE_CHECKING
from urllib.parse import urlsplit

from pydantic import BaseModel, Field
from starlette.types import ASGIApp, Receive, Scope, Send

if TYPE_CHECKING:
    from app.auth.models import AuthSettings

logger = logging.getLogger(__name__)

# Host names a public DNS name cannot be: a rebinding page never has them.
LOCAL_HOST_SUFFIXES = (".local", ".home.arpa", ".lan", ".internal")

MAX_REJECTED_HOSTS = 64
MAX_HOST_NAME_LENGTH = 253
WS_REFUSED_CLOSE_CODE = 1008


class RejectedHost(BaseModel):
    """A name seen in a ``Host`` header that the guard would refuse."""

    host: str = Field(description="The name, lowercase, without port.")
    count: int = Field(description="Requests seen with this name since it was first recorded.")
    first_seen: int = Field(description="Unix time of the first request.")
    last_seen: int = Field(description="Unix time of the latest request.")


_mode: str = "observe"
_public_urls: tuple[str, ...] = ()
_loaded = False
_remote_hostnames: frozenset[str] = frozenset()
_rejected: OrderedDict[str, RejectedHost] = OrderedDict()


# ---------------------------------------------------------------- the rule


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


def _public_hosts(public_urls: Iterable[str]) -> frozenset[str]:
    hosts: set[str] = set()
    for url in public_urls:
        try:
            host = urlsplit(url.strip()).hostname
        except ValueError:
            continue
        if host:
            hosts.add(host.rstrip(".").lower())
    return frozenset(hosts)


def request_host(raw: str) -> str | None:
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


def host_allowed(raw_host: str | None, public_urls: Iterable[str] = ()) -> bool:
    """Whether the ``Host`` header names this instance (the strict rule, any mode)."""
    host = request_host(raw_host or "")
    if not host:
        return False
    try:
        ipaddress.ip_address(host)
        return True
    except ValueError:
        pass
    if host == "localhost" or host.endswith(LOCAL_HOST_SUFFIXES):
        return True
    return (
        host in _machine_names() or host in _public_hosts(public_urls) or host in _remote_hostnames
    )


def set_remote_hostname(hostname: str | None) -> None:
    """Declare the Remote hostname (``<slug>.<relay domain>``) as one of ours."""
    global _remote_hostnames
    host = request_host(hostname or "")
    _remote_hostnames = frozenset({host}) if host else frozenset()


# ---------------------------------------------------------------- settings


def mode() -> str:
    return _mode


def apply(auth_settings: AuthSettings) -> None:
    """Replace the in-memory mode and public URLs from the settings row."""
    global _mode, _public_urls, _loaded
    _mode = auth_settings.host_guard_mode
    _public_urls = tuple(auth_settings.public_urls)
    _loaded = True


async def _ensure_loaded() -> None:
    if not _loaded:
        from app.repository.auth import AuthSettingsRepository

        try:
            apply(await AuthSettingsRepository.get())
        except Exception:
            # Database not open yet (or no settings row): observation defaults,
            # tried again on the next request. The auth gate fails on its own.
            logger.debug("Host guard settings unavailable", exc_info=True)


def reset() -> None:
    """Forget settings, Remote hostname and observed names (tests, a fresh process)."""
    global _mode, _public_urls, _loaded, _remote_hostnames
    _mode, _public_urls, _loaded = "observe", (), False
    _remote_hostnames = frozenset()
    _rejected.clear()


# ---------------------------------------------------------------- observed names


def _recordable(host: str) -> bool:
    return len(host) <= MAX_HOST_NAME_LENGTH and host.isascii() and host.isprintable()


def record_rejected(host: str, now: int | None = None) -> None:
    """Count one request from a rejected name; log the first sighting."""
    stamp = int(time.time()) if now is None else now
    entry = _rejected.get(host)
    if entry is None:
        logger.warning(
            "Host header %r is not a declared address of this instance "
            "(observation mode lets it through; declare it in the access settings)",
            host,
        )
        entry = RejectedHost(host=host, count=0, first_seen=stamp, last_seen=stamp)
        if len(_rejected) >= MAX_REJECTED_HOSTS:
            _rejected.popitem(last=False)
    entry.count += 1
    entry.last_seen = stamp
    _rejected[host] = entry
    _rejected.move_to_end(host)


def rejected_hosts() -> list[RejectedHost]:
    """Observed rejected names, most recently seen first."""
    return [entry.model_copy() for entry in reversed(_rejected.values())]


def forget_rejected(host: str) -> None:
    """Drop a name from the list (once the user declared it)."""
    _rejected.pop(host.strip().rstrip(".").lower(), None)


# ---------------------------------------------------------------- middleware


def _exempt(scope: Scope) -> bool:
    from app.auth.providers import ingress

    state = scope.get("state")
    if isinstance(state, dict) and state.get("via") == "relay":
        return True
    return ingress.is_ingress_request(scope)


def _raw_host(scope: Scope) -> str | None:
    for name, value in scope.get("headers") or ():
        if name == b"host":
            return value.decode("latin-1")
    return None


def rejection_body(host: str | None) -> bytes:
    shown = host if host and _recordable(host) else "unknown"
    return json.dumps(
        {
            "detail": {
                "code": "auth_host_rejected",
                "message": (
                    f"Host '{shown}' is not an address of this instance: declare it in "
                    "Settings → Users and access → Public addresses"
                ),
                "params": {"host": shown},
            }
        }
    ).encode()


class HostGuardMiddleware:
    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] not in ("http", "websocket") or _exempt(scope):
            await self.app(scope, receive, send)
            return
        await _ensure_loaded()
        raw = _raw_host(scope)
        if host_allowed(raw, _public_urls):
            await self.app(scope, receive, send)
            return
        host = request_host(raw or "")
        if _mode != "enforce":
            if host and _recordable(host):
                record_rejected(host)
            await self.app(scope, receive, send)
            return
        if scope["type"] == "websocket":
            message = await receive()
            if message.get("type") == "websocket.connect":
                await send({"type": "websocket.close", "code": WS_REFUSED_CLOSE_CODE})
            return
        body = rejection_body(host)
        await send(
            {
                "type": "http.response.start",
                "status": 421,
                "headers": [
                    (b"content-type", b"application/json"),
                    (b"cache-control", b"no-store"),
                    (b"content-length", str(len(body)).encode("ascii")),
                ],
            }
        )
        await send({"type": "http.response.body", "body": body})
