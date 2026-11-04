"""Same-origin rules: CORS, cross-site writes, the WebSocket upgrade, bot edits."""

from __future__ import annotations

from collections.abc import Iterator
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import FastAPI, WebSocket
from fastapi.testclient import TestClient
from starlette.testclient import WebSocketDenialResponse
from starlette.websockets import WebSocketDisconnect

from app import origin_policy
from app.config import Settings
from app.origin_policy import (
    CrossSiteGuardMiddleware,
    browser_request_allowed,
    host_allowed,
    is_ingress_request,
    parse_origin,
)
from app.security import add_optional_basic_auth_middleware
from app.websocket import ws_manager

BOT = {
    "type": "bot",
    "name": "Bot",
    "config": {"code": "def bot(**kwargs):\n    return None\n"},
    "enabled": False,
}


class TestParseOrigin:
    @pytest.mark.parametrize(
        ("raw", "expected"),
        [
            ("http://192.168.1.10:8000", ("http", "192.168.1.10", 8000)),
            ("https://Mesh.Example.COM", ("https", "mesh.example.com", 443)),
            ("http://meshloom.local", ("http", "meshloom.local", 80)),
            ("http://[fd00::1]:8000", ("http", "[fd00::1]", 8000)),
            ("http://my_host:8000", ("http", "my_host", 8000)),
        ],
    )
    def test_parses(self, raw, expected):
        assert parse_origin(raw) == expected

    @pytest.mark.parametrize(
        "raw",
        [
            "null",
            "",
            "file://",
            "ftp://host",
            "http://host/",
            "http://host/path",
            "http://host?x",
            "http://host#x",
            "http://user@host",
            "http://a\\b",
            "http://a b",
            "http://" + "a" * 300,
        ],
    )
    def test_refuses(self, raw):
        assert parse_origin(raw) is None


class TestBrowserRequestAllowed:
    @pytest.mark.parametrize(
        "headers",
        [
            # Not a browser.
            {"host": "192.168.1.10:8000"},
            # Same origin by IP, hostname, default ports, case, IPv6.
            {"host": "192.168.1.10:8000", "origin": "http://192.168.1.10:8000"},
            {"host": "meshloom.lan:8000", "origin": "http://meshloom.lan:8000"},
            {"host": "MESHLOOM.lan", "origin": "http://meshloom.lan"},
            {"host": "meshloom.lan:80", "origin": "http://meshloom.lan"},
            {"host": "[fd00::1]:8000", "origin": "http://[fd00::1]:8000"},
            # TLS terminator keeping Host, the backend sees http and no port.
            {"host": "mesh.example.com", "origin": "https://mesh.example.com"},
            # Proxy rewriting Host, saying so in X-Forwarded-Host.
            {
                "host": "127.0.0.1:8000",
                "origin": "https://mesh.example.com",
                "x-forwarded-host": "mesh.example.com",
            },
            {
                "host": "127.0.0.1:8000",
                "origin": "https://mesh.example.com:8443",
                "x-forwarded-host": "mesh.example.com:8443, inner.proxy",
            },
            # Proxy rewriting Host without saying so, on https: the browser vouches.
            {
                "host": "127.0.0.1:8000",
                "origin": "https://mesh.example.com",
                "sec-fetch-site": "same-origin",
            },
            {"host": "127.0.0.1:8000", "sec-fetch-site": "none"},
        ],
    )
    def test_allowed(self, headers):
        assert browser_request_allowed(headers)

    @pytest.mark.parametrize(
        "headers",
        [
            {"host": "192.168.1.10:8000", "origin": "https://evil.example"},
            # Another port of the same host is another origin.
            {"host": "192.168.1.10:8000", "origin": "http://192.168.1.10:3000"},
            {"host": "mesh.example.com", "origin": "http://mesh.example.com:8080"},
            # A forwarded header naming something else does not help.
            {
                "host": "127.0.0.1:8000",
                "origin": "https://evil.example",
                "x-forwarded-host": "mesh.example.com",
            },
            # Only the first forwarded name counts.
            {
                "host": "127.0.0.1:8000",
                "origin": "https://evil.example",
                "x-forwarded-host": "mesh.example.com, evil.example",
            },
            {"host": "192.168.1.10:8000", "origin": "null"},
            {"host": "192.168.1.10:8000", "origin": "garbage"},
            {"host": "192.168.1.10:8000", "sec-fetch-site": "cross-site"},
            {"host": "192.168.1.10:8000", "sec-fetch-site": "same-site"},
            {
                "host": "192.168.1.10:8000",
                "origin": "https://evil.example",
                "sec-fetch-site": "cross-site",
            },
        ],
    )
    def test_refused(self, headers):
        assert not browser_request_allowed(headers)


class TestHostAllowed:
    @pytest.mark.parametrize(
        "host",
        [
            "192.168.1.10",
            "192.168.1.10:8000",
            "[::1]:8000",
            "[fd00::1]",
            "localhost",
            "LOCALHOST:8000",
            "meshloom.local",
            "meshloom.local.",
            "meshloom.lan:8000",
            "pi.home.arpa",
            "mesh.internal",
            "this-machine",
            "this-machine.corp.example",
        ],
    )
    def test_allowed(self, host):
        with patch.object(
            origin_policy,
            "_machine_names",
            return_value=frozenset({"this-machine", "this-machine.corp.example"}),
        ):
            assert host_allowed(host)

    @pytest.mark.parametrize(
        "host",
        [
            None,
            "",
            "evil.example",
            "evil.example:8000",
            "mesh.example.com",
            "local",
            "meshloom.local.evil.example",
            "evil-localhost",
            "2130706433",
            "nas",
            "a/b",
            "user@localhost",
            "localhost:notaport",
        ],
    )
    def test_refused(self, host):
        with patch.object(
            origin_policy, "_machine_names", return_value=frozenset({"this-machine"})
        ):
            assert not host_allowed(host)


class TestIngressRequest:
    def test_supervisor_peer_on_the_addon(self, monkeypatch):
        monkeypatch.setenv("MESHLOOM_INSTALL_KIND", "addon")
        assert is_ingress_request({"client": ("172.30.32.2", 1234)})

    def test_other_peer_on_the_addon(self, monkeypatch):
        monkeypatch.setenv("MESHLOOM_INSTALL_KIND", "addon")
        assert not is_ingress_request({"client": ("172.30.32.1", 1234)})
        assert not is_ingress_request({"client": None})

    def test_supervisor_peer_elsewhere(self, monkeypatch):
        monkeypatch.setenv("MESHLOOM_INSTALL_KIND", "container")
        assert not is_ingress_request({"client": ("172.30.32.2", 1234)})


def _guarded_app() -> FastAPI:
    app = FastAPI()
    app.add_middleware(CrossSiteGuardMiddleware)

    @app.post("/write")
    async def write():
        return {"ok": True}

    @app.get("/read")
    async def read():
        return {"ok": True}

    return app


class TestCrossSiteGuardMiddleware:
    @pytest.fixture
    def client(self) -> Iterator[TestClient]:
        with TestClient(_guarded_app(), base_url="http://192.168.1.10:8000") as client:
            yield client

    def test_foreign_origin_refused_on_a_simple_request(self, client):
        response = client.post(
            "/write",
            headers={"Origin": "https://evil.example", "Content-Type": "text/plain"},
            content=b"{}",
        )
        assert response.status_code == 403
        assert response.json()["detail"]["code"] == "cross_site_refused"

    def test_same_origin_allowed(self, client):
        response = client.post("/write", headers={"Origin": "http://192.168.1.10:8000"})
        assert response.status_code == 200

    def test_no_origin_allowed(self, client):
        assert client.post("/write").status_code == 200

    def test_safe_method_not_checked(self, client):
        response = client.get("/read", headers={"Origin": "https://evil.example"})
        assert response.status_code == 200

    def test_refused_before_the_basic_auth_challenge(self):
        app = FastAPI()
        # Added first, so inside the guard, as in app/main.py.
        add_optional_basic_auth_middleware(
            app, Settings(basic_auth_username="user", basic_auth_password="pass")
        )
        app.add_middleware(CrossSiteGuardMiddleware)
        with TestClient(app, base_url="http://192.168.1.10:8000") as client:
            refused = client.post("/x", headers={"Origin": "https://evil.example"})
            challenged = client.post("/x")
        assert refused.status_code == 403
        assert "www-authenticate" not in refused.headers
        assert challenged.status_code == 401

    def test_refusal_log_is_rate_limited(self, caplog, monkeypatch):
        monkeypatch.setattr(origin_policy, "_last_refusal_log", 0.0)
        with TestClient(_guarded_app(), base_url="http://192.168.1.10:8000") as client:
            for _ in range(3):
                client.post("/write", headers={"Origin": "https://evil.example"})
        assert sum("Refused a cross-site" in r.getMessage() for r in caplog.records) == 1


@pytest.fixture
def real_app_client() -> Iterator[TestClient]:
    """The real app, without its lifespan: only routing and middleware are under test."""
    from app.main import app

    yield TestClient(app, base_url="http://192.168.1.10:8000")


class TestRealAppCors:
    def test_no_cors_header_for_a_foreign_origin(self, real_app_client):
        with patch("app.routers.fanout.FanoutConfigRepository.get_all", AsyncMock(return_value=[])):
            response = real_app_client.get(
                "/api/fanout", headers={"Origin": "https://evil.example"}
            )
        assert response.status_code == 200
        assert "access-control-allow-origin" not in response.headers
        assert "access-control-allow-credentials" not in response.headers

    def test_preflight_gets_no_cors_header(self, real_app_client):
        response = real_app_client.options(
            "/api/fanout",
            headers={
                "Origin": "https://evil.example",
                "Access-Control-Request-Method": "POST",
                "Access-Control-Request-Headers": "x-forwarded-host,content-type",
            },
        )
        assert "access-control-allow-origin" not in response.headers
        assert "access-control-allow-headers" not in response.headers

    def test_cross_site_write_refused_with_security_headers(self, real_app_client):
        response = real_app_client.post(
            "/api/radio/advertise", headers={"Origin": "https://evil.example"}
        )
        assert response.status_code == 403
        assert response.json()["detail"]["code"] == "cross_site_refused"
        assert response.headers["x-content-type-options"] == "nosniff"


class TestRealAppWebSocket:
    HOST = {"Host": "192.168.1.10:8000"}

    @pytest.fixture(autouse=True)
    def _health(self):
        with patch("app.routers.ws.build_health_data", AsyncMock(return_value={"status": "ok"})):
            yield

    @pytest.mark.parametrize(
        "origin", ["https://evil.example", "http://192.168.1.10:3000", "null", "garbage"]
    )
    def test_refused_before_accept(self, real_app_client, origin):
        """Closed before accept: uvicorn turns this into an HTTP 403 and no frame is sent."""
        with patch.object(ws_manager, "connect", new=AsyncMock()) as connect:
            with pytest.raises(WebSocketDisconnect) as refused:
                with real_app_client.websocket_connect(
                    "/api/ws", headers={**self.HOST, "Origin": origin}
                ):
                    pass
        assert refused.value.code == 1008
        connect.assert_not_awaited()

    @pytest.mark.parametrize(
        "headers",
        [
            {},
            {"Origin": "http://192.168.1.10:8000"},
            {"Origin": "https://mesh.example.com", "X-Forwarded-Host": "mesh.example.com"},
        ],
    )
    def test_allowed(self, real_app_client, headers):
        with real_app_client.websocket_connect(
            "/api/ws", headers={**self.HOST, **headers}
        ) as websocket:
            assert websocket.receive_json()["type"] == "health"

    def test_ingress_allowed_whatever_the_origin(self, monkeypatch):
        monkeypatch.setenv("MESHLOOM_INSTALL_KIND", "addon")
        from app.main import app

        client = TestClient(app, client=(origin_policy.SUPERVISOR_IP, 40000))
        with client.websocket_connect(
            "/api/ws", headers={"Origin": "https://ha.example.com"}
        ) as websocket:
            assert websocket.receive_json()["type"] == "health"


class TestWebSocketWithBasicAuth:
    def test_auth_still_applies_to_an_allowed_origin(self):
        app = FastAPI()
        add_optional_basic_auth_middleware(
            app, Settings(basic_auth_username="user", basic_auth_password="pass")
        )

        @app.websocket("/ws")
        async def endpoint(websocket: WebSocket) -> None:
            await websocket.accept()
            await websocket.close()

        with TestClient(app) as client:
            with pytest.raises(WebSocketDenialResponse) as denied:
                with client.websocket_connect("/ws", headers={"Origin": "http://testserver"}):
                    pass
        assert denied.value.status_code == 401


class TestBotEdits:
    """Bots through a name a rebinding page could have: refused, whatever the Origin."""

    @staticmethod
    def _client(base_url: str, **kwargs) -> TestClient:
        from app.main import app

        return TestClient(app, base_url=base_url, **kwargs)

    @pytest.mark.parametrize(
        "base_url",
        ["http://evil.example:8000", "https://mesh.example.com"],
    )
    @pytest.mark.asyncio
    async def test_create_refused_on_a_public_name(self, test_db, base_url):
        origin = base_url
        response = self._client(base_url).post("/api/fanout", json=BOT, headers={"Origin": origin})
        assert response.status_code == 403
        assert response.json()["detail"]["code"] == "bot_edit_host_refused"

    @pytest.mark.asyncio
    async def test_x_forwarded_host_does_not_lend_a_local_name(self, test_db):
        response = self._client("http://evil.example:8000").post(
            "/api/fanout", json=BOT, headers={"X-Forwarded-Host": "localhost"}
        )
        assert response.status_code == 403

    @pytest.mark.asyncio
    async def test_public_forwarded_host_refused_behind_a_host_rewriting_proxy(self, test_db):
        response = self._client("http://127.0.0.1:8000").post(
            "/api/fanout", json=BOT, headers={"X-Forwarded-Host": "mesh.example.com"}
        )
        assert response.status_code == 403
        assert response.json()["detail"]["code"] == "bot_edit_host_refused"

    @pytest.mark.asyncio
    async def test_local_forwarded_host_allowed(self, test_db):
        response = self._client("http://127.0.0.1:8000").post(
            "/api/fanout", json=BOT, headers={"X-Forwarded-Host": "meshloom.lan:8443"}
        )
        assert response.status_code == 200

    @pytest.mark.parametrize(
        "base_url",
        ["http://192.168.1.10:8000", "http://localhost:8000", "http://meshloom.local:8000"],
    )
    @pytest.mark.asyncio
    async def test_create_allowed_on_a_local_name(self, test_db, base_url):
        response = self._client(base_url).post(
            "/api/fanout", json=BOT, headers={"Origin": base_url}
        )
        assert response.status_code == 200
        assert response.json()["type"] == "bot"

    @pytest.mark.asyncio
    async def test_create_allowed_through_ingress(self, test_db, monkeypatch):
        monkeypatch.setenv("MESHLOOM_INSTALL_KIND", "addon")
        client = self._client("https://ha.example.com", client=(origin_policy.SUPERVISOR_IP, 40000))
        response = client.post(
            "/api/fanout", json=BOT, headers={"Origin": "https://ha.example.com"}
        )
        assert response.status_code == 200

    @pytest.mark.asyncio
    async def test_supervisor_address_alone_is_not_ingress(self, test_db, monkeypatch):
        monkeypatch.setenv("MESHLOOM_INSTALL_KIND", "container")
        client = self._client("https://ha.example.com", client=(origin_policy.SUPERVISOR_IP, 40000))
        assert client.post("/api/fanout", json=BOT).status_code == 403

    @pytest.mark.asyncio
    async def test_patch_refused_delete_and_read_allowed_on_a_public_name(self, test_db):
        local = self._client("http://192.168.1.10:8000")
        public = self._client("https://mesh.example.com")
        bot_id = local.post("/api/fanout", json=BOT).json()["id"]

        for change in ({"config": {"code": "def bot(**k):\n    return 'x'\n"}}, {"enabled": True}):
            response = public.patch(f"/api/fanout/{bot_id}", json=change)
            assert response.status_code == 403
            assert response.json()["detail"]["code"] == "bot_edit_host_refused"

        assert any(cfg["id"] == bot_id for cfg in public.get("/api/fanout").json())
        assert public.delete(f"/api/fanout/{bot_id}").status_code == 200

    @pytest.mark.asyncio
    async def test_other_fanout_types_unaffected_on_a_public_name(self, test_db):
        public = self._client("https://mesh.example.com")
        created = public.post(
            "/api/fanout",
            json={
                "type": "mqtt_private",
                "name": "MQTT",
                "config": {"broker_host": "localhost", "broker_port": 1883},
                "enabled": False,
            },
        )
        assert created.status_code == 200
        updated = public.patch(f"/api/fanout/{created.json()['id']}", json={"name": "Renamed"})
        assert updated.status_code == 200
