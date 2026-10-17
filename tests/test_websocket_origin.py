"""The /api/ws upgrade refuses a foreign Origin before accept (CSWSH).

A browser always sends ``Origin`` on a WebSocket upgrade; scripts and native
clients send none. A foreign origin is closed with 1008 before accept, which
uvicorn turns into an HTTP 403: no frame at all.
"""

import logging
from contextlib import contextmanager
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

import app.websocket as websocket_module
from app.websocket import (
    FOREIGN_ORIGIN_CLOSE_CODE,
    origin_allowed,
    reject_foreign_origin,
    ws_manager,
)


@pytest.fixture(autouse=True)
def _clean_ws_manager():
    ws_manager.active_connections.clear()
    yield
    ws_manager.active_connections.clear()


@contextmanager
def _health_patched():
    """Patch what the initial health frame reads; yields the build_health_data spy."""
    with (
        patch("app.routers.ws.radio_manager") as mock_ws_rm,
        patch("app.routers.health.radio_manager") as mock_health_rm,
        patch("app.routers.health.RawPacketRepository") as mock_repo,
        patch("app.routers.health.settings") as mock_settings,
        patch("app.routers.health.os.path.getsize", return_value=0),
    ):
        mock_ws_rm.is_connected = False
        mock_ws_rm.connection_info = None
        mock_health_rm.is_connected = False
        mock_health_rm.connection_info = None
        mock_health_rm.is_setup_in_progress = False
        mock_health_rm.is_setup_complete = False
        mock_health_rm.connection_desired = True
        mock_health_rm.is_reconnecting = False
        mock_health_rm.device_info_loaded = False
        mock_repo.get_oldest_undecrypted = AsyncMock(return_value=None)
        mock_settings.database_path = "/tmp/test.db"
        mock_settings.disable_bots = False
        yield


def _client() -> TestClient:
    from app.main import app

    # TestClient sends Host: testserver.
    return TestClient(app)


class TestUpgradeHandshake:
    @pytest.mark.parametrize(
        "origin",
        [
            "http://evil.example",
            "http://testserver.evil.example",
            "https://testserver:8443",
            "null",
        ],
    )
    def test_foreign_origin_is_refused_before_any_frame(self, origin):
        with (
            _health_patched(),
            patch("app.routers.ws.build_health_data", new_callable=AsyncMock) as health,
            patch.object(ws_manager, "connect", new_callable=AsyncMock) as connect,
        ):
            # TestClient raises on the close sent instead of the accept.
            with pytest.raises(WebSocketDisconnect) as exc_info:
                with _client().websocket_connect("/api/ws", headers={"Origin": origin}):
                    pass

        assert exc_info.value.code == FOREIGN_ORIGIN_CLOSE_CODE
        connect.assert_not_awaited()
        health.assert_not_awaited()
        assert ws_manager.active_connections == {}

    @pytest.mark.parametrize(
        "headers",
        [
            {},
            {"Origin": "http://testserver"},
            {"Origin": "HTTP://TestServer:80"},
        ],
        ids=["no-origin", "same-origin", "same-origin-default-port"],
    )
    def test_same_origin_or_no_origin_is_accepted(self, headers):
        with _health_patched():
            with _client().websocket_connect("/api/ws", headers=headers) as ws:
                assert ws.receive_json()["type"] == "health"

    def test_ingress_origin_matching_forwarded_host_is_accepted(self):
        """The HA ingress proxies to the add-on and sets X-Forwarded-Host to HA's host."""
        headers = {
            "Origin": "https://homeassistant.local:8123",
            "X-Forwarded-Host": "homeassistant.local:8123",
        }
        with _health_patched():
            with _client().websocket_connect("/api/ws", headers=headers) as ws:
                assert ws.receive_json()["type"] == "health"


class TestOriginAllowed:
    @pytest.mark.parametrize(
        ("headers", "allowed"),
        [
            ({"host": "meshloom.local:8000"}, True),
            ({"origin": "http://meshloom.local:8000", "host": "meshloom.local:8000"}, True),
            ({"origin": "http://192.168.1.20:8000", "host": "192.168.1.20:8000"}, True),
            ({"origin": "http://[::1]:8000", "host": "[::1]:8000"}, True),
            ({"origin": "https://mesh.example.org", "host": "mesh.example.org"}, True),
            ({"origin": "https://mesh.example.org", "host": "mesh.example.org:443"}, True),
            # Ingress: Host is the add-on's, X-Forwarded-Host is Home Assistant's.
            (
                {
                    "origin": "https://ha.example.org",
                    "host": "local-meshloom:8000",
                    "x-forwarded-host": "ha.example.org, proxy.internal",
                },
                True,
            ),
            ({"origin": "http://meshloom.local:8001", "host": "meshloom.local:8000"}, False),
            ({"origin": "http://evil.example", "host": "meshloom.local:8000"}, False),
            ({"origin": "http://evil.example", "host": "evil.example.meshloom.local"}, False),
            ({"origin": "http://meshloom.local:8000", "host": ""}, False),
            ({"origin": "http://meshloom.local:8000"}, False),
            ({"origin": "null", "host": "meshloom.local:8000"}, False),
            ({"origin": "", "host": "meshloom.local:8000"}, False),
            ({"origin": "file://", "host": "meshloom.local:8000"}, False),
            ({"origin": "http://meshloom.local:99999", "host": "meshloom.local:99999"}, False),
            ({"origin": "http://u@meshloom.local", "host": "meshloom.local"}, False),
            (
                {
                    "origin": "http://evil.example",
                    "host": "meshloom.local",
                    "x-forwarded-host": "meshloom.local, evil.example",
                },
                False,
            ),
        ],
    )
    def test_rule(self, headers, allowed):
        assert origin_allowed(headers) is allowed


class TestRefusalLogThrottle:
    @staticmethod
    async def _refuse(origin):
        websocket = MagicMock()
        websocket.headers = {"origin": origin, "host": "meshloom.local"}
        websocket.close = AsyncMock()
        assert await reject_foreign_origin(websocket) is True

    @staticmethod
    def _warnings(caplog):
        return [r for r in caplog.records if "foreign origin" in r.getMessage()]

    async def test_repeated_refusals_log_once_per_origin(self, caplog):
        websocket_module._refusal_last_logged.clear()
        with caplog.at_level(logging.WARNING, logger="app.websocket"):
            for _ in range(5):
                await self._refuse("http://evil.example")
            await self._refuse("http://other.example")
        assert len(self._warnings(caplog)) == 2

    async def test_logs_again_after_the_interval(self, caplog):
        websocket_module._refusal_last_logged.clear()
        with (
            caplog.at_level(logging.WARNING, logger="app.websocket"),
            patch.object(websocket_module.time, "monotonic") as clock,
        ):
            clock.return_value = 1000.0
            await self._refuse("http://evil.example")
            clock.return_value = 1000.0 + websocket_module.DROP_LOG_INTERVAL_SECONDS + 1
            await self._refuse("http://evil.example")
        assert len(self._warnings(caplog)) == 2

    async def test_tracked_origins_are_bounded(self):
        websocket_module._refusal_last_logged.clear()
        for i in range(websocket_module._REFUSAL_LOG_MAX_ORIGINS + 10):
            await self._refuse(f"http://evil{i}.example")
        assert (
            len(websocket_module._refusal_last_logged) == websocket_module._REFUSAL_LOG_MAX_ORIGINS
        )


class TestRejectForeignOrigin:
    async def test_closes_without_accepting(self):
        websocket = MagicMock()
        websocket.headers = {"origin": "http://evil.example", "host": "meshloom.local"}
        websocket.close = AsyncMock()
        websocket.accept = AsyncMock()

        assert await reject_foreign_origin(websocket) is True

        websocket.close.assert_awaited_once_with(code=FOREIGN_ORIGIN_CLOSE_CODE)
        websocket.accept.assert_not_awaited()

    async def test_allowed_origin_touches_nothing(self):
        websocket = MagicMock()
        websocket.headers = {"origin": "http://meshloom.local", "host": "meshloom.local"}
        websocket.close = AsyncMock()

        assert await reject_foreign_origin(websocket) is False
        websocket.close.assert_not_awaited()
