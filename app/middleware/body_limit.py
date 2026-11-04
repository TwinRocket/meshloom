"""Request body limit: no route buffers more than it needs (MSHL-461).

FastAPI reads a JSON body whole before validating it, and the public routes (login,
setup) do so for anyone who can reach the port. So every request gets a cap:

- ``Content-Length`` over the cap: 413 ``request_too_large`` before anything is read;
- a body without one (chunked) is counted as the route reads it, and reading stops
  with the same 413 at the chunk that crosses the cap. uvicorn stops reading the socket
  while the application is not asking for data, so an unread body takes no memory.

The answer carries ``Connection: close``: the rest of the body is never read.

A cap per request does not bound the total, so two more rules hold on capped routes:

- **Deadline.** The body must finish arriving within ``deadline_for(cap)``, counted from
  the route's first read, so a busy gate costs the client nothing: 30 s for the default cap,
  which needs 280 kbit/s, and more for a larger one. Past it: 408 ``request_timeout``.
- **Budget.** A request with no principal (the gate has run by the time a route reads)
  needs a share of ``ANONYMOUS_BUDGET_BYTES`` for whatever it holds past
  ``SMALL_ANONYMOUS_BODY_BYTES``, reserved on its declared length before reading, or as a
  chunked body grows, and given back when the request ends. Past it: 503 ``server_busy``
  with ``Retry-After``. A login is far smaller than the threshold, so filling the budget
  blocks no one's sign-in; what small bodies can hold is bounded by the deadline and by the
  number of open connections.

A refusal from the gate or a route that leaves the body unread also closes the connection,
which spares uvicorn reading and discarding the rest.

``DEFAULT_MAX_BODY_BYTES`` covers every JSON route. The exceptions:

- ``SELF_LIMITED_ROUTES`` read their body in chunks and refuse past their own cap, with
  their own error code, which the frontend translates: backup inspect and restore (64 MiB,
  ``backup_too_large``, ``app/services/backup.py``), share uploads (10 MiB,
  ``share_too_large``, ``app/services/share_upload.py``). A new route goes there only if
  it does the same. These have no deadline, budget or forced close: the gate answers an
  anonymous caller before the route reads, so only a signed-in account's upload is ever
  buffered, and a large one over a slow link may legitimately take minutes. uvicorn reads and
  discards what an anonymous caller keeps sending after the 401 (it holds none of it); closing
  instead can reset the socket mid-upload and lose the 401 of an expired session;
- ``LARGER_ROUTES``: JSON routes whose valid body exceeds the default.

The cap is not a setting: a route that needs more says so here. WebSocket messages are
not covered (uvicorn caps them).
"""

from __future__ import annotations

import asyncio
import json
import re

from fastapi import HTTPException
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.auth.principal import principal_from_scope

DEFAULT_MAX_BODY_BYTES = 1024 * 1024

BODY_READ_DEADLINE_S = 30.0
# A larger cap gets the time this rate needs (8 MiB: 128 s).
MIN_BODY_RATE_BYTES_PER_S = 64 * 1024

SMALL_ANONYMOUS_BODY_BYTES = 16 * 1024
ANONYMOUS_BUDGET_BYTES = 8 * 1024 * 1024
BUDGET_RETRY_AFTER_S = 30

SELF_LIMITED_ROUTES = frozenset(
    {
        "/api/settings/backup/inspect",
        "/api/settings/backup/restore",
        "/api/share/files",
    }
)

# The plugin source editor: 1 MiB of source (MAX_SOURCE_BYTES, app/routers/plugins.py),
# and JSON may write one byte as six (\uXXXX).
LARGER_ROUTES = ((re.compile(r"/api/plugins/[^/]+/source"), 8 * 1024 * 1024),)

_DETAIL = {"code": "request_too_large", "message": "This request is too large"}
_TIMEOUT_DETAIL = {"code": "request_timeout", "message": "This request took too long to arrive"}
_BUSY_DETAIL = {"code": "server_busy", "message": "Meshloom is busy: try again shortly"}
_CLOSE = {"Connection": "close"}

# Bytes reserved by anonymous requests in flight. One event loop: no lock needed.
_budget_in_use = 0


def budget_in_use() -> int:
    return _budget_in_use


def reset_budget() -> None:
    """Tests only."""
    global _budget_in_use
    _budget_in_use = 0


def deadline_for(limit: int) -> float:
    """Seconds a body under ``limit`` has to arrive."""
    return max(BODY_READ_DEADLINE_S, limit / MIN_BODY_RATE_BYTES_PER_S)


def limit_for(path: str) -> int | None:
    """The cap for a path, or None when the route enforces its own."""
    if path in SELF_LIMITED_ROUTES:
        return None
    for pattern, limit in LARGER_ROUTES:
        if pattern.fullmatch(path):
            return limit
    return DEFAULT_MAX_BODY_BYTES


def _declared_length(scope: Scope) -> int | None:
    for name, value in scope.get("headers") or ():
        if name == b"content-length":
            # The server already refused a malformed value; the count below still applies.
            return int(value) if value.isdigit() else None
    return None


class BodyLimitMiddleware:
    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        limit = limit_for(scope.get("path", "")) if scope["type"] == "http" else None
        if limit is None:
            await self.app(scope, receive, send)
            return
        declared = _declared_length(scope)
        if declared is not None and declared > limit:
            await _refuse(send)
            return
        request = _BodyRead(scope, receive, send, limit, declared)
        try:
            await self.app(scope, request.receive, request.send)
        finally:
            request.release()


class _BodyRead:
    """One capped request: counts its body, holds its budget share, watches its deadline."""

    def __init__(
        self, scope: Scope, receive: Receive, send: Send, limit: int, declared: int | None
    ) -> None:
        self._scope = scope
        self._receive = receive
        self._send = send
        self._limit = limit
        self._declared = declared
        self._received = 0
        self._reserved = 0
        self._deadline: float | None = None
        self._anonymous = False
        self._finished = False
        self._has_body = bool(declared) or any(
            name == b"transfer-encoding" for name, _ in scope.get("headers") or ()
        )

    def _reserve(self, total: int) -> None:
        global _budget_in_use
        wanted = max(0, total - SMALL_ANONYMOUS_BODY_BYTES)
        extra = wanted - self._reserved
        if extra <= 0:
            return
        if _budget_in_use + extra > ANONYMOUS_BUDGET_BYTES:
            raise HTTPException(
                503,
                detail=_BUSY_DETAIL,
                headers={**_CLOSE, "Retry-After": str(BUDGET_RETRY_AFTER_S)},
            )
        _budget_in_use += extra
        self._reserved = wanted

    def release(self) -> None:
        global _budget_in_use
        _budget_in_use = max(0, _budget_in_use - self._reserved)
        self._reserved = 0

    async def receive(self) -> Message:
        if self._deadline is None:
            self._deadline = asyncio.get_running_loop().time() + deadline_for(self._limit)
            self._anonymous = principal_from_scope(self._scope) is None
            if self._anonymous and self._declared:
                self._reserve(self._declared)
        try:
            async with asyncio.timeout_at(self._deadline):
                message = await self._receive()
        except TimeoutError:
            raise HTTPException(408, detail=_TIMEOUT_DETAIL, headers=_CLOSE) from None
        if message["type"] == "http.request":
            self._received += len(message.get("body", b""))
            if self._received > self._limit:
                # FastAPI's body reading lets an HTTPException through, and the
                # application's handler answers it with the usual headers.
                raise HTTPException(413, detail=_DETAIL, headers=_CLOSE)
            if self._anonymous:
                self._reserve(self._received)
            if not message.get("more_body", False):
                self._finished = True
        return message

    async def send(self, message: Message) -> None:
        if message["type"] == "http.response.start" and self._has_body and not self._finished:
            headers: list[tuple[bytes, bytes]] = list(message.get("headers") or ())
            if not any(name.lower() == b"connection" for name, _ in headers):
                headers.append((b"connection", b"close"))
            message = {**message, "headers": headers}
        await self._send(message)


async def _refuse(send: Send) -> None:
    body = json.dumps({"detail": _DETAIL}).encode()
    await send(
        {
            "type": "http.response.start",
            "status": 413,
            "headers": [
                (b"content-type", b"application/json"),
                (b"content-length", str(len(body)).encode("ascii")),
                (b"connection", b"close"),
            ],
        }
    )
    await send({"type": "http.response.body", "body": body})
