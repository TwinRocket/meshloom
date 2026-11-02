"""The Meshloom Remote relay client against an in-process fake relay (MSHL-87).

``tests/remote_relay_fakes.py`` runs the relay side of ``remote-relay.md`` v1 on a
local ``websockets`` server; the client's ``connect_url`` points at it. Covered: the
handshake (JWT, protocol header, HELLO with the plugins and the gate), relayed HTTP
(GET, POST, chunked bodies and credits, batching, 204, CANCEL) and WebSocket
sessions, the scope a relayed request gets (visitor IP, https, ``via = relay``, the
forwarding and relay headers dropped), the pass check, protocol errors, and the
close-code policy (4001, 4003, 4004, 4005, 4009, 4010, 1013). ``TestRealApp`` sends
through the real app: no trusted network or login-free LAN whatever the frame's
``client_ip``, a ``__Host-`` session cookie at login, ``GET /api/auth/session`` with
``via = relay``, ``X-Forwarded-Host`` ignored, login delays and audit rows keyed by the
visitor's address (also on a Docker install), and no HSTS from the instance (MSHL-84).
"""

from __future__ import annotations

import asyncio
import base64
import json
from dataclasses import dataclass
from typing import Any

import pytest

from app.auth.audit import SYSTEM_ACTOR
from app.repository.auth import UserRepository
from app.repository.remote import RemoteRepository
from app.services import remote, remote_relay
from app.services import remote_frames as rf
from tests.remote_relay_fakes import HOST, SLUG, FakeConnection, FakeRelay
from tests.share_fakes import PUBLIC_IP, Dns, Recorder, json_response


@pytest.fixture(autouse=True)
def _community_on(community_on):
    """Links & Files and Remote need Community on (MSHL-445)."""


class EchoApp:
    """A small ASGI app that records each scope and echoes what it gets."""

    def __init__(self) -> None:
        self.scopes: list[dict[str, Any]] = []
        self.ws_disconnects: list[int] = []
        self.http_disconnected = asyncio.Event()

    async def __call__(self, scope: dict[str, Any], receive: Any, send: Any) -> None:
        self.scopes.append(scope)
        if scope["type"] == "http":
            await self._http(scope, receive, send)
        else:
            await self._ws(scope, receive, send)

    async def _http(self, scope: dict[str, Any], receive: Any, send: Any) -> None:
        body = b""
        while True:
            message = await receive()
            body += message.get("body", b"")
            if not message.get("more_body"):
                break
        path = scope["path"]
        if path == "/empty":
            await send({"type": "http.response.start", "status": 204, "headers": []})
            await send({"type": "http.response.body", "body": b""})
            return
        if path == "/big":
            size = int(scope["query_string"])
            await send({"type": "http.response.start", "status": 200, "headers": []})
            await send({"type": "http.response.body", "body": b"x" * size})
            return
        if path == "/wait":
            await send({"type": "http.response.start", "status": 200, "headers": []})
            await send({"type": "http.response.body", "body": b"a", "more_body": True})
            assert (await receive())["type"] == "http.disconnect"
            self.http_disconnected.set()
            return
        headers = [
            (b"content-type", b"application/json"),
            (b"connection", b"keep-alive"),
            (b"transfer-encoding", b"chunked"),
            (b"strict-transport-security", b"max-age=1"),
            (b"x-kept", b"yes"),
        ]
        await send({"type": "http.response.start", "status": 200, "headers": headers})
        echo = json.dumps({"method": scope["method"], "body": body.decode()}).encode()
        await send({"type": "http.response.body", "body": echo[:5], "more_body": True})
        await send({"type": "http.response.body", "body": echo[5:]})

    async def _ws(self, scope: dict[str, Any], receive: Any, send: Any) -> None:
        assert (await receive())["type"] == "websocket.connect"
        if scope["path"] == "/deny":
            await send({"type": "websocket.close", "code": 1008})
            return
        await send({"type": "websocket.accept"})
        while True:
            message = await receive()
            if message["type"] == "websocket.disconnect":
                self.ws_disconnects.append(message["code"])
                return
            if message.get("text") == "bye":
                await send({"type": "websocket.close", "code": 1000, "reason": "done"})
                continue
            if message.get("text") is not None:
                await send({"type": "websocket.send", "text": "echo:" + message["text"]})
            else:
                await send({"type": "websocket.send", "bytes": message["bytes"][::-1]})


@dataclass
class Env:
    fake: FakeRelay
    client: remote_relay.RelayClient
    token: str
    user_id: int
    device_id: str


async def _registered() -> None:
    await RemoteRepository.update_state(
        enabled=True,
        slug=SLUG,
        hostname=HOST,
        relay_url="wss://relay.meshloom.link/v1/connect",
        registered_at=1,
    )
    await remote.load()


@pytest.fixture
def fast(monkeypatch):
    monkeypatch.setattr(remote_relay, "BACKOFF_INITIAL_S", 0.01)
    monkeypatch.setattr(remote_relay, "PAUSED_RETRY_S", (30.0, 30.0))
    monkeypatch.setattr(remote_relay, "RELAY_PAUSED_RETRY_S", (30.0, 30.0))
    monkeypatch.setattr(remote_relay, "WS_CLOSE_LINGER_S", 0.2)


async def _env(fake: FakeRelay) -> Env:
    await _registered()
    user = await UserRepository.create("alice", "member")
    device, token = await remote.issue_client_pass(user, "test", SYSTEM_ACTOR)
    client = remote_relay.RelayClient()
    client.connect_url = lambda state: fake.url
    return Env(fake, client, token, user.id, device.id)


@pytest.fixture
async def env(test_db, fast):
    async with FakeRelay() as fake:
        e = await _env(fake)
        yield e
        await e.client.stop()


async def _connect(env: Env, app: Any) -> FakeConnection:
    await env.client.start(app)
    conn = await env.fake.connection()
    for _ in range(100):
        if remote.relay_connected():
            break
        await asyncio.sleep(0.01)
    return conn


async def _until(predicate: Any, timeout: float = 5.0) -> None:
    for _ in range(int(timeout / 0.01)):
        if predicate():
            return
        await asyncio.sleep(0.01)
    raise AssertionError("condition not met")


def _claims(token: str) -> dict[str, Any]:
    part = token.split(".")[1]
    return json.loads(base64.urlsafe_b64decode(part + "=" * (-len(part) % 4)))


class TestHandshake:
    async def test_headers_hello_and_status(self, env):
        conn = await _connect(env, EchoApp())
        headers = {k.lower(): v for k, v in env.fake.attempts[0].items()}
        assert headers["x-meshloom-relay-protocol"] == "1"
        jwt = _claims(headers["authorization"].removeprefix("Bearer "))
        assert jwt["aud"] == "relay.meshloom.link" and jwt["jti"]
        assert headers["user-agent"].startswith("Meshloom/")
        assert set(conn.hello["plugins"]) == {"enabled", "isolation"}
        assert conn.hello["gate"] == await remote.gate_state()
        assert conn.hello["limits"] == remote_relay.WANTED_LIMITS
        assert remote.relay_connected() and remote.link_status() == remote_relay.CONNECTED

    async def test_gate_changes_go_out_as_control(self, env):
        conn = await _connect(env, EchoApp())
        assert await remote.revoke_device(env.device_id, SYSTEM_ACTOR, owner_id=None)
        op = await conn.next_control()
        assert op == {"op": "gate", "gen": 0, "revoked": [env.device_id]}

    async def test_control_enroll_is_answered(self, env):
        conn = await _connect(env, EchoApp())
        await conn.send(
            rf.encode_json(
                rf.CONTROL, 0, {"op": "enroll", "request_id": "A" * 22, "token": "not-a-pass"}
            )
        )
        assert await conn.next_control() == {
            "op": "pass",
            "request_id": "A" * 22,
            "error": "enroll_refused",
        }

    async def test_disable_disconnects_and_nothing_reconnects(self, env):
        conn = await _connect(env, EchoApp())
        await remote.disable(SYSTEM_ACTOR)
        await asyncio.wait_for(conn.ws.wait_closed(), 5)
        await asyncio.sleep(0.1)
        assert not env.client.running() and len(env.fake.attempts) == 1
        assert remote.link_status() is None and not remote.relay_connected()

    async def test_concurrent_restarts_leave_one_loop_and_disable_leaves_none(self, env):
        def loops() -> list[asyncio.Task[Any]]:
            return [
                t for t in asyncio.all_tasks() if t.get_name() == "remote-relay" and not t.done()
            ]

        await _connect(env, EchoApp())
        await asyncio.gather(env.client.restart(), env.client.restart(), env.client.restart())
        assert len(loops()) == 1 and env.client.running()
        await RemoteRepository.update_state(enabled=False)
        await asyncio.gather(env.client.restart(), env.client.restart())
        await asyncio.sleep(0.05)
        assert loops() == [] and not env.client.running()


class TestHttp:
    async def test_get_round_trip_and_scope(self, env):
        app = EchoApp()
        conn = await _connect(env, app)
        status, headers, body, _ = await conn.request(
            "GET",
            "/api/a%20b",
            query="x=1",
            headers=[
                ["accept", "application/json"],
                ["x-forwarded-host", "evil.example"],
                ["forwarded", "for=1.2.3.4"],
                ["x-real-ip", "1.2.3.4"],
                ["connection", "x-hop"],
                ["x-hop", "drop me"],
            ],
            client_ip="2001:db8::42",
            pass_token=env.token,
        )
        assert status == 200
        assert json.loads(body) == {"method": "GET", "body": ""}
        names = [name for name, _ in headers]
        assert "x-kept" in names
        for dropped in ("connection", "transfer-encoding", "strict-transport-security"):
            assert dropped not in names
        scope = app.scopes[0]
        assert scope["client"] == ("2001:db8::42", 0)
        assert scope["scheme"] == "https" and scope["server"] == (HOST, 443)
        assert scope["state"]["via"] == "relay"
        assert scope["state"]["remote_device"]["id"] == env.device_id
        assert scope["path"] == "/api/a b" and scope["raw_path"] == b"/api/a%20b"
        assert scope["query_string"] == b"x=1"
        sent = dict(scope["headers"])
        assert sent[b"host"] == HOST.encode() and sent[b"accept"] == b"application/json"
        for name in (
            b"x-forwarded-for",
            b"x-forwarded-proto",
            b"x-forwarded-host",
            b"forwarded",
            b"x-real-ip",
            b"x-meshloom-relay",
            b"x-meshloom-pass",
            b"connection",
            b"x-hop",
        ):
            assert name not in sent

    async def test_post_body_in_chunks_with_credits(self, test_db, fast):
        limits = {"max_streams": 4, "max_chunk": 16384, "initial_credit": 16384}
        async with FakeRelay(limits) as fake:
            env = await _env(fake)
            try:
                conn = await _connect(env, EchoApp())
                payload = b"y" * 50000
                status, _, body, stream_id = await conn.request(
                    "POST", "/api/echo", payload, pass_token=env.token
                )
                assert status == 200 and json.loads(body)["body"] == payload.decode()
                credits = [
                    f for f in conn.frames if f.type == rf.CREDIT and f.stream_id == stream_id
                ]
                assert credits, "the instance granted credit back"
                # A big response waits for the relay's credits, in max_chunk pieces.
                status, _, body, stream_id = await conn.request(
                    "GET", "/big", query="70000", pass_token=env.token
                )
                assert status == 200 and body == b"x" * 70000
                chunks = [
                    f for f in conn.frames if f.type == rf.RESP_BODY and f.stream_id == stream_id
                ]
                assert max(len(f.payload) for f in chunks) <= 16384 and len(chunks) >= 5
            finally:
                await env.client.stop()

    async def test_overrun_of_the_credit_cancels_the_stream(self, test_db, fast):
        limits = {"max_streams": 4, "max_chunk": 16384, "initial_credit": 16384}
        async with FakeRelay(limits) as fake:
            env = await _env(fake)
            try:
                conn = await _connect(env, EchoApp())
                head = conn.head("POST", "/api/echo", pass_token=env.token)
                sid = conn.new_stream()
                await conn.send(
                    rf.encode_json(rf.REQ_HEAD, sid, head),
                    rf.encode_frame(rf.REQ_BODY, sid, 0, b"z" * 16384),
                    rf.encode_frame(rf.REQ_BODY, sid, rf.END, b"z"),
                )
                frame = await conn.frame(sid)
                while frame.type == rf.CREDIT:
                    frame = await conn.frame(sid)
                assert (frame.type, frame.payload) == (rf.CANCEL, b"credit_exceeded")
                status, *_ = await conn.request("GET", "/empty", pass_token=env.token)
                assert status == 204, "the connection stays up"
            finally:
                await env.client.stop()

    async def test_a_whole_response_is_one_message(self, env):
        conn = await _connect(env, EchoApp())
        before = len(conn.messages)
        _, _, _, stream_id = await conn.request("GET", "/api/x", pass_token=env.token)
        await asyncio.sleep(0.1)
        carrying = [
            m
            for m in conn.messages[before:]
            if stream_id in {f.stream_id for f in rf.decode_message(m).frames}
        ]
        assert len(carrying) == 1
        types = [f.type for f in rf.decode_message(carrying[0]).frames]
        assert types == [rf.RESP_HEAD, rf.RESP_BODY, rf.RESP_BODY]

    async def test_no_content_is_a_final_head(self, env):
        conn = await _connect(env, EchoApp())
        status, _, body, stream_id = await conn.request("GET", "/empty", pass_token=env.token)
        assert (status, body) == (204, b"")
        head = next(f for f in conn.frames if f.stream_id == stream_id)
        assert head.type == rf.RESP_HEAD and head.has(rf.END)

    async def test_cancel_reaches_the_app_as_a_disconnect(self, env):
        app = EchoApp()
        conn = await _connect(env, app)
        head = conn.head("GET", "/wait", pass_token=env.token)
        await conn.send(rf.encode_json(rf.REQ_HEAD, 1, head, rf.END))
        assert (await conn.frame(1)).type == rf.RESP_HEAD
        await conn.send(rf.encode_frame(rf.CANCEL, 1, 0, b"client_gone"))
        await asyncio.wait_for(app.http_disconnected.wait(), 5)

    @pytest.mark.parametrize("case", ["none", "garbage", "revoked", "two"])
    async def test_a_request_without_a_valid_pass_is_401(self, env, case):
        app = EchoApp()
        conn = await _connect(env, app)
        token: str | None = {"none": None, "garbage": "abc.def.ghi"}.get(case, env.token)
        extra: list[list[str]] = []
        if case == "revoked":
            await remote.revoke_device(env.device_id, SYSTEM_ACTOR, owner_id=None)
        if case == "two":
            extra = [["x-meshloom-pass", env.token]]
        status, _, body, _ = await conn.request("GET", "/api/x", headers=extra, pass_token=token)
        assert status == 401 and json.loads(body)["detail"]["code"] == "remote_pass_required"
        assert app.scopes == []

    async def test_a_foreign_host_is_refused(self, env):
        app = EchoApp()
        conn = await _connect(env, app)
        head = conn.head("GET", "/api/x", pass_token=env.token)
        head["host"] = "abcdefgh23.meshloom.link"
        await conn.send(rf.encode_json(rf.REQ_HEAD, 1, head, rf.END))
        status, _, body = await conn.response(1)
        assert status == 421 and app.scopes == []


class TestWebSocket:
    async def test_session(self, env):
        app = EchoApp()
        conn = await _connect(env, app)
        head = conn.head("WS", "/api/ws", pass_token=env.token)
        await conn.send(rf.encode_json(rf.WS_OPEN, 1, head))
        accept = await conn.frame(1)
        assert accept.type == rf.WS_ACCEPT and json.loads(accept.payload)["status"] == 101
        assert app.scopes[0]["type"] == "websocket" and app.scopes[0]["scheme"] == "wss"
        assert app.scopes[0]["state"]["via"] == "relay"
        await conn.send(rf.encode_frame(rf.WS_MSG, 1, 0, "héllo".encode()))
        reply = await conn.frame(1)
        assert (reply.type, reply.payload.decode()) == (rf.WS_MSG, "echo:héllo")
        await conn.send(
            rf.encode_frame(rf.WS_MSG, 1, rf.MORE, b"frag"),
            rf.encode_frame(rf.WS_MSG, 1, 0, b"ment"),
        )
        assert (await conn.frame(1)).payload == b"echo:fragment"
        await conn.send(rf.encode_frame(rf.WS_MSG, 1, rf.BINARY, b"\x00\x01\x02"))
        reply = await conn.frame(1)
        assert reply.has(rf.BINARY) and reply.payload == b"\x02\x01\x00"
        await conn.send(rf.encode_frame(rf.WS_CLOSE, 1, 0, rf.close_payload(1001, "away")))
        close = await conn.frame(1)
        assert close.type == rf.WS_CLOSE and rf.parse_close(close.payload)[0] == 1001
        await _until(lambda: app.ws_disconnects == [1001])

    async def test_the_app_closes_first(self, env):
        app = EchoApp()
        conn = await _connect(env, app)
        await conn.send(
            rf.encode_json(rf.WS_OPEN, 1, conn.head("WS", "/api/ws", pass_token=env.token))
        )
        assert (await conn.frame(1)).type == rf.WS_ACCEPT
        await conn.send(rf.encode_frame(rf.WS_MSG, 1, 0, b"bye"))
        close = await conn.frame(1)
        assert rf.parse_close(close.payload) == (1000, "done")
        # No WS_CLOSE back from the relay: after the linger the app sees the end.
        await _until(lambda: app.ws_disconnects == [1000])

    async def test_refused_before_accept(self, env):
        conn = await _connect(env, EchoApp())
        await conn.send(
            rf.encode_json(rf.WS_OPEN, 1, conn.head("WS", "/deny", pass_token=env.token))
        )
        accept = await conn.frame(1)
        assert accept.type == rf.WS_ACCEPT and accept.has(rf.END)
        assert json.loads(accept.payload)["status"] == 403

    async def test_without_a_pass(self, env):
        app = EchoApp()
        conn = await _connect(env, app)
        await conn.send(rf.encode_json(rf.WS_OPEN, 1, conn.head("WS", "/api/ws")))
        accept = await conn.frame(1)
        assert json.loads(accept.payload)["status"] == 401 and app.scopes == []


class TestProtocol:
    async def test_type_zero_closes_with_1002(self, env):
        conn = await _connect(env, EchoApp())
        await conn.send(bytes.fromhex("00000000010000000000"))
        await asyncio.wait_for(conn.ws.wait_closed(), 5)
        assert conn.ws.close_code == 1002 and conn.ws.close_reason == "frame_type_invalid"

    async def test_an_oversized_stream_frame_is_cancelled_and_the_link_stays(self, env):
        conn = await _connect(env, EchoApp())
        await conn.send(
            rf.HEADER.pack(rf.REQ_BODY, 3, 0, 262145) + b"\x00" * 262145,
            bytes.fromhex("7f000000010000000002abcd"),
        )
        frame = await conn.frame(3)
        assert (frame.type, frame.payload) == (rf.CANCEL, b"frame_too_large")
        status, *_ = await conn.request("GET", "/empty", pass_token=env.token)
        assert status == 204

    async def test_even_and_unknown_streams_are_ignored(self, env):
        conn = await _connect(env, EchoApp())
        await conn.send(
            rf.encode_frame(rf.REQ_BODY, 2, 0, b"x"), rf.encode_frame(rf.REQ_BODY, 99, 0, b"x")
        )
        status, *_ = await conn.request("GET", "/empty", pass_token=env.token)
        assert status == 204
        link = env.client.link
        assert link is not None
        assert link.counters["even_stream"] == 1 and link.counters["unknown_stream"] == 1


class TestCloseCodes:
    async def _run(self, env: Env, plan: list[tuple[int, str]]) -> None:
        env.fake.close_plan = list(plan)
        await env.client.start(EchoApp())

    async def test_4009_is_terminal(self, env):
        await self._run(env, [(4009, "instance_banned")])
        await _until(lambda: remote.link_status() == remote_relay.BANNED)
        await asyncio.sleep(0.1)
        assert len(env.fake.attempts) == 1 and not env.client.running()

    async def test_4010_needs_an_update(self, env):
        await self._run(env, [(4010, "protocol_unsupported")])
        await _until(lambda: remote.link_status() == remote_relay.UPDATE_REQUIRED)
        assert len(env.fake.attempts) == 1

    async def test_4004_is_paused(self, env):
        await self._run(env, [(4004, "instance_paused")])
        await _until(lambda: remote.link_status() == remote_relay.PAUSED)
        await asyncio.sleep(0.1)
        assert len(env.fake.attempts) == 1 and env.client.running()

    async def test_1013_is_a_long_backoff(self, env):
        await self._run(env, [(1013, "relay_paused")])
        await _until(lambda: remote.link_status() == remote_relay.RELAY_PAUSED)
        await asyncio.sleep(0.1)
        assert len(env.fake.attempts) == 1

    async def test_unknown_4xxx_is_like_1013(self, env):
        await self._run(env, [(4099, "new_thing")])
        await _until(lambda: remote.link_status() == remote_relay.RELAY_PAUSED)

    async def test_4005_reconnects_once_then_replaced(self, env):
        await self._run(env, [(4005, "replaced"), (4005, "replaced")])
        await _until(lambda: remote.link_status() == remote_relay.REPLACED)
        await asyncio.sleep(0.1)
        assert len(env.fake.attempts) == 2 and not env.client.running()

    async def test_4005_once_then_connected(self, env):
        await self._run(env, [(4005, "replaced")])
        await env.fake.connection()
        await _until(lambda: remote.link_status() == remote_relay.CONNECTED)
        assert len(env.fake.attempts) == 2

    async def test_4001_mints_a_new_token(self, env):
        await self._run(env, [(4001, "token_expired"), (4001, "token_replayed")])
        await env.fake.connection()
        jtis = [
            _claims(a["Authorization"].removeprefix("Bearer "))["jti"] for a in env.fake.attempts
        ]
        assert len(jtis) == 3 and len(set(jtis)) == 3

    async def test_network_errors_back_off_and_reconnect(self, env):
        await self._run(env, [(1011, "boom"), (1001, "going_away")])
        await env.fake.connection()
        assert len(env.fake.attempts) == 3

    async def test_4003_registers_again_then_gives_up(self, env, monkeypatch):
        Dns(monkeypatch, {remote.RELAY_HOST: [PUBLIC_IP]})
        recorder = Recorder(
            monkeypatch,
            lambda request: json_response(
                200,
                {
                    "slug": SLUG,
                    "hostname": HOST,
                    "relay_url": "wss://relay.meshloom.link/v1/connect",
                },
            ),
        )
        await self._run(env, [(4003, "instance_unknown"), (4003, "instance_unknown")])
        await _until(lambda: remote.link_status() == remote_relay.NOT_REGISTERED)
        await asyncio.sleep(0.1)
        assert [r.url.path for r in recorder.requests] == ["/v1/instances"]
        assert len(env.fake.attempts) == 2 and not env.client.running()

    async def test_4003_then_connected(self, env, monkeypatch):
        Dns(monkeypatch, {remote.RELAY_HOST: [PUBLIC_IP]})
        Recorder(
            monkeypatch,
            lambda request: json_response(
                200,
                {
                    "slug": SLUG,
                    "hostname": HOST,
                    "relay_url": "wss://relay.meshloom.link/v1/connect",
                },
            ),
        )
        await self._run(env, [(4003, "instance_unknown")])
        await env.fake.connection()
        await _until(lambda: remote.link_status() == remote_relay.CONNECTED)


class TestRealApp:
    """Through the real app and its auth gate (``real_auth``)."""

    pytestmark = [pytest.mark.real_auth, pytest.mark.usefixtures("fast_argon2")]

    async def test_login_trusted_networks_and_forwarded_host(self, env, monkeypatch):
        from app.auth.models import TrustedNetworkRule
        from app.main import app
        from app.repository.auth import AuthSettingsRepository
        from tests.auth_helpers import PASSWORD, create_user

        monkeypatch.setenv("MESHLOOM_INSTALL_KIND", "package")
        owner = await create_user("owner", PASSWORD, "admin")
        _, token = await remote.issue_client_pass(owner, "owner", SYSTEM_ACTOR)
        await AuthSettingsRepository.update(
            trusted_networks=[TrustedNetworkRule(cidr="203.0.113.0/24", user_id=owner.id)],
            lan_login_free=True,
        )
        conn = await _connect(env, app)
        # The visitor's address is in a trusted network: a relayed request is never LAN.
        status, _, body, _ = await conn.request("GET", "/api/auth/session", pass_token=token)
        assert status == 200 and json.loads(body)["authenticated"] is False
        status, *_ = await conn.request("GET", "/api/contacts", pass_token=token)
        assert status == 401
        # X-Forwarded-Host never widens same-origin.
        login = json.dumps({"username": "owner", "password": PASSWORD}).encode()
        status, *_ = await conn.request(
            "POST",
            "/api/auth/login",
            login,
            headers=[
                ["content-type", "application/json"],
                ["origin", "https://evil.example"],
                ["x-forwarded-host", "evil.example"],
            ],
            pass_token=token,
        )
        assert status == 403
        status, headers, body, _ = await conn.request(
            "POST",
            "/api/auth/login",
            login,
            headers=[["content-type", "application/json"], ["origin", f"https://{HOST}"]],
            pass_token=token,
        )
        assert status == 200, body
        cookies = [value for name, value in headers if name == "set-cookie"]
        session = next(c for c in cookies if c.startswith("__Host-meshloom_session="))
        assert "Secure" in session and "Path=/" in session and "Domain" not in session
        assert not any(name == "strict-transport-security" for name, _ in headers)
        cookie = session.split(";", 1)[0]
        status, _, body, _ = await conn.request(
            "GET", "/api/auth/session", headers=[["cookie", cookie]], pass_token=token
        )
        session_body = json.loads(body)
        assert session_body["authenticated"] is True and session_body["via"] == "relay"
        assert session_body["user"]["id"] == owner.id

    @pytest.mark.parametrize(
        ("install_kind", "frame_ip"), [("package", "192.168.1.20"), ("addon", "127.0.0.1")]
    )
    async def test_never_lan_whatever_the_frame_client_ip(
        self, env, monkeypatch, install_kind, frame_ip
    ):
        """A relayed request never matches a trusted network rule, even when the
        frame's ``client_ip`` is one a direct LAN request from that address would
        be signed in from (control: same rule, same address, no relay). The rule
        maps to a ``member`` account, since an admin is never auto-signed in."""
        from app.auth.models import TrustedNetworkRule
        from app.main import app
        from app.repository.auth import AuthSettingsRepository
        from tests.auth_helpers import create_user, make_client

        monkeypatch.setenv("MESHLOOM_INSTALL_KIND", install_kind)
        await create_user()
        tablet = await create_user("tablet", password=None, role="member")
        _, token = await remote.issue_client_pass(tablet, "tablet", SYSTEM_ACTOR)
        cidr = "127.0.0.0/8" if frame_ip.startswith("127.") else "192.168.0.0/16"
        await AuthSettingsRepository.update(
            trusted_networks=[TrustedNetworkRule(cidr=cidr, user_id=tablet.id)],
            lan_login_free=True,
        )
        # Control: the same address, directly, is signed in as the tablet.
        async with make_client("http://192.168.1.10:8000", (frame_ip, 40000)) as client:
            body = (await client.get("/api/auth/session")).json()
            assert body["authenticated"] is True and body["via"] == "trusted", body
            assert body["user"]["id"] == tablet.id
            assert (await client.get("/api/contacts")).status_code == 200
        # Relayed: only ``via = relay`` differs, and it refuses the rule.
        conn = await _connect(env, app)
        status, _, body, _ = await conn.request(
            "GET", "/api/auth/session", client_ip=frame_ip, pass_token=token
        )
        assert status == 200 and json.loads(body)["authenticated"] is False
        status, *_ = await conn.request(
            "GET", "/api/contacts", client_ip=frame_ip, pass_token=token
        )
        assert status == 401

    @pytest.mark.parametrize("install_kind", ["package", "compose"])
    async def test_login_delays_and_audit_use_the_visitor_ip(self, env, monkeypatch, install_kind):
        """The pair and per-IP rules key on the frame's ``client_ip``, even on a
        Docker install without a trusted proxy (a relayed address is never shared),
        and the audit rows name that visitor with ``via = relay``."""
        from app.auth import ratelimit
        from app.main import app
        from app.repository.auth import AuthAuditRepository
        from tests.auth_helpers import PASSWORD, create_user

        monkeypatch.setenv("MESHLOOM_INSTALL_KIND", install_kind)
        ratelimit.reset_login_limiter()
        owner = await create_user("owner", PASSWORD, "admin")
        _, token = await remote.issue_client_pass(owner, "owner", SYSTEM_ACTOR)
        conn = await _connect(env, app)
        headers = [["content-type", "application/json"], ["origin", f"https://{HOST}"]]
        wrong = json.dumps({"username": "owner", "password": "not the password"}).encode()
        for _ in range(ratelimit.PAIR_FREE_FAILURES):
            status, *_ = await conn.request(
                "POST",
                "/api/auth/login",
                wrong,
                headers=headers,
                pass_token=token,
                client_ip="198.51.100.9",
            )
            assert status == 401
        limiter = ratelimit.login_limiter
        assert set(limiter._pairs) == {("198.51.100.9", "owner")}
        assert set(limiter._ips) == {"198.51.100.9"}
        # The fifth failure closed that visitor's pair: it waits, another one does not.
        status, _, body, _ = await conn.request(
            "POST",
            "/api/auth/login",
            wrong,
            headers=headers,
            pass_token=token,
            client_ip="198.51.100.9",
        )
        assert status == 429 and json.loads(body)["detail"]["code"] == "auth_rate_limited"
        status, *_ = await conn.request(
            "POST",
            "/api/auth/login",
            wrong,
            headers=headers,
            pass_token=token,
            client_ip="198.51.100.10",
        )
        assert status == 401
        rows = await AuthAuditRepository.list_recent(limit=100)
        logins = [row for row in rows if row.action == "login"]
        assert {(row.ip, row.via, row.outcome) for row in logins} == {
            ("198.51.100.9", "relay", "failure"),
            ("198.51.100.9", "relay", "delayed"),
            ("198.51.100.10", "relay", "failure"),
        }

    @pytest.mark.parametrize(
        ("lang", "sentence"),
        [
            ("en", "Visitors through Meshloom Remote are always counted by their own address"),
            ("fr", "Les visiteurs qui passent par Meshloom Remote sont toujours comptés"),
        ],
    )
    def test_user_docs_say_relayed_visitors_get_every_login_delay(self, lang, sentence):
        """The login delays section of the user doc (copied to meshloom.app) must not
        leave a relayed visitor under the Docker "per-account rule only" bullet."""
        from pathlib import Path

        doc = Path(__file__).resolve().parents[1] / "docs" / "user" / lang / "deep" / "access.md"
        text = doc.read_text(encoding="utf-8")
        assert sentence in text
        docker = "Docker install" if lang == "en" else "installation Docker"
        assert text.index(docker) < text.index(sentence)

    async def test_the_instance_never_sends_hsts_to_the_relay(self, env, monkeypatch):
        """The relay owns HSTS: the instance's security headers layer never adds it
        to a relayed answer (checked before ``outbound_headers`` would strip it)."""
        from app.main import app

        monkeypatch.setenv("MESHLOOM_INSTALL_KIND", "package")
        seen: list[list[tuple[bytes, bytes]]] = []

        async def spy(scope: dict[str, Any], receive: Any, send: Any) -> None:
            async def recording(message: dict[str, Any]) -> None:
                if message["type"] == "http.response.start":
                    seen.append(list(message.get("headers") or []))
                await send(message)

            await app(scope, receive, recording)

        conn = await _connect(env, spy)
        for path in ("/api/auth/session", "/api/health", "/api/contacts", "/"):
            status, headers, _, _ = await conn.request("GET", path, pass_token=env.token)
            assert status < 500, path
            assert not any(name == "strict-transport-security" for name, _ in headers)
        assert len(seen) == 4
        for start in seen:
            assert all(name.lower() != b"strict-transport-security" for name, _ in start)


def test_the_api_documents_every_link_state():
    from app.routers.remote import RemoteStatus

    description = RemoteStatus.model_fields["link_status"].description or ""
    for state in remote_relay.LINK_STATES:
        assert state in description


def test_the_connect_url_is_only_ever_the_relay_host():
    url = "wss://relay.meshloom.link/v1/connect"
    for relay_url in (
        url,
        "",
        "wss://evil.example/v1/connect",
        "ws://relay.meshloom.link/v1/connect",
        "wss://user@relay.meshloom.link/v1/connect",
        "wss://relay.meshloom.link:8443/v1/connect",
    ):
        assert remote_relay.connect_url(remote.RemoteState(relay_url=relay_url)) == url
