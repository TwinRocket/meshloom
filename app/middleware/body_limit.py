"""Request body limit: no route buffers more than it needs (MSHL-461).

FastAPI reads a JSON body whole before validating it, and the public routes (login,
setup) do so for anyone who can reach the port. So every request gets a cap:

- ``Content-Length`` over the cap: 413 ``request_too_large`` before anything is read;
- a body without one (chunked) is counted as the route reads it, and reading stops
  with the same 413 at the chunk that crosses the cap. uvicorn stops reading the socket
  while the application is not asking for data, so an unread body takes no memory.

The answer carries ``Connection: close``: the rest of the body is never read.

``DEFAULT_MAX_BODY_BYTES`` covers every JSON route. The exceptions:

- ``SELF_LIMITED_ROUTES`` read their body in chunks and refuse past their own cap, with
  their own error code, which the frontend translates: backup inspect and restore (64 MiB,
  ``backup_too_large``, ``app/services/backup.py``), share uploads (10 MiB,
  ``share_too_large``, ``app/services/share_upload.py``). A new route goes there only if
  it does the same. The auth gate still answers first: an anonymous caller is read nothing;
- ``LARGER_ROUTES``: JSON routes whose valid body exceeds the default.

The cap is not a setting: a route that needs more says so here. WebSocket messages are
not covered (uvicorn caps them).
"""

from __future__ import annotations

import json
import re

from fastapi import HTTPException
from starlette.types import ASGIApp, Message, Receive, Scope, Send

DEFAULT_MAX_BODY_BYTES = 1024 * 1024

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
_CLOSE = {"Connection": "close"}


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

        received = 0

        async def counting_receive() -> Message:
            nonlocal received
            message = await receive()
            if message["type"] == "http.request":
                received += len(message.get("body", b""))
                if received > limit:
                    # FastAPI's body reading lets an HTTPException through, and the
                    # application's handler answers it with the usual headers.
                    raise HTTPException(413, detail=_DETAIL, headers=_CLOSE)
            return message

        await self.app(scope, counting_receive, send)


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
