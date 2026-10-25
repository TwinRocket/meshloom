"""The TOTP second factor (Access T50): ``app/auth/totp.py``, ``app/routers/auth_totp.py``."""

from __future__ import annotations

import json
import sqlite3

import pyotp
import pytest

from app.auth import totp
from app.repository.auth import (
    AuthAuditRepository,
    AuthSettingsRepository,
    TotpRepository,
    UserRepository,
)
from tests.auth_helpers import ADMIN, PASSWORD, basic, create_user, login, make_client

pytestmark = [pytest.mark.real_auth, pytest.mark.usefixtures("test_db", "fast_argon2")]

# A fixed wall clock, in the middle of a 30 s step.
T0 = 1_800_000_015.0


@pytest.fixture
def clock(monkeypatch):
    now = {"t": T0}
    monkeypatch.setattr(totp, "clock", lambda: now["t"])
    return now


def _code(secret: str, at: float, steps: int = 0) -> str:
    return pyotp.TOTP(secret).at(at + steps * totp.PERIOD_S)


async def _enrol(client) -> tuple[str, list[str]]:
    """Enrol the signed-in account; returns the secret and the recovery codes."""
    enrolment = await client.post("/api/auth/2fa/enrol", json={"password": PASSWORD})
    assert enrolment.status_code == 200, enrolment.text
    secret = enrolment.json()["secret"]
    confirmed = await client.post(
        "/api/auth/2fa/confirm", json={"code": _code(secret, totp.clock())}
    )
    assert confirmed.status_code == 200, confirmed.text
    return secret, confirmed.json()["recovery_codes"]


async def _password_step(client, username: str = ADMIN) -> str:
    response = await login(client, username)
    body = response.json()
    assert body["authenticated"] is False
    assert body["second_factor"]["methods"] == ["totp", "recovery"]
    assert "meshloom_session" not in response.headers.get("set-cookie", "")
    return body["second_factor"]["pending_token"]


async def _audit_text() -> str:
    rows = await AuthAuditRepository.list_recent(limit=500)
    return json.dumps([row.model_dump() for row in rows])


# --- Codes, without the API -------------------------------------------------------


def test_matching_counter_accepts_one_step_of_drift_and_refuses_replays():
    secret = totp.new_secret()
    current = int(T0 // totp.PERIOD_S)
    assert totp.matching_counter(secret, _code(secret, T0), now=T0) == current
    assert totp.matching_counter(secret, _code(secret, T0, -1), now=T0) == current - 1
    assert totp.matching_counter(secret, _code(secret, T0, 1), now=T0) == current + 1
    assert totp.matching_counter(secret, _code(secret, T0, 2), now=T0) is None
    assert totp.matching_counter(secret, _code(secret, T0, -2), now=T0) is None
    # The step already used, or an earlier one, never matches again.
    assert totp.matching_counter(secret, _code(secret, T0), now=T0, last_counter=current) is None
    assert (
        totp.matching_counter(secret, _code(secret, T0, -1), now=T0, last_counter=current) is None
    )
    spaced = _code(secret, T0)
    assert totp.matching_counter(secret, f"{spaced[:3]} {spaced[3:]}", now=T0) == current
    assert totp.matching_counter(secret, "abcdef", now=T0) is None


def test_recovery_codes_are_random_hashed_and_typed_loosely():
    codes = totp.new_recovery_codes()
    assert len(codes) == totp.RECOVERY_CODE_COUNT == len(set(codes))
    assert all(totp.is_recovery_code(code) and not totp.is_totp_code(code) for code in codes)
    stored = totp.hash_recovery_code(codes[0])
    assert stored.startswith("$argon2id$") and codes[0] not in stored
    assert totp._recovery_code_matches(stored, totp.normalize_code(codes[0].upper()))
    assert not totp._recovery_code_matches(stored, totp.normalize_code(codes[1]))


def test_pending_logins_expire_and_are_void_after_the_attempt_budget():
    now = {"t": 0.0}
    pending = totp.PendingLogins(clock=lambda: now["t"])
    token = pending.issue(1, 10, "client")
    assert pending.get(token) is not None and pending.get("other") is None
    for _ in range(totp.PENDING_MAX_ATTEMPTS):
        entry = pending.take(token)
        assert entry is not None
        # Taken: a concurrent request finds nothing until it is put back.
        assert pending.take(token) is None
        pending.put_back(token, entry, failed=True)
    assert pending.get(token) is None
    token = pending.issue(1, 10, "client")
    entry = pending.take(token)
    assert entry is not None
    pending.put_back(token, entry, failed=False)  # a 429: not counted
    assert pending.get(token).attempts == 0
    now["t"] = totp.PENDING_TTL_S + 1
    assert pending.get(token) is None


def test_the_client_binding_covers_address_and_user_agent():
    base = totp.client_binding("10.0.0.1", "Firefox")
    assert base == totp.client_binding("10.0.0.1", "Firefox")
    assert base != totp.client_binding("10.0.0.2", "Firefox")
    assert base != totp.client_binding("10.0.0.1", "Chrome")
    assert "10.0.0.1" not in base


# --- Enrolment and login ----------------------------------------------------------


async def test_login_with_a_second_factor_needs_the_code_and_refuses_a_replay(clock):
    await create_user()
    async with make_client() as owner:
        await login(owner)
        secret, _ = await _enrol(owner)

    clock["t"] = T0 + 2 * totp.PERIOD_S
    async with make_client() as client:
        token = await _password_step(client)
        # The password alone opens nothing.
        assert (await client.get("/api/auth/me")).status_code == 401
        code = _code(secret, clock["t"])
        response = await client.post(
            "/api/auth/login/2fa", json={"pending_token": token, "code": code}
        )
        assert response.status_code == 200, response.text
        assert response.json()["authenticated"] is True
        assert response.json()["user"]["totp_enabled"] is True
        assert (await client.get("/api/auth/me")).status_code == 200
        # The pending token is spent.
        again = await client.post(
            "/api/auth/login/2fa", json={"pending_token": token, "code": code}
        )
        assert again.json()["detail"]["code"] == "auth_2fa_pending_invalid"

    # Same code, same step, new login: refused (replay inside the window).
    async with make_client() as replay:
        token = await _password_step(replay)
        response = await replay.post(
            "/api/auth/login/2fa", json={"pending_token": token, "code": code}
        )
        assert response.status_code == 401
        assert response.json()["detail"]["code"] == "auth_2fa_invalid"
        assert (await replay.get("/api/auth/me")).status_code == 401
        # The previous step's code is refused too, the next one is accepted.
        previous = _code(secret, clock["t"], -1)
        refused = await replay.post(
            "/api/auth/login/2fa", json={"pending_token": token, "code": previous}
        )
        assert refused.status_code == 401
        clock["t"] += totp.PERIOD_S
        accepted = await replay.post(
            "/api/auth/login/2fa",
            json={"pending_token": token, "code": _code(secret, clock["t"])},
        )
        assert accepted.status_code == 200

    text = await _audit_text()
    assert '"pending"' in text and secret not in text and code not in text


async def test_recovery_codes_work_once(clock):
    await create_user()
    async with make_client() as owner:
        await login(owner)
        _, codes = await _enrol(owner)
    async with make_client() as client:
        token = await _password_step(client)
        response = await client.post(
            "/api/auth/login/2fa", json={"pending_token": token, "code": codes[0].upper()}
        )
        assert response.status_code == 200, response.text
    async with make_client() as client:
        token = await _password_step(client)
        response = await client.post(
            "/api/auth/login/2fa", json={"pending_token": token, "code": codes[0]}
        )
        assert response.json()["detail"]["code"] == "auth_2fa_invalid"
        status = await client.post(
            "/api/auth/login/2fa", json={"pending_token": token, "code": codes[1]}
        )
        assert status.status_code == 200
        left = (await client.get("/api/auth/2fa")).json()["recovery_codes_left"]
        assert left == totp.RECOVERY_CODE_COUNT - 2
    # Only hashes are stored.
    unused = await TotpRepository.unused_codes(1)
    assert all(code.replace("-", "") not in stored for _, stored in unused for code in codes)
    text = await _audit_text()
    assert '"factor": "recovery"' in text and codes[0] not in text


async def test_a_pending_login_is_void_after_too_many_wrong_codes_and_the_limiter_counts_them(
    clock,
):
    await create_user()
    async with make_client() as owner:
        await login(owner)
        secret, _ = await _enrol(owner)
    async with make_client() as client:
        token = await _password_step(client)
        for _ in range(totp.PENDING_MAX_ATTEMPTS):
            wrong = await client.post(
                "/api/auth/login/2fa", json={"pending_token": token, "code": "000000"}
            )
            assert wrong.status_code == 401
        void = await client.post(
            "/api/auth/login/2fa",
            json={"pending_token": token, "code": _code(secret, clock["t"], 1)},
        )
        assert void.json()["detail"]["code"] == "auth_2fa_pending_invalid"
        # The wrong codes were failed logins: the next password attempt is slowed down.
        delayed = await client.post(
            "/api/auth/login", json={"username": ADMIN, "password": PASSWORD}
        )
        assert delayed.status_code == 429
        assert delayed.json()["detail"]["code"] == "auth_rate_limited"
    text = await _audit_text()
    assert "second_factor_invalid" in text


async def test_an_account_change_voids_the_pending_login(clock):
    await create_user()
    async with make_client() as owner:
        await login(owner)
        secret, _ = await _enrol(owner)
    async with make_client() as client:
        token = await _password_step(client)
        await UserRepository.update(1, display_name="Changed")
        response = await client.post(
            "/api/auth/login/2fa",
            json={"pending_token": token, "code": _code(secret, clock["t"], 1)},
        )
        assert response.json()["detail"]["code"] == "auth_2fa_pending_invalid"


async def test_basic_never_signs_in_an_account_with_a_second_factor(clock):
    await create_user()
    async with make_client() as owner:
        await login(owner)
        assert (await owner.get("/api/auth/me", headers=basic(ADMIN, PASSWORD))).status_code == 200
        await _enrol(owner)
    async with make_client() as client:
        response = await client.get("/api/auth/me", headers=basic(ADMIN, PASSWORD))
        assert response.status_code == 401


async def test_enrolment_needs_the_password_and_never_returns_the_secret_afterwards(clock):
    await create_user()
    async with make_client() as owner:
        await login(owner)
        wrong = await owner.post("/api/auth/2fa/enrol", json={"password": "not the password"})
        assert wrong.status_code == 403
        enrolment = (await owner.post("/api/auth/2fa/enrol", json={"password": PASSWORD})).json()
        secret = enrolment["secret"]
        assert enrolment["otpauth_uri"].startswith("otpauth://totp/")
        assert (await owner.get("/api/auth/2fa")).json()["pending"] is True
        bad = await owner.post("/api/auth/2fa/confirm", json={"code": "000000"})
        assert bad.json()["detail"]["code"] == "auth_2fa_invalid"
        confirmed = await owner.post(
            "/api/auth/2fa/confirm", json={"code": _code(secret, clock["t"])}
        )
        assert confirmed.status_code == 200
        codes = confirmed.json()["recovery_codes"]
        again = await owner.post("/api/auth/2fa/enrol", json={"password": PASSWORD})
        assert again.json()["detail"]["code"] == "auth_2fa_already_enabled"
        payloads = [
            (await owner.get(path)).text
            for path in ("/api/auth/2fa", "/api/auth/me", "/api/auth/session", "/api/auth/users")
        ]
    for payload in payloads:
        assert secret not in payload
        assert all(code not in payload for code in codes)
    assert json.loads(payloads[0])["enabled"] is True
    assert json.loads(payloads[1])["totp_enabled"] is True
    text = await _audit_text()
    assert "totp.enrolled" in text and secret not in text


async def test_confirming_signs_out_the_other_sessions(clock):
    await create_user()
    async with make_client() as here, make_client() as there:
        await login(here)
        await login(there)
        await _enrol(here)
        assert (await here.get("/api/auth/me")).status_code == 200
        assert (await there.get("/api/auth/me")).status_code == 401


async def test_disable_needs_the_password_and_a_code(clock):
    await create_user()
    async with make_client() as owner:
        await login(owner)
        secret, codes = await _enrol(owner)
        clock["t"] += totp.PERIOD_S
        code = _code(secret, clock["t"])
        no_password = await owner.post(
            "/api/auth/2fa/disable", json={"password": "wrong password!", "code": code}
        )
        assert no_password.status_code == 403
        no_code = await owner.post(
            "/api/auth/2fa/disable", json={"password": PASSWORD, "code": "123456"}
        )
        assert no_code.json()["detail"]["code"] == "auth_2fa_invalid"
        done = await owner.post(
            "/api/auth/2fa/disable", json={"password": PASSWORD, "code": codes[3]}
        )
        assert done.status_code == 200
        assert (await owner.get("/api/auth/2fa")).json() == {
            "enabled": False,
            "pending": False,
            "recovery_codes_left": 0,
            "enrolment_required": False,
        }
    assert await TotpRepository.unused_codes(1) == []
    async with make_client() as client:
        response = await login(client)
        assert response.json()["authenticated"] is True
    assert "totp.disabled" in await _audit_text()


async def test_regenerating_recovery_codes_needs_a_totp_code_and_replaces_them(clock):
    await create_user()
    async with make_client() as owner:
        await login(owner)
        secret, old = await _enrol(owner)
        clock["t"] += totp.PERIOD_S
        with_recovery = await owner.post(
            "/api/auth/2fa/recovery", json={"password": PASSWORD, "code": old[0]}
        )
        assert with_recovery.json()["detail"]["code"] == "auth_2fa_invalid"
        response = await owner.post(
            "/api/auth/2fa/recovery",
            json={"password": PASSWORD, "code": _code(secret, clock["t"])},
        )
        assert response.status_code == 200
        new = response.json()["recovery_codes"]
    assert set(new).isdisjoint(old)
    async with make_client() as client:
        token = await _password_step(client)
        stale = await client.post(
            "/api/auth/login/2fa", json={"pending_token": token, "code": old[1]}
        )
        assert stale.json()["detail"]["code"] == "auth_2fa_invalid"
        fresh = await client.post(
            "/api/auth/login/2fa", json={"pending_token": token, "code": new[1]}
        )
        assert fresh.status_code == 200
    assert "totp.recovery_regenerated" in await _audit_text()


async def test_accounts_without_a_password_have_no_second_factor():
    from fastapi import HTTPException

    from app.routers import auth_totp

    user = await create_user("ha", password=None, role="viewer")
    with pytest.raises(HTTPException) as refused:
        auth_totp._applicable(user)
    assert refused.value.status_code == 403


# --- require_2fa_for_admins -------------------------------------------------------


async def test_required_2fa_confines_an_admin_to_enrolment_and_leaves_members_alone(clock):
    await create_user()
    await create_user("member1", role="member")
    async with make_client() as admin, make_client() as member:
        await login(admin)
        await login(member, "member1")
        patched = await admin.patch("/api/auth/settings", json={"require_2fa_for_admins": True})
        assert patched.status_code == 200
        assert patched.json()["require_2fa_for_admins"] is True

        refused = await admin.get("/api/auth/users")
        assert refused.status_code == 403
        assert refused.json()["detail"]["code"] == "auth_2fa_enrolment_required"
        assert (await admin.get("/api/contacts")).status_code == 403
        session = (await admin.get("/api/auth/session")).json()
        assert session["user"]["second_factor_enrolment_required"] is True
        assert (await admin.get("/api/auth/2fa")).json()["enrolment_required"] is True

        assert (await member.get("/api/contacts")).status_code == 200
        member_session = (await member.get("/api/auth/session")).json()
        assert member_session["user"]["second_factor_enrolment_required"] is False

        await _enrol(admin)
        assert (await admin.get("/api/auth/users")).status_code == 200
    rows = await AuthAuditRepository.list_recent(limit=100)
    assert any(row.target == "auth_settings.require_2fa_for_admins" for row in rows)


async def test_required_2fa_refuses_the_websocket_of_an_admin_without_one():
    from starlette.testclient import TestClient
    from starlette.websockets import WebSocketDisconnect

    from app.main import app

    await create_user()
    await AuthSettingsRepository.update(require_2fa_for_admins=True)
    async with make_client() as admin:
        await login(admin)
        cookie = admin.cookies.get("meshloom_session")
    client = TestClient(app)
    client.cookies.set("meshloom_session", cookie)
    with pytest.raises(WebSocketDisconnect):
        with client.websocket_connect("/api/ws"):
            pass


async def test_an_unreadable_setting_confines_the_admin(monkeypatch):
    from app.auth import middleware

    async def broken():
        raise sqlite3.OperationalError("boom")

    await create_user()
    async with make_client() as admin:
        await login(admin)
        monkeypatch.setattr(middleware.AuthSettingsRepository, "require_2fa_for_admins", broken)
        response = await admin.get("/api/auth/users")
        assert response.json()["detail"]["code"] == "auth_2fa_enrolment_required"


# --- Review round 1 (PR #240) -------------------------------------------------------


async def test_the_pending_login_is_bound_to_the_first_step_client(clock):
    await create_user()
    async with make_client() as owner:
        await login(owner)
        secret, _ = await _enrol(owner)
    code = _code(secret, clock["t"], 1)

    # Another address: refused, and the token is gone for everyone.
    async with make_client() as first, make_client(client=("10.9.9.9", 4242)) as thief:
        token = await _password_step(first)
        stolen = await thief.post(
            "/api/auth/login/2fa", json={"pending_token": token, "code": code}
        )
        assert stolen.json()["detail"]["code"] == "auth_2fa_pending_invalid"
        after = await first.post("/api/auth/login/2fa", json={"pending_token": token, "code": code})
        assert after.json()["detail"]["code"] == "auth_2fa_pending_invalid"

    # Same address, another browser (User-Agent): refused too.
    async with make_client() as first:
        token = await _password_step(first)
        other = await first.post(
            "/api/auth/login/2fa",
            json={"pending_token": token, "code": code},
            headers={"user-agent": "another browser"},
        )
        assert other.json()["detail"]["code"] == "auth_2fa_pending_invalid"
    assert "pending_client_mismatch" in await _audit_text()


async def test_one_pending_token_opens_one_session_under_concurrency(clock):
    import asyncio

    await create_user()
    async with make_client() as owner:
        await login(owner)
        _, codes = await _enrol(owner)
    async with make_client() as client:
        token = await _password_step(client)
        answers = await asyncio.gather(
            *(
                client.post("/api/auth/login/2fa", json={"pending_token": token, "code": code})
                for code in codes[:2]
            )
        )
    assert sorted(answer.status_code for answer in answers) == [200, 401]
    assert len(await TotpRepository.unused_codes(1)) == totp.RECOVERY_CODE_COUNT - 1


@pytest.mark.parametrize("route", ["/api/auth/2fa/disable", "/api/auth/2fa/recovery"])
async def test_a_right_password_does_not_clear_the_penalty_of_wrong_codes(clock, route):
    await create_user()
    async with make_client() as owner:
        await login(owner)
        await _enrol(owner)
        statuses = []
        for _ in range(8):
            response = await owner.post(route, json={"password": PASSWORD, "code": "000000"})
            statuses.append(response.status_code)
        # The first wrong codes are free; then the delay holds even with the right password.
        assert statuses[:5] == [403] * 5
        assert 429 in statuses[5:]
    # The (address, name) pair kept every wrong code: the right password did not reset it.
    from app.auth.ratelimit import login_limiter

    assert max(counter.failures for counter in login_limiter._pairs.values()) >= 5
