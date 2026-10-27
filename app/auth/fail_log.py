"""The ``AUTH_FAIL`` log line: one per authentication failure, for fail2ban and the built-in ban.

The format is a contract (``docs/dev/auth-fail-log.md``): change it only in a
``CONTRACTS:`` commit, together with that document and
``tests/test_auth_fail_log.py``. One ``WARNING`` record on the logger
``meshloom.auth`` whose message is exactly::

    AUTH_FAIL ip=<ip> route=<route> reason=<reason> via=<via> proxied=<0|1> user=<user>

- ``ip``: ``client_ip()`` (the TCP peer, or the client a trusted proxy
  forwarded, ``app/middleware/proxy_headers.py``) in canonical form, ``-`` when
  there is none, it is not an IP address, or it carries an IPv6 zone id
  (``fe80::1%eth0``: ``ipaddress`` keeps any text after the ``%``, a newline
  included). Never free text: no log injection (MSHL-432).
- ``route`` and ``reason``: the closed vocabulary of ``ROUTE_REASONS``.
- ``via``: the transport, ``lan`` or ``relay`` (``request_via()``), never the
  provider that signed the request in (a trusted-network rule is still
  ``lan``). Ingress requests never emit: their peer is the Home Assistant
  Supervisor, which must never be banned.
- ``proxied``: ``1`` when a peer in ``LOCAL_NETWORKS`` that is not a
  declared trusted proxy sends forwarding headers (``X-Forwarded-For``,
  ``X-Real-IP``, ``Forwarded``): most likely a local reverse proxy nobody
  declared, so ``ip`` is shared by every client behind it and consumers must
  ignore the line. A public peer sending those headers is the client itself:
  ``proxied=0``, ``ip`` is that peer (adding a header must not escape a ban).
- ``user``: ``h:`` and 6 hex characters of an HMAC-SHA256 of the typed name
  (``username_key()``) under a random per-process key, ``-`` when the route
  takes no name. Never the name itself: a password typed into the username
  field must not reach the logs. The hash is stable for the life of the process
  only, and cannot be checked against a guess offline.

Every field is checked again when the line is built (``format_line()``):
``route``, ``reason`` and ``via`` must be ``[a-z0-9_]+``, the others come out
of the functions above, and a value outside its alphabet raises instead of
being written (MSHL-432). The logging layer escapes control characters in every
record anyway (``app/log_redaction.py``).

Only a credential that was presented and refused emits: an attempt refused
before any check by a login delay (429 ``auth_rate_limited``, ``app/auth/ratelimit.py``)
does not, nor does a precondition (409, 403 under ingress, a full token table).
"""

from __future__ import annotations

import hashlib
import hmac
import ipaddress
import logging
import re
import secrets
from collections.abc import Mapping
from typing import Any, Literal

from app.auth import ip_ban
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

# What route, reason and via may be made of (the contract's closed vocabulary).
_WORD_RE = re.compile(r"[a-z0-9_]+")
# What any whole line may contain: no space, no control character, nothing but this.
_LINE_RE = re.compile(r"AUTH_FAIL(?: [a-z_]+=[A-Za-z0-9_.:\-]+)+")

# Where an undeclared local reverse proxy can sit (RFC 1918, CGNAT, loopback,
# link-local, ULA). Spelled out: ``is_private`` also covers documentation and
# other reserved ranges.
LOCAL_NETWORKS = tuple(
    ipaddress.ip_network(cidr)
    for cidr in (
        "10.0.0.0/8",
        "172.16.0.0/12",
        "192.168.0.0/16",
        "100.64.0.0/10",
        "127.0.0.0/8",
        "169.254.0.0/16",
        "::1/128",
        "fc00::/7",
        "fe80::/10",
    )
)

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
    if not raw or "%" in raw:
        # No zone id: ``str(ip_address("fe80::1%x\ny"))`` would give it back verbatim.
        return "-"
    try:
        address = ipaddress.ip_address(raw)
    except ValueError:
        return "-"
    if isinstance(address, ipaddress.IPv6Address) and address.ipv4_mapped is not None:
        address = address.ipv4_mapped
    return str(address)


def _undeclared_local_proxy(scope: Mapping[str, Any]) -> bool:
    """Forwarding headers from a local peer that is not a declared trusted proxy."""
    from app.auth.ratelimit import forwarded_by_untrusted_peer
    from app.middleware import proxy_headers

    if not forwarded_by_untrusted_peer(scope):
        return False
    peer = proxy_headers.peer_address(scope)
    try:
        address = ipaddress.ip_address(peer or "")
    except ValueError:
        return False
    if isinstance(address, ipaddress.IPv6Address) and address.ipv4_mapped is not None:
        address = address.ipv4_mapped
    return any(address in network for network in LOCAL_NETWORKS)


def format_line(
    *, ip: str | None, route: str, reason: str, via: str, proxied: bool, username: str | None
) -> str:
    """The message of one ``AUTH_FAIL`` record (the contract).

    Raises ``ValueError`` rather than write a field outside its alphabet.
    """
    for name, value in (("route", route), ("reason", reason), ("via", via)):
        if not _WORD_RE.fullmatch(value):
            raise ValueError(f"{name} is not a plain word")
    line = (
        f"{MARKER} ip={ip_field(ip)} route={route} reason={reason} via={via} "
        f"proxied={1 if proxied else 0} user={user_field(username)}"
    )
    if not _LINE_RE.fullmatch(line):
        raise ValueError("the AUTH_FAIL line holds a character outside its alphabet")
    return line


def log_auth_failure(
    scope: Mapping[str, Any], route: Route, reason: str, *, username: str | None = None
) -> None:
    """Emit the ``AUTH_FAIL`` line for this request. Never raises."""
    # Imported here: the middleware imports the providers and the routers import it.
    from app.auth.middleware import client_ip, request_via

    try:
        if reason not in ROUTE_REASONS[route]:
            raise ValueError(f"reason {reason!r} is not in the contract for route {route!r}")
        via = request_via(scope)  # type: ignore[arg-type]
        if via == "ingress":
            return
        ip = client_ip(scope)  # type: ignore[arg-type]
        proxied = _undeclared_local_proxy(scope)
    except Exception:
        logger.error("Could not read an AUTH_FAIL line's fields for route %s", route, exc_info=True)
        return
    try:
        line = format_line(
            ip=ip, route=route, reason=reason, via=via, proxied=proxied, username=username
        )
    except Exception:
        # No line is better than a doubtful one, but the failure still counts.
        logger.error("Could not format an AUTH_FAIL line for route %s", route, exc_info=True)
    else:
        logger.warning(line)
    # The built-in ban counts the same failure, with the same fields (MSHL-422).
    ip_ban.note_failure(ip, via=via, proxied=proxied, route=route)
