"""One origin policy for CORS and the /api/ws upgrade (MSHL-274).

The same table of ``Origin`` headers runs through the policy itself, the CORS
middleware (a preflight) and the WebSocket upgrade, against the same
allowlist: a listed origin is answered by CORS and accepted by the WebSocket,
anything else is refused by both. The WebSocket also accepts same-origin
upgrades (``tests/test_websocket_origin.py``); CORS has nothing to answer there.
"""

import httpx
import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from app import origin_policy
from app.models import AppSettings
from app.websocket import FOREIGN_ORIGIN_CLOSE_CODE
from tests.test_websocket_origin import _health_patched

ALLOWLIST = [
    "https://dashboard.example.org",
    "http://192.168.1.50:8080",
    "https://exämple.org",
    "http://[::1]:5173",
]

# (Origin header, listed). Requests come to Host "testserver", so none of
# these is same-origin. Browsers send an IDN host in punycode; the allowlist
# entry above was typed in Unicode.
CASES = [
    ("https://dashboard.example.org", True),
    ("HTTPS://Dashboard.Example.ORG", True),
    ("https://dashboard.example.org:443", True),
    ("https://xn--exmple-cua.org", True),
    ("http://192.168.1.50:8080", True),
    ("http://[::1]:5173", True),
    ("https://dashboard.example.org/", False),
    ("https://dashboard.example.org/path", False),
    ("https://dashboard.example.org?x=1", False),
    ("https://dashboard.example.org#top", False),
    ("https://user@dashboard.example.org", False),
    ("https:dashboard.example.org", False),
    ("https://dashboard.example.org\\evil", False),
    ("https://dashboard.example.org\tx", False),
    ("http://dashboard.example.org", False),
    ("https://dashboard.example.org:8443", False),
    ("https://dashboard.example.org.evil.example", False),
    ("http://192.168.1.50", False),
    ("https://evil.example", False),
    ("null", False),
    ("", False),
    ("file://", False),
    ("*", False),
]
CASE_IDS = [raw or "empty" for raw, _ in CASES]


@pytest.fixture(autouse=True)
def _allowlist():
    origin_policy.apply(AppSettings(auth_allowed_origins=ALLOWLIST))
    yield
    origin_policy.reset()


def test_allowlist_is_stored_normalised():
    assert origin_policy.current() == (
        "https://dashboard.example.org",
        "http://192.168.1.50:8080",
        "https://xn--exmple-cua.org",
        "http://[::1]:5173",
    )


@pytest.mark.parametrize(("raw", "listed"), CASES, ids=CASE_IDS)
def test_policy(raw, listed):
    assert origin_policy.origin_listed(raw) is listed
    headers = {"origin": raw, "host": "testserver"}
    assert origin_policy.websocket_origin_allowed(headers) is listed


@pytest.mark.parametrize(("raw", "listed"), CASES, ids=CASE_IDS)
async def test_cors_path(raw, listed):
    from app.main import app

    headers = {
        "Origin": raw,
        "Access-Control-Request-Method": b"PATCH",
        "Access-Control-Request-Headers": b"content-type",
    }
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        response = await client.options("/api/settings", headers=headers)

    if listed:
        assert response.status_code == 200
        assert response.headers["access-control-allow-origin"] == raw
        assert response.headers["access-control-allow-credentials"] == "true"
    else:
        assert response.status_code == 400
        assert "access-control-allow-origin" not in response.headers


@pytest.mark.parametrize(("raw", "listed"), CASES, ids=CASE_IDS)
def test_websocket_path(raw, listed):
    from app.main import app

    headers = {"Origin": raw}
    with _health_patched():
        if listed:
            with TestClient(app).websocket_connect("/api/ws", headers=headers) as ws:
                assert ws.receive_json()["type"] == "health"
        else:
            with pytest.raises(WebSocketDisconnect) as exc_info:
                with TestClient(app).websocket_connect("/api/ws", headers=headers):
                    pass
            assert exc_info.value.code == FOREIGN_ORIGIN_CLOSE_CODE


class TestForwardedHeaders:
    """X-Forwarded-Host counts only for the WebSocket's same-origin rule, as before."""

    async def test_cors_ignores_forwarded_host(self):
        from app.main import app

        headers = {
            "Origin": "https://evil.example",
            "X-Forwarded-Host": "evil.example",
            "Access-Control-Request-Method": "PATCH",
        }
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
            response = await client.options("/api/settings", headers=headers)

        assert response.status_code == 400

    def test_websocket_ignores_forwarded_proto(self):
        headers = {
            "origin": "https://evil.example",
            "host": "meshloom.local",
            "x-forwarded-proto": "https",
        }
        assert origin_policy.websocket_origin_allowed(headers) is False

    def test_websocket_host_with_a_backslash_matches_nothing(self):
        headers = {"origin": "http://meshloom.local", "host": "meshloom.local\\evil"}
        assert origin_policy.websocket_origin_allowed(headers) is False

    def test_websocket_same_origin_through_forwarded_idn_host(self):
        headers = {
            "origin": "https://xn--mesh-loom-e4a.example",
            "host": "local-meshloom:8000",
            "x-forwarded-host": "meshé-loom.example",
        }
        assert origin_policy.websocket_origin_allowed(headers) is True


def test_empty_allowlist_refuses_every_cross_origin_upgrade():
    origin_policy.reset()
    assert not any(
        origin_policy.websocket_origin_allowed({"origin": raw, "host": "testserver"})
        for raw, _ in CASES
    )
