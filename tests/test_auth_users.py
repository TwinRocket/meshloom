"""Users management (``/api/auth/users``) and the forced password change, through the real gate."""

import pytest

from app.auth import device_cookie
from app.auth.models import User
from app.auth.principal import Principal
from app.repository.auth import (
    AuthAuditRepository,
    AuthSessionRepository,
    AuthSettingsRepository,
    UserRepository,
)
from app.websocket import WebSocketManager
from tests.auth_helpers import ADMIN, PASSWORD, create_user, login, make_client

pytestmark = [pytest.mark.real_auth, pytest.mark.usefixtures("test_db", "fast_argon2")]

NEW_PASSWORD = "another long passphrase"
HA_ID = "8f6e2c1d4b3a49e0a1b2c3d4e5f60718"


def _code(response) -> str:
    return response.json()["detail"]["code"]


async def _audit(action: str, outcome: str = "success") -> list:
    rows = await AuthAuditRepository.list_recent(limit=200)
    return [row for row in rows if row.action == action and row.outcome == outcome]


@pytest.fixture
def closed_sockets(monkeypatch):
    """The user ids whose sockets the routes closed (``ws_manager.close_user``)."""
    from app.websocket import ws_manager

    calls: list[tuple[int, str | None]] = []

    def close_user(user_id, *, except_session_id=None):
        calls.append((user_id, except_session_id))
        return 0

    monkeypatch.setattr(ws_manager, "close_user", close_user)
    return calls


async def test_admin_creates_lists_and_the_list_hides_the_home_assistant_id():
    await create_user()
    await UserRepository.create("ha-alice", "member", provider="ingress", external_id=HA_ID)
    async with make_client() as admin:
        await login(admin)
        created = await admin.post(
            "/api/auth/users",
            json={"username": "bob", "role": "member", "password": PASSWORD, "display_name": "Bob"},
        )
        assert created.status_code == 201, created.text
        body = created.json()
        assert body["role"] == "member" and body["must_change_password"] is True
        assert body["has_password"] is True and body["provider"] == "local"
        users = (await admin.get("/api/auth/users")).json()
    assert [u["username"] for u in users] == [ADMIN, "ha-alice", "bob"]
    assert users[1]["provider"] == "ingress"
    assert all("external_id" not in u and "password_hash" not in u for u in users)
    assert HA_ID not in str(users)
    [row] = await _audit("user.created")
    assert row.outcome == "success" and row.target == f"user:{body['id']}"
    assert row.details == {"role": "member", "must_change": True}


async def test_create_rules():
    await create_user()
    async with make_client() as admin:
        await login(admin)
        kiosk = await admin.post("/api/auth/users", json={"username": "wall", "role": "kiosk"})
        assert kiosk.status_code == 201
        assert kiosk.json()["has_password"] is False
        assert kiosk.json()["must_change_password"] is False
        cases = [
            ({"username": "wall2", "role": "kiosk", "password": PASSWORD}, 400,
             "auth_password_not_applicable"),
            ({"username": "carol", "role": "member"}, 400, "auth_password_required"),
            ({"username": "carol", "role": "viewer", "password": "short"}, 400,
             "auth_password_policy"),
            ({"username": "WALL", "role": "viewer", "password": PASSWORD}, 409,
             "auth_username_taken"),
            ({"username": " ", "role": "viewer", "password": PASSWORD}, 400,
             "auth_username_invalid"),
        ]  # fmt: skip
        for payload, status, code in cases:
            response = await admin.post("/api/auth/users", json=payload)
            assert response.status_code == status, payload
            assert _code(response) == code
        bad_role = await admin.post("/api/auth/users", json={"username": "x", "role": "root"})
        assert bad_role.status_code == 422


@pytest.mark.parametrize("role", ["member", "viewer", "kiosk"])
async def test_only_admins_reach_the_users_routes(role):
    await create_user()
    target = await create_user("target", role="member")
    await create_user("caller", role=role, password=None if role == "kiosk" else PASSWORD)
    async with make_client() as caller:
        if role == "kiosk":
            # A kiosk has no password: sign it in the way its device would, with a session.
            from app.auth import sessions

            user = await UserRepository.get_by_username("caller")
            assert user is not None
            issued = await sessions.issue_session(user, ip=None, user_agent=None, via="lan")
            caller.cookies.set("meshloom_session", issued.token)
        else:
            await login(caller, "caller")
        calls = [
            caller.get("/api/auth/users"),
            caller.post("/api/auth/users", json={"username": "z", "role": "admin"}),
            caller.patch(f"/api/auth/users/{target.id}", json={"role": "admin"}),
            caller.delete(f"/api/auth/users/{target.id}"),
            caller.post(f"/api/auth/users/{target.id}/password", json={"password": PASSWORD}),
            caller.delete(f"/api/auth/users/{target.id}/sessions"),
        ]
        for call in calls:
            response = await call
            assert response.status_code == 403
            assert response.json()["detail"]["params"] == {"permission": "admin"}
    unchanged = await UserRepository.get(target.id)
    assert unchanged is not None and unchanged.role == "member"


async def test_the_last_admin_can_be_neither_demoted_disabled_nor_deleted():
    owner = await create_user()
    async with make_client() as admin:
        await login(admin)
        for payload in ({"role": "member"}, {"role": "kiosk"}, {"disabled": True}):
            response = await admin.patch(f"/api/auth/users/{owner.id}", json=payload)
            assert response.status_code == 409 and _code(response) == "auth_last_admin"
        deleted = await admin.delete(f"/api/auth/users/{owner.id}")
        assert deleted.status_code == 409 and _code(deleted) == "auth_last_admin"
        # Still signed in, still an admin.
        assert (await admin.get("/api/auth/users")).status_code == 200
    kept = await UserRepository.get(owner.id)
    assert kept is not None and kept.role == "admin" and not kept.disabled and kept.has_password
    refusals = [
        r for r in await AuthAuditRepository.list_recent(limit=50) if r.outcome == "refused"
    ]
    assert sorted(r.action for r in refusals) == [
        "user.changed",
        "user.changed",
        "user.deleted",
        "user.disabled",
    ]
    assert all(r.details == {"reason": "last_admin", "status": 409} for r in refusals)
    assert all(r.target == f"user:{owner.id}" for r in refusals)


async def test_a_disabled_admin_does_not_count_and_a_second_admin_can_be_demoted():
    owner = await create_user()
    second = await create_user("second", role="admin")
    async with make_client() as admin:
        await login(admin)
        demoted = await admin.patch(f"/api/auth/users/{second.id}", json={"role": "viewer"})
        assert demoted.status_code == 200 and demoted.json()["role"] == "viewer"
        promoted = await admin.patch(f"/api/auth/users/{second.id}", json={"role": "admin"})
        assert promoted.status_code == 200
        assert (
            await admin.patch(f"/api/auth/users/{second.id}", json={"disabled": True})
        ).status_code == 200
        # The other admin is disabled: the owner is the last enabled one.
        last = await admin.patch(f"/api/auth/users/{owner.id}", json={"role": "member"})
        assert last.status_code == 409


async def test_disabling_a_user_signs_it_out_at_once(closed_sockets):
    await create_user()
    member = await create_user("member1", role="member")
    async with make_client() as admin, make_client() as other, make_client() as other2:
        await login(admin)
        await login(other, "member1")
        await login(other2, "member1")
        assert (await other.get("/api/auth/me")).status_code == 200
        response = await admin.patch(f"/api/auth/users/{member.id}", json={"disabled": True})
        assert response.status_code == 200 and response.json()["disabled"] is True
        assert (await other.get("/api/auth/me")).status_code == 401
        assert (await other2.get("/api/auth/me")).status_code == 401
        assert await AuthSessionRepository.list_for_user(member.id) == []
        assert closed_sockets == [(member.id, None)]
        # Enabled again, the old sessions stay revoked; a new login works.
        enabled = await admin.patch(f"/api/auth/users/{member.id}", json={"disabled": False})
        assert enabled.status_code == 200
        assert (await other.get("/api/auth/me")).status_code == 401
        await login(other, "member1")
    [disabled_row] = await _audit("user.disabled")
    assert disabled_row.details == {"sessions_revoked": 2}
    assert len(await _audit("user.enabled")) == 1


async def test_a_role_change_signs_the_user_out_so_its_sockets_reopen_with_the_new_role(
    closed_sockets,
):
    await create_user()
    member = await create_user("member1", role="member")
    async with make_client() as admin, make_client() as other:
        await login(admin)
        await login(other, "member1")
        response = await admin.patch(f"/api/auth/users/{member.id}", json={"role": "viewer"})
        assert response.status_code == 200
        assert (await other.get("/api/auth/me")).status_code == 401
        await login(other, "member1")
        assert (await other.get("/api/auth/me")).json()["role"] == "viewer"
        # Same role again: nothing changes, nobody is signed out.
        same = await admin.patch(f"/api/auth/users/{member.id}", json={"role": "viewer"})
        assert same.status_code == 200
        assert (await other.get("/api/auth/me")).status_code == 200
    [row] = await _audit("user.changed")
    assert row.details["fields"] == ["role"]
    assert row.details["role"] == "viewer" and row.details["previous_role"] == "member"
    assert closed_sockets == [(member.id, None)]


async def test_demoting_to_kiosk_drops_the_password():
    await create_user()
    member = await create_user("member1", role="member")
    async with make_client() as admin:
        await login(admin)
        response = await admin.patch(f"/api/auth/users/{member.id}", json={"role": "kiosk"})
        assert response.status_code == 200
        assert response.json()["has_password"] is False
        reset = await admin.post(
            f"/api/auth/users/{member.id}/password", json={"password": NEW_PASSWORD}
        )
        assert reset.status_code == 400 and _code(reset) == "auth_password_not_applicable"
        flag = await admin.patch(
            f"/api/auth/users/{member.id}", json={"must_change_password": True}
        )
        assert flag.status_code == 400 and _code(flag) == "auth_password_not_applicable"
    async with make_client() as kiosk:
        refused = await kiosk.post(
            "/api/auth/login", json={"username": "member1", "password": PASSWORD}
        )
        assert refused.status_code == 401
    [row] = await _audit("user.changed")
    assert row.details["fields"] == ["role", "password_removed"]


async def test_admin_password_reset_signs_out_forgets_devices_and_forces_a_change(
    closed_sockets,
):
    await create_user()
    member = await create_user("member1", role="member")
    async with make_client() as admin, make_client() as other:
        await login(admin)
        signed_in = await login(other, "member1")
        before = await UserRepository.get(member.id)
        assert before is not None
        cookie = await device_cookie.read(
            "; ".join(c.split(";")[0] for c in signed_in.headers.get_list("set-cookie")),
            secure=False,
        )
        assert await device_cookie.known_device(cookie, "member1") is not None

        weak = await admin.post(
            f"/api/auth/users/{member.id}/password", json={"password": "member1"}
        )
        assert weak.status_code == 400 and _code(weak) == "auth_password_policy"
        reset = await admin.post(
            f"/api/auth/users/{member.id}/password", json={"password": NEW_PASSWORD}
        )
        assert reset.status_code == 200, reset.text
        assert reset.json()["must_change_password"] is True
        assert (await other.get("/api/auth/me")).status_code == 401
        # The known-device cookie of the old password is no longer honoured.
        assert await device_cookie.known_device(cookie, "member1") is None
        assert closed_sockets == [(member.id, None)]

        refused = await other.post(
            "/api/auth/login", json={"username": "member1", "password": PASSWORD}
        )
        assert refused.status_code == 401
        session = (await login(other, "member1", NEW_PASSWORD)).json()
        assert session["user"]["must_change_password"] is True
    [row] = await _audit("password.changed")
    assert row.target == f"user:{member.id}"
    assert row.details == {"reset": True, "must_change": True, "sessions_revoked": 1}
    assert NEW_PASSWORD not in str(await AuthAuditRepository.list_recent(limit=100))


async def test_must_change_password_limits_the_session_to_the_password_change():
    owner = await create_user()
    async with make_client() as admin:
        await login(admin)
        created = await admin.post(
            "/api/auth/users",
            json={"username": "newbie", "role": "member", "password": PASSWORD},
        )
        user_id = created.json()["id"]
        assert (await admin.get("/api/auth/users")).status_code == 200
    async with make_client() as newbie:
        session = (await login(newbie, "newbie")).json()
        assert session["user"]["must_change_password"] is True
        for method, path in [
            ("GET", "/api/auth/sessions"),
            ("DELETE", "/api/auth/sessions"),
            ("GET", "/api/contacts"),
            ("GET", "/api/health"),
            ("GET", "/api/auth/users"),
        ]:
            response = await newbie.request(method, path)
            assert response.status_code == 403, path
            assert _code(response) == "auth_password_change_required"
        assert (await newbie.get("/api/auth/session")).status_code == 200
        me = await newbie.get("/api/auth/me")
        assert me.status_code == 200 and me.json()["must_change_password"] is True
        # The display name alone does not lift the flag.
        renamed = await newbie.patch("/api/auth/me", json={"display_name": "New"})
        assert renamed.status_code == 200 and renamed.json()["must_change_password"] is True
        assert (await newbie.get("/api/auth/sessions")).status_code == 403
        changed = await newbie.patch(
            "/api/auth/me", json={"current_password": PASSWORD, "new_password": NEW_PASSWORD}
        )
        assert changed.status_code == 200 and changed.json()["must_change_password"] is False
        assert (await newbie.get("/api/auth/sessions")).status_code == 200
        assert (await newbie.get("/api/auth/session")).json()["user"][
            "must_change_password"
        ] is False
    # An admin can raise the flag again on an account with a password.
    async with make_client() as admin:
        await login(admin)
        flagged = await admin.patch(
            f"/api/auth/users/{user_id}", json={"must_change_password": True}
        )
        assert flagged.status_code == 200 and flagged.json()["must_change_password"] is True
    assert owner.id != user_id


def test_must_change_password_refuses_the_websocket(monkeypatch):
    from fastapi import FastAPI, WebSocket
    from starlette.testclient import TestClient
    from starlette.websockets import WebSocketDisconnect

    from app.auth import middleware

    user = User(
        id=3,
        username="newbie",
        role="member",
        created_at=0,
        updated_at=0,
        must_change_password=True,
    )

    async def resolve(scope, headers, via, secure):
        result = middleware._Resolution()
        result.principal = Principal(user=user, via=via, credential="session", session_id="s")
        return result

    monkeypatch.setattr(middleware, "resolve", resolve)
    app = FastAPI()
    app.add_middleware(middleware.AuthMiddleware)
    reached: list[str] = []

    @app.websocket("/api/ws")
    async def ws(websocket: WebSocket) -> None:
        reached.append("called")
        await websocket.accept()

    with pytest.raises(WebSocketDisconnect) as refused:
        with TestClient(app).websocket_connect("/api/ws"):
            pass
    assert refused.value.code == middleware.WS_REFUSED_CLOSE_CODE
    assert reached == []


async def test_own_password_reset_keeps_the_admins_session(closed_sockets):
    owner = await create_user()
    async with make_client() as admin, make_client() as elsewhere:
        await login(admin)
        await login(elsewhere)
        reset = await admin.post(
            f"/api/auth/users/{owner.id}/password",
            json={"password": NEW_PASSWORD, "must_change_password": False},
        )
        assert reset.status_code == 200 and reset.json()["must_change_password"] is False
        assert (await admin.get("/api/auth/users")).status_code == 200
        assert (await elsewhere.get("/api/auth/me")).status_code == 401
        [kept] = await AuthSessionRepository.list_for_user(owner.id)
        assert closed_sockets == [(owner.id, kept.id)]


async def test_delete_removes_the_account_and_signs_it_out(closed_sockets):
    await create_user()
    member = await create_user("member1", role="member")
    async with make_client() as admin, make_client() as other:
        await login(admin)
        await login(other, "member1")
        deleted = await admin.delete(f"/api/auth/users/{member.id}")
        assert deleted.status_code == 200
        assert (await other.get("/api/auth/me")).status_code == 401
        assert await UserRepository.get(member.id) is None
        assert closed_sockets == [(member.id, None)]
        missing = await admin.delete(f"/api/auth/users/{member.id}")
        assert missing.status_code == 404 and _code(missing) == "auth_user_not_found"
        for call in (
            admin.patch(f"/api/auth/users/{member.id}", json={"role": "viewer"}),
            admin.post(f"/api/auth/users/{member.id}/password", json={"password": NEW_PASSWORD}),
            admin.delete(f"/api/auth/users/{member.id}/sessions"),
        ):
            response = await call
            assert response.status_code == 404
    [row] = await _audit("user.deleted")
    assert row.outcome == "success" and row.details == {"role": "member", "provider": "local"}


async def test_admin_signs_a_user_out_everywhere(closed_sockets):
    await create_user()
    member = await create_user("member1", role="member")
    async with make_client() as admin, make_client() as other:
        await login(admin)
        await login(other, "member1")
        response = await admin.delete(f"/api/auth/users/{member.id}/sessions")
        assert response.status_code == 200 and response.json() == {"revoked": 1}
        assert (await other.get("/api/auth/me")).status_code == 401
        assert closed_sockets == [(member.id, None)]
    [row] = await _audit("session.revoked")
    assert row.target == f"user:{member.id}" and row.details == {"scope": "all", "count": 1}


async def test_home_assistant_identities_take_any_role_but_no_password(monkeypatch):
    from app.auth.providers import ingress

    monkeypatch.setenv("MESHLOOM_INSTALL_KIND", "addon")
    await create_user()
    ha = await UserRepository.create("alice", "admin", provider="ingress", external_id=HA_ID)
    headers = {"X-Remote-User-Id": HA_ID, "X-Remote-User-Name": "alice"}
    async with make_client() as admin:
        await login(admin)
        for role in ("kiosk", "viewer", "member"):
            response = await admin.patch(f"/api/auth/users/{ha.id}", json={"role": role})
            assert response.status_code == 200 and response.json()["role"] == role
        for call in (
            admin.post(f"/api/auth/users/{ha.id}/password", json={"password": NEW_PASSWORD}),
            admin.patch(f"/api/auth/users/{ha.id}", json={"must_change_password": True}),
        ):
            response = await call
            assert response.status_code == 400 and _code(response) == "auth_password_not_applicable"
        async with make_client(client=(ingress.SUPERVISOR_IP, 40000)) as sidebar:
            session = (await sidebar.get("/api/auth/session", headers=headers)).json()
            assert session["user"]["role"] == "member"
            disabled = await admin.patch(f"/api/auth/users/{ha.id}", json={"disabled": True})
            assert disabled.status_code == 200
            refused = await sidebar.get("/api/auth/me", headers=headers)
            assert refused.status_code == 401


async def test_ingress_default_role_setting():
    await create_user()
    async with make_client() as admin:
        await login(admin)
        assert (await admin.get("/api/auth/settings")).json()["ingress_default_role"] == "member"
        response = await admin.patch("/api/auth/settings", json={"ingress_default_role": "kiosk"})
        assert response.status_code == 200 and response.json()["ingress_default_role"] == "kiosk"
        bad = await admin.patch("/api/auth/settings", json={"ingress_default_role": "root"})
        assert bad.status_code == 422
    assert (await AuthSettingsRepository.get()).ingress_default_role == "kiosk"
    [row] = await _audit("settings.changed")
    assert row.target == "auth_settings.ingress_default_role"
    assert row.details == {"ingress_default_role": "kiosk"}


async def test_close_user_closes_every_socket_of_the_user_but_the_kept_session(monkeypatch):
    manager = WebSocketManager()
    evicted: list[object] = []
    monkeypatch.setattr(manager, "_evict", lambda client, reason, code=0: evicted.append(client))

    def client(user_id: int, session_id: str | None, credential: str = "session"):
        user = User(id=user_id, username=f"u{user_id}", role="member", created_at=0, updated_at=0)
        principal = Principal(user=user, via="lan", credential=credential, session_id=session_id)  # type: ignore[arg-type]
        from app.websocket import _Client

        entry = _Client(object(), principal)  # type: ignore[arg-type]
        manager.active_connections[object()] = entry  # type: ignore[index]
        return entry

    kept = client(5, "keep")
    other_session = client(5, "other")
    trusted = client(5, None, credential="trusted_network")
    someone_else = client(6, "x")
    assert manager.close_user(5, except_session_id="keep") == 2
    assert evicted == [other_session, trusted]
    assert kept not in evicted and someone_else not in evicted


async def test_a_deleted_home_assistant_admin_comes_back_with_the_default_role(monkeypatch):
    from app.auth.providers import ingress

    monkeypatch.setenv("MESHLOOM_INSTALL_KIND", "addon")
    await create_user()
    headers = {"X-Remote-User-Id": HA_ID, "X-Remote-User-Name": "alice"}
    async with make_client(client=(ingress.SUPERVISOR_IP, 40000)) as sidebar:
        first = (await sidebar.get("/api/auth/session", headers=headers)).json()
        assert first["user"]["role"] == "admin"
        async with make_client() as admin:
            await login(admin)
            ha_id = first["user"]["id"]
            assert (
                await admin.patch(f"/api/auth/users/{ha_id}", json={"role": "viewer"})
            ).status_code == 200
            assert (await admin.delete(f"/api/auth/users/{ha_id}")).status_code == 200
        again = (await sidebar.get("/api/auth/session", headers=headers)).json()
        assert again["user"]["role"] == "member"
        assert again["user"]["id"] != ha_id
        other = {
            "X-Remote-User-Id": "0123456789abcdef0123456789abcdef",
            "X-Remote-User-Name": "bob",
        }
        assert (await sidebar.get("/api/auth/session", headers=other)).json()["user"][
            "role"
        ] == "member"
    assert await AuthSettingsRepository.ingress_admin_granted()


async def test_the_current_password_check_goes_through_the_login_delays():
    await create_user("member1", role="member")
    wrong = {"current_password": "not the password at all", "new_password": NEW_PASSWORD}
    async with make_client() as member:
        await login(member, "member1")
        statuses = [(await member.patch("/api/auth/me", json=wrong)).status_code for _ in range(8)]
        # Five free failures, then the pair is delayed, as on the login page.
        assert statuses[:5] == [403] * 5
        assert 429 in statuses[5:]
        right = {"current_password": PASSWORD, "new_password": NEW_PASSWORD}
        delayed = await member.patch("/api/auth/me", json=right)
        assert delayed.status_code == 429 and _code(delayed) == "auth_rate_limited"
        assert int(delayed.headers["Retry-After"]) >= 1
    rows = await _audit("password.changed", "delayed")
    assert len(rows) == 1 and rows[0].details["reason"] == "pair"
    # The new password was never stored.
    from app.auth.passwords import verify_password_async

    credentials = await UserRepository.get_credentials("member1")
    assert credentials is not None and credentials.password_hash is not None
    assert await verify_password_async(credentials.password_hash.get_secret_value(), PASSWORD)


async def test_the_current_password_check_shares_the_argon2_queue(monkeypatch):
    from app.auth.providers import password as provider

    await create_user("member1", role="member")
    async with make_client() as member:
        await login(member, "member1")
        monkeypatch.setattr(provider, "_pending", provider.MAX_QUEUED_VERIFICATIONS + 2)
        busy = await member.patch(
            "/api/auth/me", json={"current_password": PASSWORD, "new_password": NEW_PASSWORD}
        )
        assert busy.status_code == 503 and _code(busy) == "auth_busy"


async def test_every_refusal_of_the_users_api_is_audited():
    owner = await create_user()
    await create_user("member1", role="member")
    async with make_client() as admin:
        await login(admin)
        for payload in (
            {"username": "k", "role": "kiosk", "password": PASSWORD},
            {"username": "c", "role": "member"},
            {"username": "c", "role": "member", "password": "short"},
            {"username": "MEMBER1", "role": "member", "password": PASSWORD},
            {"username": " ", "role": "member", "password": PASSWORD},
        ):
            assert (await admin.post("/api/auth/users", json=payload)).status_code in (400, 409)
        assert (await admin.delete("/api/auth/users/999")).status_code == 404
        assert (
            await admin.post(f"/api/auth/users/{owner.id}/password", json={"password": "short"})
        ).status_code == 400
        assert (await admin.get("/api/auth/users")).status_code == 200
    async with make_client() as member:
        await login(member, "member1")
        assert (await member.get("/api/auth/users")).status_code == 403
        assert (await member.delete(f"/api/auth/users/{owner.id}")).status_code == 403
        assert (
            await member.patch(
                "/api/auth/me", json={"current_password": PASSWORD, "new_password": "short"}
            )
        ).status_code == 400
    created = await _audit("user.created", "refused")
    assert sorted(r.details["reason"] for r in created) == [
        "password_not_applicable",
        "password_policy",
        "password_required",
        "username_invalid",
        "username_taken",
    ]
    assert (
        next(r for r in created if r.details["reason"] == "password_policy").details["policy"]
        == "too_short"
    )
    [deleted_403, deleted_404] = sorted(
        await _audit("user.deleted", "refused"), key=lambda r: r.details["status"]
    )
    assert deleted_404.details == {"reason": "user_not_found", "status": 404}
    assert deleted_404.target == "user:999"
    assert deleted_403.details == {"reason": "forbidden", "status": 403}
    assert deleted_403.target == f"user:{owner.id}"
    [listed_403] = await _audit("users.listed", "refused")
    assert listed_403.details["reason"] == "forbidden"
    [listed] = await _audit("users.listed")
    assert listed.details == {"count": 2} and listed.actor_user_id == owner.id
    # The admin reset and the self-service change, without the typed password.
    password_refusals = await _audit("password.changed", "refused")
    assert [r.details["reason"] for r in password_refusals] == ["password_policy"] * 2
    assert all(r.details["policy"] == "too_short" for r in password_refusals)


async def test_a_kiosk_password_change_attempt_is_audited():
    from app.auth import sessions

    kiosk = await create_user("wall", role="kiosk", password=None)
    async with make_client() as client:
        issued = await sessions.issue_session(kiosk, ip=None, user_agent=None, via="lan")
        client.cookies.set("meshloom_session", issued.token)
        renamed = await client.patch("/api/auth/me", json={"display_name": "Wall"})
        assert renamed.status_code == 403 and _code(renamed) == "auth_forbidden"
        assert await _audit("password.changed", "refused") == []
        changed = await client.patch(
            "/api/auth/me", json={"current_password": PASSWORD, "new_password": NEW_PASSWORD}
        )
        assert changed.status_code == 403 and _code(changed) == "auth_forbidden"
    [row] = await _audit("password.changed", "refused")
    assert row.details == {"reason": "forbidden"}
    assert row.target == f"user:{kiosk.id}"


@pytest.mark.parametrize("route", ["create", "patch", "password"])
async def test_a_must_change_race_is_a_coded_400_not_a_500(monkeypatch, route):
    """The account can change between the route's check and the repository
    write (a concurrent demotion to kiosk): the repository's refusal is the
    same coded 400 as the route's own check, and it is audited."""
    from app.repository.auth import MustChangeNotApplicableError

    await create_user()
    member = await create_user("member1", role="member")

    async def refuse(*args, **kwargs):
        raise MustChangeNotApplicableError("raced")

    method = {"create": "create", "patch": "update", "password": "set_password_hash"}[route]
    async with make_client() as admin:
        await login(admin)
        monkeypatch.setattr(UserRepository, method, staticmethod(refuse))
        if route == "create":
            response = await admin.post(
                "/api/auth/users",
                json={"username": "late", "role": "member", "password": NEW_PASSWORD},
            )
            action = "user.created"
        elif route == "patch":
            response = await admin.patch(
                f"/api/auth/users/{member.id}", json={"must_change_password": True}
            )
            action = "user.changed"
        else:
            response = await admin.post(
                f"/api/auth/users/{member.id}/password", json={"password": NEW_PASSWORD}
            )
            action = "password.changed"
    assert response.status_code == 400
    assert _code(response) == "auth_password_not_applicable"
    [row] = await _audit(action, "refused")
    assert row.details == {"reason": "password_not_applicable", "status": 400}
