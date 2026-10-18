"""Device pairing (MSHL-71): codes, the QR payload, redemption for a device token,
single use, expiry, revocation, ingress refusal, login delays, audit, and the
contract document checked against the routes."""

import asyncio
import re
import time
from pathlib import Path

import pytest

from app.auth import pairing, ratelimit
from app.auth.permissions import permissions_of
from app.auth.providers import ingress
from app.auth.tokens import hash_token
from app.repository.auth import (
    MAX_PENDING_PAIRINGS_PER_USER,
    ApiTokenRepository,
    AuthAuditRepository,
    AuthSettingsRepository,
    PairingCodeRepository,
    UserRepository,
)
from app.routers import auth_pairing
from tests.auth_helpers import create_user, login, make_client
from tests.test_auth_tokens import _upgrade, bearer

pytestmark = [pytest.mark.real_auth, pytest.mark.usefixtures("test_db", "fast_argon2")]

CONTRACT = Path(__file__).resolve().parents[1] / "docs" / "dev" / "device-pairing.md"
DEVICE = ("192.168.1.50", 41000)


async def _create(client, scopes, **extra):
    return await client.post(
        "/api/auth/pairings", json={"label": "Nadia's phone", "scopes": scopes, **extra}
    )


async def _pairing(scopes, username="owner", role="admin", base_url="http://test", **extra):
    """An account (with an admin beside it) and a code it created through the API."""
    if username != "owner":
        await create_user("owner")
    user = await create_user(username, role=role)
    async with make_client(base_url=base_url) as client:
        await login(client, username)
        response = await _create(client, scopes, **extra)
    assert response.status_code == 201, response.text
    return user, response.json()


async def _pair(code, device_name="Pixel 8", client=DEVICE, **extra):
    async with make_client(client=client) as device:
        return await device.post(
            "/api/auth/pair", json={"code": code, "device_name": device_name, **extra}
        )


async def _audit(action: str):
    return [row for row in await AuthAuditRepository.list_recent(limit=200) if row.action == action]


def _code(response) -> str:
    return response.json()["detail"]["code"]


# ---------------------------------------------------------------- creation


async def test_creation_returns_the_code_once_its_qr_payload_and_stores_only_its_hash(test_db):
    user, body = await _pairing(["read", "send"])
    code = body["code"]
    assert code.startswith("mlp_") and len(code) == 4 + 22
    assert body["scopes"] == ["read", "send"] and body["created_by"] == user.id
    assert body["expires_at"] - int(time.time()) in range(pairing.DEFAULT_TTL_S - 5, 301)

    payload = pairing.decode_qr_payload(body["qr_payload"])
    assert body["qr_payload"].startswith("meshloom://pair?d=")
    assert "=" not in body["qr_payload"].split("?d=", 1)[1]
    assert list(payload) == ["v", "urls", "code"]
    assert payload == {"v": 1, "urls": body["urls"], "code": code}

    async with test_db.readonly() as conn:
        async with conn.execute("SELECT * FROM pairing_codes") as cursor:
            stored = [tuple(r) for r in await cursor.fetchall()]
    assert len(stored) == 1 and hash_token(code) in stored[0]
    assert all(code not in str(value) for value in stored[0])

    async with make_client() as client:
        await login(client)
        listed = (await client.get("/api/auth/pairings")).json()
    assert [p["id"] for p in listed] == [body["id"]]
    assert "code" not in listed[0] and "qr_payload" not in listed[0]

    (created,) = await _audit("pairing.created")
    assert created.outcome == "success" and created.target == f"pairing:{body['id']}"
    assert created.details["scopes"] == ["read", "send"]
    assert code not in str(created.model_dump())


async def test_urls_are_the_public_addresses_then_the_lan_address_of_the_creator():
    await AuthSettingsRepository.update(public_urls=["https://mesh.example.org"])
    _, body = await _pairing(["read"], base_url="http://192.168.1.10:8000")
    assert body["urls"] == ["https://mesh.example.org", "http://192.168.1.10:8000"]


@pytest.mark.parametrize("base_url", ["http://localhost:8000", "http://127.0.0.1:8000"])
async def test_a_loopback_address_is_no_candidate(base_url):
    _, body = await _pairing(["read"], base_url=base_url)
    assert body["urls"] == []


async def test_a_host_that_does_not_name_the_instance_is_no_candidate():
    _, body = await _pairing(["read"], base_url="http://evil.example.com")
    assert body["urls"] == []


async def test_a_member_cannot_bind_a_scope_beyond_its_role():
    await create_user("owner")
    await create_user("nadia", role="member")
    async with make_client() as client:
        await login(client, "nadia")
        response = await _create(client, ["read", "repeater_admin"])
    assert response.status_code == 400 and _code(response) == "auth_token_scope"
    assert response.json()["detail"]["params"] == {"scope": "repeater_admin"}
    assert await PairingCodeRepository.list_pending() == []
    (refused,) = await _audit("pairing.created")
    assert refused.outcome == "failure" and refused.details["reason"] == "token_scope"


async def test_a_viewer_and_a_token_cannot_create_codes():
    await create_user("owner")
    await create_user("vic", role="viewer")
    async with make_client() as client:
        await login(client, "vic")
        response = await _create(client, ["read"])
    assert response.status_code == 403 and _code(response) == "auth_forbidden"

    owner = await UserRepository.get_by_username("owner")
    assert owner is not None
    from app.auth.providers import token as token_provider

    _, token = await token_provider.issue_token(owner, "script", ["admin"])
    async with make_client() as client:
        response = await client.post(
            "/api/auth/pairings", json={"label": "x", "scopes": ["read"]}, headers=bearer(token)
        )
    assert response.status_code == 403


@pytest.mark.parametrize(
    "body",
    [
        {"label": "x", "scopes": []},
        {"label": "x", "scopes": ["everything"]},
        {"label": "   ", "scopes": ["read"]},
        {"label": "x", "scopes": ["read"], "ttl": pairing.MAX_TTL_S + 1},
        {"label": "x", "scopes": ["read"], "ttl": 0},
    ],
)
async def test_invalid_bodies_are_422(body):
    await create_user()
    async with make_client() as client:
        await login(client)
        response = await client.post("/api/auth/pairings", json=body)
    assert response.status_code == 422


async def test_the_pending_code_cap():
    await create_user()
    async with make_client() as client:
        await login(client)
        for _ in range(MAX_PENDING_PAIRINGS_PER_USER):
            assert (await _create(client, ["read"])).status_code == 201
        response = await _create(client, ["read"])
    assert response.status_code == 409 and _code(response) == "auth_pairing_limit"


# ---------------------------------------------------------------- redemption


async def test_a_code_works_once_for_a_token_with_exactly_its_scopes_and_its_creator():
    await AuthSettingsRepository.update(public_urls=["https://mesh.example.org"])
    member, body = await _pairing(["read", "send"], username="nadia", role="member")
    response = await _pair(body["code"], extensions={"meshenger": {"v": 1, "key": "AAAA"}})
    assert response.status_code == 201, response.text
    paired = response.json()
    assert paired["scopes"] == ["read", "send"]
    assert paired["user"] == {"id": member.id, "username": "nadia", "display_name": ""}
    assert paired["urls"] == body["urls"] == ["https://mesh.example.org"]
    secret = paired["token"]
    assert secret.startswith("mlt_")

    row = await ApiTokenRepository.get(paired["token_id"])
    assert row is not None and row.user_id == member.id and row.origin == "pairing"
    assert row.scopes == ["read", "send"] and row.name == "Pixel 8" and row.expires_at is None
    stored = await PairingCodeRepository.get(body["id"])
    assert stored is not None and stored.used_at is not None
    assert stored.token_id == row.id and stored.device_name == "Pixel 8"
    assert stored.extensions == {"meshenger": {"v": 1, "key": "AAAA"}}

    # The device token works on HTTP and on the WebSocket upgrade, within its scopes.
    async with make_client(client=DEVICE) as device:
        assert (await device.get("/api/channels", headers=bearer(secret))).status_code == 200
        assert (await device.get("/api/auth/users", headers=bearer(secret))).status_code == 403
    reached, sent = await _upgrade([(b"authorization", f"Bearer {secret}".encode())])
    (principal,) = reached
    assert principal is not None and principal.user_id == member.id
    assert permissions_of(principal) == frozenset({"read", "send"})
    assert sent == [{"type": "websocket.accept"}]

    second = await _pair(body["code"])
    assert second.status_code == 410 and _code(second) == "auth_pairing_expired"

    used = await _audit("pairing.used")
    success = [row for row in used if row.outcome == "success"]
    (failure,) = [row for row in used if row.outcome == "failure"]
    assert success[0].actor_user_id == member.id and success[0].actor_token_id == row.id
    assert success[0].details == {
        "pairing": body["id"],
        "scopes": ["read", "send"],
        "device": "Pixel 8",
    }
    assert failure.details == {"reason": "used", "status": 410}
    assert failure.target == f"pairing:{body['id']}"
    for entry in used:
        assert body["code"] not in str(entry.model_dump()) and secret not in str(entry.model_dump())


async def test_an_expired_code_answers_410(test_db):
    _, body = await _pairing(["read"])
    async with test_db.tx() as conn:
        await conn.execute(
            "UPDATE pairing_codes SET expires_at = ? WHERE id = ?", (int(time.time()), body["id"])
        )
    response = await _pair(body["code"])
    assert response.status_code == 410 and _code(response) == "auth_pairing_expired"
    assert await ApiTokenRepository.list_live() == []
    (failure,) = await _audit("pairing.used")
    assert failure.details["reason"] == "expired"


@pytest.mark.parametrize("code", ["mlp_" + "A" * 22, "not-a-code", "mlp_" + "x" * 120])
async def test_unknown_and_malformed_codes_answer_the_same_410(code):
    await create_user()
    response = await _pair(code)
    assert response.status_code == 410 and _code(response) == "auth_pairing_expired"
    (failure,) = await _audit("pairing.used")
    assert failure.target is None and failure.details["reason"] in {"unknown", "malformed"}


async def test_a_code_too_long_for_the_body_is_422_without_echoing_it():
    await create_user()
    response = await _pair("mlp_" + "y" * 200)
    assert response.status_code == 422 and "yyyy" not in response.text


async def test_a_revoked_code_stops_working_at_once():
    _, body = await _pairing(["read"])
    async with make_client() as client:
        await login(client)
        response = await client.delete(f"/api/auth/pairings/{body['id']}")
        assert response.status_code == 204
        assert (await client.get("/api/auth/pairings")).json() == []
        again = await client.delete(f"/api/auth/pairings/{body['id']}")
        assert again.status_code == 404 and _code(again) == "auth_pairing_not_found"
    response = await _pair(body["code"])
    assert response.status_code == 410
    revoked = await _audit("pairing.revoked")
    assert [r.outcome for r in revoked] == ["failure", "success"]


async def test_a_member_sees_and_revokes_only_its_own_codes_an_admin_every_code():
    await create_user("owner")
    await create_user("nadia", role="member")
    await create_user("karim", role="member")
    async with make_client() as nadia:
        await login(nadia, "nadia")
        mine = (await _create(nadia, ["read"])).json()
    async with make_client() as karim:
        await login(karim, "karim")
        assert (await karim.get("/api/auth/pairings")).json() == []
        response = await karim.delete(f"/api/auth/pairings/{mine['id']}")
        assert response.status_code == 404
    async with make_client() as admin:
        await login(admin)
        listed = (await admin.get("/api/auth/pairings")).json()
        assert [(p["id"], p["username"]) for p in listed] == [(mine["id"], "nadia")]
        assert (await admin.delete(f"/api/auth/pairings/{mine['id']}")).status_code == 204
    (success, _) = await _audit("pairing.revoked")  # newest first
    assert success.outcome == "success" and success.details == {"owner": mine["created_by"]}


async def test_a_disabled_creator_spends_the_code_and_gives_nothing():
    member, body = await _pairing(["read"], username="nadia", role="member")
    await UserRepository.update(member.id, disabled=True)
    response = await _pair(body["code"])
    assert response.status_code == 410
    await UserRepository.update(member.id, disabled=False)
    assert (await _pair(body["code"])).status_code == 410
    assert await ApiTokenRepository.list_live() == []
    reasons = [r.details["reason"] for r in await _audit("pairing.used")]
    assert reasons == ["used", "owner_unavailable"]


async def test_a_demoted_creator_cannot_hand_out_scopes_it_lost():
    user, body = await _pairing(["read", "repeater_admin"], username="ada", role="admin")
    await UserRepository.update(user.id, role="member")
    response = await _pair(body["code"])
    assert response.status_code == 410
    (failure,) = await _audit("pairing.used")
    assert failure.details["reason"] == "scope"


async def test_two_concurrent_redemptions_give_one_token():
    _, body = await _pairing(["read"])
    first, second = await asyncio.gather(_pair(body["code"]), _pair(body["code"]))
    assert sorted([first.status_code, second.status_code]) == [201, 410]
    assert len(await ApiTokenRepository.list_live()) == 1


async def test_the_creators_token_cap_spends_the_code(monkeypatch):
    from app.repository import auth as auth_repository

    monkeypatch.setattr(auth_repository, "MAX_TOKENS_PER_USER", 0)
    _, body = await _pairing(["read"])
    response = await _pair(body["code"])
    assert response.status_code == 409 and _code(response) == "auth_token_limit"
    assert (await _pair(body["code"])).status_code == 410


async def test_oversized_extensions_are_422():
    _, body = await _pairing(["read"])
    response = await _pair(body["code"], extensions={"blob": "x" * pairing.MAX_EXTENSIONS_BYTES})
    assert response.status_code == 422
    assert (await _pair(body["code"])).status_code == 201


async def test_redemption_is_refused_under_ingress(monkeypatch):
    monkeypatch.setenv("MESHLOOM_INSTALL_KIND", "addon")
    _, body = await _pairing(["read"])
    response = await _pair(body["code"], client=(ingress.SUPERVISOR_IP, 40000))
    assert response.status_code == 403 and _code(response) == "auth_pairing_ingress"
    # Refused before any lookup: the code is still good on the instance's own address.
    assert (await _pair(body["code"])).status_code == 201
    (_, refused) = await _audit("pairing.used")  # newest first
    assert refused.outcome == "refused" and refused.via == "ingress"


async def test_wrong_codes_meet_the_login_delays_and_the_right_one_gets_in_after():
    _, body = await _pairing(["read"])
    for _ in range(ratelimit.PAIR_FREE_FAILURES):
        assert (await _pair("mlp_" + "B" * 22)).status_code == 410
    delayed = await _pair(body["code"])
    assert delayed.status_code == 429 and _code(delayed) == "auth_rate_limited"
    assert int(delayed.headers["retry-after"]) >= 1
    rows = [r for r in await _audit("pairing.used") if r.outcome == "delayed"]
    assert len(rows) == 1 and rows[0].details["reason"] == "pair"

    # A login from another address is not slowed by the pairing failures of this one.
    async with make_client() as client:
        await login(client)

    ratelimit.reset_login_limiter()
    assert (await _pair(body["code"])).status_code == 201


async def test_redemption_failures_share_the_per_address_budget_with_logins(monkeypatch):
    monkeypatch.setattr(ratelimit, "IP_MAX_FAILURES", 3)
    ratelimit.reset_login_limiter()
    _, body = await _pairing(["read"])
    for _ in range(3):
        async with make_client(client=DEVICE) as device:
            response = await device.post(
                "/api/auth/login", json={"username": "owner", "password": "wrong password!"}
            )
            assert response.status_code == 401
    response = await _pair(body["code"])
    assert response.status_code == 429


async def test_purge_drops_codes_that_can_no_longer_give_a_token(test_db):
    _, used = await _pairing(["read"])
    async with make_client() as client:
        await login(client)
        expired = (await _create(client, ["read"])).json()
        revoked = (await _create(client, ["read"])).json()
        pending = (await _create(client, ["read"])).json()
        await client.delete(f"/api/auth/pairings/{revoked['id']}")
    assert (await _pair(used["code"])).status_code == 201
    async with test_db.tx() as conn:
        await conn.execute("UPDATE pairing_codes SET expires_at = 0 WHERE id = ?", (expired["id"],))
    assert await pairing.purge_pairings() == 2
    assert await PairingCodeRepository.get(used["id"]) is not None
    assert await PairingCodeRepository.get(pending["id"]) is not None


# ---------------------------------------------------------------- the contract


def test_the_contract_names_every_route_and_error_code_of_the_implementation():
    text = CONTRACT.read_text(encoding="utf-8")
    for code in auth_pairing._MESSAGES:
        if code.startswith("auth_"):
            assert f"`{code}`" in text, code
    for route in (
        "POST /api/auth/pairings",
        "GET /api/auth/pairings",
        "DELETE /api/auth/pairings/{id}",
        "POST /api/auth/pair",
    ):
        assert route in text, route
    constants = {
        "mlp_": pairing.CODE_PREFIX,
        "meshloom://pair?d=": f"{pairing.PAYLOAD_SCHEME}?d=",
    }
    for written, value in constants.items():
        assert written == value and written in text
    for number in (pairing.MAX_TTL_S, pairing.DEFAULT_TTL_S, pairing.MAX_EXTENSIONS_BYTES):
        assert re.search(rf"\b{number}\b", text), number


def test_the_contract_example_payload_decodes_to_its_json():
    text = CONTRACT.read_text(encoding="utf-8")
    (example,) = re.findall(r"meshloom://pair\?d=[A-Za-z0-9_-]{20,}", text)
    decoded = pairing.decode_qr_payload(example)
    assert decoded["v"] == 1 and decoded["code"].startswith("mlp_")
    assert pairing.qr_payload(decoded["code"], decoded["urls"]) == example
