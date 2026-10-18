"""WebSocket upgrades through the auth gate: refused before accept() without a session."""

import pytest
from fastapi import FastAPI, WebSocket
from starlette.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from app.auth import middleware, sessions
from app.auth.middleware import AuthMiddleware
from app.auth.models import AuthSession, User
from app.auth.principal import get_principal

pytestmark = pytest.mark.real_auth

USER = User(id=7, username="owner", role="admin", created_at=0, updated_at=0, has_password=True)
SESSION = AuthSession(id="s1", user_id=7, created_at=0, last_seen_at=0, expires_at=2**31)


def _app(accepted: list[str]) -> FastAPI:
    app = FastAPI()
    app.add_middleware(AuthMiddleware)

    @app.websocket("/api/ws")
    async def ws(websocket: WebSocket) -> None:
        accepted.append("called")
        principal = get_principal(websocket)
        await websocket.accept()
        await websocket.send_json({"user": principal.user.username if principal else None})
        await websocket.close()

    return app


@pytest.fixture
def valid_cookie(monkeypatch):
    async def resolve(token, now=None):
        if token == "good-token":
            return sessions.ResolvedSession(session=SESSION, user=USER)
        return None

    monkeypatch.setattr(sessions, "resolve_session", resolve)


@pytest.mark.parametrize("cookie", [None, "bad-token"])
def test_upgrade_without_a_valid_session_is_refused_before_accept(valid_cookie, cookie):
    reached: list[str] = []
    client = TestClient(_app(reached))
    if cookie:
        client.cookies.set("meshloom_session", cookie)
    with pytest.raises(WebSocketDisconnect) as refused:
        with client.websocket_connect("/api/ws"):
            pass
    assert refused.value.code == middleware.WS_REFUSED_CLOSE_CODE
    assert reached == []


def test_upgrade_with_a_session_reaches_the_route_with_its_principal(valid_cookie):
    reached: list[str] = []
    client = TestClient(_app(reached))
    client.cookies.set("meshloom_session", "good-token")
    with client.websocket_connect("/api/ws") as websocket:
        assert websocket.receive_json() == {"user": "owner"}
    assert reached == ["called"]


def test_the_real_ws_route_is_behind_the_gate():
    from app.main import app

    client = TestClient(app)
    with pytest.raises(WebSocketDisconnect) as refused:
        with client.websocket_connect("/api/ws"):
            pass
    assert refused.value.code == middleware.WS_REFUSED_CLOSE_CODE
