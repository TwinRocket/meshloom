"""An in-process fake Meshloom Remote relay (a ``websockets`` server on 127.0.0.1) that
speaks the relay side of ``remote-relay.md`` v1, for ``tests/test_remote_relay*.py``.

``FakeRelay.close_plan`` lists close codes to answer right after the upgrade (the
relay refuses that way, before any HELLO); once it is empty, the relay checks the
instance HELLO, answers its own and hands the connection to the test as a
``FakeConnection``: ``request()`` and ``websocket()`` drive streams, ``frames`` and
``messages`` record what the instance sent.
"""

from __future__ import annotations

import asyncio
import json
from collections import defaultdict
from typing import Any

from websockets.asyncio.server import ServerConnection, serve

from app.services import remote_frames as rf

SLUG = "k3xwq2m7ta"
HOST = f"{SLUG}.meshloom.link"
INSTALL_KINDS = {"package", "compose", "addon", "container", "source"}


def relay_hello(limits: dict[str, int] | None = None) -> dict[str, Any]:
    return {
        "protocol": 1,
        "role": "relay",
        "limits": limits or {"max_streams": 64, "max_chunk": 262144, "initial_credit": 1048576},
        "features": [],
        "instance": {"slug": SLUG, "hostname": HOST, "status": "active"},
        "server_time": 1760100000,
    }


class FakeConnection:
    def __init__(self, ws: ServerConnection, hello: dict[str, Any], limits: dict[str, int]):
        self.ws = ws
        self.hello = hello
        self.limits = rf.effective_limits(limits)
        self.messages: list[bytes] = []
        self.frames: list[rf.Frame] = []
        self.by_stream: dict[int, asyncio.Queue[rf.Frame]] = defaultdict(asyncio.Queue)
        self.control: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
        self.next_id = 1
        self.credit: dict[int, int] = defaultdict(lambda: self.limits["initial_credit"])
        self.auto_credit = True
        self.reader: asyncio.Task[None] | None = None

    def start(self) -> None:
        self.reader = asyncio.get_running_loop().create_task(self._read())

    async def _read(self) -> None:
        try:
            async for message in self.ws:
                assert isinstance(message, bytes), "the instance sent a text message"
                self.messages.append(message)
                for frame in rf.decode_message(message, max_chunk=self.limits["max_chunk"]).frames:
                    self.frames.append(frame)
                    if frame.stream_id == 0:
                        self.control.put_nowait(json.loads(frame.payload))
                    else:
                        self.by_stream[frame.stream_id].put_nowait(frame)
                        if frame.type == rf.CREDIT:
                            self.credit[frame.stream_id] += rf.parse_credit(frame.payload) or 0
                        if (
                            self.auto_credit
                            and frame.type in (rf.RESP_BODY, rf.WS_MSG)
                            and frame.payload
                        ):
                            await self.send(
                                rf.encode_frame(
                                    rf.CREDIT,
                                    frame.stream_id,
                                    0,
                                    rf.credit_payload(len(frame.payload)),
                                )
                            )
        except Exception:
            pass

    async def send(self, *frames: bytes) -> None:
        await self.ws.send(b"".join(frames))

    def new_stream(self) -> int:
        stream_id, self.next_id = self.next_id, self.next_id + 2
        return stream_id

    async def frame(self, stream_id: int, timeout: float = 5.0) -> rf.Frame:
        return await asyncio.wait_for(self.by_stream[stream_id].get(), timeout)

    async def next_control(self, timeout: float = 5.0) -> dict[str, Any]:
        return await asyncio.wait_for(self.control.get(), timeout)

    def head(
        self,
        method: str,
        path: str,
        *,
        query: str = "",
        headers: list[list[str]] | None = None,
        client_ip: str = "203.0.113.7",
        pass_token: str | None = None,
    ) -> dict[str, Any]:
        pairs = [["host", HOST], *(headers or [])]
        pairs += [["x-forwarded-for", client_ip], ["x-forwarded-proto", "https"]]
        pairs += [["x-meshloom-relay", "1"]]
        if pass_token is not None:
            pairs.append(["x-meshloom-pass", pass_token])
        out: dict[str, Any] = {
            "scheme": "https",
            "host": HOST,
            "path": path,
            "query": query,
            "headers": pairs,
            "client_ip": client_ip,
        }
        if method == "WS":
            out["scheme"] = "wss"
            out["subprotocols"] = []
        else:
            out["method"] = method
        return out

    async def request(
        self, method: str, path: str, body: bytes = b"", **kwargs: Any
    ) -> tuple[int, list[list[str]], bytes, int]:
        """One HTTP stream: ``(status, headers, body, stream_id)``."""
        stream_id = self.new_stream()
        head = self.head(method, path, **kwargs)
        if not body:
            await self.send(rf.encode_json(rf.REQ_HEAD, stream_id, head, rf.END))
        else:
            chunk = self.limits["max_chunk"]
            frames = [rf.encode_json(rf.REQ_HEAD, stream_id, head)]
            for offset in range(0, len(body), chunk):
                last = offset + chunk >= len(body)
                frames.append(
                    rf.encode_frame(
                        rf.REQ_BODY, stream_id, rf.END if last else 0, body[offset : offset + chunk]
                    )
                )
            await self.send(frames[0])
            for frame in frames[1:]:
                size = len(frame) - rf.HEADER_BYTES
                while self.credit[stream_id] < size:  # the relay honours the credits
                    await asyncio.sleep(0.005)
                self.credit[stream_id] -= size
                await self.send(frame)
        return (*await self.response(stream_id), stream_id)

    async def response(self, stream_id: int) -> tuple[int, list[list[str]], bytes]:
        head = await self.frame(stream_id)
        while head.type == rf.CREDIT:
            head = await self.frame(stream_id)
        assert head.type == rf.RESP_HEAD, head
        value = json.loads(head.payload)
        body = b""
        done = head.has(rf.END)
        while not done:
            frame = await self.frame(stream_id)
            if frame.type == rf.CREDIT:
                continue
            assert frame.type == rf.RESP_BODY, frame
            body += frame.payload
            done = frame.has(rf.END)
        return value["status"], value["headers"], body


class FakeRelay:
    def __init__(self, limits: dict[str, int] | None = None) -> None:
        self.limits = limits
        self.close_plan: list[tuple[int, str]] = []
        self.attempts: list[dict[str, str]] = []
        self.connections: asyncio.Queue[FakeConnection] = asyncio.Queue()
        self.server: Any = None
        self.url = ""

    async def __aenter__(self) -> FakeRelay:
        self.server = await serve(self._handler, "127.0.0.1", 0, compression=None, max_size=None)
        port = self.server.sockets[0].getsockname()[1]
        self.url = f"ws://127.0.0.1:{port}/v1/connect"
        return self

    async def __aexit__(self, *_exc: object) -> None:
        self.server.close()
        await self.server.wait_closed()

    async def _handler(self, ws: ServerConnection) -> None:
        assert ws.request is not None
        self.attempts.append(dict(ws.request.headers.raw_items()))
        if self.close_plan:
            code, reason = self.close_plan.pop(0)
            await ws.close(code, reason)
            return
        message = await ws.recv()
        assert isinstance(message, bytes)
        decoded = rf.decode_message(message)
        first = decoded.frames[0]
        assert (first.type, first.stream_id) == (rf.HELLO, 0)
        hello = json.loads(first.payload)
        assert hello["role"] == "instance" and hello["protocol"] == 1
        assert hello["instance"]["install_kind"] in INSTALL_KINDS
        assert set(hello["gate"]) == {"gen", "revoked"}
        await ws.send(rf.encode_json(rf.HELLO, 0, relay_hello(self.limits)))
        conn = FakeConnection(ws, hello, relay_hello(self.limits)["limits"])
        conn.start()
        await self.connections.put(conn)
        await ws.wait_closed()

    async def connection(self, timeout: float = 5.0) -> FakeConnection:
        return await asyncio.wait_for(self.connections.get(), timeout)
