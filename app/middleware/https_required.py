"""HTTPS from the internet once a public address is declared (MSHL-437, docs/dev/auth.md).

A Meshloom reachable from the internet must be reached over https: over plain
http the session cookie, the password and every message cross the network in
clear. Once an administrator declared a public address (``auth_settings.public_urls``,
Settings → Users and access → Public addresses) the instance is meant to be reached
from outside, so a plain-http request from a **public** client is refused:

- HTTP: 403 ``auth_https_required`` (JSON under ``/api``, a short English and
  French text elsewhere, so a browser shows something readable), with the static
  security headers and ``no-store``;
- WebSocket: closed with 1008 before the handshake completes.

Everything else goes through unchanged:

- no public address declared: nothing is checked (an install that never set one
  behaves exactly as before, whatever the network looks like);
- ``https``/``wss`` (``scope["scheme"]``, after ``TrustedProxyHeadersMiddleware``:
  a listed proxy's ``X-Forwarded-Proto`` counts, any other peer's never does);
- a local client (``ip_ban.is_local`` on ``scope["client"]``, the forwarded address
  for a listed proxy: loopback, RFC 1918, ULA, link-local, CGNAT/Tailscale, an
  on-link IPv6 prefix of the host). The local network can always reach the
  settings to fix a misconfigured proxy;
- the relay (``state["via"] == "relay"``, always ``https`` anyway) and Home
  Assistant ingress: the same exemption as the Host guard (``host_guard.exempt``).

A client address that does not parse is public (fail closed). Each refusal logs
one warning on ``meshloom.auth`` with the client address, path and scheme, rate
limited (``WARNING_BURST`` a window, then one summary line) so a scanner cannot
flood the log. No audit row: refusals come from anonymous internet clients and
would only fill the audit table.

Saving the first public address cannot lock out the administrator doing it:
``PATCH /api/auth/settings`` refuses (400 ``auth_https_would_lock_out``) a list that
``would_refuse`` the request being answered, so an administrator on plain http from
a public address (direct exposure, or a trusted proxy that does not send
``X-Forwarded-Proto``) must switch to https or come back on the local network first.
An administrator who is already remote over https keeps access; one who later loses
https (a proxy reconfigured) is refused until they reach Meshloom from the local
network or over https.

It runs inside ``TrustedProxyHeadersMiddleware`` (it reads the rewritten client
and scheme) and outside the Host guard. The declared addresses are the Host
guard's in-memory copy (``host_guard.public_urls()``), so a saved change applies
to the next request.
"""

from __future__ import annotations

import json
import logging
import time
from collections.abc import Sequence

from starlette.types import ASGIApp, Receive, Scope, Send

from app.middleware import host_guard

logger = logging.getLogger("meshloom.auth")

REFUSED_STATUS = 403
WS_REFUSED_CLOSE_CODE = 1008
ERROR_CODE = "auth_https_required"
WARNING_BURST = 5
WARNING_WINDOW_S = 60

_INSECURE_SCHEMES = ("http", "ws")

_warn_window_start = 0.0
_warn_count = 0
_warn_suppressed = 0

TEXT_BODY = (
    "HTTPS is required: this Meshloom instance is reached from the internet. "
    "Open it with an https:// address (your reverse proxy, Tailscale or Meshloom Remote).\n"
    "HTTPS est obligatoire : cette instance Meshloom est atteinte depuis Internet. "
    "Ouvrez-la avec une adresse https:// (votre reverse-proxy, Tailscale ou Meshloom Remote).\n"
)


def reset() -> None:
    """Forget the warning budget (tests)."""
    global _warn_window_start, _warn_count, _warn_suppressed
    _warn_window_start, _warn_count, _warn_suppressed = 0.0, 0, 0


def _client_is_local(scope: Scope) -> bool:
    from app.auth.ip_ban import is_local
    from app.net import local_address

    client = scope.get("client")
    address = local_address.parse_address(client[0] if client else None)
    return address is not None and is_local(address)


def would_refuse(scope: Scope, public_urls: Sequence[str]) -> bool:
    """Whether ``scope`` would be refused once ``public_urls`` are the declared addresses.

    ``PATCH /api/auth/settings`` calls it with the list about to be saved, so an
    administrator reaching Meshloom over plain http from a public address cannot
    declare a public address and lock themselves out (``auth_https_would_lock_out``).
    """
    if not public_urls or host_guard.exempt(scope):
        return False
    if scope.get("scheme", "http") not in _INSECURE_SCHEMES:
        return False
    return not _client_is_local(scope)


def must_refuse(scope: Scope) -> bool:
    """Whether ``scope`` is a plain-http request from a public client to an exposed instance."""
    return would_refuse(scope, host_guard.public_urls())


def _log_refusal(scope: Scope) -> None:
    global _warn_window_start, _warn_count, _warn_suppressed
    now = time.monotonic()
    if now - _warn_window_start >= WARNING_WINDOW_S:
        if _warn_suppressed:
            logger.warning(
                "%d more plain-http requests from public addresses were refused", _warn_suppressed
            )
        _warn_window_start, _warn_count, _warn_suppressed = now, 0, 0
    if _warn_count >= WARNING_BURST:
        _warn_suppressed += 1
        return
    _warn_count += 1
    client = scope.get("client")
    path = str(scope.get("path") or "/")[:200]
    logger.warning(
        "Refused plain-%s request from public address %s to %r: https is required "
        "once a public address is declared",
        scope.get("scheme", "http"),
        client[0] if client else "-",
        path,
    )


def _body(path: str) -> tuple[bytes, bytes]:
    if path.startswith("/api"):
        body = json.dumps(
            {
                "detail": {
                    "code": ERROR_CODE,
                    "message": (
                        "HTTPS is required from the internet: open Meshloom with its "
                        "https:// address"
                    ),
                    "params": {},
                }
            }
        ).encode()
        return body, b"application/json"
    return TEXT_BODY.encode(), b"text/plain; charset=utf-8"


class HttpsRequiredMiddleware:
    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] not in ("http", "websocket"):
            await self.app(scope, receive, send)
            return
        await host_guard.ensure_loaded()
        if not must_refuse(scope):
            await self.app(scope, receive, send)
            return
        _log_refusal(scope)
        if scope["type"] == "websocket":
            message = await receive()
            if message.get("type") == "websocket.connect":
                await send({"type": "websocket.close", "code": WS_REFUSED_CLOSE_CODE})
            return
        body, content_type = _body(str(scope.get("path") or "/"))
        await send(
            {
                "type": "http.response.start",
                "status": REFUSED_STATUS,
                "headers": [
                    (b"content-type", content_type),
                    (b"cache-control", b"no-store"),
                    *host_guard.refusal_headers(),
                    (b"content-length", str(len(body)).encode("ascii")),
                ],
            }
        )
        await send({"type": "http.response.body", "body": body})
