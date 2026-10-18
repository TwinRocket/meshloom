"""HostGuardMiddleware: observe/enforce modes, exemptions, the strict rule."""

import httpx
import pytest
from starlette.applications import Starlette
from starlette.responses import PlainTextResponse
from starlette.routing import Route, WebSocketRoute

from app.auth.models import AuthSettings
from app.auth.providers import ingress, trusted_network
from app.middleware import host_guard, proxy_headers
from app.middleware.host_guard import HostGuardMiddleware, host_allowed
from app.middleware.proxy_headers import TrustedProxyHeadersMiddleware

LAN_PEER = ("192.168.1.20", 40000)


async def _ok(request):
    return PlainTextResponse("ok")


async def _ws(websocket):
    await websocket.accept()
    await websocket.close()


@pytest.fixture(autouse=True)
def _reset():
    host_guard.reset()
    proxy_headers.reset()
    yield
    host_guard.reset()
    proxy_headers.reset()


def _settings(mode="observe", public_urls=()):
    settings = AuthSettings(host_guard_mode=mode, public_urls=list(public_urls))
    host_guard.apply(settings)
    return settings


def _client(host: str, peer=LAN_PEER, relayed=False) -> httpx.AsyncClient:
    app = Starlette(routes=[Route("/api/x", _ok), WebSocketRoute("/api/ws", _ws)])
    inner = TrustedProxyHeadersMiddleware(HostGuardMiddleware(app))

    async def asgi(scope, receive, send):
        if relayed:
            scope["state"] = {"via": "relay"}
        await inner(scope, receive, send)

    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=asgi, client=peer), base_url=f"http://{host}"
    )


@pytest.mark.parametrize(
    "host",
    [
        "192.168.1.10:8000",
        "[fe80::1]:8000",
        "10.0.0.2",
        "localhost:8000",
        "meshloom.local",
        "pi.home.arpa",
        "box.lan",
        "nas.internal",
        "MeshLoom.LOCAL.",
    ],
)
def test_local_names_are_allowed(host):
    assert host_allowed(host)


@pytest.mark.parametrize(
    "host", ["attacker.example", "evil.local.example", "", None, "a b", "x@y", "h:99999"]
)
def test_other_names_are_refused(host):
    assert not host_allowed(host)


def test_machine_names_are_allowed(monkeypatch):
    monkeypatch.setattr(host_guard, "_machine_names", lambda: frozenset({"pi", "pi.example.net"}))
    assert host_allowed("pi:8000")
    assert host_allowed("pi.example.net")
    assert not host_allowed("other.example.net")


def test_declared_public_urls_and_remote_hostname_are_allowed():
    assert host_allowed("mesh.example.org", ["https://mesh.example.org:8443/x"])
    assert not host_allowed("mesh.example.org")
    host_guard.set_remote_hostname("abc123.meshloom.link")
    assert host_allowed("abc123.meshloom.link")
    host_guard.set_remote_hostname(None)
    assert not host_allowed("abc123.meshloom.link")


async def test_observe_lets_the_request_through_and_records_the_name(caplog):
    _settings("observe")
    async with _client("attacker.example") as client:
        first = await client.get("/api/x")
        second = await client.get("/api/x")
    assert first.status_code == second.status_code == 200
    seen = host_guard.rejected_hosts()
    assert [(h.host, h.count) for h in seen] == [("attacker.example", 2)]
    assert (
        sum(
            r.name == host_guard.logger.name and "attacker.example" in r.getMessage()
            for r in caplog.records
        )
        == 1
    )


async def test_enforce_answers_421_naming_the_host():
    _settings("enforce")
    async with _client("attacker.example:8000") as client:
        response = await client.get("/api/x")
    assert response.status_code == 421
    detail = response.json()["detail"]
    assert detail["code"] == "auth_host_rejected"
    assert detail["params"] == {"host": "attacker.example"}
    assert "attacker.example" in detail["message"]
    assert response.headers["cache-control"] == "no-store"
    assert host_guard.rejected_hosts() == []


@pytest.mark.parametrize("mode", ["observe", "enforce"])
@pytest.mark.parametrize(
    "host", ["192.168.1.10:8000", "localhost", "meshloom.local", "mesh.example"]
)
async def test_known_names_work_in_both_modes(mode, host):
    _settings(mode, ["https://mesh.example"])
    async with _client(host) as client:
        assert (await client.get("/api/x")).status_code == 200
    assert host_guard.rejected_hosts() == []


async def test_a_declared_public_address_stops_being_rejected():
    _settings("enforce")
    async with _client("mesh.example") as client:
        assert (await client.get("/api/x")).status_code == 421
        _settings("enforce", ["https://mesh.example"])
        assert (await client.get("/api/x")).status_code == 200


async def test_ingress_is_exempt(monkeypatch):
    monkeypatch.setenv("MESHLOOM_INSTALL_KIND", "addon")
    _settings("enforce")
    async with _client("homeassistant.example", peer=(ingress.SUPERVISOR_IP, 40000)) as client:
        assert (await client.get("/api/x")).status_code == 200
    # Same address on another install kind is not ingress.
    monkeypatch.setenv("MESHLOOM_INSTALL_KIND", "package")
    async with _client("homeassistant.example", peer=(ingress.SUPERVISOR_IP, 40000)) as client:
        assert (await client.get("/api/x")).status_code == 421


async def test_a_lan_client_on_the_addon_is_not_exempt(monkeypatch):
    monkeypatch.setenv("MESHLOOM_INSTALL_KIND", "addon")
    _settings("enforce")
    async with _client("attacker.example") as client:
        assert (await client.get("/api/x")).status_code == 421


async def test_relay_requests_are_exempt_and_the_remote_hostname_is_accepted():
    _settings("enforce")
    async with _client("abc123.meshloom.link", relayed=True) as client:
        assert (await client.get("/api/x")).status_code == 200
    host_guard.set_remote_hostname("abc123.meshloom.link")
    async with _client("abc123.meshloom.link") as client:
        assert (await client.get("/api/x")).status_code == 200


async def test_a_header_that_does_not_parse_is_refused_in_enforce_only():
    app = Starlette(routes=[Route("/api/x", _ok)])
    inner = HostGuardMiddleware(app)
    sent = []

    async def send(message):
        sent.append(message)

    async def receive():
        return {"type": "http.request"}

    scope = {
        "type": "http",
        "path": "/api/x",
        "method": "GET",
        "headers": [(b"host", b"bad host/")],
        "client": LAN_PEER,
    }
    _settings("enforce")
    await inner(scope, receive, send)
    assert sent[0]["status"] == 421
    sent.clear()
    _settings("observe")
    await inner(scope, receive, send)
    assert sent[0]["status"] == 200
    assert host_guard.rejected_hosts() == []


async def test_enforce_closes_a_websocket_before_accept():
    _settings("enforce")
    sent = []
    inner = HostGuardMiddleware(Starlette(routes=[WebSocketRoute("/api/ws", _ws)]))

    async def receive():
        return {"type": "websocket.connect"}

    async def send(message):
        sent.append(message)

    scope = {
        "type": "websocket",
        "path": "/api/ws",
        "headers": [(b"host", b"attacker.example")],
        "client": LAN_PEER,
    }
    await inner(scope, receive, send)
    assert sent == [{"type": "websocket.close", "code": 1008}]


def test_the_rejected_list_is_bounded_and_newest_first():
    for i in range(host_guard.MAX_REJECTED_HOSTS + 5):
        host_guard.record_rejected(f"h{i}.example", now=1000 + i)
    seen = host_guard.rejected_hosts()
    assert len(seen) == host_guard.MAX_REJECTED_HOSTS
    assert seen[0].host == f"h{host_guard.MAX_REJECTED_HOSTS + 4}.example"
    assert "h0.example" not in [h.host for h in seen]
    host_guard.record_rejected("h10.example", now=5000)
    assert host_guard.rejected_hosts()[0].host == "h10.example"
    assert host_guard.rejected_hosts()[0].count == 2


def test_forgetting_a_name():
    host_guard.record_rejected("x.example")
    host_guard.forget_rejected("X.example.")
    assert host_guard.rejected_hosts() == []


@pytest.mark.parametrize("mode", ["observe", "enforce"])
def test_trusted_network_stays_strict_in_both_modes(mode):
    _settings(mode)
    scope = {"client": LAN_PEER}
    kwargs = {"trusted_proxies": [], "install_kind": "package"}
    headers = {"host": "attacker.example"}
    assert trusted_network.rule_blocker(scope, headers, "lan", **kwargs) == "host"
    assert trusted_network.rule_blocker(scope, {"host": "192.168.1.10"}, "lan", **kwargs) is None
