"""The ``AUTH_FAIL`` log line (MSHL-421): ``app/auth/fail_log.py``, ``docs/dev/auth-fail-log.md``.

One line per refused credential on every route, in the exact format the
contract pins (a fail2ban-style regex matches it, and never a ``proxied=1``
line), with no clear-text username.
"""

from __future__ import annotations

import hashlib
import hmac
import logging
import re
from pathlib import Path

import pytest

from app.auth import fail_log, setup_code
from app.auth.models import AuthSettings, TrustedNetworkRule
from app.auth.providers import ingress
from app.log_redaction import RedactingFormatter
from app.middleware import proxy_headers
from app.repository.auth import AuthSettingsRepository
from app.routers import auth as auth_router
from tests.auth_helpers import ADMIN, PASSWORD, basic, create_user, login, make_client
from tests.test_auth_totp import _enrol, _password_step
from tests.webauthn_authenticator import SoftAuthenticator

pytestmark = [pytest.mark.real_auth, pytest.mark.usefixtures("test_db", "fast_argon2")]

CONTRACT = Path(__file__).resolve().parents[1] / "docs" / "dev" / "auth-fail-log.md"
PUBLIC = "https://meshloom.example"
# app/config.py: the format every handler uses.
LOG_FORMAT = "%(asctime)s - %(name)s - %(levelname)s - %(message)s"
LOG_DATE_FORMAT = "%Y-%m-%d %H:%M:%S"

# The message, as the contract states it.
MESSAGE = re.compile(
    r"^AUTH_FAIL ip=(?P<ip>\S+) route=(?P<route>[a-z0-9_]+) reason=(?P<reason>[a-z0-9_]+) "
    r"via=(?P<via>lan|relay) proxied=(?P<proxied>[01]) user=(?P<user>-|h:[0-9a-f]{6})$"
)
# The full line a consumer sees (fail2ban failregex equivalent, §5.3): only proxied=0 lines.
EXPECTED_FAILREGEX = (
    r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2} - meshloom\.auth - WARNING - "
    r"AUTH_FAIL ip=(?P<host>\S+) route=\S+ reason=\S+ via=\S+ proxied=0 user=\S+$"
)


def _documented_failregex() -> str:
    """The regex the contract publishes: the one code line that starts with ``^\\d{4}``."""
    [line] = [line for line in CONTRACT.read_text().splitlines() if line.startswith(r"^\d{4}-")]
    return line


# The tests below use the published regex, so a change to the doc is tested too.
FAILREGEX = re.compile(_documented_failregex())


class _Records(logging.Handler):
    def __init__(self) -> None:
        super().__init__(logging.WARNING)
        self.records: list[logging.LogRecord] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.records.append(record)


@pytest.fixture
def lines():
    # On the logger itself: the app reconfigures the root handlers when it starts.
    auth_logger = logging.getLogger("meshloom.auth")
    handler = _Records()
    auth_logger.addHandler(handler)

    def collect() -> list[str]:
        return [r.getMessage() for r in handler.records if r.getMessage().startswith("AUTH_FAIL ")]

    yield collect
    auth_logger.removeHandler(handler)


def _one(collected: list[str], route: str, reason: str) -> re.Match[str]:
    assert len(collected) == 1, collected
    match = MESSAGE.match(collected[0])
    assert match, collected[0]
    assert (match["route"], match["reason"]) == (route, reason)
    return match


# --- Each route ---------------------------------------------------------------------


async def test_login_with_a_wrong_password(lines):
    await create_user()
    async with make_client() as client:
        response = await client.post(
            "/api/auth/login", json={"username": ADMIN, "password": "wrong password!"}
        )
    assert response.status_code == 401
    match = _one(lines(), "login", "invalid_credentials")
    assert match["ip"] == "127.0.0.1" and match["via"] == "lan" and match["proxied"] == "0"
    assert match["user"] == fail_log.user_field(ADMIN)
    assert ADMIN not in lines()[0]


async def test_login_with_an_unknown_name_hashes_it_too(lines):
    await create_user()
    async with make_client() as client:
        await client.post("/api/auth/login", json={"username": "Mallory", "password": PASSWORD})
    match = _one(lines(), "login", "invalid_credentials")
    assert match["user"] == fail_log.user_field("mallory")  # compared by its key
    assert "allory" not in lines()[0]


async def test_a_good_login_writes_nothing(lines):
    await create_user()
    async with make_client() as client:
        assert (await login(client)).status_code == 200
    assert lines() == []


async def test_setup_with_a_wrong_installation_code(lines, monkeypatch):
    monkeypatch.setattr(auth_router, "SETUP_FAILURE_DELAY_S", 0)
    await setup_code.prepare_first_run(existing_database=True, basic_username="", basic_password="")
    async with make_client() as client:
        response = await client.post(
            "/api/auth/setup",
            json={"username": ADMIN, "password": PASSWORD, "setup_code": "1"},
        )
    assert response.status_code == 403
    assert _one(lines(), "setup", "invalid_setup_code")["user"] == fail_log.user_field(ADMIN)


async def test_second_factor_with_a_wrong_code(lines):
    await create_user()
    async with make_client() as owner:
        await login(owner)
        await _enrol(owner)
    async with make_client() as client:
        token = await _password_step(client)
        response = await client.post(
            "/api/auth/login/2fa", json={"pending_token": token, "code": "000000"}
        )
    assert response.status_code == 401
    assert _one(lines(), "login_2fa", "bad_code")["user"] == fail_log.user_field(ADMIN)


async def test_second_factor_with_a_bogus_pending_token(lines):
    await create_user()
    async with make_client() as client:
        response = await client.post(
            "/api/auth/login/2fa", json={"pending_token": "x" * 43, "code": "123456"}
        )
    assert response.status_code == 401
    assert _one(lines(), "login_2fa", "pending_invalid")["user"] == "-"


async def test_second_factor_from_another_client(lines):
    await create_user()
    async with make_client() as owner:
        await login(owner)
        await _enrol(owner)
    async with make_client() as client:
        token = await _password_step(client)
    async with make_client(client=("127.0.0.2", 50000)) as other:
        response = await other.post(
            "/api/auth/login/2fa", json={"pending_token": token, "code": "000000"}
        )
    assert response.status_code == 401
    match = _one(lines(), "login_2fa", "pending_invalid")
    assert match["ip"] == "127.0.0.2" and match["user"] == fail_log.user_field(ADMIN)


async def _passkey_owner(*, second_factor: bool = False) -> SoftAuthenticator:
    """The owner with one passkey (and TOTP on, for the second-factor tests)."""
    await AuthSettingsRepository.update(public_urls=[PUBLIC])
    await create_user()
    authenticator = SoftAuthenticator()
    async with make_client(PUBLIC) as owner:
        await login(owner)
        options = await owner.post(
            "/api/auth/webauthn/register/options", json={"password": PASSWORD}
        )
        credential = authenticator.create(options.json(), PUBLIC)
        stored = await owner.post(
            "/api/auth/webauthn/register/verify", json={"credential": credential, "name": "Key"}
        )
        assert stored.status_code == 200, stored.text
        if second_factor:
            await _enrol(owner)
    return authenticator


async def test_second_factor_with_a_refused_passkey(lines):
    authenticator = await _passkey_owner(second_factor=True)
    async with make_client(PUBLIC) as client:
        token = (await login(client)).json()["second_factor"]["pending_token"]
        options = await client.post(
            "/api/auth/webauthn/login/options", json={"pending_token": token}
        )
        assert options.status_code == 200, options.text
        credential = authenticator.get(options.json(), "https://evil.example", uv=False)
        response = await client.post(
            "/api/auth/login/2fa", json={"pending_token": token, "passkey": credential}
        )
    assert response.status_code == 401
    assert _one(lines(), "login_2fa", "bad_passkey")["user"] == fail_log.user_field(ADMIN)


async def test_passkey_options_with_a_bogus_pending_token(lines):
    await AuthSettingsRepository.update(public_urls=[PUBLIC])
    await create_user()
    async with make_client(PUBLIC) as client:
        response = await client.post(
            "/api/auth/webauthn/login/options", json={"pending_token": "x" * 43}
        )
    assert response.status_code == 401
    assert _one(lines(), "login_2fa", "pending_invalid")["user"] == "-"


async def test_a_passkey_assertion_that_does_not_check(lines):
    authenticator = await _passkey_owner()
    async with make_client(PUBLIC) as client:
        options = (await client.post("/api/auth/webauthn/login/options", json={})).json()
        assertion = authenticator.get(options, "https://evil.example")
        response = await client.post(
            "/api/auth/webauthn/login/verify", json={"credential": assertion}
        )
    assert response.status_code == 401
    assert _one(lines(), "passkey", "bad_passkey")["user"] == "-"


async def test_a_replayed_passkey_assertion(lines):
    authenticator = await _passkey_owner()
    async with make_client(PUBLIC) as client:
        options = (await client.post("/api/auth/webauthn/login/options", json={})).json()
        assertion = authenticator.get(options, PUBLIC)
        first = await client.post("/api/auth/webauthn/login/verify", json={"credential": assertion})
        assert first.status_code == 200
        assert lines() == []
        again = await client.post("/api/auth/webauthn/login/verify", json={"credential": assertion})
    assert again.status_code == 401
    assert _one(lines(), "passkey", "bad_passkey")["user"] == "-"


async def test_an_unknown_bearer_token(lines):
    await create_user()
    async with make_client() as client:
        response = await client.get("/api/auth/me", headers={"Authorization": "Bearer mlt_bogus"})
    assert response.status_code == 401
    assert _one(lines(), "bearer", "invalid_token")["user"] == "-"


async def test_a_wrong_basic_credential(lines):
    await create_user()
    async with make_client() as client:
        response = await client.get("/api/auth/me", headers=basic(ADMIN, "wrong password!"))
    assert response.status_code == 401
    assert _one(lines(), "basic", "invalid_credentials")["user"] == fail_log.user_field(ADMIN)


@pytest.mark.parametrize("code", ["not-a-code", "mlp_" + "x" * 120])
async def test_a_malformed_pairing_code_within_the_length_limit(lines, code):
    await create_user()
    async with make_client(client=("192.168.1.50", 41000)) as device:
        response = await device.post("/api/auth/pair", json={"code": code, "device_name": "P"})
    assert response.status_code == 410
    _one(lines(), "pair", "invalid_code")


async def test_a_pairing_body_that_fails_validation_writes_no_line(lines):
    await create_user()
    async with make_client(client=("192.168.1.50", 41000)) as device:
        response = await device.post(
            "/api/auth/pair", json={"code": "mlp_" + "y" * 200, "device_name": "P"}
        )
    assert response.status_code == 422
    assert lines() == []


async def test_an_unknown_pairing_code(lines):
    await create_user()
    async with make_client(client=("192.168.1.50", 41000)) as device:
        response = await device.post(
            "/api/auth/pair", json={"code": "mlp_" + "A" * 22, "device_name": "Pixel 8"}
        )
    assert response.status_code == 410
    match = _one(lines(), "pair", "invalid_code")
    assert match["ip"] == "192.168.1.50" and match["user"] == "-"


# --- Format and fields ----------------------------------------------------------------


def _rendered(message: str) -> str:
    record = logging.LogRecord("meshloom.auth", logging.WARNING, __file__, 1, message, None, None)
    formatter = RedactingFormatter(logging.Formatter(LOG_FORMAT, LOG_DATE_FORMAT))
    return formatter.format(record)


def test_the_full_line_matches_the_failregex_and_proxied_lines_do_not():
    plain = fail_log.format_line(
        ip="203.0.113.7",
        route="login",
        reason="invalid_credentials",
        via="lan",
        proxied=False,
        username="owner",
    )
    match = FAILREGEX.match(_rendered(plain))
    assert match and match["host"] == "203.0.113.7"

    proxied = plain.replace("proxied=0", "proxied=1")
    assert MESSAGE.match(proxied)
    assert FAILREGEX.match(_rendered(proxied)) is None


def test_golden_message():
    line = fail_log.format_line(
        ip="::ffff:192.0.2.1",
        route="bearer",
        reason="invalid_token",
        via="relay",
        proxied=False,
        username=None,
    )
    assert (
        line
        == "AUTH_FAIL ip=192.0.2.1 route=bearer reason=invalid_token via=relay proxied=0 user=-"
    )


@pytest.mark.parametrize(
    ("raw", "shown"),
    [
        ("2001:DB8:0:0::1", "2001:db8::1"),
        ("::ffff:10.0.0.1", "10.0.0.1"),
        ("10.0.0.1 route=login", "-"),
        ("evil\nAUTH_FAIL ip=1.2.3.4", "-"),
        ("", "-"),
        (None, "-"),
    ],
)
def test_the_ip_is_canonical_or_a_dash(raw, shown):
    assert fail_log.ip_field(raw) == shown


def test_the_published_regex_is_the_tested_one():
    assert _documented_failregex() == EXPECTED_FAILREGEX


def test_the_user_hash_is_keyed():
    for name in ("owner", "admin", "pascal"):
        field = fail_log.user_field(name)[2:]
        for digest in (hashlib.sha256, hashlib.sha1, hashlib.md5):
            assert field != digest(name.encode()).hexdigest()[: fail_log.USER_HASH_HEX]
        expected = hmac.new(fail_log._USER_KEY, name.encode(), hashlib.sha256).hexdigest()
        assert field == expected[: fail_log.USER_HASH_HEX]


def test_the_user_is_a_short_keyed_hash():
    field = fail_log.user_field("correct horse battery")
    assert re.fullmatch(r"h:[0-9a-f]{6}", field)
    assert fail_log.user_field("owner") == fail_log.user_field("OWNER")
    assert fail_log.user_field("") == "-" and fail_log.user_field(None) == "-"


def test_an_unknown_reason_is_refused_without_raising(caplog):
    caplog.set_level(logging.WARNING, logger="meshloom.auth")
    scope = {"type": "http", "client": ("127.0.0.1", 1), "headers": [], "state": {}}
    fail_log.log_auth_failure(scope, "login", "made_up")
    assert not [r for r in caplog.records if r.getMessage().startswith("AUTH_FAIL ")]
    assert [r for r in caplog.records if r.levelno == logging.ERROR]


# --- When no line is written ----------------------------------------------------------


async def test_a_declared_proxy_logs_the_client_it_forwarded(lines):
    proxy_headers.apply(AuthSettings(trusted_proxies=["10.0.0.5"]))
    await create_user()
    async with make_client(client=("10.0.0.5", 50000)) as client:
        await client.post(
            "/api/auth/login",
            json={"username": ADMIN, "password": "wrong password!"},
            headers={"X-Forwarded-For": "1.2.3.4, 203.0.113.9"},
        )
    match = _one(lines(), "login", "invalid_credentials")
    assert match["ip"] == "203.0.113.9" and match["proxied"] == "0"
    assert FAILREGEX.match(_rendered(lines()[0]))["host"] == "203.0.113.9"


@pytest.mark.parametrize("peer", ["198.51.100.20", "192.0.2.10", "2606:4700::1111"])
async def test_a_public_peer_with_forwarding_headers_is_the_client(lines, peer):
    await create_user()
    async with make_client(client=(peer, 50000)) as client:
        await client.post(
            "/api/auth/login",
            json={"username": ADMIN, "password": "wrong password!"},
            headers={"X-Forwarded-For": "203.0.113.50"},
        )
    match = _one(lines(), "login", "invalid_credentials")
    assert match["ip"] == fail_log.ip_field(peer) and match["proxied"] == "0"
    assert FAILREGEX.match(_rendered(lines()[0]))["host"] == fail_log.ip_field(peer)


@pytest.mark.parametrize(
    "peer",
    [
        "127.0.0.1",
        "10.1.2.3",
        "192.168.1.30",
        "172.18.0.1",
        "100.100.1.2",
        "169.254.1.1",
        "::1",
        "fd00::5",
        "fe80::1",
        "::ffff:192.168.1.30",
    ],
)
async def test_forwarding_headers_from_an_undeclared_local_peer_mark_the_line_proxied(lines, peer):
    await create_user()
    async with make_client(client=(peer, 50000)) as client:
        await client.post(
            "/api/auth/login",
            json={"username": ADMIN, "password": "wrong password!"},
            headers={"X-Forwarded-For": "198.51.100.9"},
        )
    match = _one(lines(), "login", "invalid_credentials")
    assert match["proxied"] == "1" and match["ip"] == fail_log.ip_field(peer)
    assert FAILREGEX.match(_rendered(lines()[0])) is None


async def test_a_trusted_network_request_is_logged_as_lan(lines, monkeypatch):
    monkeypatch.setenv("MESHLOOM_INSTALL_KIND", "package")
    await create_user()
    tablet = await create_user("tablet", password=None, role="member")
    await AuthSettingsRepository.update(
        trusted_networks=[TrustedNetworkRule(cidr="192.168.1.0/24", user_id=tablet.id)]
    )
    async with make_client("http://192.168.1.10:8000", ("192.168.1.20", 40000)) as client:
        assert (await client.get("/api/auth/session")).json()["via"] == "trusted"
        response = await client.post(
            "/api/auth/login", json={"username": ADMIN, "password": "wrong password!"}
        )
    assert response.status_code == 401
    assert _one(lines(), "login", "invalid_credentials")["via"] == "lan"


async def test_forwarding_headers_from_an_undeclared_peer_mark_the_line_proxied(lines):
    await create_user()
    async with make_client() as client:
        await client.post(
            "/api/auth/login",
            json={"username": ADMIN, "password": "wrong password!"},
            headers={"X-Forwarded-For": "198.51.100.9"},
        )
    match = _one(lines(), "login", "invalid_credentials")
    assert match["proxied"] == "1" and match["ip"] == "127.0.0.1"
    assert FAILREGEX.match(_rendered(lines()[0])) is None


async def test_ingress_never_writes_a_line(lines, monkeypatch):
    monkeypatch.setenv("MESHLOOM_INSTALL_KIND", "addon")
    await create_user()
    async with make_client(client=(ingress.SUPERVISOR_IP, 40000)) as client:
        await client.get("/api/auth/me", headers={"Authorization": "Bearer mlt_bogus"})
        await client.post("/api/auth/login", json={"username": ADMIN, "password": "nope nope"})
    assert lines() == []


async def test_a_login_delay_writes_no_line(lines, monkeypatch):
    from app.auth import ratelimit

    await create_user()

    def delayed(*_args, **_kwargs):
        raise ratelimit.LoginDelayed("pair", 30, True)

    monkeypatch.setattr(ratelimit.login_limiter, "check", delayed)
    async with make_client() as client:
        response = await client.post(
            "/api/auth/login", json={"username": ADMIN, "password": "wrong password!"}
        )
    assert response.status_code == 429
    assert lines() == []


# --- The contract document --------------------------------------------------------------


def test_the_contract_lists_every_route_and_reason():
    text = CONTRACT.read_text()
    assert (
        "AUTH_FAIL ip=<ip> route=<route> reason=<reason> via=<via> proxied=<0|1> user=<user>"
        in text
    )
    for route, reasons in fail_log.ROUTE_REASONS.items():
        for reason in reasons:
            assert re.search(rf"`{route}`.*`{reason}`", text), (route, reason)
