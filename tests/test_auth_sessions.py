"""Session and profile routes: GET/DELETE /api/auth/sessions, GET/PATCH /api/auth/me, logout."""

import pytest

from app.repository.auth import AuthSessionRepository, UserRepository
from tests.auth_helpers import ADMIN, PASSWORD, basic, create_user, login, make_client

pytestmark = [pytest.mark.real_auth, pytest.mark.usefixtures("test_db", "fast_argon2")]


async def test_each_login_is_a_new_session_and_the_list_marks_the_current_one():
    await create_user()
    async with make_client() as first, make_client() as second:
        await login(first)
        await login(second)
        sessions = (await first.get("/api/auth/sessions")).json()
        assert len(sessions) == 2
        assert [s["current"] for s in sessions].count(True) == 1
        assert all("token_hash" not in s and "user_id" not in s for s in sessions)

        other = next(s for s in sessions if not s["current"])
        assert (await first.delete(f"/api/auth/sessions/{other['id']}")).status_code == 200
        assert (await second.get("/api/auth/me")).status_code == 401
        missing = await first.delete("/api/auth/sessions/nope")
        assert missing.status_code == 404


async def test_a_user_cannot_revoke_someone_elses_session():
    await create_user()
    await create_user("member1", role="member")
    async with make_client() as owner, make_client() as member:
        await login(owner)
        await login(member, "member1")
        [member_session] = await AuthSessionRepository.list_for_user(2)
        response = await owner.delete(f"/api/auth/sessions/{member_session.id}")
        assert response.status_code == 404
        assert (await member.get("/api/auth/me")).status_code == 200


async def test_sign_out_everywhere_else_keeps_this_session():
    await create_user()
    async with make_client() as here, make_client() as there:
        await login(here)
        await login(there)
        response = await here.delete("/api/auth/sessions")
        assert response.json() == {"revoked": 1}
        assert (await here.get("/api/auth/me")).status_code == 200
        assert (await there.get("/api/auth/me")).status_code == 401


async def test_logout_revokes_and_clears_the_cookie():
    await create_user()
    async with make_client() as client:
        await login(client)
        token = client.cookies.get("meshloom_session")
        response = await client.post("/api/auth/logout")
        assert response.status_code == 200
        assert response.headers["clear-site-data"] == '"cookies"'
        assert "Max-Age=0" in response.headers["set-cookie"]
    async with make_client() as replay:
        replay.cookies.set("meshloom_session", token)
        assert (await replay.get("/api/auth/me")).status_code == 401


async def test_password_change_needs_the_current_one_applies_the_policy_and_revokes_others():
    await create_user()
    async with make_client() as here, make_client() as there:
        await login(here)
        await login(there)
        wrong = await here.patch(
            "/api/auth/me", json={"current_password": "nope", "new_password": "a new long phrase"}
        )
        assert wrong.status_code == 403
        weak = await here.patch(
            "/api/auth/me", json={"current_password": PASSWORD, "new_password": "short"}
        )
        assert weak.json()["detail"]["code"] == "auth_password_policy"

        # Warm the Basic cache with the old password, then change it.
        assert (await here.get("/api/auth/me", headers=basic(ADMIN, PASSWORD))).status_code == 200
        changed = await here.patch(
            "/api/auth/me",
            json={"current_password": PASSWORD, "new_password": "a new long phrase"},
        )
        assert changed.status_code == 200
        assert (await here.get("/api/auth/me")).status_code == 200
        assert (await there.get("/api/auth/me")).status_code == 401
        old_basic = await here.get("/api/auth/me", headers=basic(ADMIN, PASSWORD))
        assert old_basic.status_code == 401
    async with make_client() as client:
        await login(client, ADMIN, "a new long phrase")


async def test_kiosk_accounts_have_no_profile_and_no_sessions():
    kiosk = await UserRepository.create("screen", "kiosk", provider="system")
    from app.auth.sessions import issue_session

    issued = await issue_session(kiosk, ip=None, user_agent=None, via="lan")
    async with make_client() as client:
        client.cookies.set("meshloom_session", issued.token)
        assert (await client.get("/api/auth/session")).json()["user"]["role"] == "kiosk"
        for method, path in [
            ("GET", "/api/auth/me"),
            ("PATCH", "/api/auth/me"),
            ("GET", "/api/auth/sessions"),
            ("DELETE", "/api/auth/sessions"),
        ]:
            response = await client.request(method, path, json={"display_name": "x"})
            assert response.status_code == 403, path
            assert response.json()["detail"]["code"] == "auth_forbidden", path
