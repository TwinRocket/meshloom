"""Login delays (app/auth/ratelimit.py): fake clock for the policy, counting hash for the route."""

from __future__ import annotations

import pytest

from app import security
from app.auth import ratelimit
from app.auth.models import AuthSettings
from app.auth.providers import password as provider
from app.auth.ratelimit import (
    FORGET_AFTER_S,
    IP_MAX_FAILURES,
    MAX_KEYS,
    PAIR_FREE_FAILURES,
    PAIR_MAX_DELAY_S,
    SWEEP_INTERVAL_S,
    USERNAME_INTERVAL_S,
    LoginDelayed,
    LoginLimiter,
)
from app.middleware import proxy_headers
from app.repository.auth import AuthAuditRepository
from tests.auth_helpers import ADMIN, PASSWORD, basic, create_user, make_client


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


@pytest.mark.parametrize("kind", ["compose", "container"])
async def test_no_hard_refusal_behind_docker_without_a_trusted_proxy(kind):
    clock = FakeClock()
    limiter = make_limiter(clock, kind=kind)
    await _hundred_failures(limiter)
    limiter.check("203.0.113.5", "someone-new")
    assert limiter.sizes()[2] == 0  # no bucket kept while the rule is off


@pytest.mark.parametrize("kind", ["compose", "container"])
async def test_hard_refusal_behind_docker_with_a_trusted_proxy(kind):
    clock = FakeClock()
    limiter = make_limiter(clock, kind=kind, trusted=True)
    await _hundred_failures(limiter)
    with pytest.raises(LoginDelayed):
        limiter.check("203.0.113.5", "someone-new")


def test_the_trusted_proxy_list_is_read_live():
    proxy_headers.reset()
    limiter = LoginLimiter(install_kind=lambda: "compose")
    assert not limiter.hard_ip_limit_active()
    proxy_headers.apply(AuthSettings(trusted_proxies=["10.0.0.2"]))
    try:
        assert limiter.hard_ip_limit_active()
    finally:
        proxy_headers.reset()


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


# --- MSHL-288: the Basic cooldown is per resolved client, not per proxy ----------


@pytest.mark.real_auth
@pytest.mark.usefixtures("test_db", "fast_argon2")
async def test_basic_cooldown_uses_the_forwarded_client_behind_a_trusted_proxy():
    proxy_headers.apply(AuthSettings(trusted_proxies=["127.0.0.1"]))
    try:
        await create_user()
        async with make_client() as client:
            bad = await client.get(
                "/api/auth/me",
                headers={**basic(ADMIN, "wrong password"), "X-Forwarded-For": "203.0.113.7"},
            )
            assert bad.status_code == 401
            good = await client.get(
                "/api/auth/me",
                headers={**basic(ADMIN, PASSWORD), "X-Forwarded-For": "203.0.113.8"},
            )
            assert good.status_code == 200, good.text
        assert list(security._failed_ips) == ["203.0.113.7"]
    finally:
        proxy_headers.reset()
