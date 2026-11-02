"""HttpsRequiredMiddleware: plain http from public clients once a public address is declared."""

import logging

import httpx
import pytest
from starlette.applications import Starlette
from starlette.responses import PlainTextResponse
from starlette.routing import Route, WebSocketRoute

from app.auth.models import AuthSettings
from app.auth.providers import ingress
from app.middleware import host_guard, https_required, proxy_headers
from app.middleware.host_guard import HostGuardMiddleware
from app.middleware.https_required import HttpsRequiredMiddleware, must_refuse
from app.middleware.proxy_headers import TrustedProxyHeadersMiddleware

PUBLIC_PEER = ("203.0.113.7", 40000)
PUBLIC_URL = "https://mesh.example.com"


async def _ok(request):
    return PlainTextResponse("ok")


async def _ws(websocket):
    await websocket.accept()
    await websocket.close()


@pytest.fixture(autouse=True)
def _reset():
    host_guard.reset()
    proxy_headers.reset()
    https_required.reset()
    yield
    host_guard.reset()
    proxy_headers.reset()
    https_required.reset()


def _declare(public_urls=(PUBLIC_URL,), mode="observe", trusted_proxies=()):
    settings = AuthSettings(
        host_guard_mode=mode,
        public_urls=list(public_urls),
        trusted_proxies=list(trusted_proxies),
    )
    host_guard.apply(settings)
    proxy_headers.apply(settings)


def _client(peer=PUBLIC_PEER, scheme="http", relayed=False) -> httpx.AsyncClient:
    """The production order: proxy headers, then this guard, then the Host guard."""
    app = Starlette(routes=[Route("/api/x", _ok), Route("/", _ok), WebSocketRoute("/api/ws", _ws)])
    inner = TrustedProxyHeadersMiddleware(HttpsRequiredMiddleware(HostGuardMiddleware(app)))

    async def asgi(scope, receive, send):
        if relayed:
            scope["state"] = {"via": "relay"}
        await inner(scope, receive, send)

    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=asgi, client=peer),
        base_url=f"{scheme}://mesh.example.com",
    )


async def test_without_a_public_address_nothing_changes():
    _declare(public_urls=())
    async with _client() as client:
        assert (await client.get("/api/x")).status_code == 200


async def test_plain_http_from_a_public_address_is_refused_with_a_code(caplog):
    _declare()
    with caplog.at_level(logging.WARNING, logger="meshloom.auth"):
        async with _client() as client:
            response = await client.get("/api/x")
    assert response.status_code == 403
    assert response.json()["detail"]["code"] == "auth_https_required"
    assert response.headers["cache-control"] == "no-store"
    assert response.headers["x-frame-options"] == "DENY"
    assert "203.0.113.7" in caplog.text and "/api/x" in caplog.text


async def test_a_page_gets_a_bilingual_text():
    _declare()
    async with _client() as client:
        response = await client.get("/")
    assert response.status_code == 403
    assert response.headers["content-type"].startswith("text/plain")
    assert "HTTPS is required" in response.text and "HTTPS est obligatoire" in response.text


@pytest.mark.parametrize(
    "peer",
    [
        ("127.0.0.1", 1),
        ("::1", 1),
        ("192.168.1.20", 1),
        ("10.0.0.5", 1),
        ("fd12:3456::1", 1),
        ("fe80::1", 1),
        ("100.101.102.103", 1),
        ("::ffff:192.168.1.20", 1),
    ],
)
async def test_local_clients_keep_plain_http(peer):
    _declare()
    async with _client(peer=peer) as client:
        assert (await client.get("/api/x")).status_code == 200


async def test_https_from_a_public_address_is_accepted():
    _declare()
    async with _client(scheme="https") as client:
        assert (await client.get("/api/x")).status_code == 200


async def test_a_trusted_proxy_saying_https_is_believed():
    _declare(trusted_proxies=["192.168.1.2/32"])
    async with _client(peer=("192.168.1.2", 1)) as client:
        ok = await client.get(
            "/api/x", headers={"x-forwarded-for": "203.0.113.7", "x-forwarded-proto": "https"}
        )
        assert ok.status_code == 200
        # The same proxy forwarding a plain-http visitor from the internet: refused.
        refused = await client.get(
            "/api/x", headers={"x-forwarded-for": "203.0.113.7", "x-forwarded-proto": "http"}
        )
        assert refused.status_code == 403
        # A LAN visitor through the proxy over plain http: accepted.
        lan = await client.get(
            "/api/x", headers={"x-forwarded-for": "192.168.1.30", "x-forwarded-proto": "http"}
        )
        assert lan.status_code == 200


async def test_an_untrusted_peer_cannot_claim_https():
    _declare()
    async with _client() as client:
        response = await client.get("/api/x", headers={"x-forwarded-proto": "https"})
    assert response.status_code == 403


async def test_an_untrusted_public_peer_cannot_claim_a_lan_address():
    _declare()
    async with _client() as client:
        response = await client.get("/api/x", headers={"x-forwarded-for": "192.168.1.30"})
    assert response.status_code == 403


async def test_relayed_requests_are_never_refused():
    _declare(mode="enforce")
    host_guard.set_remote_hostname("abc123.meshloom.link")
    async with _client(relayed=True) as client:
        assert (await client.get("/api/x")).status_code == 200


async def test_the_relay_scope_is_https(monkeypatch):
    """The scope the relay client builds (MSHL-87) passes on its own, tag or not."""
    from app.services import remote_relay

    _declare(mode="enforce")
    host_guard.set_remote_hostname("abc123.meshloom.link")

    async def _pass(_pairs):
        return {"sub": "d1", "uid": 1, "kind": "browser"}

    monkeypatch.setattr(remote_relay, "check_pass", _pass)
    for kind in ("http", "websocket"):
        head = {
            "host": "abc123.meshloom.link",
            "path": "/api/x",
            "query": "",
            "client_ip": "203.0.113.7",
            "headers": [],
            "method": "GET",
            "subprotocols": [],
        }
        scope, error = await remote_relay.relayed_scope(kind, head)
        assert error is None and scope is not None
        assert not must_refuse(scope)
        scope["state"] = {}
        assert not must_refuse(scope)


async def test_ingress_is_exempt(monkeypatch):
    monkeypatch.setenv("MESHLOOM_INSTALL_KIND", "addon")
    _declare()
    scope = {
        "type": "http",
        "scheme": "http",
        "client": ("203.0.113.7", 1),
        "state": {proxy_headers.PEER_STATE_KEY: ingress.SUPERVISOR_IP},
        "headers": [],
    }
    assert not must_refuse(scope)
    monkeypatch.setenv("MESHLOOM_INSTALL_KIND", "package")
    assert must_refuse(scope)


@pytest.mark.parametrize("client", [None, ("testclient", 1), ("127.0.0.1%x", 1), (" 10.0.0.1", 1)])
def test_a_client_that_does_not_parse_is_public(client):
    _declare()
    scope = {"type": "http", "scheme": "http", "client": client, "headers": []}
    assert must_refuse(scope)


async def test_a_plain_websocket_from_a_public_address_is_closed():
    _declare()
    sent = []
    inner = HttpsRequiredMiddleware(Starlette(routes=[WebSocketRoute("/api/ws", _ws)]))

    async def receive():
        return {"type": "websocket.connect"}

    async def send(message):
        sent.append(message)

    scope = {
        "type": "websocket",
        "scheme": "ws",
        "path": "/api/ws",
        "headers": [(b"host", b"mesh.example.com")],
        "client": PUBLIC_PEER,
    }
    await inner(scope, receive, send)
    assert sent == [{"type": "websocket.close", "code": 1008}]
    scope["scheme"] = "wss"
    assert not must_refuse(scope)


async def test_the_refusal_log_is_rate_limited(caplog):
    _declare()
    with caplog.at_level(logging.WARNING, logger="meshloom.auth"):
        async with _client() as client:
            for _ in range(https_required.WARNING_BURST + 3):
                assert (await client.get("/api/x")).status_code == 403
    refused = [r for r in caplog.records if "Refused plain-http" in r.getMessage()]
    assert len(refused) == https_required.WARNING_BURST


async def test_a_saved_change_applies_to_the_next_request():
    _declare(public_urls=())
    async with _client() as client:
        assert (await client.get("/api/x")).status_code == 200
        _declare()
        assert (await client.get("/api/x")).status_code == 403
        _declare(public_urls=())
        assert (await client.get("/api/x")).status_code == 200


def test_registered_between_the_proxy_headers_and_the_host_guard():
    """The shipped app: proxy headers first (rewritten scheme/client), then this rule."""
    from app.main import app

    classes = [m.cls for m in app.user_middleware]  # outermost first
    assert HttpsRequiredMiddleware in classes
    assert (
        classes.index(TrustedProxyHeadersMiddleware)
        < classes.index(HttpsRequiredMiddleware)
        < classes.index(HostGuardMiddleware)
    )


def test_would_refuse_reads_the_list_it_is_given():
    from app.middleware.https_required import would_refuse

    public = {"type": "http", "scheme": "http", "client": ("203.0.113.7", 1), "headers": []}
    lan = {**public, "client": ("192.168.1.20", 1)}
    secure = {**public, "scheme": "https"}
    assert would_refuse(public, [PUBLIC_URL])
    assert not would_refuse(public, [])
    assert not would_refuse(lan, [PUBLIC_URL])
    assert not would_refuse(secure, [PUBLIC_URL])
    assert not must_refuse(public)  # nothing declared in memory
