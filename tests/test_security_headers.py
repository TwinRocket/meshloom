"""SecurityHeadersMiddleware: HSTS only over direct https, no-store on auth routes."""

import re

import httpx
import pytest
from starlette.applications import Starlette
from starlette.responses import PlainTextResponse
from starlette.routing import Route

from app.auth.models import AuthSettings
from app.auth.providers import ingress
from app.middleware import proxy_headers
from app.middleware.proxy_headers import TrustedProxyHeadersMiddleware
from app.middleware.security_headers import (
    PERMISSIONS_POLICY,
    SecurityHeadersMiddleware,
    _wants_no_store,
)

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
def _reset_proxies():
    proxy_headers.reset()
    yield
    proxy_headers.reset()


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


SECRET_FIELD = re.compile(r"secret|private_key|password|token|psk|key_hex|^key$", re.I)


def _model_fields(model, seen=None) -> set[str]:
    seen = seen if seen is not None else set()
    names: set[str] = set()
    if not hasattr(model, "model_fields") or model in seen:
        return names
    seen.add(model)
    for name, field in model.model_fields.items():
        names.add(name)
        for inner in (field.annotation, *getattr(field.annotation, "__args__", ())):
            names |= _model_fields(inner, seen)
    return names


@pytest.mark.parametrize(
    "path",
    [
        "/api/radio/private-key",
        "/api/settings/backup/database",
        "/api/settings/backup/json",
        "/api/settings/backup/export",
        "/api/channels",
        "/api/push/subscriptions",
        "/api/debug",
    ],
)
def test_secret_routes_are_listed_for_no_store(path):
    assert _wants_no_store(path)


def test_typed_get_routes_with_secret_fields_are_listed_for_no_store():
    """A new GET route whose response model carries a key, secret or password
    must be added to NO_STORE_PATHS (untyped routes go in the list above)."""
    from fastapi.routing import APIRoute

    from app.main import app

    missing = [
        route.path
        for route in app.routes
        if isinstance(route, APIRoute)
        and "GET" in route.methods
        and route.path.startswith("/api")
        and route.response_model is not None
        and any(SECRET_FIELD.search(n) for n in _model_fields(route.response_model))
        and not _wants_no_store(route.path)
    ]
    assert missing == []


async def test_unhandled_exception_still_carries_the_headers():
    async def boom(request):
        raise RuntimeError("boom")

    app = SecurityHeadersMiddleware(Starlette(routes=[Route("/api/auth/boom", boom)]))
    sent: list[dict] = []

    async def send(message):
        sent.append(message)

    scope = {
        "type": "http",
        "method": "GET",
        "path": "/api/auth/boom",
        "scheme": "https",
        "headers": [],
        "client": LAN_PEER,
        "query_string": b"",
    }
    with pytest.raises(RuntimeError):
        await app(scope, None, send)  # type: ignore[arg-type]
    start = sent[0]
    headers = dict(start["headers"])
    assert start["status"] == 500
    assert headers[b"cache-control"] == b"no-store"
    assert headers[b"strict-transport-security"] == b"max-age=31536000"
    assert b"permissions-policy" in headers


def _trusted_stack(peer: tuple[str, int], proxies: list[str]):
    inner = SecurityHeadersMiddleware(Starlette(routes=[Route("/api/health", _ok)]))
    stack = TrustedProxyHeadersMiddleware(inner)
    proxy_headers.apply(AuthSettings(trusted_proxies=proxies))
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=stack, client=peer), base_url="http://nas.example"
    )


async def test_hsts_behind_a_trusted_proxy_saying_https():
    async with _trusted_stack(("10.0.0.2", 40000), ["10.0.0.2/32"]) as client:
        response = await client.get("/api/health", headers={"X-Forwarded-Proto": "https"})
    assert response.headers["strict-transport-security"] == "max-age=31536000"


async def test_no_hsts_when_the_forwarding_peer_is_not_trusted():
    async with _trusted_stack(("10.0.0.9", 40000), ["10.0.0.2/32"]) as client:
        response = await client.get("/api/health", headers={"X-Forwarded-Proto": "https"})
    assert "strict-transport-security" not in response.headers


async def test_websocket_scope_passes_through_untouched():
    seen: list[str] = []

    async def inner(scope, receive, send):
        seen.append(scope["type"])

    await SecurityHeadersMiddleware(inner)({"type": "websocket", "path": "/api/ws"}, None, None)  # type: ignore[arg-type]
    assert seen == ["websocket"]
