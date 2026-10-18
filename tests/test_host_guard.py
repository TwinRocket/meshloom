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
    assert [e.host for e in host_guard.rejected_hosts()] == ["attacker.example"]


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


# ---------------------------------------------------------------- MSHL-65


@pytest.mark.parametrize(
    ("raw", "canonical"),
    [
        ("https://Meshloom.Example.COM", "https://meshloom.example.com"),
        ("https://meshloom.example.com:443/", "https://meshloom.example.com"),
        ("https://meshloom.example.com:8443/mesh/", "https://meshloom.example.com:8443/mesh"),
        ("  https://192.0.2.7  ", "https://192.0.2.7"),
        ("https://[2001:db8::1]:8443", "https://[2001:db8::1]:8443"),
        ("https://bücher.de", "https://xn--bcher-kva.de"),
        ("https://Bücher.DE:8443/a-b/c_d.e~f%20g", "https://xn--bcher-kva.de:8443/a-b/c_d.e~f%20g"),
    ],
)
def test_public_urls_are_normalized(raw, canonical):
    assert host_guard.normalize_public_url(raw) == canonical


@pytest.mark.parametrize(
    "raw",
    [
        "",
        "meshloom.example.com",
        "http://meshloom.example.com",
        "ftp://meshloom.example.com",
        "https://user:pw@meshloom.example.com",
        "https://meshloom.example.com?x=1",
        "https://meshloom.example.com#frag",
        "https://meshloom.example.com:99999",
        "https://meshloom.example.com/a/../b",
        "https://meshloom.example.com//x",
        "https://mesh loom.example.com",
        "https://mesh_loom.example.com",
        'https://a"b.example.com',
        "https:///path",
        "https://foo-.example.com",
        "https://-foo.example.com",
        f"https://{'a' * 64}.example.com",
        "https://exa mple.com",
        "https://\u202eexample.com",
        "https://example.com:0",
        "https://example.com/<img/src=x/onerror=alert(1)>",
        "https://example.com/%2e%2e/a",
        "https://example.com/%2E/a",
        "https://example.com/a%2Fb",
        "https://example.com/a;b",
        "https://example.com/a%zz",
        "https://example.com/a'b",
    ],
)
def test_invalid_public_urls_are_refused(raw):
    with pytest.raises(host_guard.PublicUrlError):
        host_guard.normalize_public_url(raw)


def test_public_urls_are_deduplicated_and_bounded():
    assert host_guard.normalize_public_urls(
        ["https://a.example.com", "https://A.example.com:443"]
    ) == ["https://a.example.com"]
    with pytest.raises(host_guard.PublicUrlError):
        host_guard.normalize_public_urls(
            [f"https://h{i}.example.com" for i in range(host_guard.MAX_PUBLIC_URLS + 1)]
        )


async def test_only_ldh_names_are_recorded():
    _settings()
    for host in ("a'b.example.com", "a;b.example.com", "{x}.example.com", "a_b.example.com"):
        async with _client(host) as client:
            assert (await client.get("/api/x")).status_code == 200
    assert host_guard.rejected_hosts() == []
    async with _client("proxy.example.com") as client:
        await client.get("/api/x")
    assert [e.host for e in host_guard.rejected_hosts()] == ["proxy.example.com"]


def test_the_first_sighting_warning_is_rate_limited(caplog):
    caplog.set_level("WARNING", logger=host_guard.logger.name)
    for i in range(host_guard.MAX_REJECTED_HOSTS * 2):
        host_guard.record_rejected(f"h{i}.example.com")
    lines = [r for r in caplog.records if "not a declared address" in r.getMessage()]
    assert len(lines) == host_guard.WARNING_BURST
    # The names are all still recorded (bounded), only the log is limited.
    assert len(host_guard.rejected_hosts()) == host_guard.MAX_REJECTED_HOSTS


def test_the_warning_budget_comes_back_with_a_summary(caplog):
    caplog.set_level("WARNING", logger=host_guard.logger.name)
    for i in range(7):
        host_guard._warn_first_sighting(f"h{i}.example.com", 1000.0)
    host_guard._warn_first_sighting("later.example.com", 1000.0 + host_guard.WARNING_WINDOW_S)
    messages = [r.getMessage() for r in caplog.records]
    assert sum("not a declared address" in m for m in messages) == host_guard.WARNING_BURST + 1
    assert any("2 more unknown Host names" in m for m in messages)


async def test_the_421_carries_the_security_headers():
    _settings("enforce")
    async with _client("attacker.example") as client:
        response = await client.get("/api/x")
    assert response.status_code == 421
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["x-frame-options"] == "DENY"
    assert response.headers["referrer-policy"]
    assert "frame-ancestors 'none'" in response.headers["content-security-policy"]
    assert response.headers["permissions-policy"]
    assert response.headers["cache-control"] == "no-store"
    assert "strict-transport-security" not in response.headers


async def test_enforce_mode_records_the_refused_names_and_only_ldh_ones():
    _settings("enforce")
    async with _client("evil.example") as client:
        assert (await client.get("/api/x")).status_code == 421
        assert (await client.get("/api/x")).status_code == 421
    async with _client("a;b.example") as client:
        assert (await client.get("/api/x")).status_code == 421
    names = host_guard.rejected_hosts()
    assert [(e.host, e.count) for e in names] == [("evil.example", 2)]


async def test_enforce_mode_records_a_refused_websocket_upgrade():
    from starlette.testclient import TestClient
    from starlette.websockets import WebSocketDisconnect

    _settings("enforce")
    app = Starlette(routes=[Route("/api/x", _ok), WebSocketRoute("/api/ws", _ws)])
    client = TestClient(HostGuardMiddleware(app), base_url="http://evil.example")
    with (
        pytest.raises(WebSocketDisconnect),
        client.websocket_connect("/api/ws", headers={"host": "evil.example"}),
    ):
        pass
    assert [e.host for e in host_guard.rejected_hosts()] == ["evil.example"]


@pytest.mark.parametrize("state", [{"via": "relay"}, None])
def test_would_lock_out_exempts_relay_and_judges_the_rest(state):
    scope = {"type": "http", "headers": [(b"host", b"evil.example")], "state": state or {}}
    assert host_guard.would_lock_out(scope, "enforce", []) is (state is None)
    assert host_guard.would_lock_out(scope, "observe", []) is False
    assert host_guard.would_lock_out(scope, "enforce", ["https://evil.example"]) is False
