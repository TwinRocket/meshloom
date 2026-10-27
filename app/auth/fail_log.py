"""The ``AUTH_FAIL`` log line: one per authentication failure, for fail2ban and the built-in ban.

The format is a contract (``docs/dev/auth-fail-log.md``): change it only in a
``CONTRACTS:`` commit, together with that document and
``tests/test_auth_fail_log.py``. One ``WARNING`` record on the logger
``meshloom.auth`` whose message is exactly::

    AUTH_FAIL ip=<ip> route=<route> reason=<reason> via=<via> proxied=<0|1> user=<user>

- ``ip``: ``client_ip()`` (the TCP peer, or the client a trusted proxy
  forwarded, ``app/middleware/proxy_headers.py``) in canonical form, ``-`` when
  there is none or it is not an IP address (never free text: no log injection).
- ``route`` and ``reason``: the closed vocabulary of ``ROUTE_REASONS``.
- ``via``: ``lan`` or ``relay`` (``request_via()``). Ingress requests never
  emit: their peer is the Home Assistant Supervisor, which must never be banned.
- ``proxied``: ``1`` when the request carries forwarding headers
  (``X-Forwarded-For``, ``X-Real-IP``, ``Forwarded``) from a peer that is not a
  trusted proxy. Its ``ip`` is then that proxy, shared by every client behind
  it: consumers must ignore the line.
- ``user``: ``h:`` and 6 hex characters of an HMAC-SHA256 of the typed name
  (``username_key()``) under a random per-process key, ``-`` when the route
  takes no name. Never the name itself: a password typed into the username
  field must not reach the logs. The hash is stable for the life of the process
  only, and cannot be checked against a guess offline.

Only a credential that was presented and refused emits: an attempt refused
before any check by a login delay (429 ``auth_rate_limited``, ``app/auth/ratelimit.py``)
does not, nor does a precondition (409, 403 under ingress, a full token table).
"""

from __future__ import annotations

import hashlib
import hmac
import ipaddress
import logging
import secrets
from collections.abc import Mapping
from typing import Any, Literal

from app.repository.auth import InvalidUsernameError, username_key

logger = logging.getLogger("meshloom.auth")

MARKER = "AUTH_FAIL"

Route = Literal["login", "login_2fa", "passkey", "basic", "bearer", "pair", "setup"]

# The reasons each route may give. Adding one is a contract change.
ROUTE_REASONS: Mapping[str, frozenset[str]] = {
    "login": frozenset({"invalid_credentials"}),
    "login_2fa": frozenset({"bad_code", "bad_passkey", "pending_invalid"}),
    "passkey": frozenset({"bad_passkey"}),
    "basic": frozenset({"invalid_credentials"}),
    "bearer": frozenset({"invalid_token"}),
    "pair": frozenset({"invalid_code"}),
    "setup": frozenset({"invalid_setup_code"}),
}

USER_HASH_HEX = 6

# Per process: the hash correlates attempts on one name in one run and nothing more.
_USER_KEY = secrets.token_bytes(32)


def user_field(username: str | None) -> str:
    """``h:<6 hex>`` for a typed name, ``-`` for none."""
    if not username:
        return "-"
    try:
        key = username_key(username)
    except InvalidUsernameError:
        key = username.casefold()
    digest = hmac.new(_USER_KEY, key.encode("utf-8", "surrogatepass"), hashlib.sha256)
    return f"h:{digest.hexdigest()[:USER_HASH_HEX]}"


def ip_field(raw: str | None) -> str:
    """The canonical address, or ``-``. IPv4-mapped IPv6 is shown as IPv4."""
    if not raw:
        return "-"
    try:
        address = ipaddress.ip_address(raw)
    except ValueError:
        return "-"
    if isinstance(address, ipaddress.IPv6Address) and address.ipv4_mapped is not None:
        address = address.ipv4_mapped
    return str(address)


def format_line(
    *, ip: str | None, route: str, reason: str, via: str, proxied: bool, username: str | None
) -> str:
    """The message of one ``AUTH_FAIL`` record (the contract)."""
    return (
        f"{MARKER} ip={ip_field(ip)} route={route} reason={reason} via={via} "
        f"proxied={1 if proxied else 0} user={user_field(username)}"
    )


def log_auth_failure(
    scope: Mapping[str, Any], route: Route, reason: str, *, username: str | None = None
) -> None:
    """Emit the ``AUTH_FAIL`` line for this request. Never raises."""
    # Imported here: the middleware imports the providers and the routers import it.
    from app.auth.middleware import client_ip, request_via
    from app.auth.ratelimit import forwarded_by_untrusted_peer

    try:
        if reason not in ROUTE_REASONS[route]:
            raise ValueError(f"reason {reason!r} is not in the contract for route {route!r}")
        via = scope.get("auth_via") or request_via(scope)  # type: ignore[arg-type]
        if via == "ingress":
            return
        line = format_line(
            ip=client_ip(scope),  # type: ignore[arg-type]
            route=route,
            reason=reason,
            via=via,
            proxied=forwarded_by_untrusted_peer(scope),
            username=username,
        )
    except Exception:
        logger.error("Could not format an AUTH_FAIL line for route %s", route, exc_info=True)
        return
    logger.warning(line)
