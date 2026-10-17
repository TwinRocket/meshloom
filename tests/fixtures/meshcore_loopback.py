"""A real meshcore stack wired to an in-memory radio, for "no secret in logs" tests.

``make_loopback()`` builds a real ``MeshCore`` (reader, dispatcher, command
handler) on a real ``TCPConnection`` or ``SerialConnection``. Only the asyncio
transport is fake: its ``write()`` records the framed packet and hands the
command payload to a responder, whose reply frames are fed back through the
protocol's ``data_received`` path, exactly like bytes arriving from a radio. So
every meshcore log site (``Sending raw data``, ``sending pkt``, ``Received
data``, ``Dispatching event``) runs for real.

The real ``TCPConnection.send`` stops logging ``sending pkt`` (and sending at
all) once five frames went out without a reply (``TCP_DISCONNECT_THRESHOLD``):
reply to every command, or call ``balance()``. Tests should assert that the
record they check exists before checking its content.

Reused by the Wi-Fi settings and backup lots for their own log checks.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field

from meshcore import MeshCore, SerialConnection, TCPConnection

Responder = Callable[[bytes], Iterable[bytes]]


def cli_reply(text: str | bytes) -> bytes:
    """An incoming RESP_CODE_CLI_REPLY frame (code 29) carrying ``text``."""
    body = text.encode("utf-8") if isinstance(text, str) else text
    return b"\x1d" + body


class LoopbackTransport(asyncio.Transport):
    def __init__(self, protocol: asyncio.Protocol) -> None:
        super().__init__()
        self.protocol = protocol
        self.writes: list[bytes] = []
        self.responder: Responder | None = None
        self.closed = False

    def write(self, data) -> None:  # type: ignore[override]
        packet = bytes(data)
        self.writes.append(packet)
        responder = self.responder
        if responder is None:
            return
        # Framed as 0x3c + 2-byte little-endian length + payload.
        for reply in responder(packet[3:]) or ():
            asyncio.get_running_loop().call_soon(self.feed, reply)

    def feed(self, frame: bytes) -> None:
        """Deliver one incoming frame (0x3e + length + payload) to the protocol."""
        self.protocol.data_received(b"\x3e" + len(frame).to_bytes(2, "little") + frame)

    def is_closing(self) -> bool:
        return self.closed

    def close(self) -> None:
        self.closed = True

    @property
    def sent_payloads(self) -> list[bytes]:
        return [packet[3:] for packet in self.writes]


@dataclass
class Loopback:
    mc: MeshCore
    connection: TCPConnection | SerialConnection
    transport: LoopbackTransport
    kind: str
    _extra_tasks: set[asyncio.Task] = field(default_factory=set)

    def set_responder(self, responder: Responder | None) -> None:
        self.transport.responder = responder

    def feed(self, frame: bytes) -> None:
        self.transport.feed(frame)

    def balance(self) -> None:
        """Forget unanswered sends (TCP disconnect heuristic)."""
        if isinstance(self.connection, TCPConnection):
            self.connection._receive_count = self.connection._send_count

    async def settle(self) -> None:
        """Wait until every fed frame was parsed and its event dispatched."""
        for _ in range(50):
            await asyncio.sleep(0)
            pending = list(self.connection._background_tasks)
            if pending:
                await asyncio.gather(*pending, return_exceptions=True)
            queue = self.mc.dispatcher.queue
            if queue is not None:
                await queue.join()
            dispatcher_tasks = list(getattr(self.mc.dispatcher, "_background_tasks", ()))
            if dispatcher_tasks:
                await asyncio.gather(*dispatcher_tasks, return_exceptions=True)
            if not self.connection._background_tasks and (queue is None or queue.empty()):
                return

    async def close(self) -> None:
        await self.mc.dispatcher.stop()


async def make_loopback(kind: str = "tcp", responder: Responder | None = None) -> Loopback:
    if kind == "tcp":
        connection: TCPConnection | SerialConnection = TCPConnection("loopback", 0)
        protocol: asyncio.Protocol = TCPConnection.MCClientProtocol(connection)
    elif kind == "serial":
        connection = SerialConnection("loopback", 115200)
        protocol = SerialConnection.MCSerialClientProtocol(connection)
    else:
        raise ValueError(kind)
    mc = MeshCore(connection)
    transport = LoopbackTransport(protocol)
    transport.responder = responder
    protocol.connection_made(transport)
    await mc.dispatcher.start()
    return Loopback(mc=mc, connection=connection, transport=transport, kind=kind)
