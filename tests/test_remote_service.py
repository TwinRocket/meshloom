"""Meshloom Remote on the instance (MSHL-79): registration against a fake registry,
QR enrolment and access requests against a fake relay and edge, revocation, rotation,
restore, the host guard and the ``/api/remote`` routes.

The fake registry answers through the egress guard (``tests/share_fakes.py``: DNS
table and ``httpx.AsyncClient`` recorder). The fake edge knows only what the relay
knows: the key the instance registered (read from its JWT) and the last ``gate``
state the instance sent; it checks passes with the contract verifier.
"""

from __future__ import annotations

import base64
import json
import logging
from pathlib import Path
from typing import Any

import httpx
import pytest

from app.auth.audit import SYSTEM_ACTOR
from app.middleware import host_guard
from app.repository.auth import AuthAuditRepository, UserRepository
from app.repository.remote import RemoteRepository
from app.services import remote, remote_passes
from app.services.backup_sections import run_on_restore_hooks
from app.services.instance_identity import REMOTE_SERVICE, fingerprint, instance_public_key
from app.services.remote_passes import PassState, verify_pass
from tests.share_fakes import PUBLIC_IP, Dns, Recorder, json_response


@pytest.fixture(autouse=True)
def _community_on(community_on):
    """Links & Files and Remote need Community on (MSHL-445)."""


_RealAsyncClient = httpx.AsyncClient
VECTOR_PASSES = json.loads(
    (Path(__file__).parent / "fixtures" / "remote_frames_vectors.json").read_text()
)["passes"]["cases"]
SLUG = "k3xwq2m7ta"
SLUG2 = "abcdefgh23"


def _claims(token: str) -> dict[str, Any]:
    part = token.split(".")[1]
    return json.loads(base64.urlsafe_b64decode(part + "=" * (-len(part) % 4)))


class FakeRelay:
    """Registry routes plus the relay side of the CONTROL channel."""

    def __init__(self) -> None:
        self.registered: dict[str, str] = {}  # publicKey -> slug
        self.jtis: list[str] = []
        self.bodies: list[dict[str, Any]] = []
        self.sent: list[dict[str, Any]] = []
        self.gate: dict[str, Any] | None = None
        self.next_slugs = [SLUG, SLUG2]
        self.fail_with: httpx.Response | None = None
        self.alert = "none"

    def handle(self, request: httpx.Request) -> httpx.Response:
        token = request.headers["authorization"].removeprefix("Bearer ")
        claims = _claims(token)
        assert claims["aud"] == "relay.meshloom.link"
        self.jtis.append(claims["jti"])
        if self.fail_with is not None:
            return self.fail_with
        key = claims["publicKey"]
        if request.method == "POST" and request.url.path == "/v1/instances":
            self.bodies.append(json.loads(request.content))
            status = 200 if key in self.registered else 201
            slug = self.registered.setdefault(key, self.next_slugs.pop(0))
            return json_response(
                status,
                {
                    "slug": slug,
                    "hostname": f"{slug}.meshloom.link",
                    "relay_url": "wss://relay.meshloom.link/v1/connect",
                },
            )
        if request.method == "DELETE" and request.url.path == "/v1/instances/self":
            if self.registered.pop(key, None) is None:
                return json_response(404, {"code": "instance_unknown", "detail": "x"})
            return httpx.Response(204)
        if request.method == "GET" and request.url.path == "/v1/instances/self":
            return json_response(200, {"status": "active", "alert_email": {"state": self.alert}})
        if request.url.path == "/v1/instances/self/alert-email":
            if request.method == "PUT":
                self.alert = "pending"
                return json_response(202, {"alert_email": {"state": "pending"}})
            if request.method == "DELETE":
                self.alert = "none"
                return httpx.Response(204)
        return json_response(404, {"code": "invalid_request", "detail": "x"})

    async def sender(self, frame: dict[str, Any]) -> bool:
        self.sent.append(frame)
        if frame["op"] == "gate":
            self.gate = {"gen": frame["gen"], "revoked": list(frame["revoked"])}
        return True

    def edge(self, token: str, slot: str) -> str:
        """The relay's verdict with only what the relay holds."""
        key_hex = next(iter(self.registered))
        slug = self.registered[key_hex]
        gate = self.gate or {"gen": 0, "revoked": []}
        state = PassState(
            slug=slug,
            public_key=bytes.fromhex(key_hex),
            gen=gate["gen"],
            revoked=frozenset(gate["revoked"]),
        )
        return verify_pass(token, slot, state, int(remote._rt.clock()))[0]  # noqa: SLF001


@pytest.fixture
def relay(test_db, monkeypatch) -> FakeRelay:
    fake = FakeRelay()
    Dns(monkeypatch, {remote.RELAY_HOST: [PUBLIC_IP]})
    fake.recorder = Recorder(monkeypatch, fake.handle)  # type: ignore[attr-defined]
    return fake


@pytest.fixture
async def admin(test_db):
    return await UserRepository.create("test-admin", "admin")


@pytest.fixture
async def client():
    from app.main import app

    async with _RealAsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as c:
        yield c


class TestRegistration:
    async def test_enable_registers_and_stores_slug_and_hostname(self, relay, admin):
        state = await remote.enable(SYSTEM_ACTOR)
        assert state.enabled and state.slug == SLUG
        assert state.hostname == f"{SLUG}.meshloom.link"
        assert state.relay_url == "wss://relay.meshloom.link/v1/connect"
        assert host_guard.host_allowed(f"{SLUG}.meshloom.link")
        body = relay.bodies[0]
        assert set(body) == {"version", "install_kind", "plugins"}
        assert set(body["plugins"]) == {"enabled", "isolation"}
        public_key = await instance_public_key(REMOTE_SERVICE)
        assert public_key is not None and public_key.hex().upper() in relay.registered
        # Idempotent: a second registration keeps the slug; every token is new.
        await remote.register(SYSTEM_ACTOR)
        assert (await RemoteRepository.get_state()).slug == SLUG
        assert len(relay.jtis) == len(set(relay.jtis)) == 2
        assert all(len(jti) == 22 for jti in relay.jtis)

    async def test_registry_refusal_leaves_it_unregistered(self, relay, admin):
        relay.fail_with = json_response(503, {"code": "registration_paused", "detail": "x"})
        state = await remote.enable(SYSTEM_ACTOR)
        assert state.enabled and not state.slug
        assert state.last_error == "registration_paused"
        assert not host_guard.host_allowed(f"{SLUG}.meshloom.link")
        relay.fail_with = None
        state = await remote.enable(SYSTEM_ACTOR)  # enabling again retries
        assert state.slug == SLUG and state.last_error is None

    async def test_a_non_contract_answer_is_unavailable(self, relay, admin):
        relay.fail_with = httpx.Response(200, content=b"<html>")
        state = await remote.enable(SYSTEM_ACTOR)
        assert not state.slug and state.last_error == "remote_unavailable"

    async def test_disable_withdraws_the_hostname_and_keeps_the_registration(self, relay, admin):
        await remote.enable(SYSTEM_ACTOR)
        state = await remote.disable(SYSTEM_ACTOR)
        assert not state.enabled and state.slug == SLUG
        assert not host_guard.host_allowed(f"{SLUG}.meshloom.link")
        await remote.load()
        assert not host_guard.host_allowed(f"{SLUG}.meshloom.link")

    async def test_unregister_keeps_the_key(self, relay, admin):
        await remote.enable(SYSTEM_ACTOR)
        before = await fingerprint(REMOTE_SERVICE)
        state = await remote.unregister(SYSTEM_ACTOR)
        assert not state.enabled and not state.slug
        assert relay.registered == {}
        assert await fingerprint(REMOTE_SERVICE) == before
        with pytest.raises(remote.RemoteError) as exc:
            await remote.unregister(SYSTEM_ACTOR)
        assert exc.value.code == "remote_disabled"

    async def test_rotate_replaces_key_slug_and_devices(self, relay, admin):
        await remote.enable(SYSTEM_ACTOR)
        await remote.issue_client_pass(admin, "script", SYSTEM_ACTOR)
        old_key = (await instance_public_key(REMOTE_SERVICE)).hex().upper()
        state = await remote.rotate(SYSTEM_ACTOR)
        new_key = (await instance_public_key(REMOTE_SERVICE)).hex().upper()
        assert new_key != old_key
        assert old_key not in relay.registered  # deleted with the old key
        assert relay.registered == {new_key: SLUG2}
        assert state.slug == SLUG2 and state.gate_gen == 0
        assert await RemoteRepository.list_devices() == []
        assert host_guard.host_allowed(f"{SLUG2}.meshloom.link")
        assert not host_guard.host_allowed(f"{SLUG}.meshloom.link")

    async def test_restore_regenerates_the_key_and_unregisters(self, relay, admin):
        await remote.enable(SYSTEM_ACTOR)
        await remote.issue_client_pass(admin, None, SYSTEM_ACTOR)
        before = await fingerprint(REMOTE_SERVICE)
        await run_on_restore_hooks("json")
        state = await RemoteRepository.get_state()
        assert not state.enabled and not state.slug and not state.hostname
        assert await fingerprint(REMOTE_SERVICE) != before
        assert await RemoteRepository.list_devices() == []
        assert not host_guard.host_allowed(f"{SLUG}.meshloom.link")


class TestEnrolment:
    async def _registered(self, relay) -> None:
        await remote.enable(SYSTEM_ACTOR)
        remote.set_relay_link(relay.sender, "connected")

    async def test_qr_enrolment_yields_a_pass_the_edge_accepts(self, relay, admin):
        await self._registered(relay)
        ticket = await remote.issue_enroll_token(admin, SYSTEM_ACTOR)
        prefix = f"https://{SLUG}.meshloom.link/_meshloom/enroll#token="
        assert ticket.url.startswith(prefix)
        token = ticket.url.removeprefix(prefix)
        claims = _claims(token)
        assert claims["kind"] == "enroll" and claims["exp"] - claims["iat"] == 900
        # The relay checks the token (slot enroll), then asks the instance.
        assert relay.edge(token, "enroll") == "accept"
        reply = await remote.handle_control(
            {"op": "enroll", "request_id": "R" * 22, "token": token}
        )
        assert reply is not None and reply["op"] == "pass" and reply["request_id"] == "R" * 22
        browser_pass = reply["pass"]
        assert relay.edge(browser_pass, "cookie") == "accept"
        assert _claims(browser_pass)["uid"] == str(admin.id)
        assert (await remote.verify_device_pass(browser_pass, "cookie"))[0] == "accept"
        # Single use.
        again = await remote.handle_control(
            {"op": "enroll", "request_id": "S" * 22, "token": token}
        )
        assert again == {"op": "pass", "request_id": "S" * 22, "error": "enroll_refused"}

    async def test_unknown_or_foreign_tokens_are_refused(self, relay, admin):
        await self._registered(relay)
        reply = await remote.answer_enroll("R" * 22, "not.a.token")
        assert reply["error"] == "enroll_refused"
        case = next(c for c in VECTOR_PASSES if c["id"] == "enroll_valid")
        reply = await remote.answer_enroll("R" * 22, case["token"])
        assert reply["error"] == "enroll_unknown"

    async def test_enrolment_needs_registration(self, relay, admin):
        with pytest.raises(remote.RemoteError) as exc:
            await remote.issue_enroll_token(admin, SYSTEM_ACTOR)
        assert exc.value.code == "remote_disabled"

    async def test_access_request_approval_yields_a_pass_the_edge_accepts(self, relay, admin):
        await self._registered(relay)
        await remote.handle_control(
            {
                "op": "access_request",
                "request_id": "Q" * 22,
                "code": "7K3QM-X9TZ2",
                "user_agent": "Firefox",
                "created_at": 1,
            }
        )
        assert [r.code for r in remote.pending_access_requests()] == ["7K3QM-X9TZ2"]
        with pytest.raises(remote.RemoteError):
            await remote.approve_access("7K3QM-X9TZ3", admin, None, SYSTEM_ACTOR)
        device = await remote.approve_access("7k3qm x9tz2", admin, "phone", SYSTEM_ACTOR)
        grant = relay.sent[-1]
        assert grant["op"] == "access_grant" and grant["request_id"] == "Q" * 22
        assert relay.edge(grant["pass"], "cookie") == "accept"
        assert _claims(grant["pass"])["sub"] == device.id
        assert remote.pending_access_requests() == []

    async def test_approval_without_a_relay_link_creates_nothing(self, relay, admin):
        await remote.enable(SYSTEM_ACTOR)
        remote.receive_access_request(
            {"request_id": "Q" * 22, "code": "7K3QM-X9TZ2", "user_agent": "", "created_at": 1}
        )
        with pytest.raises(remote.RemoteError) as exc:
            await remote.approve_access("7K3QM-X9TZ2", admin, None, SYSTEM_ACTOR)
        assert exc.value.code == "remote_offline"
        assert await RemoteRepository.list_devices() == []

    async def test_deny_and_the_pending_cap(self, relay, admin):
        await self._registered(relay)
        for i in range(7):
            remote.receive_access_request(
                {
                    "request_id": chr(65 + i) * 22,
                    "code": "7K3QM-X9TZ2",
                    "user_agent": "x" * 400,
                    "created_at": i,
                }
            )
        pending = remote.pending_access_requests()
        assert len(pending) == remote.ACCESS_REQUESTS_PENDING_MAX
        assert pending[0].request_id == "C" * 22  # the two oldest went first
        assert len(pending[0].user_agent) == 256
        await remote.deny_access("C" * 22, SYSTEM_ACTOR)
        assert relay.sent[-1] == {"op": "access_deny", "request_id": "C" * 22}

    async def test_requests_expire(self, relay, admin):
        now = [1_800_000_000.0]
        remote.reset_remote_for_tests(clock=lambda: now[0])
        remote.receive_access_request(
            {"request_id": "Q" * 22, "code": "7K3QM-X9TZ2", "user_agent": "", "created_at": 1}
        )
        now[0] += remote.ACCESS_REQUEST_TTL_S + 1
        assert remote.pending_access_requests() == []


class TestRevocation:
    async def test_revoke_and_revoke_all_bump_the_state_sent_to_the_relay(self, relay, admin):
        await remote.enable(SYSTEM_ACTOR)
        remote.set_relay_link(relay.sender, "connected")
        first, first_pass = await remote.issue_client_pass(admin, "a", SYSTEM_ACTOR)
        second, second_pass = await remote.issue_client_pass(admin, "b", SYSTEM_ACTOR)
        assert relay.edge(first_pass, "header") == "accept"

        assert await remote.revoke_device(first.id, SYSTEM_ACTOR, owner_id=None) is True
        assert relay.gate == {"gen": 0, "revoked": [first.id]}
        assert relay.edge(first_pass, "header") == "pass_revoked"
        assert relay.edge(second_pass, "header") == "accept"
        assert (await remote.verify_device_pass(first_pass, "header"))[0] == "pass_revoked"

        assert await remote.revoke_all_devices(SYSTEM_ACTOR) is True
        assert relay.gate == {"gen": 1, "revoked": []}
        assert relay.edge(second_pass, "header") == "pass_generation"
        assert await remote.gate_state() == {"gen": 1, "revoked": []}

    async def test_a_user_revokes_only_their_own_devices(self, relay, admin):
        await remote.enable(SYSTEM_ACTOR)
        device, _ = await remote.issue_client_pass(admin, None, SYSTEM_ACTOR)
        with pytest.raises(remote.RemoteError) as exc:
            await remote.revoke_device(device.id, SYSTEM_ACTOR, owner_id=admin.id + 1)
        assert exc.value.code == "remote_device_not_found"

    async def test_without_a_link_the_hello_carries_the_state(self, relay, admin):
        await remote.enable(SYSTEM_ACTOR)
        device, _ = await remote.issue_client_pass(admin, None, SYSTEM_ACTOR)
        assert await remote.revoke_device(device.id, SYSTEM_ACTOR, owner_id=None) is False
        assert await remote.gate_state() == {"gen": 0, "revoked": [device.id]}


class TestRoutes:
    async def test_state_and_lifecycle(self, relay, admin, client):
        body = (await client.get("/api/remote")).json()
        assert body["state"] == "disabled" and body["key_fingerprint"] is None
        assert relay.recorder.requests == []
        body = (await client.post("/api/remote/enable")).json()
        assert body["state"] == "registered" and body["hostname"] == f"{SLUG}.meshloom.link"
        assert body["key_fingerprint"] == await fingerprint(REMOTE_SERVICE)
        response = await client.post("/api/remote/enroll")
        assert response.status_code == 200 and "#token=" in response.json()["url"]
        assert response.headers["cache-control"] == "no-store"
        response = await client.post("/api/remote/devices/client", json={"label": "cron"})
        assert response.status_code == 200
        device_id = response.json()["device"]["id"]
        assert [d["id"] for d in (await client.get("/api/remote/devices")).json()] == [device_id]
        response = await client.delete(f"/api/remote/devices/{device_id}")
        assert response.json() == {"gate_pushed": False}
        assert (await client.get("/api/remote")).json()["gate"] == {"gen": 0, "revoked": 1}
        assert (await client.post("/api/remote/devices/revoke-all")).status_code == 200
        body = (await client.post("/api/remote/rotate")).json()
        assert body["slug"] == SLUG2 and body["devices"] == []
        body = (await client.post("/api/remote/disable")).json()
        assert body["state"] == "disabled"
        response = await client.delete("/api/remote")
        assert response.status_code == 409
        assert response.json()["detail"]["code"] == "remote_disabled"

    async def test_enrolment_is_refused_through_the_relay(self, relay, admin, monkeypatch):
        from app.routers import remote as remote_router

        monkeypatch.setattr(remote_router, "request_via", lambda scope: "relay")
        from app.main import app

        await remote.enable(SYSTEM_ACTOR)
        async with _RealAsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as c:
            response = await c.post("/api/remote/enroll")
        assert response.status_code == 403
        assert response.json()["detail"]["code"] == "remote_lan_only"

    async def test_member_sees_only_their_devices_and_no_admin_routes(
        self, relay, admin, client, sign_in_as
    ):
        await remote.enable(SYSTEM_ACTOR)
        await remote.issue_client_pass(admin, None, SYSTEM_ACTOR)
        sign_in_as("member")
        assert (await client.get("/api/remote")).json()["devices"] == []
        assert (await client.post("/api/remote/enable")).status_code == 403
        assert (await client.post("/api/remote/devices/revoke-all")).status_code == 403
        sign_in_as("kiosk")
        assert (await client.get("/api/remote")).status_code == 403


ALERT_ADDRESS = "someone.private@example.org"


class TestAlertEmail:
    async def test_set_read_and_clear_through_the_routes(self, relay, admin, client):
        await remote.enable(SYSTEM_ACTOR)
        relay.recorder.requests.clear()
        response = await client.get("/api/remote/alert-email")
        assert response.status_code == 200 and response.json() == {"state": "none"}
        assert response.headers["cache-control"] == "no-store"

        response = await client.put(
            "/api/remote/alert-email",
            json={"email": f"  {ALERT_ADDRESS} "},
            headers={"Accept-Language": "fr-FR,fr;q=0.9,en;q=0.8"},
        )
        assert response.status_code == 200 and response.json() == {"state": "pending"}
        sent = relay.recorder.requests[-1]
        assert (sent.method, sent.headers["host"]) == ("PUT", remote.RELAY_HOST)
        assert sent.url.path == "/v1/instances/self/alert-email"
        assert json.loads(sent.content) == {"email": ALERT_ADDRESS}
        assert sent.headers["accept-language"] == "fr"
        assert ALERT_ADDRESS not in str(sent.url)
        assert (await client.get("/api/remote/alert-email")).json() == {"state": "pending"}

        response = await client.delete("/api/remote/alert-email")
        assert response.status_code == 200 and response.json() == {"state": "none"}
        assert relay.recorder.requests[-1].method == "DELETE"
        assert relay.recorder.requests[-1].url.path == "/v1/instances/self/alert-email"

    @pytest.mark.parametrize(
        "email",
        [
            "",
            "nope",
            "a@b",
            "a b@example.org",
            "<a@example.org>",
            "a@@example.org",
            "x" * 65 + "@e.org",
        ]
        + ["a@" + "e" * 250 + ".org"],
    )
    async def test_a_bad_address_never_reaches_the_relay(self, relay, admin, client, email):
        await remote.enable(SYSTEM_ACTOR)
        relay.recorder.requests.clear()
        response = await client.put("/api/remote/alert-email", json={"email": email})
        assert response.status_code == 400
        assert response.json()["detail"]["code"] == "remote_alert_email_invalid"
        assert relay.recorder.requests == []

    @pytest.mark.parametrize(
        ("status", "body", "code"),
        [
            (400, {"code": "invalid_request"}, "remote_alert_email_invalid"),
            (403, {"code": "instance_frozen"}, "remote_alert_email_frozen"),
            (503, {"code": "registration_paused"}, "remote_alert_email_paused"),
            (404, {"code": "instance_unknown"}, "remote_not_registered"),
            (403, {"code": "instance_banned"}, "remote_registry_error"),
            (403, {"code": "instance_retired"}, "remote_registry_error"),
            (500, {}, "remote_unavailable"),
        ],
    )
    async def test_relay_refusals_become_short_codes(
        self, relay, admin, client, status, body, code
    ):
        await remote.enable(SYSTEM_ACTOR)
        relay.fail_with = json_response(status, {**body, "detail": "x"})
        for response in (
            await client.put("/api/remote/alert-email", json={"email": ALERT_ADDRESS}),
            await client.delete("/api/remote/alert-email"),
        ):
            assert response.json()["detail"]["code"] == code

    async def test_rate_limit_carries_retry_after(self, relay, admin, client):
        await remote.enable(SYSTEM_ACTOR)
        relay.fail_with = httpx.Response(
            429, json={"code": "rate_limited"}, headers={"Retry-After": "3600"}
        )
        response = await client.put("/api/remote/alert-email", json={"email": ALERT_ADDRESS})
        assert response.status_code == 429
        assert response.json()["detail"]["code"] == "remote_rate_limited"
        assert response.json()["detail"]["params"]["retry_after"] == 3600

    async def test_an_unexpected_answer_is_not_a_state(self, relay, admin, client):
        await remote.enable(SYSTEM_ACTOR)
        for body in ({}, {"alert_email": "none"}, {"alert_email": {"state": ["none"]}}):
            relay.fail_with = json_response(200, body)
            response = await client.get("/api/remote/alert-email")
            assert response.json()["detail"]["code"] == "remote_unavailable"

    async def test_a_network_failure_is_a_coded_error(self, relay, admin, client):
        await remote.enable(SYSTEM_ACTOR)

        def fail(request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("down")

        relay.recorder.handler = fail
        response = await client.get("/api/remote/alert-email")
        assert response.status_code == 502
        assert response.json()["detail"]["code"] == "remote_unavailable"

    async def test_off_or_unregistered_sends_nothing(self, relay, admin, client):
        for method in ("get", "delete"):
            response = await getattr(client, method)("/api/remote/alert-email")
            assert response.json()["detail"]["code"] == "remote_disabled"
        await RemoteRepository.update_state(enabled=True)
        response = await client.put("/api/remote/alert-email", json={"email": ALERT_ADDRESS})
        assert response.json()["detail"]["code"] == "remote_not_registered"
        assert relay.recorder.requests == []

    async def test_rotate_removes_the_address_first_while_the_old_key_signs(self, relay, admin):
        await remote.enable(SYSTEM_ACTOR)
        relay.recorder.requests.clear()
        await remote.rotate(SYSTEM_ACTOR)
        calls = [(r.method, r.url.path) for r in relay.recorder.requests]
        assert calls[:2] == [
            ("DELETE", "/v1/instances/self/alert-email"),
            ("DELETE", "/v1/instances/self"),
        ]

    async def test_a_refused_removal_never_blocks_the_rotation(self, relay, admin):
        await remote.enable(SYSTEM_ACTOR)
        real = relay.handle

        def frozen(request: httpx.Request) -> httpx.Response:
            if request.url.path == "/v1/instances/self/alert-email":
                return json_response(403, {"code": "instance_frozen", "detail": "x"})
            return real(request)

        relay.recorder.handler = frozen
        state = await remote.rotate(SYSTEM_ACTOR)
        assert state.slug == SLUG2

    async def test_rotate_with_remote_off_sends_nothing(self, relay, admin):
        await remote.enable(SYSTEM_ACTOR)
        await remote.disable(SYSTEM_ACTOR)
        relay.recorder.requests.clear()
        await remote.rotate(SYSTEM_ACTOR)
        assert relay.recorder.requests == []

    async def test_administrators_only(self, relay, admin, client, sign_in_as):
        await remote.enable(SYSTEM_ACTOR)
        relay.recorder.requests.clear()
        for role in ("member", "kiosk"):
            sign_in_as(role)
            assert (await client.get("/api/remote/alert-email")).status_code == 403
            response = await client.put("/api/remote/alert-email", json={"email": ALERT_ADDRESS})
            assert response.status_code == 403
            assert (await client.delete("/api/remote/alert-email")).status_code == 403
        assert relay.recorder.requests == []

    async def test_the_address_stays_out_of_logs_and_audit(self, relay, admin, client, caplog):
        caplog.set_level(logging.DEBUG)
        await remote.enable(SYSTEM_ACTOR)
        await client.put("/api/remote/alert-email", json={"email": ALERT_ADDRESS})
        relay.fail_with = json_response(403, {"code": "instance_frozen", "detail": "x"})
        await client.put("/api/remote/alert-email", json={"email": ALERT_ADDRESS})
        relay.fail_with = None
        await client.delete("/api/remote/alert-email")
        assert ALERT_ADDRESS not in caplog.text
        assert ALERT_ADDRESS.split("@")[0] not in caplog.text
        events = [
            row.model_dump()
            for row in await AuthAuditRepository.list_recent(limit=50)
            if row.action.startswith("remote.alert")
        ]
        assert sorted((row["action"], row["outcome"]) for row in events) == [
            ("remote.alert_email_cleared", "success"),
            ("remote.alert_email_set", "failure"),
            ("remote.alert_email_set", "success"),
        ]
        assert any(row["details"] == {"error": "remote_alert_email_frozen"} for row in events)
        assert ALERT_ADDRESS not in str(events)


class TestOtherControlOps:
    async def test_status_updates_the_name_and_unknown_ops_are_ignored(self, relay, admin):
        await remote.enable(SYSTEM_ACTOR)
        await remote.handle_control(
            {"op": "status", "slug": SLUG2, "hostname": f"{SLUG2}.meshloom.link"}
        )
        assert (await RemoteRepository.get_state()).slug == SLUG2
        assert host_guard.host_allowed(f"{SLUG2}.meshloom.link")
        assert await remote.handle_control({"op": "whatever"}) is None
        await remote.handle_control({"op": "status", "slug": "BAD", "hostname": "evil.example"})
        assert (await RemoteRepository.get_state()).slug == SLUG2

    def test_instance_facts_follow_the_hello_enums(self):
        info = remote.instance_info()
        assert info["install_kind"] in ("package", "compose", "addon", "container", "source")
        plugins = remote.plugins_info()
        assert isinstance(plugins["enabled"], bool)
        assert plugins["isolation"] in ("none", "setuid", "bwrap", None)
        assert remote_passes.GATE_COOKIE == "__Host-mlgate"
