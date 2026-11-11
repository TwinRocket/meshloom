"""First-run hardening for upgraded 4.x instances (app/auth/first_run.py).

- A public IP literal in ``Host`` is an exposed request.
- While a retired ``MESHCORE_BASIC_AUTH_*`` variable is set and no account exists,
  setup also needs the 4.x Basic credentials (body or ``Authorization: Basic``).
- The LAN address is told only to a visitor seen on the local network, and the
  listening check behind it runs at most once a minute.
- Anonymous ``/api/health`` gives the running version while no account exists, so a
  4.18.3 tab that ran the in-app update reloads into the setup page.
"""

import logging
import sqlite3
from pathlib import Path

import pytest

from app.auth import first_run
from app.middleware import proxy_headers
from app.repository.auth import AuthAuditRepository, AuthSettingsRepository, UserRepository
from app.services import install_kind
from tests.auth_helpers import ADMIN, PASSWORD, basic, make_client, set_cookie_headers
from tests.conftest import patched_database

pytestmark = [pytest.mark.real_auth, pytest.mark.usefixtures("fast_argon2")]

LAN = ("192.168.1.10", 40000)
BASIC_USER = "mesh"
BASIC_PASSWORD = "4x-basic-secret"
# 4.18.3 databases are at schema 95.
SCHEMA_418 = 95
FIXTURE_418 = Path(__file__).parent / "fixtures" / "meshcore_4_18_3_schema95.sql"


def _code(response) -> str:
    return response.json()["detail"]["code"]


def _setup_body(**extra):
    return {"username": ADMIN, "password": PASSWORD, **extra}


@pytest.fixture(autouse=True)
def _app_imported():
    """Importing app.main configures logging, which replaces the root handlers."""
    import app.main  # noqa: F401


@pytest.fixture
async def upgraded_db(tmp_path):
    """A real 4.18.3 database (schema 95) holding a contact, migrated by this code."""
    path = tmp_path / "meshcore.db"
    conn = sqlite3.connect(path)
    try:
        conn.executescript(FIXTURE_418.read_text(encoding="utf-8"))
        conn.execute(
            "INSERT INTO contacts (public_key, name) VALUES (?, 'old contact')", ("ab" * 32,)
        )
        conn.execute(f"PRAGMA user_version = {SCHEMA_418}")
        conn.commit()
    finally:
        conn.close()
    async with patched_database(str(path)) as db:
        async with db.readonly() as read:
            async with read.execute("SELECT name FROM contacts") as cursor:
                assert [row[0] for row in await cursor.fetchall()] == ["old contact"]
        yield db


@pytest.fixture
def basic_env(monkeypatch):
    monkeypatch.setenv("MESHCORE_BASIC_AUTH_USERNAME", BASIC_USER)
    monkeypatch.setenv("MESHCORE_BASIC_AUTH_PASSWORD", BASIC_PASSWORD)


async def _prepared() -> None:
    await first_run.prepare_first_run()


# --- A public IP literal in Host --------------------------------------------------------


@pytest.mark.usefixtures("test_db")
@pytest.mark.parametrize("host", ["8.8.8.8", "203.0.113.7:8000", "[2001:db8::1]:8000", "0.0.0.0"])
async def test_a_public_ip_literal_host_is_exposed_even_from_the_lan(host):
    await _prepared()
    headers = {"Host": host}
    async with make_client(client=LAN) as client:
        session = (await client.get("/api/auth/session", headers=headers)).json()
        assert session["setup_local_only"] is True
        response = await client.post("/api/auth/setup", json=_setup_body(), headers=headers)
    assert response.status_code == 403
    assert _code(response) == "auth_setup_local_only"
    assert not await UserRepository.any_exist()


@pytest.mark.parametrize(
    ("host", "exposed"),
    [
        ("8.8.8.8", True),
        ("[2001:db8::1]:8000", True),
        ("[::ffff:8.8.8.8]", True),
        ("192.168.1.5:8000", False),
        ("127.0.0.1:8000", False),
        ("[fd00::5]", False),
        ("[::ffff:192.168.1.5]:8000", False),
        ("mypi", False),
        ("meshloom.local", False),
    ],
)
def test_public_ip_literal(host, exposed):
    assert first_run._public_ip_literal(host) is exposed


# --- The 4.x Basic credentials on an upgraded database -----------------------------------


@pytest.mark.usefixtures("upgraded_db", "basic_env")
async def test_upgraded_with_basic_variables_refuses_setup_without_them():
    await _prepared()
    async with make_client(client=LAN) as client:
        session = (await client.get("/api/auth/session")).json()
        assert session["setup_required"] is True
        assert session["setup_local_only"] is False
        assert session["setup_legacy_basic"] is True
        for extra, headers in (
            ({}, {}),
            ({"legacy_basic_username": BASIC_USER, "legacy_basic_password": "wrong"}, {}),
            ({"legacy_basic_username": "other", "legacy_basic_password": BASIC_PASSWORD}, {}),
            ({}, basic(BASIC_USER, "wrong")),
            ({}, {"Authorization": "Basic not-base64!"}),
        ):
            response = await client.post(
                "/api/auth/setup", json=_setup_body(**extra), headers=headers
            )
            assert response.status_code == 401, (extra, headers)
            assert _code(response) == "auth_setup_legacy_basic_invalid"
            assert set_cookie_headers(response) == []
    assert not await UserRepository.any_exist()
    rows = [e for e in await AuthAuditRepository.list_recent() if e.action == "setup"]
    assert rows and all(row.details == {"reason": "legacy_basic_invalid"} for row in rows)


@pytest.mark.usefixtures("upgraded_db", "basic_env")
@pytest.mark.parametrize(
    "client_address", [("127.0.0.1", 40000), ("172.17.0.1", 40000)]
)  # a local proxy or tunnel, the Docker gateway
async def test_a_peer_that_looks_local_still_needs_the_basic_credentials(client_address):
    """nginx's default ``proxy_set_header Host $proxy_host``: a loopback IP Host and peer."""
    await _prepared()
    headers = {"Host": "127.0.0.1:8000"}
    async with make_client(client=client_address) as client:
        response = await client.post("/api/auth/setup", json=_setup_body(), headers=headers)
    assert response.status_code == 401
    assert _code(response) == "auth_setup_legacy_basic_invalid"
    assert not await UserRepository.any_exist()


@pytest.mark.usefixtures("upgraded_db", "basic_env")
async def test_an_exposed_request_is_refused_before_the_basic_check():
    await _prepared()
    body = _setup_body(legacy_basic_username=BASIC_USER, legacy_basic_password=BASIC_PASSWORD)
    async with make_client(client=LAN) as client:
        response = await client.post("/api/auth/setup", json=body, headers={"Host": "8.8.8.8"})
    assert response.status_code == 403
    assert _code(response) == "auth_setup_local_only"
    assert not await UserRepository.any_exist()


@pytest.mark.usefixtures("upgraded_db", "basic_env")
async def test_the_basic_credentials_in_the_body_open_setup():
    await _prepared()
    body = _setup_body(legacy_basic_username=BASIC_USER, legacy_basic_password=BASIC_PASSWORD)
    async with make_client(client=LAN) as client:
        response = await client.post("/api/auth/setup", json=body)
        assert response.status_code == 200, response.text
        assert response.json()["user"]["role"] == "admin"
        session = (await client.get("/api/auth/session")).json()
    assert "setup_legacy_basic" not in session
    assert [user.username for user in await UserRepository.list_all()] == [ADMIN]


@pytest.mark.usefixtures("upgraded_db", "basic_env")
async def test_a_basic_header_the_browser_still_sends_opens_setup():
    await _prepared()
    async with make_client(client=LAN) as client:
        response = await client.post(
            "/api/auth/setup", json=_setup_body(), headers=basic(BASIC_USER, BASIC_PASSWORD)
        )
    assert response.status_code == 200, response.text


@pytest.mark.usefixtures("upgraded_db", "basic_env")
async def test_the_body_wins_over_the_header():
    await _prepared()
    body = _setup_body(legacy_basic_username=BASIC_USER, legacy_basic_password="wrong")
    async with make_client(client=LAN) as client:
        response = await client.post(
            "/api/auth/setup", json=body, headers=basic(BASIC_USER, BASIC_PASSWORD)
        )
    assert response.status_code == 401


@pytest.mark.usefixtures("upgraded_db")
@pytest.mark.parametrize("only", ["MESHCORE_BASIC_AUTH_USERNAME", "MESHCORE_BASIC_AUTH_PASSWORD"])
async def test_one_basic_variable_alone_leaves_only_the_command(monkeypatch, only):
    monkeypatch.delenv("MESHCORE_BASIC_AUTH_USERNAME", raising=False)
    monkeypatch.delenv("MESHCORE_BASIC_AUTH_PASSWORD", raising=False)
    monkeypatch.setenv(only, "value")
    await _prepared()
    async with make_client(client=LAN) as client:
        assert (await client.get("/api/auth/session")).json()["setup_legacy_basic"] is True
        for user, password in (("value", ""), ("", "value"), ("value", "value")):
            body = _setup_body(legacy_basic_username=user, legacy_basic_password=password)
            response = await client.post("/api/auth/setup", json=body)
            assert response.status_code == 401
    assert not await UserRepository.any_exist()


@pytest.mark.usefixtures("upgraded_db", "basic_env")
async def test_repeated_wrong_basic_credentials_meet_the_login_delay():
    await _prepared()
    body = _setup_body(legacy_basic_username=BASIC_USER, legacy_basic_password="wrong")
    async with make_client(client=LAN) as client:
        statuses = [(await client.post("/api/auth/setup", json=body)).status_code for _ in range(8)]
    assert statuses[:5] == [401] * 5
    assert 429 in statuses[5:]


@pytest.mark.usefixtures("upgraded_db")
async def test_upgraded_without_basic_variables_is_unchanged(monkeypatch):
    monkeypatch.delenv("MESHCORE_BASIC_AUTH_USERNAME", raising=False)
    monkeypatch.delenv("MESHCORE_BASIC_AUTH_PASSWORD", raising=False)
    await _prepared()
    async with make_client(client=LAN) as client:
        session = (await client.get("/api/auth/session")).json()
        assert "setup_legacy_basic" not in session
        response = await client.post("/api/auth/setup", json=_setup_body())
    assert response.status_code == 200, response.text


def test_blank_basic_variables_do_not_gate(monkeypatch):
    monkeypatch.setenv("MESHCORE_BASIC_AUTH_USERNAME", "  ")
    monkeypatch.setenv("MESHCORE_BASIC_AUTH_PASSWORD", "")
    assert first_run.legacy_basic_gate_active() is False


@pytest.mark.usefixtures("upgraded_db", "basic_env")
async def test_the_start_warns_with_the_command_and_no_secret(caplog):
    from app.services.legacy_env_settings import warn_about_retired_env

    with caplog.at_level(logging.WARNING):
        await _prepared()
        warn_about_retired_env()
    assert "meshloom-admin create-admin" in caplog.text
    assert "remove the variable once it exists" in caplog.text
    assert BASIC_PASSWORD not in caplog.text


@pytest.mark.usefixtures("upgraded_db", "basic_env")
async def test_the_basic_warning_says_it_once_and_changes_once_the_account_exists(caplog):
    """G2: "HTTP Basic auth is removed" twice per line, and "until the first account
    exists" still logged after setup."""
    from app.services.legacy_env_settings import import_legacy_env_settings

    def basic_lines() -> list[str]:
        return [r.getMessage() for r in caplog.records if "MESHCORE_BASIC_AUTH_" in r.getMessage()]

    with caplog.at_level(logging.WARNING):
        await import_legacy_env_settings()
    before = basic_lines()
    assert len(before) == 2
    assert all(line.count("HTTP Basic auth is removed") == 1 for line in before)
    assert all("Until the first account exists" in line for line in before)

    await _prepared()
    body = _setup_body(legacy_basic_username=BASIC_USER, legacy_basic_password=BASIC_PASSWORD)
    async with make_client(client=LAN) as client:
        assert (await client.post("/api/auth/setup", json=body)).status_code == 200
    caplog.clear()
    with caplog.at_level(logging.WARNING):
        await import_legacy_env_settings()
    after = basic_lines()
    assert len(after) == 2
    assert all("no longer read" in line and "Remove the variable" in line for line in after)
    assert not any("first account" in line for line in after)
    assert BASIC_PASSWORD not in caplog.text


def test_the_comparison_is_exact(monkeypatch):
    """As 4.x compared: the values as set, nothing stripped."""
    monkeypatch.setenv("MESHCORE_BASIC_AUTH_USERNAME", "mesh")
    monkeypatch.setenv("MESHCORE_BASIC_AUTH_PASSWORD", " pass word ")
    assert first_run.legacy_basic_credentials_match("mesh", " pass word ") is True
    assert first_run.legacy_basic_credentials_match("mesh", "pass word") is False
    assert first_run.legacy_basic_credentials_match("Mesh", " pass word ") is False
    assert first_run.legacy_basic_credentials_match("méš", "x") is False


def test_the_basic_header_parser():
    header = {name.lower(): value for name, value in basic("a", "b:c").items()}
    assert first_run.basic_credentials_from_header(header) == ("a", "b:c")
    assert first_run.basic_credentials_from_header({"authorization": "Bearer x"}) is None
    assert first_run.basic_credentials_from_header({"authorization": "Basic"}) is None
    assert first_run.basic_credentials_from_header({"authorization": "Basic Zm9v"}) is None
    assert first_run.basic_credentials_from_header({}) is None


# --- The LAN address told to a refused visitor -------------------------------------------


@pytest.fixture
def on_a_lan(monkeypatch):
    calls = []

    async def listening(address, port):
        calls.append((address, port))
        return True

    monkeypatch.setattr(install_kind, "in_container", lambda: False)
    monkeypatch.setattr(first_run, "_primary_private_ipv4", lambda: "192.168.1.5")
    monkeypatch.setattr(first_run, "_accepts_connections", listening)
    return calls


def _scope(client="192.168.1.10", headers=(), **extra):
    return {
        "type": "http",
        "client": (client, 1),
        "server": ("192.168.1.5", 8000),
        "scheme": "http",
        "state": {proxy_headers.PEER_STATE_KEY: client},
        "headers": list(headers),
        **extra,
    }


@pytest.mark.usefixtures("test_db", "on_a_lan")
@pytest.mark.parametrize(
    ("client", "headers"),
    [
        ("8.8.8.8", ()),
        ("203.0.113.9", ()),
        ("127.0.0.1", ()),  # a proxy or tunnel on this machine
        ("::1", ()),
        ("192.168.1.2", ((b"x-forwarded-for", b"8.8.8.8"),)),  # a proxy nobody declared
    ],
)
async def test_no_local_address_for_a_visitor_not_seen_on_the_lan(client, headers):
    assert await first_run.local_setup_url(_scope(client, headers)) is None


@pytest.mark.usefixtures("test_db", "on_a_lan")
async def test_a_lan_visitor_under_a_public_name_gets_the_local_address():
    await _prepared()
    async with make_client(client=LAN, base_url="http://mesh.example.com:8000") as client:
        session = (await client.get("/api/auth/session")).json()
    assert session["setup_local_only"] is True
    assert session["setup_local_url"] == "http://192.168.1.5:8000"


@pytest.mark.usefixtures("test_db", "on_a_lan")
async def test_an_internet_visitor_is_not_told_the_local_address():
    await _prepared()
    async with make_client(client=("8.8.8.8", 1), base_url="http://mesh.example.com:8000") as c:
        session = (await c.get("/api/auth/session")).json()
    assert session["setup_local_only"] is True
    assert "setup_local_url" not in session


@pytest.mark.usefixtures("test_db", "on_a_lan")
async def test_no_local_address_once_a_public_address_is_declared():
    await AuthSettingsRepository.update(public_urls=["https://mesh.example.com"])
    assert await first_run.local_setup_url(_scope()) is None


@pytest.mark.usefixtures("test_db")
async def test_the_listening_check_runs_once_a_minute(on_a_lan):
    for _ in range(5):
        assert await first_run.local_setup_url(_scope()) == "http://192.168.1.5:8000"
    assert len(on_a_lan) == 1
    later = first_run.time.monotonic() + first_run.LOCAL_URL_PROBE_CACHE_S + 1
    assert await first_run._listening("192.168.1.5", 8000, now=later) is True
    assert len(on_a_lan) == 2


async def test_a_failed_listening_check_is_reused_too(monkeypatch):
    calls = []

    async def refused(address, port):
        calls.append(port)
        raise ConnectionRefusedError

    monkeypatch.setattr(first_run, "_accepts_connections", refused)
    assert await first_run._listening("192.168.1.5", 8000, now=10.0) is False
    assert await first_run._listening("192.168.1.5", 8000, now=20.0) is False
    assert calls == [8000]


# --- Anonymous health while no account exists (a 4.18.3 tab after the update) ----------


def _418_versions_match(left, right) -> bool:
    """``versionsMatch`` of the 4.18.3 ``useOssUpdates.ts`` (a leading v dropped)."""

    def norm(value):
        text = (value or "").strip()
        if len(text) > 1 and text[0] in "vV" and text[1].isdigit():
            return text[1:]
        return text

    return bool(norm(left)) and norm(left) == norm(right)


@pytest.mark.usefixtures("upgraded_db")
async def test_a_4183_tab_sees_the_new_version_and_reloads():
    """Replays the 4.18.3 hook's restart branch: ``/api/updates`` fails (no admin
    yet), then ``/api/health`` must name the target version for the tab to reload."""
    from app.version_info import get_app_build_info

    version = get_app_build_info().version
    await _prepared()
    async with make_client(client=LAN) as client:
        updates = await client.get("/api/updates")
        assert updates.status_code >= 400
        health = await client.get("/api/health")
    assert health.status_code == 200
    body = health.json()
    assert body == {"ok": True, "app_info": {"version": version}}
    assert _418_versions_match(body["app_info"]["version"], f"v{version}")


@pytest.mark.usefixtures("test_db")
async def test_anonymous_health_is_minimal_once_an_account_exists():
    await UserRepository.create(ADMIN, "admin")
    async with make_client(client=LAN) as client:
        assert (await client.get("/api/health")).json() == {"ok": True}


@pytest.mark.usefixtures("test_db")
async def test_a_kiosk_account_alone_still_gives_the_version():
    await UserRepository.create("kiosk", "kiosk", provider="system")
    async with make_client(client=LAN) as client:
        assert "app_info" in (await client.get("/api/health")).json()
