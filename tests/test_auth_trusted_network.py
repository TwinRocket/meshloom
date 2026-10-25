"""Trusted networks (app/auth/providers/trusted_network.py) through the real gate."""

from __future__ import annotations

from contextlib import asynccontextmanager

import httpx
import pytest

from app.auth.models import TrustedNetworkRule
from app.auth.providers import trusted_network
from app.repository.auth import AuthAuditRepository, AuthSettingsRepository, UserRepository
from tests.auth_helpers import create_user, login, make_client

pytestmark = [pytest.mark.real_auth, pytest.mark.usefixtures("test_db", "fast_argon2")]

LAN_PEER = ("192.168.1.20", 40000)
OTHER_LAN_PEER = ("192.168.1.21", 40000)
OUTSIDE_PEER = ("10.9.9.9", 40000)
LAN_URL = "http://192.168.1.10:8000"


@pytest.fixture(autouse=True)
def package(monkeypatch):
    monkeypatch.setenv("MESHLOOM_INSTALL_KIND", "package")


async def _rule(cidr: str = "192.168.1.0/24", role: str = "member", username: str = "tablet"):
    await create_user()
    user = await create_user(username, password=None, role=role)
    await AuthSettingsRepository.update(
        trusted_networks=[TrustedNetworkRule(cidr=cidr, user_id=user.id)]
    )
    return user


async def _session(client: httpx.AsyncClient, headers: dict | None = None) -> dict:
    return (await client.get("/api/auth/session", headers=headers or {})).json()


def _code(response: httpx.Response) -> str:
    return response.json()["detail"]["code"]


async def test_a_listed_network_without_forwarding_headers_is_signed_in():
    tablet = await _rule()
    async with make_client(LAN_URL, LAN_PEER) as client:
        body = await _session(client)
        assert body["authenticated"] is True
        assert body["via"] == "trusted"
        assert body["user"]["id"] == tablet.id
        assert (await client.get("/api/contacts")).status_code == 200
        # No cookie: the address is the credential.
        assert "set-cookie" not in (await client.get("/api/contacts")).headers
    async with make_client(LAN_URL, OUTSIDE_PEER) as client:
        assert (await _session(client))["authenticated"] is False
        assert (await client.get("/api/contacts")).status_code == 401


@pytest.mark.parametrize(
    "header",
    [
        "X-Forwarded-For",
        "X-Forwarded-Proto",
        "X-Forwarded-Host",
        "Forwarded",
        "X-Real-IP",
        "CF-Connecting-IP",
        "Via",
    ],
)
async def test_any_forwarding_header_from_an_unlisted_peer_closes_the_rule(header):
    await _rule()
    async with make_client(LAN_URL, LAN_PEER) as client:
        response = await client.get("/api/contacts", headers={header: "192.168.1.30"})
        assert response.status_code == 401
        assert _code(response) == "auth_required"


async def test_a_peer_listed_as_trusted_proxy_is_never_auto_signed_in():
    tablet = await _rule("192.168.1.0/24")
    # Saved directly: the API refuses the overlap (tested below).
    await AuthSettingsRepository.update(trusted_proxies=["192.168.2.5"])
    proxied = {"X-Forwarded-For": "192.168.1.20"}
    async with make_client(LAN_URL, ("192.168.2.5", 40000)) as client:
        # The forwarded address is in the rule; the proxy rewrote the client to it.
        assert (await client.get("/api/contacts", headers=proxied)).status_code == 401
        assert (await client.get("/api/contacts")).status_code == 401
    # Even a proxy inside the rule's network, without any header.
    await AuthSettingsRepository.update(trusted_proxies=[], trusted_networks=[])
    trusted_network.reset()
    await AuthSettingsRepository.update(
        trusted_proxies=["192.168.1.20"],
        trusted_networks=[TrustedNetworkRule(cidr="192.168.1.0/24", user_id=tablet.id)],
    )
    async with make_client(LAN_URL, LAN_PEER) as client:
        assert (await client.get("/api/contacts")).status_code == 401


async def test_a_relayed_request_is_not_auto_signed_in():
    await _rule()
    from app.main import app

    async def relayed(scope, receive, send):
        scope.setdefault("state", {})["via"] = "relay"
        await app(scope, receive, send)

    transport = httpx.ASGITransport(app=relayed, client=LAN_PEER)
    async with httpx.AsyncClient(transport=transport, base_url=LAN_URL) as client:
        response = await client.get("/api/contacts")
        assert response.status_code == 401


async def test_ingress_is_never_a_trusted_network(monkeypatch):
    monkeypatch.setenv("MESHLOOM_INSTALL_KIND", "addon")
    await _rule("172.30.32.0/24")
    async with make_client(LAN_URL, ("172.30.32.2", 40000)) as client:
        assert (await client.get("/api/contacts")).status_code == 401


@pytest.mark.parametrize("kind", ["package", "source", "compose", "container"])
async def test_loopback_never_matches_where_a_local_proxy_may_sit(monkeypatch, kind):
    monkeypatch.setenv("MESHLOOM_INSTALL_KIND", kind)
    await _rule("127.0.0.0/8")
    async with make_client("http://localhost:8000", ("127.0.0.1", 40000)) as client:
        assert (await client.get("/api/contacts")).status_code == 401


async def test_loopback_matches_inside_the_add_on(monkeypatch):
    monkeypatch.setenv("MESHLOOM_INSTALL_KIND", "addon")
    await _rule("127.0.0.0/8")
    async with make_client("http://localhost:8000", ("127.0.0.1", 40000)) as client:
        assert (await client.get("/api/contacts")).status_code == 200


@pytest.mark.parametrize(
    "host",
    [
        "evil.example.com",
        "192.168.1.10.nip.io",
        "",
        "user@192.168.1.10",
        "meshloom.example.com:8000",
    ],
)
async def test_the_host_guard_is_strict(host):
    await _rule()
    async with make_client(LAN_URL, LAN_PEER) as client:
        response = await client.get("/api/contacts", headers={"Host": host})
        assert response.status_code == 401


@pytest.mark.parametrize(
    "host",
    ["192.168.1.10:8000", "[fe80::1]:8000", "localhost", "meshloom.local", "pi.home.arpa", "x.lan"],
)
async def test_local_host_names_pass_the_guard(host):
    await _rule()
    async with make_client(LAN_URL, LAN_PEER) as client:
        response = await client.get("/api/contacts", headers={"Host": host})
        assert response.status_code == 200, host


async def test_a_declared_public_url_passes_the_guard():
    await _rule()
    await AuthSettingsRepository.update(public_urls=["https://mesh.example.org"])
    assert trusted_network.host_allowed("mesh.example.org", ["https://mesh.example.org"])
    async with make_client(LAN_URL, LAN_PEER) as client:
        ok = await client.get("/api/contacts", headers={"Host": "mesh.example.org"})
        assert ok.status_code == 200


async def test_a_disabled_or_deleted_account_is_not_signed_in():
    tablet = await _rule()
    await UserRepository.update(tablet.id, disabled=True)
    async with make_client(LAN_URL, LAN_PEER) as client:
        assert (await client.get("/api/contacts")).status_code == 401
    await UserRepository.delete(tablet.id)
    async with make_client(LAN_URL, LAN_PEER) as client:
        assert (await client.get("/api/contacts")).status_code == 401


async def test_the_most_specific_rule_wins():
    await create_user()
    wide = await create_user("wide", password=None, role="viewer")
    narrow = await create_user("narrow", password=None, role="member")
    await AuthSettingsRepository.update(
        trusted_networks=[
            TrustedNetworkRule(cidr="192.168.0.0/16", user_id=wide.id),
            TrustedNetworkRule(cidr="192.168.1.20/32", user_id=narrow.id),
        ]
    )
    async with make_client(LAN_URL, LAN_PEER) as client:
        assert (await _session(client))["user"]["id"] == narrow.id
    async with make_client(LAN_URL, OTHER_LAN_PEER) as client:
        assert (await _session(client))["user"]["id"] == wide.id


async def test_explicit_credentials_win_over_the_rule():
    await _rule()
    async with make_client(LAN_URL, LAN_PEER) as client:
        await login(client)
        body = await _session(client)
        assert body["user"]["username"] == "owner"
        assert body["via"] == "lan"
        # A bad bearer token is a failure, never a fallback to the rule.
        bad = await client.get("/api/contacts", headers={"Authorization": "Bearer nope"})
        assert bad.status_code == 401


async def test_unsafe_methods_keep_the_origin_rule():
    await _rule(role="admin", username="lanadmin")
    async with make_client(LAN_URL, LAN_PEER) as client:
        refused = await client.post(
            "/api/auth/logout", headers={"Origin": "http://evil.example.com"}
        )
        assert refused.status_code == 403
        assert _code(refused) == "auth_origin_rejected"


async def test_auto_logins_are_audited_once_per_address_per_day():
    tablet = await _rule()
    async with make_client(LAN_URL, LAN_PEER) as client:
        for _ in range(5):
            assert (await client.get("/api/contacts")).status_code == 200
    async with make_client(LAN_URL, OTHER_LAN_PEER) as client:
        assert (await client.get("/api/contacts")).status_code == 200
    rows = [r for r in await AuthAuditRepository.list_recent() if r.action == "login"]
    assert sorted(r.ip for r in rows) == ["192.168.1.20", "192.168.1.21"]
    assert all(r.via == "trusted" and r.actor_user_id == tablet.id for r in rows)
    assert all(r.details == {"method": "trusted_network", "cidr": "192.168.1.0/24"} for r in rows)


async def test_the_audit_memory_is_bounded(monkeypatch):
    tablet = await _rule()
    monkeypatch.setattr(trusted_network, "MAX_AUDITED", 3)
    rule = TrustedNetworkRule(cidr="192.168.1.0/24", user_id=tablet.id)
    for i in range(10):
        await trusted_network._audit_login(f"192.168.1.{i}", tablet, rule)
    assert len(trusted_network._audited) <= 3


# ---------------------------------------------------------------- settings API


@asynccontextmanager
async def _admin_client():
    await create_user()
    async with make_client(LAN_URL, OUTSIDE_PEER) as client:
        await login(client)
        yield client


async def test_settings_round_trip_and_audit():
    async with _admin_client() as client:
        tablet = await create_user("tablet", password=None, role="viewer")
        response = await client.patch(
            "/api/auth/settings",
            json={"trusted_networks": [{"cidr": "192.168.1.7", "user_id": tablet.id}]},
        )
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["trusted_networks"] == [{"cidr": "192.168.1.7/32", "user_id": tablet.id}]
        assert body["security_notices"] == []
        assert (await client.get("/api/auth/settings")).json()["trusted_networks"] == body[
            "trusted_networks"
        ]
    rows = [r for r in await AuthAuditRepository.list_recent() if r.action == "settings.changed"]
    assert rows[0].target == "auth_settings.trusted_networks"
    # Applied to the next request without a restart.
    async with make_client(LAN_URL, ("192.168.1.7", 1)) as client:
        assert (await _session(client))["via"] == "trusted"


async def test_a_rule_overlapping_a_trusted_proxy_is_refused():
    async with _admin_client() as client:
        tablet = await create_user("tablet", password=None, role="viewer")
        assert (
            await client.patch("/api/auth/settings", json={"trusted_proxies": ["192.168.1.5"]})
        ).status_code == 200
        response = await client.patch(
            "/api/auth/settings",
            json={"trusted_networks": [{"cidr": "192.168.1.0/24", "user_id": tablet.id}]},
        )
        assert response.status_code == 400
        assert _code(response) == "auth_trusted_network_overlaps_proxy"
        assert response.json()["detail"]["params"] == {
            "cidr": "192.168.1.0/24",
            "proxy": "192.168.1.5/32",
        }
        # The other way round: a proxy inside a saved rule.
        await client.patch("/api/auth/settings", json={"trusted_proxies": []})
        ok = await client.patch(
            "/api/auth/settings",
            json={"trusted_networks": [{"cidr": "192.168.1.0/24", "user_id": tablet.id}]},
        )
        assert ok.status_code == 200
        refused = await client.patch(
            "/api/auth/settings", json={"trusted_proxies": ["192.168.1.0/28"]}
        )
        assert refused.status_code == 400
        assert _code(refused) == "auth_trusted_network_overlaps_proxy"
    settings = await AuthSettingsRepository.get()
    assert settings.trusted_proxies == []


@pytest.mark.parametrize(
    ("rules", "code"),
    [
        ([{"cidr": "not-a-network", "user_id": 1}], "auth_trusted_networks_invalid"),
        ([{"cidr": "0.0.0.0/0", "user_id": 1}], "auth_trusted_networks_invalid"),
        ([{"cidr": "::ffff:192.168.1.0/120", "user_id": 1}], "auth_trusted_networks_invalid"),
        (
            [{"cidr": "192.168.1.0/24", "user_id": 1}, {"cidr": "192.168.1.9/24", "user_id": 1}],
            "auth_trusted_networks_invalid",
        ),
        ([{"cidr": "192.168.1.0/24", "user_id": 999}], "auth_trusted_network_user_invalid"),
        ([{"cidr": "127.0.0.1", "user_id": 1}], "auth_trusted_network_loopback"),
        ([{"cidr": "::1", "user_id": 1}], "auth_trusted_network_loopback"),
    ],
)
async def test_invalid_rules_are_refused_with_a_code(rules, code):
    async with _admin_client() as client:
        response = await client.patch("/api/auth/settings", json={"trusted_networks": rules})
        assert response.status_code == 400, response.text
        assert _code(response) == code
    assert (await AuthSettingsRepository.get()).trusted_networks == []


async def test_the_docker_gateway_is_refused_while_a_public_address_exists(monkeypatch, tmp_path):
    monkeypatch.setenv("MESHLOOM_INSTALL_KIND", "compose")
    route = tmp_path / "route"
    route.write_text(
        "Iface\tDestination\tGateway\tFlags\n"
        "eth0\t00000000\t010011AC\t0003\n"
        "eth0\t000011AC\t00000000\t0001\n"
    )
    monkeypatch.setattr(trusted_network, "ROUTE_TABLE", route)
    assert trusted_network.docker_gateways() == ("172.17.0.1",)
    async with _admin_client() as client:
        tablet = await create_user("tablet", password=None, role="viewer")
        rules = {"trusted_networks": [{"cidr": "172.17.0.0/16", "user_id": tablet.id}]}
        # No public address: allowed (the published port is the LAN's way in).
        assert (await client.patch("/api/auth/settings", json=rules)).status_code == 200
        await AuthSettingsRepository.update(public_urls=["https://mesh.example.org"])
        notices = (await client.get("/api/auth/settings")).json()["security_notices"]
        assert "trusted_loopback_or_gateway_public" in notices
        response = await client.patch("/api/auth/settings", json=rules)
        assert response.status_code == 400
        assert _code(response) == "auth_trusted_network_overlaps_gateway"


async def test_the_notice_names_an_admin_rule_with_reduced_plugin_isolation(monkeypatch):
    from app.plugins import lifecycle

    fields = {"plugins_disabled_source": None, "plugins_isolation": "none"}
    monkeypatch.setattr(lifecycle, "health_fields", lambda: fields)
    async with _admin_client() as client:
        lanadmin = await create_user("lanadmin", password=None, role="admin")
        body = (
            await client.patch(
                "/api/auth/settings",
                json={"trusted_networks": [{"cidr": "192.168.1.0/24", "user_id": lanadmin.id}]},
            )
        ).json()
        assert body["security_notices"] == ["trusted_admin_reduced_plugin_isolation"]
        fields["plugins_isolation"] = "bwrap"
        assert (await client.get("/api/auth/settings")).json()["security_notices"] == []


async def test_only_an_admin_reads_or_changes_the_rules():
    await create_user()
    await create_user("bob", role="member")
    async with make_client(LAN_URL, OUTSIDE_PEER) as client:
        await login(client, "bob")
        assert (await client.get("/api/auth/settings")).status_code == 403
        assert (
            await client.patch("/api/auth/settings", json={"trusted_networks": []})
        ).status_code == 403


# ---------------------------------------------------------------- units


def test_rule_blocker_table():
    headers = {"host": "192.168.1.10"}
    scope = {"type": "http", "client": LAN_PEER}
    kwargs = {"trusted_proxies": (), "install_kind": "package"}
    assert trusted_network.rule_blocker(scope, headers, "lan", **kwargs) is None
    assert trusted_network.rule_blocker(scope, headers, "relay", **kwargs) == "via"
    assert trusted_network.rule_blocker(scope, headers, "ingress", **kwargs) == "via"
    relayed = {**scope, "state": {"via": "relay"}}
    assert trusted_network.rule_blocker(relayed, headers, "lan", **kwargs) == "via"
    assert trusted_network.rule_blocker({"type": "http"}, headers, "lan", **kwargs) == "no_peer"
    assert (
        trusted_network.rule_blocker(
            scope, headers, "lan", trusted_proxies=("192.168.1.0/24",), install_kind="package"
        )
        == "trusted_proxy"
    )
    mapped = {"type": "http", "client": ("::ffff:127.0.0.1", 1)}
    assert trusted_network.rule_blocker(mapped, headers, "lan", **kwargs) == "loopback"
    assert trusted_network.rule_blocker(scope, {}, "lan", **kwargs) == "host"


def test_matching_maps_ipv4_mapped_peers():
    rules = [TrustedNetworkRule(cidr="192.168.1.0/24", user_id=1)]
    address = trusted_network._parse_address("::ffff:192.168.1.20")
    assert address is not None
    assert trusted_network.matching_rule(address, rules) == rules[0]


def test_invalid_stored_rules_are_dropped():
    rules = trusted_network.valid_stored_rules(
        [
            TrustedNetworkRule(cidr="10.0.0.0/8", user_id=1),
            TrustedNetworkRule(cidr="0.0.0.0/0", user_id=1),
        ]
    )
    assert [rule.cidr for rule in rules] == ["10.0.0.0/8"]
