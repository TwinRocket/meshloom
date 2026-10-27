"""Login delays (app/auth/ratelimit.py): fake clock for the policy, counting hash for the route."""

from __future__ import annotations

import pytest

from app.auth import ratelimit
from app.auth.models import AuthSettings
from app.auth.providers import password as provider
from app.auth.ratelimit import (
    DEVICE_MAX_FAILURES,
    FORGET_AFTER_S,
    IP_MAX_FAILURES,
    MAX_KEYS,
    PAIR_FREE_FAILURES,
    PAIR_MAX_DELAY_S,
    SWEEP_INTERVAL_S,
    USERNAME_FREE_FAILURES,
    USERNAME_INTERVAL_S,
    LoginDelayed,
    LoginLimiter,
    client_address_key,
)
from app.middleware import proxy_headers
from app.repository.auth import AuthAuditRepository
from tests.auth_helpers import ADMIN, PASSWORD, create_user, make_client


class FakeClock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


def make_limiter(clock: FakeClock, kind: str = "package", trusted: bool = False) -> LoginLimiter:
    return LoginLimiter(clock=clock, install_kind=lambda: kind, trusted_proxies_set=lambda: trusted)


async def fail(limiter: LoginLimiter, ip: str, username: str) -> None:
    limiter.check(ip, username)
    await limiter.record_failure(ip, username)


# --- Per (IP, username): progressive delay -------------------------------------


async def test_the_sixth_attempt_of_a_pair_is_delayed_then_the_right_password_passes():
    clock = FakeClock()
    limiter = make_limiter(clock)
    for _ in range(PAIR_FREE_FAILURES):
        await fail(limiter, "192.0.2.1", "alice")
    with pytest.raises(LoginDelayed) as delayed:
        limiter.check("192.0.2.1", "alice")
    assert delayed.value.reason == "pair"
    assert delayed.value.retry_after_s == 1
    # Another IP, or another name from this IP, is not delayed by this pair.
    limiter.check("192.0.2.2", "bob")

    clock.advance(1)
    limiter.check("192.0.2.1", "alice")
    limiter.record_success("192.0.2.1", "alice")
    clock.advance(USERNAME_INTERVAL_S)  # the name itself is slowed down after five failures
    limiter.check("192.0.2.1", "alice")


async def test_the_pair_delay_doubles_up_to_the_cap():
    clock = FakeClock()
    limiter = make_limiter(clock)
    delays = []
    for _ in range(PAIR_FREE_FAILURES + 9):
        while True:
            try:
                limiter.check("192.0.2.1", "alice")
                break
            except LoginDelayed as delayed:
                if delayed.reason == "pair":
                    delays.append(delayed.retry_after)
                clock.advance(delayed.retry_after)
        await limiter.record_failure("192.0.2.1", "alice")
    assert delays[:7] == [1, 2, 4, 8, 16, 32, PAIR_MAX_DELAY_S]
    assert max(delays) == PAIR_MAX_DELAY_S


async def test_usernames_are_compared_by_their_key():
    clock = FakeClock()
    limiter = make_limiter(clock)
    for name in ["Alice", "ALICE", "alice", "aLiCe", "Alice"]:
        await fail(limiter, "192.0.2.1", name)
    with pytest.raises(LoginDelayed):
        limiter.check("192.0.2.1", "alice")


# --- Per username: global slow-down, never a lockout ----------------------------


async def test_a_thousand_failures_on_admin_from_many_ips_never_lock_the_right_password_out():
    clock = FakeClock()
    limiter = make_limiter(clock)
    longest = 0.0
    for attempt in range(1000):
        ip = f"10.{attempt // 250}.{attempt % 250}.1"
        while True:
            try:
                limiter.check(ip, "admin")
                break
            except LoginDelayed as delayed:
                assert delayed.reason == "username"
                longest = max(longest, delayed.retry_after)
                clock.advance(delayed.retry_after)
        await limiter.record_failure(ip, "admin")
    # At most one attempt a second on the name: never more than that to wait.
    assert 0 < longest <= USERNAME_INTERVAL_S
    # The owner, from an address of their own, gets in after at most that wait.
    with pytest.raises(LoginDelayed) as delayed:
        limiter.check("198.51.100.9", "admin")
        limiter.check("198.51.100.9", "admin")
    assert delayed.value.retry_after <= USERNAME_INTERVAL_S
    clock.advance(delayed.value.retry_after)
    limiter.check("198.51.100.9", "admin")


async def test_the_username_slowdown_starts_after_five_failures():
    clock = FakeClock()
    limiter = make_limiter(clock)
    for n in range(5):
        await fail(limiter, f"10.0.0.{n}", "admin")
    limiter.check("10.0.1.1", "admin")
    with pytest.raises(LoginDelayed) as delayed:
        limiter.check("10.0.1.2", "admin")
    assert delayed.value.reason == "username"


# --- Per IP: hard refusal --------------------------------------------------------


async def _hundred_failures(limiter: LoginLimiter, ip: str = "203.0.113.5") -> None:
    for n in range(IP_MAX_FAILURES):
        await fail(limiter, ip, f"user{n}")


async def test_the_101st_failure_from_one_ip_in_15_minutes_is_refused():
    clock = FakeClock()
    limiter = make_limiter(clock)
    await _hundred_failures(limiter)
    with pytest.raises(LoginDelayed) as delayed:
        limiter.check("203.0.113.5", "someone-new")
    assert delayed.value.reason == "ip"
    assert delayed.value.retry_after_s == 9  # one failure leaks every 900/100 s
    limiter.check("203.0.113.6", "someone-new")  # other addresses are not affected
    clock.advance(9)
    limiter.check("203.0.113.5", "someone-new")


def scope(client: str, *, peer: str | None = None, forwarded: bool = False) -> dict:
    """An ASGI scope as the trusted-proxy middleware leaves it."""
    headers = [(b"x-forwarded-for", client.encode())] if forwarded else []
    return {
        "type": "http",
        "client": (client, 50000),
        "headers": headers,
        "state": {"peer": peer or client},
    }


@pytest.mark.parametrize("kind", ["compose", "container"])
async def test_docker_without_a_trusted_proxy_applies_the_username_rule_only(kind):
    clock = FakeClock()
    limiter = make_limiter(clock, kind=kind)
    address = limiter.address_key(scope("172.17.0.1"))
    assert address is None
    for n in range(IP_MAX_FAILURES):
        await fail(limiter, address, f"user{n}")
    limiter.check(address, "someone-new")
    assert limiter.sizes()[0] == 0 and limiter.sizes()[2] == 0  # no pair, no bucket kept


@pytest.mark.parametrize("kind", ["compose", "container"])
async def test_docker_with_a_trusted_proxy_keys_the_forwarded_client(kind):
    clock = FakeClock()
    limiter = make_limiter(clock, kind=kind, trusted=True)
    proxy_headers.apply(AuthSettings(trusted_proxies=["172.17.0.2"]))
    try:
        address = limiter.address_key(scope("203.0.113.5", peer="172.17.0.2", forwarded=True))
    finally:
        proxy_headers.reset()
    assert address == "203.0.113.5"
    await _hundred_failures(limiter, address)
    with pytest.raises(LoginDelayed):
        limiter.check(address, "someone-new")


@pytest.mark.parametrize("kind", ["compose", "container"])
async def test_docker_with_addresses_identifying_clients_applies_the_ip_rule(kind):
    """MSHL-424: the owner states that the peer address is the client's own."""
    clock = FakeClock()
    limiter = LoginLimiter(
        clock=clock,
        install_kind=lambda: kind,
        trusted_proxies_set=lambda: False,
        addresses_identify_clients=lambda: True,
    )
    address = limiter.address_key(scope("203.0.113.9"))
    assert address == "203.0.113.9"
    await _hundred_failures(limiter, address)
    with pytest.raises(LoginDelayed):
        limiter.check(address, "someone-new")


def test_the_addresses_identify_clients_setting_is_read_live():
    proxy_headers.reset()
    limiter = LoginLimiter(install_kind=lambda: "compose")
    assert not limiter.address_rules_active()
    proxy_headers.apply(AuthSettings(addresses_identify_clients=True))
    try:
        assert limiter.address_rules_active()
    finally:
        proxy_headers.reset()
    assert not limiter.address_rules_active()


@pytest.mark.parametrize(
    ("address", "public"),
    [
        ("8.8.8.8", True),
        ("2001:4860:4860::8888", True),
        ("192.168.1.10", False),
        ("10.0.0.1", False),
        ("172.17.0.1", False),
        ("100.64.0.1", False),
        ("127.0.0.1", False),
        ("fd00::1", False),
        ("fe80::1", False),
        ("::ffff:192.168.1.10", False),
        ("fe80::1%eth0", False),
        # Not routable, yet never handed out by a LAN: public (app/net/local_address.py).
        ("192.0.2.10", True),
        ("198.18.0.1", True),
        ("2001:db8::1", True),
        ("0.0.0.0", True),
        ("::", True),
        ("240.0.0.1", True),
        ("", True),
        ("127.0.0.1%x", True),
        (" 10.0.0.1", True),
        ("::ffff:127.0.0.1%x", True),
        ("not-an-address", True),
    ],
)
def test_client_is_public(address, public):
    assert ratelimit.client_is_public(scope(address)) is public


def test_a_scope_without_a_client_counts_as_public():
    assert ratelimit.client_is_public({"type": "http", "headers": []}) is True


async def test_a_slow_guesser_on_the_docker_gateway_never_locks_the_owner_out():
    """Review r1 scenario: compose, no trusted proxy, one wrong guess a minute from
    the gateway for an hour. The owner, on the same address and without a known
    device, never meets more than the one-second username slot (no pair delay)."""
    clock = FakeClock()
    limiter = make_limiter(clock, kind="compose")
    gateway = scope("172.17.0.1")
    longest = 0.0
    for _ in range(60):
        await fail(limiter, limiter.address_key(gateway), "admin")
        try:
            limiter.check(limiter.address_key(gateway), "admin")
        except LoginDelayed as delayed:
            assert delayed.reason == "username"
            longest = max(longest, delayed.retry_after)
            clock.advance(delayed.retry_after)
            limiter.check(limiter.address_key(gateway), "admin")
        clock.advance(60)
    assert longest <= USERNAME_INTERVAL_S


async def _checked_guesses(limiter: LoginLimiter, clock: FakeClock, request_scope: dict) -> int:
    """One wrong guess every 0.5 s for 10 minutes: how many reach the password check."""
    checked = 0
    for _ in range(1200):
        address = limiter.address_key(request_scope)
        try:
            limiter.check(address, "admin")
        except LoginDelayed:
            pass
        else:
            checked += 1
            await limiter.record_failure(address, "admin")
        clock.advance(0.5)
    return checked


async def test_forwarding_headers_from_an_untrusted_peer_do_not_reduce_protection(caplog):
    """Review r2: a LAN client adding X-Forwarded-For used to switch the address
    rules off for itself. Headers never decide the shared-address mode."""
    proxy_headers.reset()
    plain_clock, forged_clock = FakeClock(), FakeClock()
    plain = await _checked_guesses(make_limiter(plain_clock), plain_clock, scope("192.168.1.66"))
    forged_limiter = make_limiter(forged_clock)
    with caplog.at_level("WARNING", logger="app.auth.ratelimit"):
        forged = await _checked_guesses(
            forged_limiter, forged_clock, scope("192.168.1.66", forwarded=True)
        )
    assert forged == plain
    assert forged_limiter.address_key(scope("192.168.1.66", forwarded=True)) == "192.168.1.66"
    hints = [r for r in caplog.records if "not a trusted proxy" in r.getMessage()]
    assert len(hints) == 1  # once per peer


# --- Known devices -------------------------------------------------------------------


async def test_a_known_device_skips_the_username_slot_under_a_flood():
    clock = FakeClock()
    limiter = make_limiter(clock, kind="compose")
    for _ in range(USERNAME_FREE_FAILURES):
        await fail(limiter, None, "admin")
    limiter.check(None, "admin")  # the attacker takes the slot
    with pytest.raises(LoginDelayed):
        limiter.check(None, "admin")
    limiter.check(None, "admin", "owner-device")
    limiter.record_success(None, "admin", "owner-device")


async def test_a_device_that_keeps_failing_loses_its_bypass():
    clock = FakeClock()
    limiter = make_limiter(clock)
    for _ in range(DEVICE_MAX_FAILURES):
        limiter.check("192.0.2.1", "admin", "stolen")
        await limiter.record_failure("192.0.2.1", "admin", "stolen")
    assert not limiter.known_device_trusted("stolen")
    with pytest.raises(LoginDelayed):
        limiter.check("192.0.2.1", "admin", "stolen")
    clock.advance(FORGET_AFTER_S + SWEEP_INTERVAL_S)
    limiter.check("192.0.2.9", "nobody")  # sweep
    assert limiter.known_device_trusted("stolen")


def test_a_declared_proxy_keys_the_forwarded_client():
    limiter = make_limiter(FakeClock())
    proxy_headers.apply(AuthSettings(trusted_proxies=["127.0.0.1"]))
    try:
        key = limiter.address_key(scope("198.51.100.4", peer="127.0.0.1", forwarded=True))
    finally:
        proxy_headers.reset()
    assert key == "198.51.100.4"


def test_the_trusted_proxy_list_is_read_live():
    proxy_headers.reset()
    limiter = LoginLimiter(install_kind=lambda: "compose")
    assert not limiter.address_rules_active()
    proxy_headers.apply(AuthSettings(trusted_proxies=["10.0.0.2"]))
    try:
        assert limiter.address_rules_active()
    finally:
        proxy_headers.reset()


# --- IPv6: one /64 is one client ---------------------------------------------------


def test_ipv6_is_keyed_by_its_64_and_ipv4_by_its_address():
    assert client_address_key("2001:db8:1:2::1") == "2001:db8:1:2::/64"
    assert client_address_key("2001:db8:1:2:ffff:1:2:3") == "2001:db8:1:2::/64"
    assert client_address_key("::ffff:192.0.2.7") == "192.0.2.7"
    assert client_address_key("192.0.2.7") == "192.0.2.7"


async def test_rotating_inside_a_64_does_not_escape_the_pair_delay():
    clock = FakeClock()
    limiter = make_limiter(clock)
    for n in range(PAIR_FREE_FAILURES):
        await fail(limiter, limiter.address_key(scope(f"2001:db8:1:2::{n + 1:x}")), "alice")
    with pytest.raises(LoginDelayed) as delayed:
        limiter.check(limiter.address_key(scope("2001:db8:1:2:abcd::99")), "alice")
    assert delayed.value.reason == "pair"
    # Another /64 is another client (only the per-username slot applies to it).
    clock.advance(USERNAME_INTERVAL_S)
    limiter.check(limiter.address_key(scope("2001:db8:1:3::1")), "alice")


async def test_rotating_inside_a_64_does_not_escape_the_per_ip_refusal():
    clock = FakeClock()
    limiter = make_limiter(clock)
    for n in range(IP_MAX_FAILURES):
        await fail(limiter, limiter.address_key(scope(f"2001:db8:9:9::{n + 1:x}")), f"user{n}")
    with pytest.raises(LoginDelayed) as delayed:
        limiter.check(limiter.address_key(scope("2001:db8:9:9:1:2:3:4")), "someone-new")
    assert delayed.value.reason == "ip"


# --- Bounded memory ----------------------------------------------------------------


async def test_keys_are_evicted_when_their_window_empties():
    clock = FakeClock()
    limiter = make_limiter(clock)
    for n in range(20):
        await limiter.record_failure(f"10.0.0.{n}", "admin")
        await limiter.record_failure(f"10.0.0.{n}", f"user{n}")
    pairs, names, ips = limiter.sizes()
    assert (pairs, names, ips) == (40, 21, 20)
    clock.advance(FORGET_AFTER_S + SWEEP_INTERVAL_S)
    limiter.check("192.0.2.1", "nobody")  # a check sweeps at most once a minute
    assert limiter.sizes() == (0, 0, 0)


async def test_tables_are_capped():
    clock = FakeClock()
    limiter = make_limiter(clock)
    for n in range(MAX_KEYS + 10):
        await limiter.record_failure(f"10.{n // 65536}.{n // 256 % 256}.{n % 256}", f"u{n}")
    assert limiter.sizes() == (MAX_KEYS, MAX_KEYS, MAX_KEYS)


# --- The route: 429 before any password check, audit, correct password after ---


@pytest.fixture
def clock(monkeypatch) -> FakeClock:
    fake = FakeClock()
    monkeypatch.setattr(ratelimit, "login_limiter", make_limiter(fake))
    return fake


@pytest.fixture
def hash_calls(monkeypatch) -> list[int]:
    calls: list[int] = []
    real_verify = provider._verify_or_dummy

    def counting(*args):
        calls.append(1)
        return real_verify(*args)

    monkeypatch.setattr(provider, "_verify_or_dummy", counting)
    return calls


def _login(username: str, password: str) -> dict:
    return {"username": username, "password": password}


@pytest.mark.real_auth
@pytest.mark.usefixtures("test_db", "fast_argon2")
async def test_route_answers_429_without_a_password_check_then_accepts_the_right_password(
    clock, hash_calls
):
    await create_user()
    async with make_client() as client:
        for _ in range(PAIR_FREE_FAILURES):
            wrong = await client.post("/api/auth/login", json=_login(ADMIN, "wrong password"))
            assert wrong.status_code == 401
        assert len(hash_calls) == PAIR_FREE_FAILURES

        for _ in range(3):
            delayed = await client.post("/api/auth/login", json=_login(ADMIN, PASSWORD))
            assert delayed.status_code == 429
            assert delayed.json()["detail"]["code"] == "auth_rate_limited"
            assert delayed.json()["detail"]["params"] == {"retry_after": 1}
            assert delayed.headers["retry-after"] == "1"
        assert len(hash_calls) == PAIR_FREE_FAILURES

        clock.advance(1)
        ok = await client.post("/api/auth/login", json=_login(ADMIN, PASSWORD))
        assert ok.status_code == 200, ok.text

    rows = await AuthAuditRepository.list_recent(limit=20)
    delayed_rows = [row for row in rows if row.outcome == "delayed"]
    # One row for the delay, not one per refused attempt.
    assert len(delayed_rows) == 1
    assert delayed_rows[0].action == "login"
    assert delayed_rows[0].details == {"reason": "pair", "retry_after": 1}
    assert delayed_rows[0].ip == "127.0.0.1"
    assert rows[0].outcome == "success"


@pytest.mark.real_auth
@pytest.mark.usefixtures("test_db", "fast_argon2")
async def test_route_refuses_the_101st_failure_from_one_ip_and_audits_it(clock, hash_calls):
    limiter = ratelimit.login_limiter
    for n in range(IP_MAX_FAILURES):
        await limiter.record_failure("127.0.0.1", f"user{n}")
    async with make_client() as client:
        for _ in range(2):
            refused = await client.post("/api/auth/login", json=_login("someone", "x"))
            assert refused.status_code == 429
            assert refused.json()["detail"]["params"]["retry_after"] == 9
    assert hash_calls == []
    rows = await AuthAuditRepository.list_recent(limit=5)
    assert [(row.action, row.outcome) for row in rows] == [("login", "refused")]
    assert rows[0].details == {"reason": "ip", "retry_after": 9}


@pytest.mark.real_auth
@pytest.mark.usefixtures("test_db", "fast_argon2")
async def test_route_uses_the_client_a_trusted_proxy_forwarded(clock):
    proxy_headers.apply(AuthSettings(trusted_proxies=["127.0.0.1"]))
    try:
        await create_user()
        async with make_client() as client:
            for _ in range(PAIR_FREE_FAILURES):
                wrong = await client.post(
                    "/api/auth/login",
                    json=_login(ADMIN, "wrong password"),
                    headers={"X-Forwarded-For": "203.0.113.7"},
                )
                assert wrong.status_code == 401
            delayed = await client.post(
                "/api/auth/login",
                json=_login(ADMIN, PASSWORD),
                headers={"X-Forwarded-For": "203.0.113.7"},
            )
            assert delayed.status_code == 429
            # Another client behind the same proxy has its own pair (the
            # username slow-down only spaces it by a second).
            clock.advance(USERNAME_INTERVAL_S)
            other = await client.post(
                "/api/auth/login",
                json=_login(ADMIN, PASSWORD),
                headers={"X-Forwarded-For": "203.0.113.8"},
            )
            assert other.status_code == 200, other.text
    finally:
        proxy_headers.reset()


# --- Known-device cookie through the route --------------------------------------------


@pytest.mark.real_auth
@pytest.mark.usefixtures("test_db", "fast_argon2")
async def test_route_known_device_gets_in_every_time_under_a_flood_from_the_shared_address(
    monkeypatch,
):
    """Review r2 PoC: compose, no trusted proxy, an attacker sends 20 attempts a
    second on admin from the gateway; the owner's known browser tries every 5 s."""
    clock = FakeClock()
    monkeypatch.setattr(ratelimit, "login_limiter", make_limiter(clock, kind="compose"))
    await create_user()
    gateway = ("172.17.0.1", 50000)
    async with make_client(client=gateway) as owner, make_client(client=gateway) as attacker:
        first = await owner.post("/api/auth/login", json=_login(ADMIN, PASSWORD))
        assert first.status_code == 200
        assert any(c.startswith("meshloom_device=") for c in first.headers.get_list("set-cookie"))
        owner_ok = attacker_checked = 0
        for tick in range(600):  # 30 s at 20 attempts a second
            guess = await attacker.post("/api/auth/login", json=_login(ADMIN, f"guess {tick}"))
            assert guess.status_code in (401, 429)
            attacker_checked += guess.status_code == 401
            if tick % 100 == 0:
                mine = await owner.post("/api/auth/login", json=_login(ADMIN, PASSWORD))
                assert mine.status_code == 200, mine.text
                owner_ok += 1
            clock.advance(0.05)
    assert owner_ok == 6
    # The attacker gets about one checked guess a second, no more.
    assert attacker_checked <= USERNAME_FREE_FAILURES + 31


@pytest.mark.real_auth
@pytest.mark.usefixtures("test_db", "fast_argon2")
async def test_device_cookie_refused_when_forged_expired_for_another_user_or_stale(monkeypatch):
    from app.auth import device_cookie
    from app.auth.passwords import hash_password_async
    from app.repository.auth import UserRepository

    owner = await create_user()
    other = await create_user("someone", PASSWORD, "member")
    value = await device_cookie.issue(owner.id, owner.updated_at, "dev1")

    assert await device_cookie.known_device(await device_cookie.parse(value), ADMIN) == "dev1"
    # Forged: any change to the signed part, or a cookie signed with another key.
    tampered = value.replace(".dev1.", ".dev2.")
    assert await device_cookie.parse(tampered) is None
    assert await device_cookie.parse(value[:-1] + ("0" if value[-1] != "0" else "1")) is None
    # Another user's name.
    assert await device_cookie.known_device(await device_cookie.parse(value), "someone") is None
    assert other.id != owner.id
    # Expired.
    real_time = device_cookie.time.time
    monkeypatch.setattr(
        device_cookie.time, "time", lambda: real_time() + device_cookie.DEVICE_COOKIE_TTL_S + 1
    )
    assert await device_cookie.parse(value) is None
    monkeypatch.setattr(device_cookie.time, "time", real_time)
    # After a password change (the generation moves).
    device_cookie_clock = owner.updated_at + 5
    monkeypatch.setattr("app.repository.auth.time.time", lambda: device_cookie_clock)
    await UserRepository.set_password_hash(owner.id, await hash_password_async("another long pass"))
    assert await device_cookie.known_device(await device_cookie.parse(value), ADMIN) is None
    # A disabled account.
    fresh = await device_cookie.issue(
        owner.id, (await UserRepository.get(owner.id)).updated_at, "dev1"
    )
    assert await device_cookie.known_device(await device_cookie.parse(fresh), ADMIN) == "dev1"
    device_cookie_clock += 5
    await UserRepository.update(owner.id, disabled=True)
    assert await device_cookie.known_device(await device_cookie.parse(fresh), ADMIN) is None
    # A role change.
    device_cookie_clock += 5
    await UserRepository.update(owner.id, disabled=False)
    before_role = await device_cookie.issue(
        owner.id, (await UserRepository.get(owner.id)).updated_at, "dev1"
    )
    assert await device_cookie.known_device(await device_cookie.parse(before_role), ADMIN) == "dev1"
    device_cookie_clock += 5
    await UserRepository.update(owner.id, role="member")
    assert await device_cookie.known_device(await device_cookie.parse(before_role), ADMIN) is None
    # A key rotation (meshloom-admin rotate-device-key).
    from app.repository.auth import AuthSettingsRepository

    await AuthSettingsRepository.rotate_device_cookie_key()
    assert await device_cookie.parse(fresh) is None


@pytest.mark.real_auth
@pytest.mark.usefixtures("fast_argon2")
async def test_device_cookie_key_is_persisted_and_survives_a_restart(test_db):
    """The key lives in ``auth_settings`` (migration 119): a new process (the
    module state is gone, the database stays) still honours the cookie."""
    import importlib

    from app.auth import device_cookie
    from app.repository.auth import AuthSettingsRepository

    async with test_db.readonly() as conn:
        async with conn.execute("SELECT device_cookie_key FROM auth_settings") as cursor:
            assert (await cursor.fetchone())[0] is None
    owner = await create_user()
    value = await device_cookie.issue(owner.id, owner.updated_at, "dev1")
    async with test_db.readonly() as conn:
        async with conn.execute("SELECT device_cookie_key FROM auth_settings") as cursor:
            stored = (await cursor.fetchone())[0]
    assert len(stored) == 64 and bytes.fromhex(stored) == (
        await AuthSettingsRepository.device_cookie_key()
    )
    reloaded = importlib.reload(device_cookie)
    assert await reloaded.known_device(await reloaded.parse(value), ADMIN) == "dev1"
    # A key that is not 64 hex characters (hand-edited) is replaced, never used.
    async with test_db.tx() as conn:
        await conn.execute("UPDATE auth_settings SET device_cookie_key = 'not-hex'")
    assert await reloaded.parse(value) is None
    assert len((await AuthSettingsRepository.device_cookie_key()).hex()) == 64


@pytest.mark.real_auth
@pytest.mark.usefixtures("fast_argon2")
async def test_device_cookie_is_unknown_without_the_auth_settings_row(test_db):
    from app.auth import device_cookie

    owner = await create_user()
    value = await device_cookie.issue(owner.id, owner.updated_at, "dev1")
    async with test_db.tx() as conn:
        await conn.execute("DELETE FROM auth_settings")
    assert await device_cookie.parse(value) is None
    assert await device_cookie.parse(value.replace(value[-4:], "é" * 4)) is None


@pytest.mark.real_auth
@pytest.mark.usefixtures("test_db", "fast_argon2")
async def test_route_device_cookie_attributes(clock):
    await create_user()
    async with make_client(base_url="https://test") as client:
        ok = await client.post("/api/auth/login", json=_login(ADMIN, PASSWORD))
    [cookie] = [c for c in ok.headers.get_list("set-cookie") if "meshloom_device" in c]
    attributes = [part.strip() for part in cookie.split(";")]
    # __Host-: Secure, Path=/ and no Domain, so a sibling host on the same
    # registrable domain (no Public Suffix List) cannot toss one that hides it.
    assert attributes[0].startswith("__Host-meshloom_device=")
    assert {"Path=/", "HttpOnly", "SameSite=Strict", "Secure"} <= set(attributes)
    assert not any(a.lower().startswith("domain") for a in attributes)


@pytest.mark.real_auth
@pytest.mark.usefixtures("test_db", "fast_argon2")
async def test_route_device_cookie_attributes_over_plain_http(clock):
    await create_user()
    async with make_client() as client:
        ok = await client.post("/api/auth/login", json=_login(ADMIN, PASSWORD))
    [cookie] = [c for c in ok.headers.get_list("set-cookie") if "meshloom_device" in c]
    attributes = [part.strip() for part in cookie.split(";")]
    assert attributes[0].startswith("meshloom_device=")
    assert {"Path=/", "HttpOnly", "SameSite=Strict"} <= set(attributes)
    assert "Secure" not in attributes
    assert not any(a.lower().startswith("domain") for a in attributes)


@pytest.mark.real_auth
@pytest.mark.usefixtures("test_db", "fast_argon2")
async def test_device_cookie_is_read_only_under_the_name_for_the_scheme():
    from app.auth import device_cookie

    owner = await create_user()
    value = await device_cookie.issue(owner.id, owner.updated_at, "dev1")
    assert (await device_cookie.read(f"__Host-meshloom_device={value}", secure=True)) is not None
    # Over https a plain or a __Secure- cookie (which may carry a Domain) is ignored.
    assert await device_cookie.read(f"meshloom_device={value}", secure=True) is None
    assert await device_cookie.read(f"__Secure-meshloom_device={value}", secure=True) is None
    assert await device_cookie.read(f"__Host-meshloom_device={value}", secure=False) is None
    assert (await device_cookie.read(f"meshloom_device={value}", secure=False)) is not None


@pytest.mark.real_auth
@pytest.mark.usefixtures("test_db", "fast_argon2")
async def test_route_known_device_with_a_wrong_password_is_refused_and_counted(clock, hash_calls):
    """A known-device cookie skips the delays, never the password check: a wrong
    password is a 401 and spends the device's budget."""
    from app.auth import device_cookie

    await create_user()
    async with make_client() as owner:
        first = await owner.post("/api/auth/login", json=_login(ADMIN, PASSWORD))
        assert first.status_code == 200
        cookie = await device_cookie.parse(owner.cookies.get(device_cookie.PLAIN_COOKIE_NAME))
        assert cookie is not None
        checks_before = len(hash_calls)

        wrong = await owner.post("/api/auth/login", json=_login(ADMIN, "wrong password"))
        assert wrong.status_code == 401
        assert wrong.json()["detail"]["code"] == "auth_invalid_credentials"
        assert len(hash_calls) == checks_before + 1
        assert ratelimit.login_limiter._devices[cookie.device_id].failures == 1

        for _ in range(DEVICE_MAX_FAILURES - 1):
            assert (
                await owner.post("/api/auth/login", json=_login(ADMIN, "wrong password"))
            ).status_code == 401
        assert not ratelimit.login_limiter.known_device_trusted(cookie.device_id)


@pytest.mark.real_auth
@pytest.mark.usefixtures("test_db", "fast_argon2")
async def test_route_applies_the_ip_rule_under_docker_once_addresses_identify_clients(
    monkeypatch, hash_calls
):
    clock = FakeClock()
    limiter = LoginLimiter(
        clock=clock, install_kind=lambda: "compose", trusted_proxies_set=lambda: False
    )
    monkeypatch.setattr(ratelimit, "login_limiter", limiter)
    for n in range(IP_MAX_FAILURES):
        await limiter.record_failure("127.0.0.1", f"user{n}")
    async with make_client() as client:
        # Off (the default): the shared Docker address carries no per-IP rule.
        first = await client.post("/api/auth/login", json=_login("someone", "x"))
        assert first.status_code == 401
        proxy_headers.apply(AuthSettings(addresses_identify_clients=True))
        try:
            for n in range(IP_MAX_FAILURES):
                await limiter.record_failure("127.0.0.1", f"user{n}")
            refused = await client.post("/api/auth/login", json=_login("someone", "x"))
        finally:
            proxy_headers.reset()
    assert refused.status_code == 429
    assert refused.json()["detail"]["params"]["retry_after"] > 0
