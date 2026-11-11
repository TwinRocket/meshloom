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
declared (``auth_settings.public_urls``), its ``Host`` is a name a public DNS
zone could hold (``host_guard.name_cannot_be_rebound`` fails, whatever
``host_guard_mode`` says), or its ``Host`` is an IP literal that is not a local
address (``Host: 203.0.113.7:8000``: the visitor typed a public address, which a
LAN visitor never needs). A page that re-pointed its own name at this instance
(DNS rebinding) is same-origin and comes from a LAN browser, so only the name
gives it away.

An exposed request is refused (403 ``auth_setup_local_only``) and the page asks
the visitor to come back from the local network, at ``local_setup_url()`` when
that address can be known and the visitor is itself seen on the local network.
Home Assistant ingress is not concerned: setup is refused there anyway (403
``auth_ingress_only``).

**The 4.x Basic credentials** (``legacy_basic_gate_active()``). A 4.x install that
still sets ``MESHCORE_BASIC_AUTH_USERNAME`` or ``_PASSWORD`` was protected by them,
and may be published in a way this module cannot see (below). While no account
exists and either variable is set, setup also needs those credentials, in the
request body or as an ``Authorization: Basic`` header (what a browser that still
remembers the 4.x login sends), compared in constant time
(``legacy_basic_credentials_match()``); a wrong pair is 401
``auth_setup_legacy_basic_invalid`` and counts in the login delays. With only one
of the two set nothing matches: the account is then created with
``meshloom-admin create-admin``. The variables are never imported (MSHL-100).

Residual risk: some exposures cannot be seen from here, for example Docker's
userland proxy (every client shown as the gateway, also for a port published over
IPv6), a local tunnel that forwards without headers (``ssh -R``, ``socat``, HAProxy
in TCP mode: Internet visitors shown as 127.0.0.1), a bare nginx ``proxy_pass`` (no
forwarding header, no public ``Host``) and a router port forwarded with source NAT.
Without the Basic variables, each lets the first visitor become administrator, on an
upgraded database too: the Docker gateway still counts as a local client. On such a
machine the account is created with ``meshloom-admin create-admin`` before it is
published (``docs/dev/auth.md``).

**Fail closed.** Setup is refused (503 ``auth_setup_unavailable``) until
``prepare_first_run`` has succeeded in this process: when it fails at startup (a
locked database, say) the setup route tries it again, and keeps refusing while it
still fails.
"""

from __future__ import annotations

import asyncio
import base64
import binascii
import hmac
import logging
import os
import socket
import time
from collections.abc import Mapping, MutableMapping
from typing import Any

from app.net import local_address
from app.repository.auth import AuthSettingsRepository, UserRepository
from app.services.legacy_env_settings import ENV_BASIC_PASSWORD, ENV_BASIC_USERNAME

logger = logging.getLogger(__name__)

# How long the check that this address and port accept connections may take.
LOCAL_URL_PROBE_TIMEOUT_S = 0.5
# How long one answer of that check is reused: anyone can read the session.
LOCAL_URL_PROBE_CACHE_S = 60.0
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
# (address, port) -> (when it was checked, whether it accepted a connection).
_probe_cache: dict[tuple[str, int], tuple[float, bool]] = {}


def reset_first_run_state() -> None:
    """Back to "never prepared" (tests, and a fresh process)."""
    global _prepared, _attempted, _last_refusal_audit
    _prepared = False
    _attempted = False
    _last_refusal_audit = None
    _probe_cache.clear()


def _public_ip_literal(raw_host: str | None) -> bool:
    """Whether ``Host`` is an IP address that is not a local one (``Host: 8.8.8.8``)."""
    from app.middleware import host_guard

    host = host_guard.request_host(raw_host or "")
    return local_address.parse_address(host) is not None and local_address.is_public(host)


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
    host = headers.get("host")
    if not host_guard.name_cannot_be_rebound(host) or _public_ip_literal(host):
        return True
    if trusted_network.carries_proxy_headers(headers) and not is_trusted_proxy(
        proxy_headers.peer_address(scope), proxy_headers.current()
    ):
        return True
    if client_is_public(scope):
        return True
    return bool((await AuthSettingsRepository.get()).public_urls)


def legacy_basic_gate_active() -> bool:
    """Whether a retired 4.x Basic variable is still set (not blank): setup then
    needs those credentials (module docstring, "The 4.x Basic credentials")."""
    return any(
        (os.environ.get(name) or "").strip() for name in (ENV_BASIC_USERNAME, ENV_BASIC_PASSWORD)
    )


def legacy_basic_credentials_match(username: str, password: str) -> bool:
    """Whether the pair is the 4.x Basic login, compared as 4.x did (exact values, in
    constant time). False when either variable is unset or blank: nothing matches."""
    expected_user = os.environ.get(ENV_BASIC_USERNAME) or ""
    expected_password = os.environ.get(ENV_BASIC_PASSWORD) or ""
    if not expected_user.strip() or not expected_password.strip():
        return False
    user_ok = hmac.compare_digest(username.encode(), expected_user.encode())
    password_ok = hmac.compare_digest(password.encode(), expected_password.encode())
    return user_ok & password_ok


def basic_credentials_from_header(headers: Mapping[str, str]) -> tuple[str, str] | None:
    """The ``(username, password)`` of an ``Authorization: Basic`` header, or None.

    Read only by ``POST /api/auth/setup`` for the 4.x Basic gate: the auth gate
    ignores the scheme everywhere (``app/auth/middleware.py``)."""
    authorization = headers.get("authorization") or ""
    scheme, _, token = authorization.partition(" ")
    if scheme.lower() != "basic" or not token.strip():
        return None
    try:
        decoded = base64.b64decode(token.strip(), validate=True).decode("utf-8")
    except (binascii.Error, UnicodeDecodeError):
        return None
    username, sep, password = decoded.partition(":")
    return (username, password) if sep else None


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


async def _listening(address: str, port: int, *, now: float | None = None) -> bool:
    """``_accepts_connections``, its answer (a failure included) reused for
    ``LOCAL_URL_PROBE_CACHE_S``: an anonymous session read must not open a socket each time."""
    ts = time.monotonic() if now is None else now
    cached = _probe_cache.get((address, port))
    if cached is not None and ts - cached[0] < LOCAL_URL_PROBE_CACHE_S:
        return cached[1]
    try:
        accepted = await _accepts_connections(address, port)
    except (OSError, TimeoutError):
        accepted = False
    _probe_cache[(address, port)] = (ts, accepted)
    return accepted


def _client_seen_on_the_lan(scope: MutableMapping[str, Any]) -> bool:
    """Whether the visitor itself is on the local network: no forwarding header from
    a peer that is not a trusted proxy, and a local client address that is not
    loopback (a proxy or tunnel on this machine shows every Internet visitor as
    127.0.0.1). Only such a visitor is told the LAN address."""
    from starlette.datastructures import Headers

    from app.auth.providers import trusted_network
    from app.auth.ratelimit import is_trusted_proxy
    from app.middleware import proxy_headers

    if trusted_network.carries_proxy_headers(Headers(scope=scope)) and not is_trusted_proxy(
        proxy_headers.peer_address(scope), proxy_headers.current()
    ):
        return False
    client = scope.get("client")
    address = local_address.parse_address(client[0] if client else None)
    return address is not None and local_address.is_local(address) and not address.is_loopback


async def local_setup_url(scope: MutableMapping[str, Any]) -> str | None:
    """Where the refused visitor should go on the local network, or None.

    None when it would be wrong or useless: in a container (its address means
    nothing to a visitor), through the relay (``scope["server"]`` is then the Remote
    hostname), for a visitor not seen on the local network (``_client_seen_on_the_lan``:
    the address is not given to the Internet), once a public address is declared (the
    local network is refused too), without a private address, when nothing answers
    there (checked at most once a minute, ``_listening``), and on any failure. Never
    raises. The port is the one the connection reached, which a proxy
    on the way does not change. The scheme is only https when no trusted proxy
    sent this request: its ``X-Forwarded-Proto`` says nothing about
    the LAN address, and plain http is what a local install serves."""
    from app.auth.ratelimit import is_trusted_proxy
    from app.middleware import proxy_headers
    from app.services.install_kind import in_container

    try:
        if scope.get("auth_via", "lan") != "lan" or in_container():
            return None
        if not _client_seen_on_the_lan(scope):
            return None
        if (await AuthSettingsRepository.get()).public_urls:
            return None
        port = scope["server"][1]
        address = _primary_private_ipv4()
        if address is None or not isinstance(port, int):
            return None
        if not await _listening(address, port):
            return None
        forwarded = is_trusted_proxy(proxy_headers.peer_address(scope), proxy_headers.current())
        scheme = "https" if not forwarded and scope.get("scheme") == "https" else "http"
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
    if await UserRepository.any_exist():
        return
    if legacy_basic_gate_active():
        logger.warning(
            "No account yet and %s or %s is still set: creating the administrator from the "
            "local network also asks for those 4.x Basic credentials. Or create it on this "
            "machine with: meshloom-admin create-admin <user>",
            ENV_BASIC_USERNAME,
            ENV_BASIC_PASSWORD,
        )
        return
    logger.info("No account yet: open Meshloom on the local network to create the administrator")
