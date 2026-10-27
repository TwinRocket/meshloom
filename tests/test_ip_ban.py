"""The built-in IP ban (MSHL-422): ``app/auth/ip_ban.py``, ``docs/dev/auth.md``, "IP ban".

Fed by the ``AUTH_FAIL`` events (``app/auth/fail_log.py``) with their fields;
enforced by ``AuthMiddleware`` before any credential is read.
"""

from __future__ import annotations

import ipaddress

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


# --- Policy: address range x proxy situation (MSHL-434) ---------------------------------

# (address, banned): the ranges the ban never touches, and the ones it does.
RANGES = [
    ("127.0.0.1", False),  # loopback
    ("::1", False),
    ("10.1.2.3", False),  # RFC 1918
    ("172.18.0.1", False),
    ("192.168.1.30", False),
    ("169.254.1.1", False),  # link-local
    ("fe80::1", False),
    ("fd00::5", False),  # ULA
    ("100.64.0.1", True),  # CGNAT / tailnet, bottom
    ("100.100.1.2", True),
    ("100.127.255.254", True),  # top
    ("198.51.100.20", True),  # public
    ("2001:db8:1:2::5", True),
]


def _banned_keys(bans: list[IpBan]) -> list[str]:
    return [ban.address for ban in bans]


@pytest.mark.usefixtures("test_db")
class TestPolicy:
    """Every range, as the TCP peer, as a peer forging ``X-Forwarded-For``, and as
    the client a trusted proxy on loopback (``tailscale serve``) forwarded."""

    @pytest.mark.parametrize(("address", "banned"), RANGES)
    async def test_as_the_peer(self, address, banned):
        await create_user()
        await _strict()
        await _fail(address, 4)
        expected = [ban_key(canonical_address(address))] if banned else []
        assert _banned_keys(await IpBanRepository.active(ip_ban.clock())) == expected

    @pytest.mark.parametrize(("address", "banned"), RANGES)
    async def test_as_a_peer_forging_forwarding_headers(self, address, banned):
        """An untrusted peer's header is never read: the peer is banned (or not,
        by its own range), never the address it names."""
        await create_user()
        await _strict()
        await _fail(address, 4, {**BOGUS, "X-Forwarded-For": OTHER})
        await _fail(address, 4, {**BOGUS, "X-Forwarded-For": "100.100.9.9"})
        expected = [ban_key(canonical_address(address))] if banned else []
        assert _banned_keys(await IpBanRepository.active(ip_ban.clock())) == expected

    @pytest.mark.parametrize(("address", "banned"), RANGES)
    async def test_as_the_client_of_a_trusted_loopback_proxy(self, address, banned):
        await create_user()
        await _strict(trusted_proxies=["127.0.0.1", "::1"])
        await _fail("127.0.0.1", 4, {**BOGUS, "X-Forwarded-For": address})
        expected = [ban_key(canonical_address(address))] if banned else []
        assert _banned_keys(await IpBanRepository.active(ip_ban.clock())) == expected

    async def test_a_forged_header_through_a_trusted_proxy_bans_the_hop_it_saw(self):
        """The trusted proxy appends the peer it saw: the rightmost untrusted entry
        is banned, never an address the client wrote to the left of it."""
        await create_user()
        await _strict(trusted_proxies=["127.0.0.1"])
        await _fail("127.0.0.1", 4, {**BOGUS, "X-Forwarded-For": f"{OTHER}, 100.100.1.2"})
        assert _banned_keys(await IpBanRepository.active(ip_ban.clock())) == ["100.100.1.2"]

    @pytest.mark.parametrize("address", ["100.100.1.2", "100.64.0.0/10"])
    async def test_a_whitelisted_tailnet_device_is_never_banned(self, address):
        """The remedy for a tailnet device locked out (docs: fail2ban.md)."""
        await create_user()
        await _strict()
        assert await IpBanRepository.add_to_whitelist(parse_network(address), "tailnet", 1)
        statuses = await _fail("100.100.1.2", 6)
        assert 429 not in statuses
        assert await IpBanRepository.active(ip_ban.clock()) == []


def test_ban_exempt_is_local_minus_cgnat():
    from app.net import local_address

    for address, banned in RANGES:
        assert local_address.is_ban_exempt(address) is (not banned), address
    assert local_address.is_ban_exempt("::ffff:100.100.1.2") is False
    assert local_address.is_ban_exempt("::ffff:192.168.1.30") is True
    assert local_address.is_ban_exempt("100.128.0.0") is False  # public
    assert local_address.is_ban_exempt(None) is False
    assert local_address.is_ban_exempt("garbage") is False
    # The login-free gate still reads CGNAT as local.
    assert ip_ban.is_local(canonical_address("100.100.1.2"))


@pytest.mark.usefixtures("test_db")
async def test_a_cgnat_ban_applies(clock):
    """``check()`` skips only exempt addresses: a CGNAT ban is enforced."""
    await _strict()
    for _ in range(3):
        _note("100.100.1.2")
    await ip_ban.settle()
    assert await ip_ban.check("100.100.1.2") == 3600
    async with make_client(client=("100.100.1.2", 50000)) as client:
        assert (await client.get("/api/auth/me")).status_code == 429


# --- Escalation (MSHL-434) --------------------------------------------------------------


@pytest.mark.parametrize(
    ("strikes", "duration", "cap", "seconds"),
    [
        (1, 3600, 86400, 3600),
        (2, 3600, 86400, 7200),
        (5, 3600, 86400, 57600),
        (6, 3600, 86400, 86400),  # capped
        (32, 3600, 86400, 86400),
        (10**6, 60, 43200 * 60, 43200 * 60),  # never past the cap, whatever the strikes
        (2**62, 60, 43200 * 60, 43200 * 60),  # and never a huge integer on the way
        (0, 3600, 86400, 3600),  # at least the duration
        (3, 3600, 60, 3600),  # a cap below the duration: no escalation
        (3, 3600, 3600, 3600),
    ],
)
def test_ban_seconds(strikes, duration, cap, seconds):
    assert ip_ban.ban_seconds(strikes, duration, cap) == seconds


@pytest.mark.usefixtures("test_db")
class TestEscalation:
    @staticmethod
    async def _ban_once(clock: Clock) -> IpBan:
        for _ in range(3):
            _note(PUBLIC)
        await ip_ban.settle()
        ban = await IpBanRepository.get(PUBLIC)
        assert ban is not None
        clock.now = ban.expires_at + 1  # the ban has ended
        return ban

    async def test_a_repeat_offender_gets_twice_as_long_up_to_the_cap(self, clock):
        await _strict(ip_ban_duration_minutes=60, ip_ban_max_duration_minutes=300)
        durations, strikes = [], []
        for _ in range(5):
            ban = await self._ban_once(clock)
            durations.append((ban.expires_at - ban.banned_at) // 60)
            strikes.append(ban.strikes)
        assert durations == [60, 120, 240, 300, 300]
        assert strikes == [1, 2, 3, 4, 5]
        rows = [
            r for r in await AuthAuditRepository.list_recent(limit=20) if r.action == "ip.banned"
        ]
        assert sorted(r.details["minutes"] for r in rows) == [60, 120, 240, 300, 300]
        assert sorted(r.details["strikes"] for r in rows) == [1, 2, 3, 4, 5]

    async def test_the_strikes_are_bounded(self, clock, monkeypatch):
        monkeypatch.setattr(ip_ban, "MAX_STRIKES", 3)
        await _strict(ip_ban_duration_minutes=1, ip_ban_max_duration_minutes=43200)
        strikes = [(await self._ban_once(clock)).strikes for _ in range(5)]
        assert strikes == [1, 2, 3, 3, 3]

    async def test_the_memory_fades(self, clock):
        from app.repository.ip_ban import STRIKE_MEMORY_S

        await _strict()
        await self._ban_once(clock)
        await self._ban_once(clock)
        clock.now += STRIKE_MEMORY_S
        ban = await self._ban_once(clock)
        assert ban.strikes == 1 and ban.expires_at - ban.banned_at == 3600

    async def test_just_inside_the_memory_it_still_escalates(self, clock):
        from app.repository.ip_ban import STRIKE_MEMORY_S

        await _strict()
        await self._ban_once(clock)
        clock.now += STRIKE_MEMORY_S - 2
        assert (await self._ban_once(clock)).strikes == 2

    async def test_an_unban_forgets_the_strikes(self, clock):
        await _strict()
        await self._ban_once(clock)
        assert await IpBanRepository.unban(PUBLIC)
        assert (await self._ban_once(clock)).strikes == 1

    async def test_off_by_default_cap_below_the_duration(self, clock):
        await _strict(ip_ban_duration_minutes=60, ip_ban_max_duration_minutes=30)
        bans = [await self._ban_once(clock) for _ in range(3)]
        assert [(b.expires_at - b.banned_at) // 60 for b in bans] == [60, 60, 60]
        assert [b.strikes for b in bans] == [1, 2, 3]

    async def test_an_ended_ban_is_remembered_but_not_listed_nor_enforced(self, clock):
        await _strict()
        await self._ban_once(clock)
        assert await IpBanRepository.get(PUBLIC) is not None
        assert await IpBanRepository.active(clock.now) == []
        assert await ip_ban.check(PUBLIC) is None

    async def test_another_ban_keeps_the_memory(self, clock):
        await _strict()
        await self._ban_once(clock)
        for _ in range(3):
            _note(OTHER)  # writing another ban drops ended rows, not remembered ones
        await ip_ban.settle()
        assert (await self._ban_once(clock)).strikes == 2

    async def test_remembered_rows_are_dropped_after_the_memory(self, clock):
        from app.repository.ip_ban import STRIKE_MEMORY_S

        await _strict()
        await self._ban_once(clock)
        clock.now += STRIKE_MEMORY_S
        for _ in range(3):
            _note(OTHER)
        await ip_ban.settle()
        assert await IpBanRepository.get(PUBLIC) is None
        assert await IpBanRepository.get(OTHER) is not None


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
                {"ip_ban_max_duration_minutes": 0},
                {"ip_ban_max_duration_minutes": 43201},
                {"ip_ban_max_duration_minutes": "60"},
                {"ip_ban_enabled": "yes"},
                {"ip_ban_max_failures": "5"},
            ):
                response = await client.patch("/api/auth/settings", json=bad)
                assert response.status_code == 422, bad
            response = await client.patch(
                "/api/auth/settings",
                json={
                    "ip_ban_max_failures": 5,
                    "ip_ban_duration_minutes": 30,
                    "ip_ban_max_duration_minutes": 600,
                },
            )
            assert response.status_code == 200
            assert response.json()["ip_ban_max_duration_minutes"] == 600
        assert ip_ban._config.max_failures == 5 and ip_ban._config.duration_s == 1800
        assert ip_ban._config.max_duration_s == 36000
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
    "2a010e0a001256000000000000000001 05 38 00 00 eth1",  # global /56: too wide
    "2a010e0a001248000000000000000001 07 30 00 00 eth3",  # global /48: too wide
    "2a010e0a001278000000000000000005 06 80 00 00 eth2",  # global /128: one address
    "fe800000000000000211223344556677 02 40 20 80 eth0",  # link-local
    "00000000000000000000000000000001 01 80 10 80 lo",  # loopback
    "2a010e0a00ff00000000000000000001 03 20 00 00 wan0",  # /32: too wide for a LAN
    "20010db8000000000000000000000001 04 40 00 00 doc0",  # documentation: not global
)


def test_the_host_prefixes_are_its_global_on_link_ones(tmp_path):
    assert [str(n) for n in ip_ban.read_host_prefixes(_if_inet6(tmp_path, *IF_INET6))] == [
        HOST_LAN,
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
        for _ in range(10):
            _note("2a01:e0a:12:56ab::1")  # in the host's /56: a site, not a LAN
            _note("2a01:e0a:12:48cd::1")  # in the host's /48: a site, not a LAN
        await ip_ban.settle()
        assert await IpBanRepository.get("2a01:e0a:12:56ab::/64") is not None
        assert await IpBanRepository.get("2a01:e0a:12:48cd::/64") is not None
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


# --- On-link routes: a DHCPv6 host (MSHL-434) ---------------------------------------------

# A DHCPv6 host: one /128 on eth0, its LAN /64 learnt from the router advertisements.
DHCP_LAN = "2a01:e0a:12:3460::/64"
DHCP_CLIENT = "2a01:e0a:12:3460:aaaa:bbbb:cccc:dddd"
DHCP_IF_INET6 = (
    "2a010e0a00123460000000000000beef 02 80 00 00 eth0",  # global /128 (DHCPv6)
    "2a010e0a00ab00000000000000000001 03 80 00 00 wg0",  # a /128 on a VPN interface
    "fe800000000000000211223344556677 02 40 20 80 eth0",
)
_Z = "0" * 32
_ROUTE = "{dest} {plen} {zero} 00 {hop} 00000100 00000001 00000000 {flags} {name}"


def _route(dest: str, plen: str, flags: str, name: str, hop: str = _Z) -> str:
    return _ROUTE.format(dest=dest, plen=plen, zero=_Z, hop=hop, flags=flags, name=name)


IPV6_ROUTE = (
    _route("2a010e0a001234600000000000000000", "40", "00040001", "eth0"),  # RA on-link /64
    _route("2a010e0a001234600000000000000000", "40", "00040001", "eth1"),  # same /64, no address
    _route("2a010e0a001200000000000000000000", "30", "00000001", "eth0"),  # /48: too wide
    _route("2a010e0a00ab00000000000000000000", "40", "00000001", "wg1"),  # no address on wg1
    _route("2a010e0a00cd00000000000000000000", "40", "00000003", "eth0"),  # via a gateway
    _route(
        "2a010e0a00ce00000000000000000000",
        "40",
        "00000001",
        "eth0",
        hop="fe800000000000000000000000000001",
    ),  # a next hop
    _route("2a010e0a00cf00000000000000000000", "40", "00000201", "eth0"),  # reject
    _route("2a010e0a00123460000000000000beef", "80", "80200001", "lo"),  # the host's own: lo
    _route("2a010e0a001234600000000000000000", "40", "00040000", "eth0"),  # not up
    _route("20010db8000000000000000000000000", "40", "00000001", "eth0"),  # not global
    _route(_Z, "00", "00000003", "eth0", hop="fe800000000000000000000000000001"),  # default
)


def _files(tmp_path, if_inet6: tuple[str, ...], routes: tuple[str, ...] | str | None):
    inet6 = _if_inet6(tmp_path, *if_inet6)
    route_path = tmp_path / "ipv6_route"
    if routes is not None:
        route_path.write_text(
            routes if isinstance(routes, str) else "".join(f"{r}\n" for r in routes)
        )
    return inet6, str(route_path)


def test_the_on_link_routes_are_the_ones_holding_a_host_address(tmp_path):
    inet6, routes = _files(tmp_path, DHCP_IF_INET6, IPV6_ROUTE)
    addresses = ip_ban.read_host_addresses(inet6)
    assert [str(n) for n in ip_ban.read_onlink_routes(routes, addresses)] == [DHCP_LAN]


@pytest.mark.parametrize(
    "content",
    [
        None,  # missing
        "",
        "garbage\n",
        # malformed fields: too few, bad hex, bad length, bad flags, a short address
        "2a010e0a001234600000000000000000 40 eth0\n",
        _route("2a010e0a00123460000000000000000z", "40", "00040001", "eth0") + "\n",
        _route("2a010e0a001234600000000000000000", "4", "00040001", "eth0") + "\n",
        _route("2a010e0a001234600000000000000000", "zz", "00040001", "eth0") + "\n",
        _route("2a010e0a001234600000000000000000", "40", "0004000x", "eth0") + "\n",
        _route("2a010e0a00123460", "40", "00040001", "eth0") + "\n",
        _route("2a010e0a001234600000000000000000", "40", "00040001", "eth0") + " extra\n",
        "\xff\xfe binary\n",
    ],
)
def test_a_missing_or_malformed_route_file_adds_nothing(tmp_path, content):
    path = tmp_path / "ipv6_route"
    if content is not None:
        path.write_bytes(content.encode("latin-1"))
    addresses = ip_ban.read_host_addresses(_if_inet6(tmp_path, *DHCP_IF_INET6))
    assert ip_ban.read_onlink_routes(str(path), addresses) == ()


def test_routes_without_host_addresses_add_nothing(tmp_path):
    _, routes = _files(tmp_path, (), IPV6_ROUTE)
    assert ip_ban.read_onlink_routes(routes, ()) == ()


@pytest.mark.usefixtures("test_db")
class TestOnLinkRoutes:
    async def test_a_lan_client_of_a_dhcpv6_host_is_never_banned(self, monkeypatch, tmp_path):
        inet6, routes = _files(tmp_path, DHCP_IF_INET6, IPV6_ROUTE)
        monkeypatch.setattr(ip_ban, "IF_INET6_PATH", inet6)
        monkeypatch.setattr(ip_ban, "IPV6_ROUTE_PATH", routes)
        for _ in range(10):
            _note(DHCP_CLIENT)
            _note("2a01:e0a:ab::5")  # behind wg1, which holds no address of it
            _note("2a01:e0a:cd::5")  # behind a gateway
            _note("2a01:e0a:12:3461::5")  # the /48 around the LAN
        await ip_ban.settle()
        assert await IpBanRepository.get(DHCP_LAN) is None
        assert {b.address for b in await IpBanRepository.active(ip_ban.clock())} == {
            "2a01:e0a:ab::/64",
            "2a01:e0a:cd::/64",
            "2a01:e0a:12:3461::/64",
        }
        assert ip_ban.is_local(canonical_address(DHCP_CLIENT))  # the login-free gate too

    async def test_without_the_route_file_the_client_counts_as_public(
        self, monkeypatch, tmp_path
    ):
        inet6, routes = _files(tmp_path, DHCP_IF_INET6, None)
        monkeypatch.setattr(ip_ban, "IF_INET6_PATH", inet6)
        monkeypatch.setattr(ip_ban, "IPV6_ROUTE_PATH", routes)
        for _ in range(10):
            _note(DHCP_CLIENT)
        await ip_ban.settle()
        assert await IpBanRepository.get(DHCP_LAN) is not None

    async def test_without_the_address_file_routes_add_nothing(self, monkeypatch, tmp_path):
        _, routes = _files(tmp_path, (), IPV6_ROUTE)
        monkeypatch.setattr(ip_ban, "IPV6_ROUTE_PATH", routes)
        for _ in range(10):
            _note(DHCP_CLIENT)
        await ip_ban.settle()
        assert await IpBanRepository.get(DHCP_LAN) is not None


LAN_ROUTE = "2a010e0a001234600000000000000000"
DHCP_HOST = ip_ban.HostAddress(canonical_address("2a01:e0a:12:3460::beef"), 128, "eth0")


@pytest.mark.parametrize(
    ("line", "host", "found"),
    [
        (_route(LAN_ROUTE, "40", "00040001", "eth0"), DHCP_HOST, True),  # the reference
        (_route(LAN_ROUTE, "40", "00000001", "eth0"), DHCP_HOST, True),  # a static route
        (_route(LAN_ROUTE, "80", "00000001", "eth0"), DHCP_HOST, False),  # a /128 beside it
        (_route(LAN_ROUTE, "40", "00040003", "eth0"), DHCP_HOST, False),  # a gateway
        (_route(LAN_ROUTE, "40", "00040201", "eth0"), DHCP_HOST, False),  # reject
        (_route(LAN_ROUTE, "40", "01040001", "eth0"), DHCP_HOST, False),  # cache
        (_route(LAN_ROUTE, "40", "00140001", "eth0"), DHCP_HOST, False),  # anycast
        (_route(LAN_ROUTE, "40", "80040001", "eth0"), DHCP_HOST, False),  # local
        (_route(LAN_ROUTE, "40", "00040000", "eth0"), DHCP_HOST, False),  # not up
        (
            _route(LAN_ROUTE, "40", "00040001", "eth0", hop="fe800000000000000000000000000001"),
            DHCP_HOST,
            False,
        ),  # a next hop
        (_route(LAN_ROUTE, "40", "00040001", "lo"), DHCP_HOST._replace(interface="lo"), False),
        (_route(LAN_ROUTE, "40", "00040001", "eth1"), DHCP_HOST, False),  # address elsewhere
        (
            _route(LAN_ROUTE, "40", "00040001", "eth0"),
            DHCP_HOST._replace(address=canonical_address("2a01:e0a:12:3461::beef")),
            False,
        ),  # no host address inside the prefix
        (_route(LAN_ROUTE, "3c", "00040001", "eth0"), DHCP_HOST, False),  # a /60
        (_route(LAN_ROUTE, "30", "00040001", "eth0"), DHCP_HOST, False),  # a /48
        (_route(LAN_ROUTE, "81", "00040001", "eth0"), DHCP_HOST, False),  # past /128
        (
            _route("20010db8000000000000000000000000", "40", "00040001", "eth0"),
            DHCP_HOST._replace(address=canonical_address("2001:db8::beef")),
            False,
        ),  # not global
        (_route(LAN_ROUTE, "040", "00040001", "eth0"), DHCP_HOST, False),  # a 3-digit length
        (_route(LAN_ROUTE[1:], "40", "00040001", "eth0"), DHCP_HOST, False),  # 31 digits
        (_route("0" + LAN_ROUTE, "40", "00040001", "eth0"), DHCP_HOST, False),  # 33 digits
        (_route(LAN_ROUTE, "40", "0040001", "eth0"), DHCP_HOST, False),  # 7-digit flags
    ],
)
def test_each_on_link_route_rule(tmp_path, line, host, found):
    path = tmp_path / "ipv6_route"
    path.write_text(f"{line}\n")
    expected = (ipaddress.IPv6Network(DHCP_LAN),) if found else ()
    assert ip_ban.read_onlink_routes(str(path), (host,)) == expected
