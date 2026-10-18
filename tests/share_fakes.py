"""Fakes for the share service tests: a DNS table and an ``httpx.AsyncClient`` recorder.

The share client sends through the egress guard, so a request reaches the
transport with the pinned IP in the URL and the share host in ``Host``.
"""

from __future__ import annotations

import ipaddress
import json
import socket
from collections.abc import Callable
from typing import Any

import httpx

from app.services import share

PUBLIC_IP = "93.184.215.14"


class Dns:
    """``socket.getaddrinfo`` answering from a table, recording every lookup."""

    def __init__(self, monkeypatch, table: dict[str, list[str]] | None = None) -> None:
        self.table = table if table is not None else {share.SHARE_HOST: [PUBLIC_IP]}
        self.lookups: list[str] = []
        monkeypatch.setattr(socket, "getaddrinfo", self)

    def __call__(self, host, port, family=0, type=0, proto=0, flags=0):
        self.lookups.append(host)
        if host not in self.table:
            raise socket.gaierror(socket.EAI_NONAME, "Name or service not known")
        out = []
        for ip in self.table[host]:
            if ipaddress.ip_address(ip).version == 4:
                out.append((socket.AF_INET, socket.SOCK_STREAM, 6, "", (ip, port)))
            else:
                out.append((socket.AF_INET6, socket.SOCK_STREAM, 6, "", (ip, port, 0, 0)))
        return out


class _Stream(httpx.AsyncByteStream):
    def __init__(self, body: bytes) -> None:
        self.body = body

    async def __aiter__(self):
        yield self.body


def json_response(status: int, body: Any, headers: dict[str, str] | None = None) -> httpx.Response:
    return httpx.Response(
        status,
        content=json.dumps(body).encode(),
        headers={"Content-Type": "application/json", **(headers or {})},
    )


class Recorder:
    """Replaces ``httpx.AsyncClient``: every request is recorded, then answered by ``handler``.

    ``handler`` may return a response or raise (an ``httpx`` transport error).
    """

    def __init__(
        self, monkeypatch, handler: Callable[[httpx.Request], httpx.Response] | None = None
    ) -> None:
        self.requests: list[httpx.Request] = []
        self.client_kwargs: list[dict[str, Any]] = []
        self.handler = handler or (lambda request: httpx.Response(503))
        real = httpx.AsyncClient
        outer = self

        def record(request: httpx.Request) -> httpx.Response:
            outer.requests.append(request)
            response = outer.handler(request)
            # The guard reads the body raw: stream it like a socket would.
            return httpx.Response(
                response.status_code, headers=response.headers, stream=_Stream(response.content)
            )

        class Recording(real):
            def __init__(self, **kwargs):
                outer.client_kwargs.append(dict(kwargs))
                kwargs["transport"] = httpx.MockTransport(record)
                super().__init__(**kwargs)

        monkeypatch.setattr(httpx, "AsyncClient", Recording)

    def hosts(self) -> list[str]:
        return [r.headers["host"] for r in self.requests]

    def dumps(self) -> list[str]:
        """Each request as text (line, headers, body) for secret scans."""
        out = []
        for r in self.requests:
            head = "\n".join(f"{k}: {v}" for k, v in r.headers.items())
            out.append(f"{r.method} {r.url}\n{head}\n\n{r.content.decode('utf-8', 'replace')}")
        return out
