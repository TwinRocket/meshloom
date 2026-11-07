"""First run: who may create the administrator (cadrage P1, P3).

While no account exists, ``POST /api/auth/setup`` creates the administrator for
the first request from the local network, on a new database and on an existing
one (a 4.x upgrade, the kiosk image) alike: whoever installed Meshloom is on
that network. There is no secret to fetch.

**Exposed first run** (MSHL-424). An instance published on the Internet must not
go to its first visitor. A first-run request is *exposed* (``request_exposed()``)
when it did not come straight from the local network: it came through the relay,
its client address (after a trusted proxy's rewrite,
``app/middleware/proxy_headers.py``) is not a local address
(``app/net/local_address.py``: RFC 1918, loopback, link-local, ULA and CGNAT; a
documentation or reserved address counts as public), it carries forwarding
headers (any of ``PROXY_HEADERS``, ``app/auth/providers/trusted_network.py``) from
a peer that is not a trusted proxy (the client is unknown), a public address is
declared (``auth_settings.public_urls``), or its ``Host`` is a name a public DNS
zone could hold (``host_guard.name_cannot_be_rebound`` fails, whatever
``host_guard_mode`` says): a page that re-pointed its own name at this instance
(DNS rebinding) is same-origin and comes from a LAN browser, so only the name
gives it away.

An exposed request is refused (403 ``auth_setup_local_only``) and the page asks
the visitor to come back from the local network, at ``local_setup_url()`` when
that address can be known. Home Assistant ingress is not concerned: setup is
refused there anyway (403 ``auth_ingress_only``).

Residual risk: two exposures cannot be seen from here. Docker's userland proxy
shows every client as the gateway, and a local tunnel that forwards without
headers (``ssh -R``, ``socat``, HAProxy in TCP mode) shows Internet visitors as
127.0.0.1. Either lets the first visitor become administrator, on an upgraded
database too. On such a machine the account is created with
``meshloom-admin create-admin`` before it is published (``docs/dev/auth.md``).

**Fail closed.** Setup is refused (503 ``auth_setup_unavailable``) until
``prepare_first_run`` has succeeded in this process: when it fails at startup (a
locked database, say) the setup route tries it again, and keeps refusing while it
still fails.
"""

from __future__ import annotations

import asyncio
import logging
import socket
import time
from collections.abc import MutableMapping
from typing import Any

from app.net import local_address
from app.repository.auth import AuthSettingsRepository, UserRepository

logger = logging.getLogger(__name__)

# How long the check that this address and port accept connections may take.
LOCAL_URL_PROBE_TIMEOUT_S = 0.5
# Refused setups write one audit row per this many seconds: anyone on the Internet
# can send them, and audit rows are kept for a year.
REFUSAL_AUDIT_INTERVAL_S = 60.0


class SetupUnavailable(RuntimeError):
    """First-run preparation has not succeeded: refuse setup (503)."""


# Whether a prepare_first_run() succeeded in this process, and whether one was tried.
_prepared = False
_attempted = False
# When the last refused setup was written to the audit log (monotonic clock).
_last_refusal_audit: float | None = None


def reset_first_run_state() -> None:
    """Back to "never prepared" (tests, and a fresh process)."""
    global _prepared, _attempted, _last_refusal_audit
    _prepared = False
    _attempted = False
    _last_refusal_audit = None


async def request_exposed(scope: MutableMapping[str, Any]) -> bool:
    """Whether a first-run request may come from outside the local network, or
    from a page that is not this instance's (module docstring, "Exposed first run")."""
    from starlette.datastructures import Headers

    from app.auth.providers import trusted_network
    from app.auth.ratelimit import client_is_public, is_trusted_proxy
    from app.middleware import host_guard, proxy_headers

    if scope.get("auth_via", "lan") != "lan":
        return True
    headers = Headers(scope=scope)
    if not host_guard.name_cannot_be_rebound(headers.get("host")):
        return True
    if trusted_network.carries_proxy_headers(headers) and not is_trusted_proxy(
        proxy_headers.peer_address(scope), proxy_headers.current()
    ):
        return True
    if client_is_public(scope):
        return True
    return bool((await AuthSettingsRepository.get()).public_urls)


def refusal_audit_due(*, now: float | None = None) -> bool:
    """Whether this refused setup gets an audit row (``REFUSAL_AUDIT_INTERVAL_S``)."""
    global _last_refusal_audit
    ts = time.monotonic() if now is None else now
    if _last_refusal_audit is not None and ts - _last_refusal_audit < REFUSAL_AUDIT_INTERVAL_S:
        return False
    _last_refusal_audit = ts
    return True


def _primary_private_ipv4() -> str | None:
    """The address of the interface holding the default route, when it is RFC 1918.

    Connecting a UDP socket only picks a route: no packet leaves. The destination
    is a documentation address, so no network is ever named here."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        sock.connect(("192.0.2.1", 9))
        address = sock.getsockname()[0]
    finally:
        sock.close()
    return address if local_address.is_rfc1918(address) else None


async def _accepts_connections(address: str, port: int) -> bool:
    """Whether Meshloom can be reached there. A server bound to loopback behind a
    proxy on the same machine cannot, and its address would be a dead link."""
    _, writer = await asyncio.wait_for(
        asyncio.open_connection(address, port), LOCAL_URL_PROBE_TIMEOUT_S
    )
    writer.close()
    return True


async def local_setup_url(scope: MutableMapping[str, Any]) -> str | None:
    """Where the refused visitor should go on the local network, or None.

    None when it would be wrong or useless: in a container (its address means
    nothing to a visitor), through the relay (``scope["server"]`` is then the Remote
    hostname), without a private address, when nothing answers there, and on any
    failure. Never raises. The port is the one the connection reached, which a proxy
    on the way does not change. The scheme is only https when this request reached
    Meshloom directly: a trusted proxy's ``X-Forwarded-Proto`` says nothing about
    the LAN address, and plain http is what a local install serves."""
    from app.middleware import proxy_headers
    from app.services.install_kind import in_container

    try:
        if scope.get("auth_via", "lan") != "lan" or in_container():
            return None
        port = scope["server"][1]
        address = _primary_private_ipv4()
        if address is None or not isinstance(port, int):
            return None
        if not await _accepts_connections(address, port):
            return None
        client = scope.get("client")
        direct = bool(client) and proxy_headers.peer_address(scope) == client[0]
        scheme = "https" if direct and scope.get("scheme") == "https" else "http"
        return f"{scheme}://{address}:{port}"
    except Exception as exc:
        logger.debug("No local address to offer for first-run setup: %s", type(exc).__name__)
        return None


async def ensure_first_run_ready() -> None:
    """Raise ``SetupUnavailable`` unless first-run preparation succeeded (retrying it once)."""
    if _prepared:
        return
    if not _attempted:
        raise SetupUnavailable("first run was never prepared")
    try:
        await prepare_first_run()
    except Exception as exc:
        logger.warning("First-run preparation failed again; setup stays refused")
        raise SetupUnavailable("first run preparation failed") from exc


async def prepare_first_run() -> None:
    """Create the kiosk account and its device token on a kiosk host, and check the
    database answers. Setup stays refused until this returns without raising (module
    docstring)."""
    global _prepared, _attempted
    from app.services.kiosk import ensure_kiosk_token, ensure_kiosk_user

    try:
        await ensure_kiosk_user()
        # The launcher's device token (T60): the screen signs in with it.
        await ensure_kiosk_token()
    except Exception:
        # The screen only lacks its account; setup does not depend on it.
        logger.exception("Failed to create the kiosk account or its device token")
    _attempted = True
    _prepared = False
    await _prepare()
    _prepared = True


async def _prepare() -> None:
    if not await UserRepository.any_exist():
        logger.info(
            "No account yet: open Meshloom on the local network to create the administrator"
        )
