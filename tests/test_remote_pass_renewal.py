"""Renewal of a browser pass through ``x-meshloom-set-pass`` (MSHL-468), against the
in-process fake relay (``tests/remote_relay_fakes.py``), which does not implement the
relay's rule: ``_relay_accepts`` below mirrors ``instanceRelay.renewal`` of
``meshloom-remote`` (a pass that verifies in the cookie slot, for the same ``sub``).
Nothing here runs against the real relay.
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

import pytest

from app.auth.audit import SYSTEM_ACTOR
from app.auth.models import User
from app.auth.principal import Principal, set_principal
from app.repository.auth import UserRepository
from app.repository.remote import RemoteRepository
from app.services import remote, remote_relay
from app.services import remote_frames as rf
from app.services.remote_passes import verify_pass
from tests.remote_relay_fakes import HOST, SLUG, FakeConnection, FakeRelay

SET_PASS = "x-meshloom-set-pass"
DAY = 86400
T0 = 1_800_000_000


@pytest.fixture(autouse=True)
def _community_on(community_on):
    return community_on


class Clock:
    def __init__(self) -> None:
        self.now = float(T0)

    def __call__(self) -> float:
        return self.now


class App:
    """Answers ``status`` to everything, signed in when ``user`` is set. ``during`` runs
    while the request is handled; ``extra`` adds response headers."""

    def __init__(self, user: User | None) -> None:
        self.user = user
        self.status = 200
        self.during: Any = None
        self.extra: list[tuple[bytes, bytes]] = []
        self.stream = False
        self.gate: asyncio.Event | None = None

    async def __call__(self, scope: dict[str, Any], receive: Any, send: Any) -> None:
        if self.user is not None:
            set_principal(scope, Principal(user=self.user, via="relay", credential="session"))
        if scope["type"] == "websocket":
            assert (await receive())["type"] == "websocket.connect"
            await send({"type": "websocket.accept", "headers": self.extra})
            await receive()
            return
        if self.gate is not None:
            await self.gate.wait()
        if self.during is not None:
            await self.during()
        await send({"type": "http.response.start", "status": self.status, "headers": self.extra})
        if self.stream:
            await send({"type": "http.response.body", "body": b"a", "more_body": True})
            await send({"type": "http.response.body", "body": b"b", "more_body": True})
        await send({"type": "http.response.body", "body": b"c"})


class Env:
    def __init__(self, fake: FakeRelay, clock: Clock, user: User) -> None:
        self.fake = fake
        self.clock = clock
        self.user = user
        self.client = remote_relay.RelayClient()
        self.client.connect_url = lambda state: fake.url

    async def device(self, kind: str = "browser") -> tuple[Any, str]:
        state = await RemoteRepository.get_state()
        return await remote._new_device(self.user.id, kind, "qr", None, state)  # type: ignore[arg-type]

    async def connect(self, app: Any) -> FakeConnection:
        await self.client.start(app)
        conn = await self.fake.connection()
        for _ in range(100):
            if remote.relay_connected():
                break
            await asyncio.sleep(0.01)
        return conn


@pytest.fixture
async def env(test_db):
    clock = Clock()
    remote.reset_remote_for_tests(clock)
    await RemoteRepository.update_state(
        enabled=True,
        slug=SLUG,
        hostname=HOST,
        relay_url="wss://relay.meshloom.link/v1/connect",
        registered_at=1,
    )
    await remote.load()
    user = await UserRepository.create("alice", "member")
    async with FakeRelay() as fake:
        e = Env(fake, clock, user)
        yield e
        await e.client.stop()
    remote.reset_remote_for_tests()


def _set_passes(headers: list[list[str]]) -> list[str]:
    return [value for name, value in headers if name == SET_PASS]


async def _relay_accepts(request_pass: str, set_pass: str, now: int) -> bool:
    """``instanceRelay.renewal``: verifies in the cookie slot, same ``sub``."""
    state = await remote.pass_state()
    assert state is not None
    _, asked = verify_pass(request_pass, "cookie", state, now)
    verdict, renewed = verify_pass(set_pass, "cookie", state, now)
    return (
        verdict == "accept"
        and renewed is not None
        and asked is not None
        and (renewed["sub"] == asked["sub"])
    )


class TestRenewal:
    async def test_a_used_pass_is_renewed_for_90_more_days(self, env):
        device, token = await env.device()
        env.clock.now += 30 * DAY
        conn = await env.connect(App(env.user))
        status, headers, _, _ = await conn.request("GET", "/api/contacts", pass_token=token)
        assert status == 200
        [renewed] = _set_passes(headers)
        assert renewed != token
        now = int(env.clock.now)
        assert await _relay_accepts(token, renewed, now)
        state = await remote.pass_state()
        _, claims = verify_pass(renewed, "cookie", state, now)
        assert claims["sub"] == device.id and claims["uid"] == str(env.user.id)
        assert claims["iat"] == now and claims["exp"] == now + 90 * DAY
        stored = await RemoteRepository.get_device(device.id)
        assert stored.pass_expires_at == now + 90 * DAY

    async def test_used_daily_a_device_outlives_the_first_pass(self, env):
        _, token = await env.device()
        conn = await env.connect(App(env.user))
        for _ in range(200):  # days
            env.clock.now += DAY + 1
            remote._rt.renewal_attempts.clear()  # the retry interval runs on real time
            status, headers, _, _ = await conn.request("GET", "/api/x", pass_token=token)
            assert status == 200
            [token] = _set_passes(headers)

    async def test_a_recent_pass_is_left_alone_and_costs_no_signature(self, env, monkeypatch):
        _, token = await env.device()
        env.clock.now += DAY - 1
        conn = await env.connect(App(env.user))

        async def no_signature(*_a: Any, **_k: Any) -> Any:
            raise AssertionError("signed a pass that needs no renewal")

        monkeypatch.setattr(remote, "_sign", no_signature)
        _, headers, _, _ = await conn.request("GET", "/api/x", pass_token=token)
        assert _set_passes(headers) == []

    async def test_a_client_pass_is_never_renewed(self, env):
        _, token = await remote.issue_client_pass(env.user, "script", SYSTEM_ACTOR)
        env.clock.now += 100 * DAY
        conn = await env.connect(App(env.user))
        status, headers, _, _ = await conn.request("GET", "/api/x", pass_token=token)
        assert status == 200 and _set_passes(headers) == []

    async def test_the_header_is_only_added_to_the_response_head_of_a_stream(self, env):
        _, token = await env.device()
        env.clock.now += 2 * DAY
        app = App(env.user)
        app.stream = True
        conn = await env.connect(app)
        status, headers, body, _ = await conn.request("GET", "/api/stream", pass_token=token)
        assert status == 200 and body == b"abc" and len(_set_passes(headers)) == 1


class TestRate:
    async def test_concurrent_requests_get_one_renewal(self, env):
        _, token = await env.device()
        env.clock.now += 2 * DAY
        app = App(env.user)
        app.gate = asyncio.Event()
        conn = await env.connect(app)
        pending = [
            asyncio.create_task(conn.request("GET", "/api/x", pass_token=token)) for _ in range(6)
        ]
        await asyncio.sleep(0.05)
        app.gate.set()
        answers = await asyncio.gather(*pending)
        assert [a[0] for a in answers] == [200] * 6
        assert sum(len(_set_passes(a[1])) for a in answers) == 1

    async def test_a_lost_renewal_is_retried_after_the_interval_only(self, env):
        device, token = await env.device()
        env.clock.now += 2 * DAY
        conn = await env.connect(App(env.user))
        _, first, _, _ = await conn.request("GET", "/api/x", pass_token=token)
        assert len(_set_passes(first)) == 1  # the browser never got this answer
        for _ in range(3):
            _, later, _, _ = await conn.request("GET", "/api/x", pass_token=token)
            assert _set_passes(later) == []
        remote._rt.renewal_attempts[device.id] -= remote.BROWSER_PASS_RENEW_RETRY_S
        _, again, _, _ = await conn.request("GET", "/api/x", pass_token=token)
        assert len(_set_passes(again)) == 1

    async def test_the_attempts_kept_in_memory_are_bounded(self, env, monkeypatch):
        monkeypatch.setattr(remote, "RENEWAL_ATTEMPTS_MAX", 2)
        tokens = [(await env.device())[1] for _ in range(4)]
        env.clock.now += 2 * DAY
        conn = await env.connect(App(env.user))
        for token in tokens:
            _, headers, _, _ = await conn.request("GET", "/api/x", pass_token=token)
            assert len(_set_passes(headers)) == 1
        assert len(remote._rt.renewal_attempts) == 2


class TestNothingRenewed:
    @pytest.mark.parametrize("status", [204, 201])
    async def test_success_statuses_renew(self, env, status):
        _, token = await env.device()
        env.clock.now += 2 * DAY
        app = App(env.user)
        app.status = status
        conn = await env.connect(app)
        _, headers, _, _ = await conn.request("POST", "/api/x", pass_token=token)
        assert len(_set_passes(headers)) == 1

    @pytest.mark.parametrize("status", [302, 304, 400, 401, 403, 404, 429, 500, 503])
    async def test_a_request_that_did_not_succeed(self, env, status):
        device, token = await env.device()
        before = (await RemoteRepository.get_device(device.id)).pass_expires_at
        env.clock.now += 2 * DAY
        app = App(env.user)
        app.status = status
        conn = await env.connect(app)
        answer, headers, _, _ = await conn.request("GET", "/api/x", pass_token=token)
        assert answer == status and _set_passes(headers) == []
        assert (await RemoteRepository.get_device(device.id)).pass_expires_at == before
        # It used up no attempt: the next good request renews at once.
        app.status = 200
        _, headers, _, _ = await conn.request("GET", "/api/x", pass_token=token)
        assert len(_set_passes(headers)) == 1

    async def test_a_request_of_nobody_signed_in(self, env):
        device, token = await env.device()
        before = (await RemoteRepository.get_device(device.id)).pass_expires_at
        env.clock.now += 2 * DAY
        conn = await env.connect(App(None))
        status, headers, _, _ = await conn.request("GET", "/api/health", pass_token=token)
        assert status == 200 and _set_passes(headers) == []
        assert (await RemoteRepository.get_device(device.id)).pass_expires_at == before

    async def test_a_response_outside_the_api_which_a_cache_may_keep(self, env):
        _, token = await env.device()
        env.clock.now += 2 * DAY
        conn = await env.connect(App(env.user))
        status, headers, _, _ = await conn.request("GET", "/assets/app.js", pass_token=token)
        assert status == 200 and _set_passes(headers) == []

    async def test_a_request_refused_by_the_instance(self, env):
        env.clock.now += 2 * DAY
        conn = await env.connect(App(env.user))
        status, headers, _, _ = await conn.request("GET", "/api/x", pass_token="nope")
        assert status == 401 and _set_passes(headers) == []

    async def test_a_device_revoked_while_the_request_runs(self, env):
        device, token = await env.device()
        before = (await RemoteRepository.get_device(device.id)).pass_expires_at
        env.clock.now += 2 * DAY
        app = App(env.user)

        async def revoke() -> None:
            await remote.revoke_device(device.id, SYSTEM_ACTOR, owner_id=None)

        app.during = revoke
        conn = await env.connect(app)
        status, headers, _, _ = await conn.request("DELETE", "/api/x", pass_token=token)
        assert status == 200 and _set_passes(headers) == []
        assert (await RemoteRepository.get_device(device.id)).pass_expires_at == before

    async def test_a_revoked_device_cannot_come_back(self, env):
        device, token = await env.device()
        env.clock.now += 2 * DAY
        await remote.revoke_device(device.id, SYSTEM_ACTOR, owner_id=None)
        conn = await env.connect(App(env.user))
        status, headers, _, _ = await conn.request("GET", "/api/x", pass_token=token)
        assert status == 401 and _set_passes(headers) == []

    async def test_every_device_revoked_while_the_request_runs(self, env):
        _, token = await env.device()
        env.clock.now += 2 * DAY
        app = App(env.user)

        async def revoke_all() -> None:
            await remote.revoke_all_devices(SYSTEM_ACTOR)

        app.during = revoke_all
        conn = await env.connect(app)
        status, headers, _, _ = await conn.request("POST", "/api/x", pass_token=token)
        assert status == 200 and _set_passes(headers) == []

    async def test_the_row_decides_when_a_revocation_lands_after_the_check(self, env, monkeypatch):
        """The last step refuses a device revoked between the check and the signature."""
        device, token = await env.device()
        env.clock.now += 2 * DAY
        real_sign = remote._sign

        async def revoke_then_sign(*a: Any, **k: Any) -> Any:
            await RemoteRepository.revoke_device(device.id, int(env.clock.now))
            return await real_sign(*a, **k)

        monkeypatch.setattr(remote, "_sign", revoke_then_sign)
        conn = await env.connect(App(env.user))
        status, headers, _, _ = await conn.request("GET", "/api/x", pass_token=token)
        assert status == 200 and _set_passes(headers) == []

    async def test_an_expired_pass_in_the_clock_skew_grace(self, env):
        """The verifier tolerates 60 s of clock skew; that is no second life."""
        _, token = await env.device()
        env.clock.now += 90 * DAY + 30
        conn = await env.connect(App(env.user))
        status, headers, _, _ = await conn.request("GET", "/api/x", pass_token=token)
        assert status == 200 and _set_passes(headers) == []

    async def test_an_expired_pass(self, env):
        _, token = await env.device()
        env.clock.now += 90 * DAY + 61
        conn = await env.connect(App(env.user))
        status, headers, _, _ = await conn.request("GET", "/api/x", pass_token=token)
        assert status == 401 and _set_passes(headers) == []


class TestRevocationAfterRenewal:
    async def test_a_revoked_device_stays_listed_until_its_renewed_pass_expires(self, env):
        """The revoked list keeps a device until ``pass_expires_at``: it must follow the
        renewal, or the relay would forget the revocation while the new pass still lives."""
        device, token = await env.device()
        env.clock.now += 60 * DAY
        conn = await env.connect(App(env.user))
        _, headers, _, _ = await conn.request("GET", "/api/x", pass_token=token)
        [renewed] = _set_passes(headers)
        await remote.revoke_device(device.id, SYSTEM_ACTOR, owner_id=None)
        await asyncio.wait_for(conn.next_control(), 5)
        env.clock.now += 80 * DAY  # past the first pass's expiry, not the renewed one's
        gate = await remote.gate_state()
        assert device.id in gate["revoked"]
        state = await remote.pass_state()
        assert verify_pass(renewed, "cookie", state, int(env.clock.now))[0] == "pass_revoked"

    async def test_extending_never_shortens_and_skips_a_revoked_row(self, env):
        device, _ = await env.device()
        stored = (await RemoteRepository.get_device(device.id)).pass_expires_at
        assert await RemoteRepository.extend_device_pass(device.id, stored - 10) is True
        assert (await RemoteRepository.get_device(device.id)).pass_expires_at == stored
        await RemoteRepository.revoke_device(device.id, T0)
        assert await RemoteRepository.extend_device_pass(device.id, stored + 10) is False
        assert (await RemoteRepository.get_device(device.id)).pass_expires_at == stored
        assert await RemoteRepository.extend_device_pass("unknown", stored + 10) is False


class TestHeaderContainment:
    async def test_the_app_cannot_set_the_header_on_http(self, env):
        _, token = await env.device()
        app = App(env.user)
        app.extra = [(SET_PASS.encode(), b"from-a-plugin"), (b"x-kept", b"yes")]
        conn = await env.connect(app)
        _, headers, _, _ = await conn.request("GET", "/api/x", pass_token=token)
        assert _set_passes(headers) == [] and ["x-kept", "yes"] in headers

    async def test_the_app_header_is_replaced_when_a_renewal_is_due(self, env):
        _, token = await env.device()
        env.clock.now += 2 * DAY
        app = App(env.user)
        app.extra = [(SET_PASS.encode(), b"from-a-plugin")]
        conn = await env.connect(app)
        _, headers, _, _ = await conn.request("GET", "/api/x", pass_token=token)
        [value] = _set_passes(headers)
        assert value != "from-a-plugin" and await _relay_accepts(token, value, int(env.clock.now))

    async def test_a_websocket_is_never_renewed(self, env):
        """The relay reads the header on HTTP streams only."""
        device, token = await env.device()
        before = (await RemoteRepository.get_device(device.id)).pass_expires_at
        env.clock.now += 2 * DAY
        app = App(env.user)
        app.extra = [(SET_PASS.encode(), b"from-a-plugin")]
        conn = await env.connect(app)
        await conn.send(rf.encode_json(rf.WS_OPEN, 1, conn.head("WS", "/api/ws", pass_token=token)))
        accept = await conn.frame(1)
        assert accept.type == rf.WS_ACCEPT
        assert _set_passes(json.loads(accept.payload)["headers"]) == []
        assert (await RemoteRepository.get_device(device.id)).pass_expires_at == before

    async def test_a_head_near_the_limit_stays_a_valid_head(self, env):
        _, token = await env.device()
        env.clock.now += 2 * DAY
        app = App(env.user)
        app.extra = [(b"x-big", b"y" * (rf.MAX_HEADER_BYTES - 1000))]
        conn = await env.connect(app)
        status, headers, _, _ = await conn.request("GET", "/api/x", pass_token=token)
        assert status == 200 and _set_passes(headers) == []


class TestFailureAndLogs:
    async def test_a_signing_failure_never_fails_the_request(self, env, monkeypatch, caplog):
        _, token = await env.device()
        env.clock.now += 2 * DAY

        async def boom(*_a: Any, **_k: Any) -> Any:
            raise RuntimeError(f"cannot sign {token}")

        monkeypatch.setattr(remote, "_sign", boom)
        conn = await env.connect(App(env.user))
        with caplog.at_level(logging.DEBUG):
            status, headers, _, _ = await conn.request("GET", "/api/x", pass_token=token)
        assert status == 200 and _set_passes(headers) == []
        assert "renewal failed" in caplog.text
        assert token not in caplog.text

    async def test_no_pass_in_the_logs(self, env, caplog):
        _, token = await env.device()
        env.clock.now += 2 * DAY
        conn = await env.connect(App(env.user))
        with caplog.at_level(logging.DEBUG):
            _, headers, _, _ = await conn.request("GET", "/api/x", pass_token=token)
        [renewed] = _set_passes(headers)
        assert token not in caplog.text and renewed not in caplog.text

    async def test_the_scope_never_holds_the_pass(self, env):
        _, token = await env.device()
        env.clock.now += 2 * DAY
        seen: list[dict[str, Any]] = []
        inner = App(env.user)

        async def app(scope: dict[str, Any], receive: Any, send: Any) -> None:
            seen.append(scope)
            await inner(scope, receive, send)

        conn = await env.connect(app)
        await conn.request("GET", "/api/x", pass_token=token)
        assert token not in repr(seen[0])


class TestDeletedAccount:
    async def test_a_device_of_a_deleted_account_stops_working(self, env):
        """Even with no revocation recorded (a row older than this rule, or a crash right
        after the deletion), and with another account signed in behind the edge lock."""
        bob = await UserRepository.create("bob", "member")
        owner, env.user = env.user, bob
        device, token = await env.device()
        env.clock.now += 2 * DAY
        await UserRepository.delete(bob.id)
        assert (await RemoteRepository.get_device(device.id)).revoked_at is None
        conn = await env.connect(App(owner))
        status, headers, _, _ = await conn.request("GET", "/api/x", pass_token=token)
        assert status == 401 and _set_passes(headers) == []
        stream = conn.new_stream()
        await conn.send(
            rf.encode_json(rf.WS_OPEN, stream, conn.head("WS", "/api/ws", pass_token=token))
        )
        accept = await conn.frame(stream)
        assert accept.type == rf.WS_ACCEPT and json.loads(accept.payload)["status"] == 401

    async def test_revoking_an_account_tells_the_relay(self, env):
        bob = await UserRepository.create("bob", "member")
        alice, env.user = env.user, bob
        device, _ = await env.device()
        spare, _ = await env.device()
        env.user = alice
        kept, kept_token = await env.device()
        conn = await env.connect(App(alice))
        await UserRepository.delete(bob.id)
        assert await remote.revoke_account_devices(bob.id, SYSTEM_ACTOR) is True
        gate = await conn.next_control()
        assert gate["op"] == "gate" and set(gate["revoked"]) == {device.id, spare.id}
        assert (await RemoteRepository.get_device(kept.id)).revoked_at is None
        status, *_ = await conn.request("GET", "/api/x", pass_token=kept_token)
        assert status == 200

    async def test_no_device_no_gate(self, env):
        conn = await env.connect(App(env.user))
        assert await remote.revoke_account_devices(env.user.id, SYSTEM_ACTOR) is False
        await asyncio.sleep(0.1)
        assert conn.control.empty()

    async def test_past_the_relay_limit_a_new_generation(self, env, monkeypatch):
        monkeypatch.setattr(remote, "REVOKED_MAX", 1)
        await env.device()
        await env.device()
        gen = (await RemoteRepository.get_state()).gate_gen
        await remote.revoke_account_devices(env.user.id, SYSTEM_ACTOR)
        assert (await RemoteRepository.get_state()).gate_gen == gen + 1

    async def test_a_device_approved_for_someone_follows_its_approver(self, env):
        """An approved device belongs to the approver, not to who signs in on it: deleting
        the approver cuts it, deleting the person who used it does not."""
        approver, guest = env.user, await UserRepository.create("guest", "member")
        state = await RemoteRepository.get_state()
        device, token = await remote._new_device(approver.id, "browser", "approval", None, state)
        env.clock.now += 2 * DAY
        app = App(guest)
        conn = await env.connect(app)
        _, headers, _, _ = await conn.request("GET", "/api/x", pass_token=token)
        [token] = _set_passes(headers)

        await UserRepository.delete(guest.id)
        await remote.revoke_account_devices(guest.id, SYSTEM_ACTOR)
        assert (await RemoteRepository.get_device(device.id)).revoked_at is None
        app.user = None  # the guest's session is gone with the account
        status, headers, _, _ = await conn.request("GET", "/api/x", pass_token=token)
        assert status == 200 and _set_passes(headers) == []

        await UserRepository.delete(approver.id)
        await remote.revoke_account_devices(approver.id, SYSTEM_ACTOR)
        assert (await RemoteRepository.get_device(device.id)).revoked_at is not None
        status, *_ = await conn.request("GET", "/api/x", pass_token=token)
        assert status == 401


class TestRealApp:
    """Through the real app and its auth gate: signed in by a real session or not."""

    pytestmark = [pytest.mark.real_auth, pytest.mark.usefixtures("fast_argon2")]

    async def test_only_a_signed_in_request_renews(self, env):
        from app.main import app
        from tests.auth_helpers import PASSWORD, create_user

        owner = await create_user("owner", PASSWORD, "admin")
        env.user = owner
        _, token = await env.device()
        # Sign in within the session's life: only the pass is old.
        env.clock.now += 2 * DAY
        conn = await env.connect(app)
        status, headers, body, _ = await conn.request("GET", "/api/auth/session", pass_token=token)
        assert status == 200 and json.loads(body)["authenticated"] is False
        assert _set_passes(headers) == []
        status, headers, _, _ = await conn.request("GET", "/api/contacts", pass_token=token)
        assert status == 401 and _set_passes(headers) == []
        status, headers, _, _ = await conn.request(
            "POST",
            "/api/auth/login",
            json.dumps({"username": "owner", "password": "wrong-password"}).encode(),
            headers=[["content-type", "application/json"], ["origin", f"https://{HOST}"]],
            pass_token=token,
        )
        assert status == 401 and _set_passes(headers) == []
        status, headers, body, _ = await conn.request(
            "POST",
            "/api/auth/login",
            json.dumps({"username": "owner", "password": PASSWORD}).encode(),
            headers=[["content-type", "application/json"], ["origin", f"https://{HOST}"]],
            pass_token=token,
        )
        assert status == 200, body
        assert _set_passes(headers) == []  # nobody was signed in when it arrived
        session = next(v for n, v in headers if v.startswith("__Host-meshloom_session="))
        assert session
        cookie = session.split(";", 1)[0]
        status, headers, body, _ = await conn.request(
            "GET", "/api/auth/session", headers=[["cookie", cookie]], pass_token=token
        )
        assert status == 200 and json.loads(body)["authenticated"] is True
        [renewed] = _set_passes(headers)
        assert await _relay_accepts(token, renewed, int(env.clock.now))

    async def test_the_delete_route_revokes_the_devices_of_the_account(self, env):
        from app.main import app
        from app.repository.auth import AuthAuditRepository
        from tests.auth_helpers import PASSWORD, create_user

        owner = await create_user("owner", PASSWORD, "admin")
        bob = await create_user("bob", PASSWORD, "member")
        env.user = owner
        _, token = await env.device()
        env.user = bob
        device, bob_token = await env.device()
        conn = await env.connect(app)
        same_origin = [["content-type", "application/json"], ["origin", f"https://{HOST}"]]
        status, headers, body, _ = await conn.request(
            "POST",
            "/api/auth/login",
            json.dumps({"username": "owner", "password": PASSWORD}).encode(),
            headers=same_origin,
            pass_token=token,
        )
        assert status == 200, body
        session = next(v for n, v in headers if v.startswith("__Host-meshloom_session="))
        cookie = ["cookie", session.split(";", 1)[0]]
        status, _, body, _ = await conn.request(
            "DELETE",
            f"/api/auth/users/{bob.id}",
            headers=[["origin", f"https://{HOST}"], cookie],
            pass_token=token,
        )
        assert status == 200, body
        assert (await RemoteRepository.get_device(device.id)).revoked_at is not None
        gate = await conn.next_control()
        assert gate["op"] == "gate" and device.id in gate["revoked"]
        status, *_ = await conn.request("GET", "/api/auth/session", pass_token=bob_token)
        assert status == 401
        rows = await AuthAuditRepository.list_recent(limit=50)
        revoked = [row.target for row in rows if row.action == "remote.device_revoked"]
        assert revoked == [f"device:{device.id}"]
