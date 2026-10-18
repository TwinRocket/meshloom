"""Home Assistant ingress identities (app/auth/providers/ingress.py) through the real gate."""

import importlib.util
import re
from pathlib import Path

import httpx
import pytest
import uvicorn
from fastapi import FastAPI, WebSocket
from starlette.testclient import TestClient
from starlette.websockets import WebSocketDisconnect
from uvicorn.main import main as uvicorn_main

from app.auth import middleware, setup_code
from app.auth.middleware import AuthMiddleware
from app.auth.principal import get_principal
from app.auth.providers import ingress
from app.repository.auth import (
    AuthAuditRepository,
    AuthSettingsRepository,
    UserRepository,
)
from tests.auth_helpers import ADMIN, create_user, login, make_client, set_cookie_headers

pytestmark = [pytest.mark.real_auth, pytest.mark.usefixtures("test_db", "fast_argon2")]

SUPERVISOR = (ingress.SUPERVISOR_IP, 40000)
# A published port 8000 reaches the add-on through the Docker gateway, not the Supervisor.
PUBLISHED_PORT_PEER = ("172.30.32.1", 40000)
LAN_PEER = ("192.168.1.20", 40000)

ALICE_ID = "8f6e2c1d4b3a49e0a1b2c3d4e5f60718"
BOB_ID = "0123456789abcdef0123456789abcdef"


def ha_headers(user_id: str = ALICE_ID, name: str = "alice", display: str = "Alice") -> dict:
    return {
        "X-Remote-User-Id": user_id,
        "X-Remote-User-Name": name,
        "X-Remote-User-Display-Name": display,
    }


@pytest.fixture(autouse=True)
def addon(monkeypatch):
    monkeypatch.setenv("MESHLOOM_INSTALL_KIND", "addon")


def _code(response) -> str:
    return response.json()["detail"]["code"]


async def test_supervisor_headers_sign_the_user_in_and_lan_copies_are_ignored():
    await create_user()
    async with make_client(client=SUPERVISOR) as client:
        response = await client.get("/api/auth/session", headers=ha_headers())
        body = response.json()
        assert body["authenticated"] is True
        assert body["via"] == "ingress"
        assert body["user"]["display_name"] == "Alice"
        assert body["methods"] == {"password": False, "ingress": True, "passkey": False}
        assert (await client.get("/api/contacts", headers=ha_headers())).status_code == 200
    for peer in (LAN_PEER, PUBLISHED_PORT_PEER):
        async with make_client(client=peer) as client:
            response = await client.get("/api/contacts", headers=ha_headers())
            assert response.status_code == 401, peer
            assert _code(response) == "auth_required"
            session = (await client.get("/api/auth/session", headers=ha_headers())).json()
            assert session["authenticated"] is False
            assert session["methods"] == {"password": True, "ingress": False, "passkey": False}
    # Only the Supervisor request created an identity.
    assert [u.provider for u in await UserRepository.list_all()] == ["local", "ingress"]


async def test_supervisor_address_outside_the_add_on_is_not_ingress(monkeypatch):
    monkeypatch.setenv("MESHLOOM_INSTALL_KIND", "compose")
    await create_user()
    async with make_client(client=SUPERVISOR) as client:
        assert (await client.get("/api/contacts", headers=ha_headers())).status_code == 401
    assert await UserRepository.get_by_external_id("ingress", ALICE_ID) is None


def test_a_relayed_request_is_never_ingress():
    scope = {"type": "http", "client": SUPERVISOR, "state": {"via": "relay"}}
    assert ingress.is_ingress_request(scope) is False
    assert middleware.request_via(scope) == "relay"
    assert middleware.request_via({"type": "http", "client": SUPERVISOR}) == "ingress"


async def test_first_ingress_visit_on_an_empty_database_creates_the_admin():
    # A 4.18.x database: setup would ask for the installation code.
    await setup_code.prepare_first_run(existing_database=True, basic_username="", basic_password="")
    assert await AuthSettingsRepository.get_setup_code_hash() is not None
    async with make_client(client=SUPERVISOR) as client:
        anonymous = (await client.get("/api/auth/session")).json()
        assert anonymous["setup_required"] is True
        assert anonymous["methods"] == {"password": False, "ingress": True, "passkey": False}

        first = (await client.get("/api/auth/session", headers=ha_headers())).json()
        assert first["authenticated"] is True
        assert first["setup_required"] is False
        assert first["user"]["role"] == "admin"
        assert await AuthSettingsRepository.get_setup_code_hash() is None

        second = await client.get("/api/auth/session", headers=ha_headers(BOB_ID, "bob", "Bob"))
        assert second.json()["user"]["role"] == "member"
        # The same HA user again is the same account.
        again = (await client.get("/api/auth/session", headers=ha_headers())).json()
        assert again["user"]["id"] == first["user"]["id"]
    users = await UserRepository.list_all()
    assert [(u.username, u.role, u.provider, u.has_password) for u in users] == [
        ("alice", "admin", "ingress", False),
        ("bob", "member", "ingress", False),
    ]
    audit = await AuthAuditRepository.list_recent()
    assert {row.action for row in audit} >= {"user.created"}


async def test_later_identities_take_the_default_role_and_a_local_admin_does_not_count():
    # 4.18.x with basic_auth_*: the imported account exists before any ingress visit.
    await create_user()
    await AuthSettingsRepository.update(ingress_default_role="viewer")
    async with make_client(client=SUPERVISOR) as client:
        first = (await client.get("/api/auth/session", headers=ha_headers())).json()
        second = (
            await client.get("/api/auth/session", headers=ha_headers(BOB_ID, "bob", "Bob"))
        ).json()
    assert first["user"]["role"] == "admin"
    assert second["user"]["role"] == "viewer"


async def test_no_cookie_is_ever_set_or_read_under_ingress():
    await create_user()
    async with make_client(client=SUPERVISOR) as client:
        for method, path in [
            ("GET", "/api/auth/session"),
            ("GET", "/api/contacts"),
            ("POST", "/api/auth/logout"),
        ]:
            response = await client.request(method, path, headers=ha_headers())
            assert response.status_code == 200, path
            assert set_cookie_headers(response) == [], path
        # A cookie sent through ingress is never read.
        client.cookies.set("meshloom_session", "anything")
        assert (await client.get("/api/auth/me")).status_code == 401


async def test_password_login_and_setup_are_refused_under_ingress():
    await create_user()
    async with make_client(client=SUPERVISOR) as client:
        response = await client.post(
            "/api/auth/login", json={"username": ADMIN, "password": "correct horse battery"}
        )
        assert response.status_code == 403
        assert _code(response) == "auth_ingress_only"
        assert set_cookie_headers(response) == []
        response = await client.post(
            "/api/auth/setup", json={"username": "x", "password": "correct horse battery"}
        )
        assert response.status_code == 403
        assert _code(response) == "auth_ingress_only"


async def test_ingress_without_user_headers_lands_on_the_login_page():
    await create_user()
    async with make_client(client=SUPERVISOR) as client:
        page = await client.get("/")
        assert page.status_code != 401
        session = await client.get("/api/auth/session")
        assert session.status_code == 200
        assert session.json()["authenticated"] is False
        assert set_cookie_headers(session) == []
        assert _code(await client.get("/api/contacts")) == "auth_required"


async def test_published_port_still_requires_a_normal_login():
    await create_user()
    async with make_client(client=PUBLISHED_PORT_PEER) as client:
        assert (await client.get("/api/contacts", headers=ha_headers())).status_code == 401
        response = await login(client)
        assert set_cookie_headers(response) != []
        assert response.json()["via"] == "lan"
        assert (await client.get("/api/contacts")).status_code == 200


async def test_a_disabled_identity_is_refused():
    async with make_client(client=SUPERVISOR) as client:
        first = (await client.get("/api/auth/session", headers=ha_headers())).json()
        await UserRepository.update(first["user"]["id"], disabled=True)
        response = await client.get("/api/contacts", headers=ha_headers())
        assert response.status_code == 401
        assert _code(response) == "auth_invalid_credentials"


async def test_unsafe_ingress_requests_follow_the_origin_rule():
    async with make_client(client=SUPERVISOR) as client:
        foreign = await client.post(
            "/api/auth/logout",
            headers={
                **ha_headers(),
                "Origin": "http://evil.example",
                "Sec-Fetch-Site": "cross-site",
            },
        )
        assert foreign.status_code == 403
        assert _code(foreign) == "auth_origin_rejected"
        # The HA frontend's own requests: same origin as the page, X-Forwarded-Host set.
        own = await client.post(
            "/api/auth/logout",
            headers={
                **ha_headers(),
                "Origin": "http://homeassistant.local:8123",
                "X-Forwarded-Host": "homeassistant.local:8123",
            },
        )
        assert own.status_code == 200


async def test_display_name_follows_home_assistant_and_names_do_not_clash():
    await create_user("alice")
    utf8 = {**ha_headers(display="Élise"), "X-Remote-User-Display-Name": "Élise".encode()}
    async with make_client(client=SUPERVISOR) as client:
        created = (await client.get("/api/auth/session", headers=utf8)).json()
        assert created["user"]["username"] == "alice-ha"
        assert created["user"]["display_name"] == "Élise"
        renamed_headers = {**ha_headers(), "X-Remote-User-Display-Name": "Élise M.".encode()}
        renamed = (await client.get("/api/auth/session", headers=renamed_headers)).json()
        assert renamed["user"]["display_name"] == "Élise M."


@pytest.mark.parametrize("bad_id", ["", " ", "x" * 200, "idé"])
def test_malformed_ids_are_not_identities(bad_id):
    assert ingress.ingress_identity({"x-remote-user-id": bad_id}) is None


def test_websocket_upgrade_through_ingress_carries_the_identity(monkeypatch):
    from app.auth.models import User

    user = User(
        id=3, username="alice", role="admin", provider="ingress", created_at=0, updated_at=0
    )

    async def resolve(headers, ip):
        return user if headers.get("x-remote-user-id") == ALICE_ID else None

    monkeypatch.setattr(ingress, "resolve_ingress_user", resolve)
    app = FastAPI()
    app.add_middleware(AuthMiddleware)

    @app.websocket("/api/ws")
    async def ws(websocket: WebSocket) -> None:
        principal = get_principal(websocket)
        await websocket.accept()
        await websocket.send_json({"via": principal.via if principal else None})
        await websocket.close()

    client = TestClient(app, client=SUPERVISOR)
    with client.websocket_connect("/api/ws", headers=ha_headers()) as socket:
        assert socket.receive_json() == {"via": "ingress"}
    with pytest.raises(WebSocketDisconnect):
        with client.websocket_connect("/api/ws"):
            pass


async def test_no_basic_challenge_under_ingress():
    await create_user()
    await AuthSettingsRepository.update(basic_imported=True)
    async with make_client(client=LAN_PEER) as client:
        assert "www-authenticate" in (await client.get("/api/contacts")).headers
    async with make_client(client=SUPERVISOR) as client:
        anonymous = await client.get("/api/contacts")
        assert anonymous.status_code == 401
        assert "www-authenticate" not in anonymous.headers
        first = (await client.get("/api/auth/session", headers=ha_headers())).json()
        await UserRepository.update(first["user"]["id"], disabled=True)
        disabled = await client.get("/api/contacts", headers=ha_headers())
        assert disabled.status_code == 401
        assert "www-authenticate" not in disabled.headers


REPO = Path(__file__).resolve().parents[1]


def _uvicorn_args(launcher: str) -> list[str]:
    """The uvicorn arguments after ``app.main:app`` in a launcher's command line."""
    if launcher == "meshloom/run.py":
        spec = importlib.util.spec_from_file_location("addon_run", REPO / launcher)
        assert spec and spec.loader
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        argv = list(module.UVICORN_ARGV)
    elif launcher == "scripts/setup/install_docker.sh":
        # The snake-oil compose: one ``echo "      - <arg>"`` per list item after ``command:``.
        text = (REPO / launcher).read_text(encoding="utf-8")
        block = text.split('echo "    command:"', 1)[1].split("\n    fi", 1)[0]
        argv = [
            item.replace('\\"', "")
            for item in re.findall(r'echo "      - (.*)"$', block, flags=re.MULTILINE)
        ]
    else:
        text = (REPO / launcher).read_text(encoding="utf-8")
        line = next(line for line in text.splitlines() if "app.main:app" in line)
        argv = re.findall(r"[\w.:/-]+", line.split("app.main:app", 1)[1])
        argv = ["app.main:app", *argv]
    return argv[argv.index("app.main:app") + 1 :]


LAUNCHERS = [
    "meshloom/run.py",
    "Dockerfile",
    "pkg/nfpm/meshloom.service",
    "scripts/setup/install_service.sh",
    "scripts/setup/install_docker.sh",
]


@pytest.mark.parametrize("launcher", LAUNCHERS)
def test_every_launcher_disables_uvicorn_proxy_headers(launcher):
    """``app/middleware/proxy_headers.py`` is the one interpreter of forwarded headers:
    uvicorn must not rewrite ``scope["client"]`` from ``X-Forwarded-For`` (docs/dev/auth.md)."""
    params = uvicorn_main.make_context("uvicorn", ["app.main:app", *_uvicorn_args(launcher)]).params
    assert params["proxy_headers"] is False, launcher


@pytest.mark.parametrize("proxy_headers", [False, True])
async def test_a_loopback_caller_forging_x_forwarded_for_is_not_the_supervisor(proxy_headers):
    """The add-on's uvicorn stack: a bot or a webhook aimed at 127.0.0.1:8000 cannot pass
    for the Supervisor. ``True`` shows what ``--no-proxy-headers`` prevents."""
    from app.main import app

    await create_user()
    config = uvicorn.Config(app, proxy_headers=proxy_headers, lifespan="off")
    config.load()
    transport = httpx.ASGITransport(app=config.loaded_app, client=("127.0.0.1", 50000))
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        session = await client.get(
            "/api/auth/session",
            headers={**ha_headers(), "X-Forwarded-For": ingress.SUPERVISOR_IP},
        )
    assert session.json()["authenticated"] is proxy_headers
    created = await UserRepository.get_by_external_id("ingress", ALICE_ID)
    assert (created is not None) is proxy_headers


def _behind_proxy_headers(peer: tuple[str, int]) -> httpx.AsyncClient:
    """The app as app.main builds it, trusted-proxy middleware included."""
    return make_client(client=peer)


async def test_ingress_reads_the_tcp_peer_even_when_the_supervisor_is_a_trusted_proxy():
    """The Supervisor forwards the browser's address; listing it as a trusted
    proxy must not hide that the request came through ingress."""
    from app.middleware import proxy_headers

    await create_user()
    await AuthSettingsRepository.update(trusted_proxies=["172.30.32.0/23"])
    try:
        async with _behind_proxy_headers(SUPERVISOR) as client:
            session = await client.get(
                "/api/auth/session", headers={**ha_headers(), "X-Forwarded-For": "192.168.1.20"}
            )
        assert session.json()["authenticated"] is True
        assert session.json()["via"] == "ingress"
    finally:
        proxy_headers.reset()


async def test_a_trusted_proxy_cannot_forward_the_supervisor_address():
    from app.middleware import proxy_headers

    await create_user()
    await AuthSettingsRepository.update(trusted_proxies=["127.0.0.1"])
    try:
        async with _behind_proxy_headers(("127.0.0.1", 50000)) as client:
            session = await client.get(
                "/api/auth/session",
                headers={**ha_headers(), "X-Forwarded-For": ingress.SUPERVISOR_IP},
            )
        assert session.json()["authenticated"] is False
        assert await UserRepository.get_by_external_id("ingress", ALICE_ID) is None
    finally:
        proxy_headers.reset()
