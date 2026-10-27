"""The built-in IP ban (MSHL-422): ``app/auth/ip_ban.py``, ``docs/dev/auth.md``, "IP ban".

Fed by the ``AUTH_FAIL`` events (``app/auth/fail_log.py``) with their fields;
enforced by ``AuthMiddleware`` before any credential is read.
"""

from __future__ import annotations

import pytest

from app.auth import audit, ip_ban
from app.auth.providers import ingress
from app.repository.auth import AuthAuditRepository, AuthSettingsRepository
from app.repository.ip_ban import (
    IpBan,
    IpBanRepository,
    ban_key,
    canonical_address,
    parse_ban_key,
    parse_network,
)
from tests.auth_helpers import ADMIN, create_user, make_client
from tests.conftest import patched_database

pytestmark = [pytest.mark.real_auth, pytest.mark.usefixtures("fast_argon2")]

PUBLIC = "198.51.100.20"
OTHER = "203.0.113.40"
BOGUS = {"Authorization": "Bearer mlt_bogus"}


class Clock:
    def __init__(self, now: float = 1_800_000_000.0) -> None:
        self.now = now

    def __call__(self) -> float:
        return self.now


@pytest.fixture
def clock(monkeypatch) -> Clock:
    fake = Clock()
    monkeypatch.setattr(ip_ban, "clock", fake)
    return fake


async def _strict(max_failures: int = 3, **extra) -> None:
    await AuthSettingsRepository.update(ip_ban_max_failures=max_failures, **extra)


async def _fail(peer: str, times: int, headers: dict[str, str] | None = None) -> list[int]:
    statuses = []
    async with make_client(client=(peer, 50000)) as client:
        for _ in range(times):
            response = await client.get("/api/auth/me", headers=headers or BOGUS)
            statuses.append(response.status_code)
    await ip_ban.settle()
    return statuses


def _note(ip: str | None, *, via: str = "lan", proxied: bool = False) -> None:
    ip_ban.note_failure(ip, via=via, proxied=proxied, route="login")


# --- Keys and parsing -------------------------------------------------------------------


@pytest.mark.parametrize(
    ("raw", "key"),
    [
        ("198.51.100.20", "198.51.100.20"),
        ("::ffff:198.51.100.20", "198.51.100.20"),
        ("2001:db8:1:2:3:4:5:6", "2001:db8:1:2::/64"),
        ("2001:db8:1:2::9", "2001:db8:1:2::/64"),
    ],
)
def test_the_key_is_the_address_or_its_ipv6_64(raw, key):
    assert ban_key(canonical_address(raw)) == key
    assert parse_ban_key(raw) == key


@pytest.mark.parametrize("raw", ["", "nope", "10.0.0.0/8", "2001:db8::/48"])
def test_a_bad_ban_key_is_refused(raw):
    assert parse_ban_key(raw) is None


@pytest.mark.parametrize("raw", ["8.8.8.8 ", " 8.8.8.8", "8.8.8.8\n", "2001:db8::1%x"])
def test_a_failure_whose_line_reads_no_ip_counts_nothing(raw):
    """The AUTH_FAIL line shows ``ip=-`` for these: the ban agrees (MSHL-431)."""
    from app.auth import fail_log

    assert fail_log.ip_field(raw) == "-"
    assert ip_ban.bannable(raw) is None
    assert ip_ban.bannable(raw.strip().split("%")[0]) is not None


def test_networks_are_canonical():
    assert str(parse_network("198.51.100.0/24")) == "198.51.100.0/24"
    assert str(parse_network("198.51.100.7")) == "198.51.100.7/32"
    assert str(parse_network("2001:db8::1/64")) == "2001:db8::/64"
    assert parse_network("garbage") is None


# --- Counting (the guards) ------------------------------------------------------------


@pytest.mark.usefixtures("test_db")
class TestCounting:
    async def test_the_threshold_bans_and_audits(self, clock):
        await _strict()
        for _ in range(3):
            _note(PUBLIC)
        await ip_ban.settle()
        ban = await IpBanRepository.get(PUBLIC)
        assert ban is not None and ban.failures == 3 and ban.route == "login"
        assert ban.expires_at == int(clock.now) + 3600
        assert await ip_ban.check(PUBLIC) == 3600
        [row] = [
            r for r in await AuthAuditRepository.list_recent(limit=5) if r.action == "ip.banned"
        ]
        assert row.target == f"ip:{PUBLIC}" and row.outcome == "success"
        assert row.actor_user_id is None

    async def test_below_the_threshold_nothing_happens(self, clock):
        await _strict()
        _note(PUBLIC)
        _note(PUBLIC)
        await ip_ban.settle()
        assert await IpBanRepository.get(PUBLIC) is None
        assert await ip_ban.check(PUBLIC) is None

    async def test_failures_outside_the_window_do_not_add_up(self, clock):
        await _strict(ip_ban_window_minutes=1)
        _note(PUBLIC)
        _note(PUBLIC)
        clock.now += 61
        _note(PUBLIC)
        await ip_ban.settle()
        assert await IpBanRepository.get(PUBLIC) is None

    async def test_a_proxied_failure_never_counts(self, clock):
        await _strict()
        for _ in range(10):
            _note(PUBLIC, proxied=True)
        await ip_ban.settle()
        assert await IpBanRepository.active(clock.now) == []

    @pytest.mark.parametrize(
        "local",
        [
            "127.0.0.1",
            "::1",
            "10.1.2.3",
            "172.18.0.1",
            "192.168.1.30",
            "100.100.1.2",
            "169.254.1.1",
            "fd00::5",
            "fe80::1",
            "::ffff:192.168.1.30",
            "0.0.0.0",
            None,
            "not-an-ip",
        ],
    )
    async def test_a_local_or_missing_address_is_never_banned(self, clock, local):
        await _strict()
        for _ in range(10):
            _note(local)
        await ip_ban.settle()
        assert await IpBanRepository.active(clock.now) == []

    async def test_ingress_never_counts(self, clock):
        await _strict()
        for _ in range(10):
            _note(PUBLIC, via="ingress")
        await ip_ban.settle()
        assert await IpBanRepository.active(clock.now) == []

    async def test_the_relay_bans_the_client_it_names_not_a_local_peer(self, clock):
        await _strict()
        for _ in range(10):
            _note("127.0.0.1", via="relay")  # a dispatcher that set the tunnel: nothing
            _note(None, via="relay")  # one that set no client: nothing
        await ip_ban.settle()
        assert await IpBanRepository.active(clock.now) == []
        for _ in range(3):
            _note(PUBLIC, via="relay")
        await ip_ban.settle()
        assert [b.address for b in await IpBanRepository.active(clock.now)] == [PUBLIC]

    async def test_a_whitelisted_address_is_never_banned(self, clock):
        await _strict()
        assert await IpBanRepository.add_to_whitelist("198.51.100.0/24", "office", 1)
        for _ in range(10):
            _note(PUBLIC)
        await ip_ban.settle()
        assert await IpBanRepository.active(clock.now) == []
        assert await ip_ban.check(PUBLIC) is None
        assert not [r for r in await AuthAuditRepository.list_recent() if r.action == "ip.banned"]

    async def test_an_ipv6_64_is_banned_as_one(self, clock):
        await _strict()
        _note("2001:db8:1:2::1")
        _note("2001:db8:1:2::2")
        _note("2001:db8:1:2:ffff::3")
        await ip_ban.settle()
        assert await ip_ban.check("2001:db8:1:2::99") is not None
        assert await ip_ban.check("2001:db8:1:3::1") is None

    async def test_turned_off_counts_nothing_and_enforces_nothing(self, clock):
        await _strict()
        for _ in range(3):
            _note(PUBLIC)
        await ip_ban.settle()
        await AuthSettingsRepository.update(ip_ban_enabled=False)
        assert await ip_ban.check(PUBLIC) is None
        for _ in range(10):
            _note(OTHER)
        await ip_ban.settle()
        assert await IpBanRepository.get(OTHER) is None
        await AuthSettingsRepository.update(ip_ban_enabled=True)
        assert await ip_ban.check(PUBLIC) is not None  # the row stayed, until it ends


# --- Enforcement ----------------------------------------------------------------------


@pytest.mark.usefixtures("test_db")
class TestEnforcement:
    async def test_bans_expire(self, clock):
        await _strict(ip_ban_duration_minutes=1)
        for _ in range(3):
            _note(PUBLIC)
        await ip_ban.settle()
        assert await ip_ban.check(PUBLIC) == 60
        clock.now += 59
        assert await ip_ban.check(PUBLIC) == 1
        clock.now += 1
        assert await ip_ban.check(PUBLIC) is None
        assert PUBLIC not in ip_ban._bans

    async def test_the_row_end_wins_over_the_memory(self, clock):
        """A row that ends sooner than the memory of it (rewritten elsewhere) is over."""
        await IpBanRepository.ban(
            IpBan(
                address=PUBLIC, banned_at=1, expires_at=int(clock.now) + 60, failures=3, route="x"
            )
        )
        ip_ban._bans[PUBLIC] = clock.now + 3600
        clock.now += 60
        assert await ip_ban.check(PUBLIC) is None
        assert PUBLIC not in ip_ban._bans

    async def test_the_database_down_fails_closed_until_the_ban_ends(self, clock, monkeypatch):
        await _strict()
        for _ in range(3):
            _note(PUBLIC)
        await ip_ban.settle()

        async def broken(*_args, **_kwargs):
            raise RuntimeError("database is locked")

        monkeypatch.setattr(IpBanRepository, "get", broken)
        assert await ip_ban.check(PUBLIC) == 3600
        clock.now += 3600
        assert await ip_ban.check(PUBLIC) is None  # over: no read needed

    async def test_the_row_is_re_read_so_an_unban_elsewhere_applies_at_once(self, clock):
        await _strict()
        for _ in range(3):
            _note(PUBLIC)
        await ip_ban.settle()
        assert await IpBanRepository.unban(PUBLIC)  # what the CLI does, from another process
        assert PUBLIC in ip_ban._bans
        assert await ip_ban.check(PUBLIC) is None
        assert PUBLIC not in ip_ban._bans

    async def test_a_whitelist_entry_added_elsewhere_applies_at_once(self, clock):
        await _strict()
        for _ in range(3):
            _note(PUBLIC)
        await ip_ban.settle()
        await IpBanRepository.add_to_whitelist(f"{PUBLIC}/32", "", 1)
        assert await ip_ban.check(PUBLIC) is None
        await IpBanRepository.remove_from_whitelist(f"{PUBLIC}/32")
        assert await ip_ban.check(PUBLIC) is None  # the hit is cached for a while
        clock.now += ip_ban.WHITELIST_CACHE_S
        assert await ip_ban.check(PUBLIC) is not None  # the row stayed

    async def test_a_ban_survives_a_restart(self, clock, tmp_path):
        path = str(tmp_path / "restart.db")
        async with patched_database(path):
            await _strict()
            for _ in range(3):
                _note(PUBLIC)
            await ip_ban.settle()
        ip_ban.reset()  # a new process
        ip_ban.clock = clock
        async with patched_database(path):
            assert await ip_ban.check(PUBLIC) is None  # nothing loaded yet
            await ip_ban.load()
            assert await ip_ban.check(PUBLIC) == 3600
            assert ip_ban._config.max_failures == 3  # the settings came back too

    async def test_a_banned_client_gets_429_before_any_credential_is_read(self, monkeypatch):
        from app.auth import middleware

        await create_user()
        await _strict()
        assert await _fail(PUBLIC, 3) == [401, 401, 401]

        def no_resolution(*_args, **_kwargs):
            raise AssertionError("a banned client reached the credential checks")

        monkeypatch.setattr(middleware, "resolve", no_resolution)
        async with make_client(client=(PUBLIC, 50000)) as client:
            response = await client.post(
                "/api/auth/login", json={"username": ADMIN, "password": "whatever it is"}
            )
        assert response.status_code == 429
        detail = response.json()["detail"]
        assert detail["code"] == "auth_ip_banned"
        assert 3590 <= detail["params"]["retry_after"] <= 3600
        assert response.headers["retry-after"] == str(detail["params"]["retry_after"])

    async def test_other_clients_are_not_affected(self):
        await create_user()
        await _strict()
        await _fail(PUBLIC, 3)
        async with make_client(client=(OTHER, 50000)) as client:
            assert (await client.get("/api/auth/me", headers=BOGUS)).status_code == 401

    async def test_the_page_itself_is_not_refused(self):
        """Only the API is refused: the UI loads and can show the error."""
        await create_user()
        await _strict()
        await _fail(PUBLIC, 3)
        async with make_client(client=(PUBLIC, 50000)) as client:
            assert (await client.get("/api/auth/me")).status_code == 429
            assert (await client.get("/")).status_code != 429

    @pytest.mark.parametrize("path", ["/", "/assets/x.js", "/some/page"])
    async def test_a_banned_client_reaches_no_credential_check_anywhere(self, monkeypatch, path):
        """A session cookie on a public path is looked up: a banned client is anonymous there."""
        from app.auth import middleware, sessions

        await create_user()
        await _strict()
        calls: list[str] = []
        real = sessions.resolve_session

        async def counting(*args, **kwargs):
            calls.append(path)
            return await real(*args, **kwargs)

        monkeypatch.setattr(middleware.sessions, "resolve_session", counting)
        cookie = {"Cookie": f"{sessions.cookie_name(False)}=not-a-session"}
        async with make_client(client=(OTHER, 50000)) as client:
            await client.get(path, headers=cookie)
        assert calls == [path]  # not banned: the cookie is checked
        calls.clear()
        await _fail(PUBLIC, 3)
        async with make_client(client=(PUBLIC, 50000)) as client:
            response = await client.get(path, headers=cookie)
        assert calls == []
        assert response.status_code != 429

    @pytest.mark.parametrize("path", ["/docs", "/redoc", "/openapi.json"])
    async def test_the_api_docs_are_refused_to_a_banned_client(self, monkeypatch, path):
        from app.auth import middleware

        assert path in middleware.AUTHENTICATED_NON_API_PATHS
        await create_user()
        await _strict()
        await _fail(PUBLIC, 3)

        def no_resolution(*_args, **_kwargs):
            raise AssertionError("a banned client reached the credential checks")

        monkeypatch.setattr(middleware, "resolve", no_resolution)
        async with make_client(client=(PUBLIC, 50000)) as client:
            response = await client.get(path)
        assert response.status_code == 429
        assert response.json()["detail"]["code"] == "auth_ip_banned"

    async def test_a_whitelisted_ban_is_not_read_on_every_request(self, monkeypatch, clock):
        await create_user()
        await _strict()
        await _fail(PUBLIC, 3)
        await IpBanRepository.add_to_whitelist(parse_network(PUBLIC), "", clock.now)
        reads: list[str] = []
        real = IpBanRepository.whitelisted

        async def counting(address):
            reads.append(str(address))
            return await real(address)

        monkeypatch.setattr(IpBanRepository, "whitelisted", staticmethod(counting))
        monkeypatch.setattr(IpBanRepository, "get", None)  # never read for a whitelisted key
        for _ in range(5):
            assert await ip_ban.check(PUBLIC) is None
        assert reads == [PUBLIC]
        clock.now += ip_ban.WHITELIST_CACHE_S
        assert await ip_ban.check(PUBLIC) is None
        assert reads == [PUBLIC, PUBLIC]

    async def test_a_whitelist_removal_by_the_route_applies_at_once(self):
        await create_user()
        await _strict()
        await _fail(PUBLIC, 3)
        await IpBanRepository.add_to_whitelist(parse_network(PUBLIC), "", ip_ban.clock())
        assert await ip_ban.check(PUBLIC) is None
        async with make_client() as client:
            from tests.auth_helpers import login

            await login(client)
            gone = await client.delete("/api/auth/ban-whitelist", params={"network": PUBLIC})
            assert gone.status_code == 204
        assert await ip_ban.check(PUBLIC) is not None

    async def test_the_bans_in_memory_are_bounded(self, monkeypatch, clock):
        monkeypatch.setattr(ip_ban, "MAX_BANS", 2)
        await _strict()
        ip_ban._bans["192.0.2.99"] = clock.now - 1  # ended
        for n in range(3):
            for _ in range(3):
                _note(f"198.51.100.{n + 1}")
            await ip_ban.settle()
            if n == 0:  # under the cap: only the ended rule drops it
                assert set(ip_ban._bans) == {"198.51.100.1"}
            clock.now += 1
        assert "192.0.2.99" not in ip_ban._bans
        assert set(ip_ban._bans) == {"198.51.100.2", "198.51.100.3"}

    async def test_a_websocket_from_a_banned_client_is_refused(self, monkeypatch):
        from app.auth import middleware

        await create_user()
        await _strict()
        await _fail(PUBLIC, 3)
        monkeypatch.setattr(middleware, "resolve", None)  # never reached
        sent: list[dict] = []

        async def app(scope, receive, send):  # pragma: no cover - never reached
            raise AssertionError("a banned client reached the app")

        async def receive():
            return {"type": "websocket.connect"}

        async def send(message):
            sent.append(message)

        scope = {
            "type": "websocket",
            "path": "/api/ws",
            "query_string": b"",
            "headers": [(b"host", b"test")],
            "client": (PUBLIC, 50000),
            "scheme": "ws",
        }
        await middleware.AuthMiddleware(app)(scope, receive, send)
        assert sent == [{"type": "websocket.close", "code": middleware.WS_REFUSED_CLOSE_CODE}]

    async def test_ingress_is_never_refused(self, monkeypatch):
        monkeypatch.setenv("MESHLOOM_INSTALL_KIND", "addon")
        await create_user()
        await IpBanRepository.ban(
            IpBan(
                address=ingress.SUPERVISOR_IP,
                banned_at=1,
                expires_at=4_000_000_000,
                failures=3,
                route="login",
            )
        )
        await ip_ban.load()
        async with make_client(client=(ingress.SUPERVISOR_IP, 40000)) as client:
            response = await client.get("/api/auth/me")
        assert response.status_code != 429

    async def test_proxied_requests_do_not_ban_the_proxy(self):
        await create_user()
        await _strict()
        statuses = await _fail("127.0.0.1", 10, {**BOGUS, "X-Forwarded-For": PUBLIC})
        assert 429 not in statuses
        statuses = await _fail(PUBLIC, 10, {**BOGUS, "X-Forwarded-For": OTHER})
        assert 429 in statuses  # a public peer is the client, whatever it forwards
        assert await IpBanRepository.get(OTHER) is None


# --- Settings and routes ----------------------------------------------------------------


@pytest.mark.usefixtures("test_db")
class TestSettingsAndRoutes:
    @staticmethod
    async def _admin(client):
        from tests.auth_helpers import login

        await create_user()
        await login(client)

    async def test_settings_are_validated_applied_and_audited(self):
        async with make_client() as client:
            await self._admin(client)
            body = (await client.get("/api/auth/settings")).json()
            assert body["ip_ban_enabled"] is True and body["ip_ban_max_failures"] == 10
            for bad in (
                {"ip_ban_max_failures": 2},
                {"ip_ban_max_failures": 1001},
                {"ip_ban_window_minutes": 0},
                {"ip_ban_duration_minutes": 43201},
                {"ip_ban_enabled": "yes"},
                {"ip_ban_max_failures": "5"},
            ):
                response = await client.patch("/api/auth/settings", json=bad)
                assert response.status_code == 422, bad
            response = await client.patch(
                "/api/auth/settings", json={"ip_ban_max_failures": 5, "ip_ban_duration_minutes": 30}
            )
            assert response.status_code == 200
        assert ip_ban._config.max_failures == 5 and ip_ban._config.duration_s == 1800
        [row] = [
            r
            for r in await AuthAuditRepository.list_recent(limit=5)
            if r.target == "auth_settings.ip_ban"
        ]
        assert row.action == "settings.changed"

    async def test_list_unban_and_whitelist_routes(self, clock):
        await _strict()
        for _ in range(3):
            _note(PUBLIC)
        await ip_ban.settle()
        async with make_client() as client:
            await self._admin(client)
            view = (await client.get("/api/auth/bans")).json()
            assert [b["address"] for b in view["bans"]] == [PUBLIC]
            assert view["whitelist"] == []
            bad = await client.delete("/api/auth/bans", params={"address": "nope"})
            assert bad.json()["detail"]["code"] == "auth_ban_address_invalid"
            missing = await client.delete("/api/auth/bans", params={"address": OTHER})
            assert missing.status_code == 404
            assert missing.json()["detail"]["code"] == "auth_ban_not_found"
            assert (
                await client.delete("/api/auth/bans", params={"address": PUBLIC})
            ).status_code == 204
            assert await ip_ban.check(PUBLIC) is None

            added = await client.post(
                "/api/auth/ban-whitelist", json={"network": "203.0.113.0/24", "note": "office"}
            )
            assert added.status_code == 201 and added.json()["network"] == "203.0.113.0/24"
            again = await client.post("/api/auth/ban-whitelist", json={"network": "203.0.113.0/24"})
            assert again.json()["detail"]["code"] == "auth_ban_whitelist_exists"
            invalid = await client.post("/api/auth/ban-whitelist", json={"network": "x"})
            assert invalid.json()["detail"]["code"] == "auth_ban_whitelist_invalid"
            gone = await client.delete(
                "/api/auth/ban-whitelist", params={"network": "203.0.113.0/24"}
            )
            assert gone.status_code == 204
            assert (
                await client.delete("/api/auth/ban-whitelist", params={"network": "203.0.113.0/24"})
            ).json()["detail"]["code"] == "auth_ban_whitelist_not_found"
        actions = [r.action for r in await AuthAuditRepository.list_recent(limit=20)]
        assert {"ip.banned", "ip.unbanned", "ip.whitelisted", "ip.unwhitelisted"} <= set(actions)

    async def test_the_routes_are_admin_only(self):
        from tests.auth_helpers import login

        await create_user()
        await create_user("member1", role="member")
        async with make_client() as client:
            await login(client, "member1")
            assert (await client.get("/api/auth/bans")).status_code == 403
            assert (
                await client.delete("/api/auth/bans", params={"address": PUBLIC})
            ).status_code == 403
            assert (
                await client.post("/api/auth/ban-whitelist", json={"network": PUBLIC})
            ).status_code == 403
            assert (
                await client.delete("/api/auth/ban-whitelist", params={"network": PUBLIC})
            ).status_code == 403
            assert (await client.get("/api/auth/settings")).status_code == 403
            assert (
                await client.patch("/api/auth/settings", json={"ip_ban_enabled": False})
            ).status_code == 403
        assert (await AuthSettingsRepository.get()).ip_ban_enabled is True

    async def test_the_whitelist_is_capped(self, monkeypatch):
        from app.repository import ip_ban as repository

        monkeypatch.setattr(repository, "MAX_WHITELIST", 2)
        async with make_client() as client:
            await self._admin(client)
            for network in ("203.0.113.0/24", "198.51.100.0/24"):
                added = await client.post("/api/auth/ban-whitelist", json={"network": network})
                assert added.status_code == 201
            full = await client.post("/api/auth/ban-whitelist", json={"network": "192.0.2.0/24"})
        assert full.status_code == 409
        assert full.json()["detail"]["code"] == "auth_ban_whitelist_full"
        assert len(await IpBanRepository.whitelist()) == 2


def test_the_audit_actions_exist():
    actions = set(audit.AUDIT_ACTIONS)
    assert {"ip.banned", "ip.unbanned", "ip.whitelisted", "ip.unwhitelisted"} <= actions


# --- The host's own IPv6 prefixes ---------------------------------------------------------

HOST_LAN = "2a01:e0a:12:3450::/64"
LAN_CLIENT = "2a01:e0a:12:3450:1c2d:3e4f:5a6b:7c8d"


def _if_inet6(tmp_path, *lines: str) -> str:
    path = tmp_path / "if_inet6"
    path.write_text("".join(f"{line}\n" for line in lines))
    return str(path)


IF_INET6 = (
    "2a010e0a001234500211223344556677 02 40 00 00 eth0",  # global /64: the LAN
    "2a010e0a001256000000000000000001 05 38 00 00 eth1",  # global /56
    "2a010e0a001278000000000000000005 06 80 00 00 eth2",  # global /128: one address
    "fe800000000000000211223344556677 02 40 20 80 eth0",  # link-local
    "00000000000000000000000000000001 01 80 10 80 lo",  # loopback
    "2a010e0a00ff00000000000000000001 03 20 00 00 wan0",  # /32: too wide for a LAN
    "20010db8000000000000000000000001 04 40 00 00 doc0",  # documentation: not global
)


def test_the_host_prefixes_are_its_global_on_link_ones(tmp_path):
    assert [str(n) for n in ip_ban.read_host_prefixes(_if_inet6(tmp_path, *IF_INET6))] == [
        HOST_LAN,
        "2a01:e0a:12:5600::/56",
        "2a01:e0a:12:7800::5/128",
    ]


@pytest.mark.parametrize("content", [None, "", "garbage\nzz 1 2\nxyz 02 40 00 00 eth0\n"])
def test_an_unreadable_prefix_file_adds_nothing(tmp_path, content):
    path = tmp_path / "if_inet6"
    if content is not None:
        path.write_text(content)
    assert ip_ban.read_host_prefixes(str(path)) == ()


@pytest.mark.usefixtures("test_db")
class TestHostPrefixes:
    async def test_a_lan_client_on_global_ipv6_is_never_banned(self, monkeypatch, tmp_path):
        monkeypatch.setattr(ip_ban, "IF_INET6_PATH", _if_inet6(tmp_path, *IF_INET6))
        for _ in range(20):
            _note(LAN_CLIENT)
        await ip_ban.settle()
        assert await IpBanRepository.active(ip_ban.clock()) == []
        for _ in range(10):
            _note("2a01:e0a:12:9999::1")  # another /64 of the same /48: public
        await ip_ban.settle()
        assert await IpBanRepository.get("2a01:e0a:12:9999::/64") is not None

    async def test_the_prefix_length_is_kept(self, monkeypatch, tmp_path):
        monkeypatch.setattr(ip_ban, "IF_INET6_PATH", _if_inet6(tmp_path, *IF_INET6))
        for _ in range(20):
            _note("2a01:e0a:12:56ab::1")  # another /64 of the host's /56: local
        await ip_ban.settle()
        assert await IpBanRepository.active(ip_ban.clock()) == []
        for _ in range(10):
            _note("2a01:e0a:12:7800::6")  # beside the host's /128: public
        await ip_ban.settle()
        assert await IpBanRepository.get("2a01:e0a:12:7800::/64") is not None

    async def test_without_the_file_it_counts_as_public(self):
        for _ in range(10):
            _note(LAN_CLIENT)
        await ip_ban.settle()
        assert await IpBanRepository.get(HOST_LAN) is not None

    async def test_a_ban_on_a_host_prefix_does_not_apply(self, monkeypatch, tmp_path):
        """A ban written before the prefix was seen (renumbering, a restart)."""
        for _ in range(10):
            _note(LAN_CLIENT)
        await ip_ban.settle()
        assert await ip_ban.check(LAN_CLIENT) is not None
        monkeypatch.setattr(ip_ban, "IF_INET6_PATH", _if_inet6(tmp_path, *IF_INET6))
        ip_ban._host_prefixes = None
        assert await ip_ban.check(LAN_CLIENT) is None

    async def test_the_prefixes_are_read_again_after_their_ttl(self, monkeypatch, tmp_path):
        path = _if_inet6(tmp_path)
        monkeypatch.setattr(ip_ban, "IF_INET6_PATH", path)
        now = [1000.0]
        monkeypatch.setattr(ip_ban.time, "monotonic", lambda: now[0])
        assert ip_ban.bannable(LAN_CLIENT) is not None
        _if_inet6(tmp_path, *IF_INET6)
        assert ip_ban.bannable(LAN_CLIENT) is not None  # cached
        now[0] += ip_ban.HOST_PREFIXES_TTL_S
        assert ip_ban.bannable(LAN_CLIENT) is None
