"""API and device tokens (MSHL-69): creation, scopes, Bearer on HTTP and on the
WebSocket upgrade, revocation, expiry, ingress refusal, audit."""

import asyncio
import time

import pytest

from app.auth import middleware
from app.auth.middleware import AuthMiddleware
from app.auth.models import User
from app.auth.permissions import permissions_of
from app.auth.principal import Principal, get_principal
from app.auth.providers import token as token_provider
from app.auth.tokens import hash_token
from app.repository.auth import (
    MAX_TOKENS_PER_USER,
    ApiTokenRepository,
    AuthAuditRepository,
    UserRepository,
)
from app.websocket import REVOKED_CLOSE_CODE, ws_manager
from tests.auth_helpers import create_user, login, make_client
from tests.test_auth_session_hygiene import ManagedSocket

pytestmark = [pytest.mark.real_auth, pytest.mark.usefixtures("test_db", "fast_argon2")]


@pytest.fixture(autouse=True)
def _fresh_failure_audit():
    token_provider.reset_failure_audit()
    yield
    token_provider.reset_failure_audit()


def bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


async def _create(client, scopes, **extra):
    return await client.post("/api/auth/tokens", json={"name": "phone", "scopes": scopes, **extra})


async def _member_token(scopes: list[str]) -> tuple[User, str, str]:
    """A member account and a token it created through the API."""
    await create_user("owner")
    member = await create_user("nadia", role="member")
    async with make_client() as client:
        await login(client, "nadia")
        response = await _create(client, scopes)
    assert response.status_code == 201, response.text
    body = response.json()
    return member, body["id"], body["token"]


async def _audit(action: str):
    return [row for row in await AuthAuditRepository.list_recent(limit=100) if row.action == action]


# ---------------------------------------------------------------- creation


async def test_creation_returns_the_secret_once_and_stores_only_its_hash(test_db):
    member, token_id, token = await _member_token(["read", "send"])
    assert token.startswith("mlt_") and len(token) == 4 + 43

    row = await ApiTokenRepository.get(token_id)
    assert row is not None and row.user_id == member.id
    assert row.scopes == ["read", "send"] and row.origin == "ui"
    async with test_db.readonly() as conn:
        async with conn.execute("SELECT * FROM api_tokens") as cursor:
            stored = [tuple(r) for r in await cursor.fetchall()]
    assert hash_token(token) in stored[0]
    assert all(token not in str(value) for value in stored[0])

    async with make_client() as client:
        await login(client, "nadia")
        listed = (await client.get("/api/auth/tokens")).json()
    assert [t["id"] for t in listed] == [token_id]
    assert "token" not in listed[0] and listed[0]["username"] == "nadia"

    (created,) = await _audit("token.created")
    assert created.outcome == "success" and created.target == f"token:{token_id}"
    assert created.details["scopes"] == ["read", "send"]
    assert token not in str(created.model_dump())


async def test_a_member_cannot_create_a_repeater_admin_token():
    await create_user("owner")
    await create_user("nadia", role="member")
    async with make_client() as client:
        await login(client, "nadia")
        response = await _create(client, ["read", "repeater_admin"])
    assert response.status_code == 400
    assert response.json()["detail"]["code"] == "auth_token_scope"
    assert response.json()["detail"]["params"] == {"scope": "repeater_admin"}
    assert await ApiTokenRepository.list_live() == []
    (refused,) = await _audit("token.created")
    assert refused.outcome == "failure" and refused.details["reason"] == "token_scope"


@pytest.mark.parametrize(
    "body",
    [
        {"name": "x", "scopes": []},
        {"name": "x", "scopes": ["everything"]},
        {"name": "   ", "scopes": ["read"]},
        {"name": "x" * 65, "scopes": ["read"]},
    ],
)
async def test_invalid_bodies_are_422(body):
    await create_user()
    async with make_client() as client:
        await login(client)
        response = await client.post("/api/auth/tokens", json=body)
    assert response.status_code == 422


async def test_an_expiry_in_the_past_is_refused():
    await create_user()
    async with make_client() as client:
        await login(client)
        response = await _create(client, ["read"], expires_at=int(time.time()) - 1)
    assert response.status_code == 400
    assert response.json()["detail"]["code"] == "auth_token_expiry_invalid"


async def test_a_viewer_cannot_hold_tokens():
    await create_user()
    await create_user("someone", role="viewer")
    async with make_client() as client:
        await login(client, "someone")
        response = await _create(client, ["read"])
        assert response.status_code == 403
        assert response.json()["detail"]["code"] == "auth_forbidden"
        assert (await client.get("/api/auth/tokens")).status_code == 403
    (refused,) = await _audit("token.created")
    assert refused.outcome == "refused"


def test_only_member_and_admin_hold_tokens():
    # ``kiosk`` signs in with a host-made token (T60), never through the API.
    assert frozenset({"admin", "member"}) == token_provider.TOKEN_ROLES


async def test_a_token_cannot_mint_list_or_revoke_tokens():
    await create_user()
    async with make_client() as client:
        await login(client)
        created = (await _create(client, ["admin"])).json()
    async with make_client() as device:
        headers = bearer(created["token"])
        assert (await device.get("/api/auth/tokens", headers=headers)).status_code == 403
        response = await device.post(
            "/api/auth/tokens", json={"name": "x", "scopes": ["read"]}, headers=headers
        )
        assert response.status_code == 403
        assert (
            await device.delete(f"/api/auth/tokens/{created['id']}", headers=headers)
        ).status_code == 403


async def test_the_live_token_cap(monkeypatch):
    from app.repository import auth as auth_repo

    monkeypatch.setattr(auth_repo, "MAX_TOKENS_PER_USER", 2)
    await create_user()
    async with make_client() as client:
        await login(client)
        assert (await _create(client, ["read"])).status_code == 201
        assert (await _create(client, ["read"])).status_code == 201
        response = await _create(client, ["read"])
    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "auth_token_limit"
    assert MAX_TOKENS_PER_USER == 100


# ---------------------------------------------------------------- use on HTTP


async def test_a_read_token_reads_and_gets_403_on_send_routes():
    _, _, token = await _member_token(["read"])
    async with make_client() as device:
        assert (await device.get("/api/channels", headers=bearer(token))).status_code == 200
        for path, body in (
            ("/api/messages/channel", {"channel_key": "00" * 16, "text": "hi"}),
            ("/api/messages/direct", {"destination": "00" * 32, "text": "hi"}),
        ):
            response = await device.post(path, json=body, headers=bearer(token))
            assert response.status_code == 403, path
            assert response.json()["detail"]["params"] == {"permission": "send"}


async def test_a_token_in_a_query_string_is_ignored():
    _, _, token = await _member_token(["read"])
    async with make_client() as client:
        for name in ("token", "access_token", "bearer", "authorization"):
            response = await client.get(f"/api/channels?{name}={token}")
            assert response.status_code == 401, name


async def test_bearer_requests_are_exempt_from_the_origin_rule():
    _, _, token = await _member_token(["read", "send"])
    async with make_client() as device:
        response = await device.delete(
            "/api/messages/999999",
            headers={**bearer(token), "Origin": "https://evil.example"},
        )
    # Past the gate's CSRF rule (403 auth_origin_rejected) and the permission.
    assert response.status_code != 403


async def test_unknown_or_malformed_tokens_are_401_without_a_basic_challenge():
    await create_user()
    async with make_client() as client:
        for value in ("mlt_" + "A" * 43, "not-a-token", "mlt_" + "x" * 200):
            response = await client.get("/api/channels", headers=bearer(value))
            assert response.status_code == 401
            assert response.json()["detail"]["code"] == "auth_invalid_credentials"
            assert "www-authenticate" not in response.headers
    assert await _audit("token.used") == []


async def test_last_use_is_written_at_most_once_a_minute():
    _, token_id, token = await _member_token(["read"])
    start = 1_900_000_000
    assert await token_provider.resolve_token(token, "lan", None, now=start) is not None
    assert (await ApiTokenRepository.get(token_id)).last_used_at == start  # type: ignore[union-attr]
    assert await token_provider.resolve_token(token, "lan", None, now=start + 30) is not None
    assert (await ApiTokenRepository.get(token_id)).last_used_at == start  # type: ignore[union-attr]
    assert await token_provider.resolve_token(token, "lan", None, now=start + 61) is not None
    assert (await ApiTokenRepository.get(token_id)).last_used_at == start + 61  # type: ignore[union-attr]


# ---------------------------------------------------------------- scopes vs role


async def test_a_token_never_exceeds_its_owners_current_role():
    admin = await create_user()
    await create_user("second")
    async with make_client() as client:
        await login(client)
        token = (await _create(client, ["admin"])).json()["token"]
    principal = await token_provider.resolve_token(token, "lan", None)
    assert principal is not None and "admin" in permissions_of(principal)

    await UserRepository.update(admin.id, role="member")
    principal = await token_provider.resolve_token(token, "lan", None)
    assert principal is not None and permissions_of(principal) == frozenset()
    async with make_client() as device:
        assert (await device.get("/api/auth/users", headers=bearer(token))).status_code == 403
        assert (await device.get("/api/channels", headers=bearer(token))).status_code == 403


async def test_a_scope_list_with_nothing_valid_left_is_refused(test_db):
    member, token_id, token = await _member_token(["read"])
    async with test_db.tx() as conn:
        await conn.execute(
            "UPDATE api_tokens SET scopes = '[\"galaxy\"]' WHERE id = ?", (token_id,)
        )
    assert await token_provider.resolve_token(token, "lan", None) is None
    (row,) = await _audit("token.used")
    assert row.details == {"reason": "scopes_invalid"} and row.actor_user_id == member.id


# ---------------------------------------------------------------- refusals


async def test_a_revoked_token_answers_401_at_once_and_its_websocket_closes():
    member, token_id, token = await _member_token(["read"])
    socket = ManagedSocket()
    principal = await token_provider.resolve_token(token, "lan", None)
    await ws_manager.connect(socket.ws, principal)
    try:
        async with make_client() as client, make_client() as device:
            assert (await device.get("/api/channels", headers=bearer(token))).status_code == 200
            await login(client, "nadia")
            assert (await client.delete(f"/api/auth/tokens/{token_id}")).status_code == 204
            assert await socket.wait_closed(timeout=1.0) == REVOKED_CLOSE_CODE
            response = await device.get("/api/channels", headers=bearer(token))
            assert response.status_code == 401
            assert response.json()["detail"]["code"] == "auth_invalid_credentials"
            # A second refused use within a minute writes no second row.
            assert (await device.get("/api/channels", headers=bearer(token))).status_code == 401
            assert (await client.get("/api/auth/tokens")).json() == []
            assert (await client.delete(f"/api/auth/tokens/{token_id}")).status_code == 404
    finally:
        await ws_manager.disconnect(socket.ws)
    revoked = await _audit("token.revoked")
    assert [(r.outcome, r.target) for r in revoked] == [
        ("failure", f"token:{token_id}"),
        ("success", f"token:{token_id}"),
    ]
    (used,) = await _audit("token.used")
    assert used.outcome == "failure" and used.details == {"reason": "revoked"}
    assert used.actor_token_id == token_id and used.actor_user_id == member.id


async def test_an_expired_token_is_refused():
    user = await create_user()
    now = int(time.time())
    row, token = await token_provider.issue_token(user, "old", ["read"], expires_at=now + 60)
    assert await token_provider.resolve_token(token, "lan", None, now=now) is not None
    assert await token_provider.resolve_token(token, "lan", None, now=now + 60) is None
    (used,) = await _audit("token.used")
    assert used.details == {"reason": "expired"} and used.target == f"token:{row.id}"


async def test_a_disabled_owner_signs_the_token_out():
    user = await create_user()
    await create_user("second")
    _, token = await token_provider.issue_token(user, "phone", ["read"])
    await UserRepository.update(user.id, disabled=True)
    assert await token_provider.resolve_token(token, "lan", None) is None
    (used,) = await _audit("token.used")
    assert used.details == {"reason": "owner_disabled"}


async def test_tokens_are_refused_under_ingress():
    user = await create_user()
    _, token = await token_provider.issue_token(user, "phone", ["read"])
    assert await token_provider.resolve_token(token, "ingress", "172.30.32.2") is None
    assert await token_provider.resolve_token(token, "relay", None) is not None
    (used,) = await _audit("token.used")
    assert used.details == {"reason": "ingress"} and used.via == "ingress"


async def test_a_deleted_owner_takes_its_tokens_with_it(test_db):
    await create_user()
    member = await create_user("nadia", role="member")
    _, token = await token_provider.issue_token(member, "phone", ["read"])
    await UserRepository.delete(member.id)
    assert await token_provider.resolve_token(token, "lan", None) is None
    async with test_db.readonly() as conn:
        async with conn.execute("SELECT COUNT(*) FROM api_tokens") as cursor:
            assert (await cursor.fetchone())[0] == 0


# ---------------------------------------------------------------- admin vs member


async def test_admin_sees_and_revokes_every_token_a_member_only_its_own():
    admin = await create_user()
    member = await create_user("nadia", role="member")
    admin_row, _ = await token_provider.issue_token(admin, "script", ["admin"])
    member_row, _ = await token_provider.issue_token(member, "phone", ["read"])
    async with make_client() as admin_client, make_client() as member_client:
        await login(admin_client)
        await login(member_client, "nadia")
        assert {t["id"] for t in (await admin_client.get("/api/auth/tokens")).json()} == {
            admin_row.id,
            member_row.id,
        }
        assert [t["id"] for t in (await member_client.get("/api/auth/tokens")).json()] == [
            member_row.id
        ]
        response = await member_client.delete(f"/api/auth/tokens/{admin_row.id}")
        assert response.status_code == 404
        assert response.json()["detail"]["code"] == "auth_token_not_found"
        assert (await admin_client.delete(f"/api/auth/tokens/{member_row.id}")).status_code == 204
    assert (await ApiTokenRepository.get(admin_row.id)).revoked_at is None  # type: ignore[union-attr]
    assert (await ApiTokenRepository.get(member_row.id)).revoked_at is not None  # type: ignore[union-attr]
    success = [r for r in await _audit("token.revoked") if r.outcome == "success"]
    assert success[0].details == {"owner": member.id}


# ---------------------------------------------------------------- WebSocket upgrade


async def _upgrade(headers: list[tuple[bytes, bytes]], query: bytes = b"") -> tuple[list, list]:
    """Run one WebSocket upgrade of ``/api/ws`` through the real auth gate."""
    reached: list[Principal | None] = []
    sent: list[dict] = []

    async def app(scope, receive, send):
        reached.append(get_principal_from(scope))
        await send({"type": "websocket.accept"})

    def get_principal_from(scope):
        class _Conn:
            pass

        conn = _Conn()
        conn.scope = scope  # type: ignore[attr-defined]
        return get_principal(conn)

    async def receive():
        return {"type": "websocket.connect"}

    async def send(message):
        sent.append(message)

    scope = {
        "type": "websocket",
        "path": "/api/ws",
        "query_string": query,
        "headers": [(b"host", b"meshloom.local"), *headers],
        "client": ("192.168.1.20", 50000),
        "scheme": "ws",
    }
    await AuthMiddleware(app)(scope, receive, send)
    return reached, sent


async def test_a_read_token_opens_the_websocket_with_the_header():
    member, token_id, token = await _member_token(["read"])
    reached, sent = await _upgrade([(b"authorization", f"Bearer {token}".encode())])
    (principal,) = reached
    assert principal is not None and principal.user_id == member.id
    assert principal.token_id == token_id and principal.credential == "token"
    assert permissions_of(principal) == frozenset({"read"})
    assert sent == [{"type": "websocket.accept"}]


async def test_the_websocket_ignores_a_token_in_the_query_string():
    _, _, token = await _member_token(["read"])
    for name in (b"token", b"access_token"):
        reached, sent = await _upgrade([], query=name + b"=" + token.encode())
        assert reached == []
        assert sent == [{"type": "websocket.close", "code": middleware.WS_REFUSED_CLOSE_CODE}]


async def test_a_revoked_token_cannot_open_a_websocket():
    _, token_id, token = await _member_token(["read"])
    await token_provider.revoke_token(token_id)
    reached, sent = await _upgrade([(b"authorization", f"Bearer {token}".encode())])
    assert reached == []
    assert sent == [{"type": "websocket.close", "code": middleware.WS_REFUSED_CLOSE_CODE}]


async def test_the_recheck_closes_token_sockets_that_expired_or_were_revoked_elsewhere(
    monkeypatch, test_db
):
    from app import websocket as ws_module

    monkeypatch.setattr(ws_module, "SESSION_RECHECK_INTERVAL_SECONDS", 0.01)
    user = await create_user()
    revoked_row, revoked = await token_provider.issue_token(user, "a", ["read"])
    kept_row, kept = await token_provider.issue_token(user, "b", ["read"])
    sockets = []
    for secret in (revoked, kept):
        socket = ManagedSocket()
        await ws_manager.connect(socket.ws, await token_provider.resolve_token(secret, "lan", None))
        sockets.append(socket)
    try:
        # Another process (meshloom-admin) writes the database.
        async with test_db.tx() as conn:
            await conn.execute(
                "UPDATE api_tokens SET revoked_at = 1 WHERE id = ?", (revoked_row.id,)
            )
        assert await sockets[0].wait_closed(timeout=1.0) == REVOKED_CLOSE_CODE
        await asyncio.sleep(0.05)
        assert not sockets[1].closed.is_set()
    finally:
        for socket in sockets:
            await ws_manager.disconnect(socket.ws)
        task = ws_manager._recheck_task
        if task is not None and not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
    assert kept_row.id != revoked_row.id


async def test_websocket_events_are_scoped_for_token_principals_too():
    from app import websocket as ws_module
    from app.auth.ws_events import WsEvent

    _, _, read_only = await _member_token(["read"])
    principal = await token_provider.resolve_token(read_only, "lan", None)
    private = {"type": "PRIV", "conversation_key": "ab" * 32, "text": "hi"}
    channel = {"type": "CHAN", "conversation_key": "AB" * 16, "text": "hi"}
    assert ws_module._view(WsEvent.MESSAGE, private, principal, "read") is None
    assert ws_module._view(WsEvent.MESSAGE, channel, principal, "read") == ws_module._FULL
    assert ws_module._view(WsEvent.RADIO_WIFI, {}, principal, "admin") is None


async def test_a_token_socket_closes_when_the_token_expires():
    user = await create_user()
    now = int(time.time())
    _, token = await token_provider.issue_token(user, "short", ["read"], expires_at=now + 1)
    principal = await token_provider.resolve_token(token, "lan", None, now=now)
    assert principal is not None and principal.token_expires_at == now + 1
    socket = ManagedSocket()
    await ws_manager.connect(socket.ws, principal)
    try:
        assert await socket.wait_closed(timeout=3.0) == REVOKED_CLOSE_CODE
    finally:
        await ws_manager.disconnect(socket.ws)
