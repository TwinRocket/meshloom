"""SecurityHeadersMiddleware: HSTS only over direct https, no-store on auth routes."""

import httpx
import pytest
from starlette.applications import Starlette
from starlette.responses import PlainTextResponse
from starlette.routing import Route

from app.auth.providers import ingress
from app.middleware.security_headers import PERMISSIONS_POLICY, SecurityHeadersMiddleware

LAN_PEER = ("192.168.1.20", 40000)
SUPERVISOR = (ingress.SUPERVISOR_IP, 40000)


async def _ok(request):
    return PlainTextResponse("ok", headers={"Cache-Control": "max-age=60"})


def _client(base_url: str, peer: tuple[str, int], relayed: bool = False) -> httpx.AsyncClient:
    app = Starlette(
        routes=[
            Route("/api/auth/session", _ok),
            Route("/api/remote/status", _ok),
            Route("/api/authentic", _ok),
            Route("/api/health", _ok),
        ]
    )
    inner = SecurityHeadersMiddleware(app)

    async def asgi(scope, receive, send):
        if relayed and scope["type"] == "http":
            scope["state"] = {"via": "relay"}
        await inner(scope, receive, send)

    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=asgi, client=peer), base_url=base_url
    )


@pytest.fixture(autouse=True)
def addon(monkeypatch):
    monkeypatch.setenv("MESHLOOM_INSTALL_KIND", "addon")


async def test_hsts_over_https_only():
    async with _client("https://nas.example", LAN_PEER) as client:
        response = await client.get("/api/health")
    assert response.headers["strict-transport-security"] == "max-age=31536000"
    assert response.headers["permissions-policy"] == PERMISSIONS_POLICY


async def test_no_hsts_over_plain_http():
    async with _client("http://192.168.1.20:8000", LAN_PEER) as client:
        response = await client.get("/api/health")
    assert "strict-transport-security" not in response.headers
    assert response.headers["permissions-policy"] == PERMISSIONS_POLICY


async def test_no_hsts_under_ingress_even_when_https():
    async with _client("https://ha.example", SUPERVISOR) as client:
        response = await client.get("/api/health")
    assert "strict-transport-security" not in response.headers


async def test_no_hsts_through_the_relay():
    async with _client("https://relay.example", LAN_PEER, relayed=True) as client:
        response = await client.get("/api/health")
    assert "strict-transport-security" not in response.headers


@pytest.mark.parametrize("path", ["/api/auth/session", "/api/remote/status"])
async def test_auth_and_remote_routes_are_no_store(path):
    async with _client("http://192.168.1.20:8000", LAN_PEER) as client:
        response = await client.get(path)
    assert response.headers["cache-control"] == "no-store"


@pytest.mark.parametrize("path", ["/api/health", "/api/authentic"])
async def test_other_routes_keep_their_cache_control(path):
    async with _client("http://192.168.1.20:8000", LAN_PEER) as client:
        response = await client.get(path)
    assert response.headers["cache-control"] == "max-age=60"
