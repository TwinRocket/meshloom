"""First-run setup (app/auth/setup_code.py, POST /api/auth/setup); no Basic import (MSHL-100)."""

import logging
import re

import pytest

from app.auth import setup_code
from app.repository.auth import AuthSettingsRepository, UserRepository
from app.routers import auth as auth_router
from tests.auth_helpers import ADMIN, PASSWORD, basic, make_client, set_cookie_headers

pytestmark = [pytest.mark.real_auth, pytest.mark.usefixtures("test_db", "fast_argon2")]


@pytest.fixture(autouse=True)
def _no_failure_delay(monkeypatch):
    monkeypatch.setattr(auth_router, "SETUP_FAILURE_DELAY_S", 0)


def _code(response) -> str:
    return response.json()["detail"]["code"]


def _setup_body(**extra):
    return {"username": ADMIN, "password": PASSWORD, **extra}


async def test_fresh_database_asks_for_no_code_and_setup_works_once():
    await setup_code.prepare_first_run(existing_database=False)
    async with make_client() as client:
        session = (await client.get("/api/auth/session")).json()
        assert session["setup_required"] is True
        assert session["setup_code_required"] is False

        response = await client.post("/api/auth/setup", json=_setup_body(display_name="Owner"))
        assert response.status_code == 200, response.text
        assert response.json()["user"]["role"] == "admin"
        assert set_cookie_headers(response)[0].startswith("meshloom_session=")
        assert (await client.get("/api/auth/me")).json()["display_name"] == "Owner"

        again = await client.post("/api/auth/setup", json=_setup_body(username="other"))
        assert again.status_code == 409
        assert _code(again) == "auth_setup_done"
    assert [user.username for user in await UserRepository.list_all()] == [ADMIN]


async def test_existing_database_requires_the_logged_installation_code(caplog):
    with caplog.at_level(logging.WARNING, logger="app.auth.setup_code"):
        await setup_code.prepare_first_run(existing_database=True)
    [line] = [r.getMessage() for r in caplog.records if "installation code" in r.getMessage()]
    code = re.search(r"\b(\d{8})\b", line).group(1)
    assert (await AuthSettingsRepository.get()).setup_code_set

    async with make_client() as client:
        session = (await client.get("/api/auth/session")).json()
        assert session["setup_code_required"] is True

        for body in (
            _setup_body(),
            _setup_body(setup_code="00000000" if code != "00000000" else "1"),
        ):
            refused = await client.post("/api/auth/setup", json=body)
            assert refused.status_code == 403
            assert _code(refused) == "auth_setup_code_invalid"
        assert await UserRepository.list_all() == []

        typed = f"{code[:4]} {code[4:]}"
        response = await client.post("/api/auth/setup", json=_setup_body(setup_code=typed))
        assert response.status_code == 200, response.text
    assert not (await AuthSettingsRepository.get()).setup_code_set


async def test_each_start_replaces_the_code_and_an_account_clears_it():
    await setup_code.issue_setup_code()
    first_hash = await AuthSettingsRepository.get_setup_code_hash()
    await setup_code.prepare_first_run(existing_database=True)
    assert await AuthSettingsRepository.get_setup_code_hash() not in (None, first_hash)
    await UserRepository.create(ADMIN, "admin")
    await setup_code.prepare_first_run(existing_database=True)
    assert not (await AuthSettingsRepository.get()).setup_code_set


async def test_setup_applies_the_password_policy():
    await setup_code.prepare_first_run(existing_database=False)
    async with make_client() as client:
        short = await client.post("/api/auth/setup", json=_setup_body(password="short pass"))
        assert short.status_code == 400
        assert short.json()["detail"]["code"] == "auth_password_policy"
        assert short.json()["detail"]["params"] == {"reason": "too_short", "min_length": 12}

        same = await client.post(
            "/api/auth/setup", json={"username": "LongUsername12", "password": "longusername12"}
        )
        assert same.status_code == 400
        assert same.json()["detail"]["params"]["reason"] == "same_as_username"
    assert await UserRepository.list_all() == []


async def test_retired_basic_variables_import_nothing_and_only_warn(caplog, monkeypatch):
    """MSHL-100: no account comes from MESHCORE_BASIC_AUTH_*; the installation code
    path applies and the header signs nobody in."""
    from app.services.legacy_env_settings import warn_about_retired_env

    monkeypatch.setenv("MESHCORE_BASIC_AUTH_USERNAME", "mesh")
    monkeypatch.setenv("MESHCORE_BASIC_AUTH_PASSWORD", "secret")
    with caplog.at_level(logging.WARNING):
        await setup_code.prepare_first_run(existing_database=True)
        warn_about_retired_env()
    assert await UserRepository.list_all() == []
    assert (await AuthSettingsRepository.get()).setup_code_set
    assert "secret" not in caplog.text
    assert any("no longer read" in r.getMessage() for r in caplog.records)
    async with make_client() as client:
        response = await client.get("/api/contacts", headers=basic("mesh", "secret"))
        assert response.status_code == 401
        assert "www-authenticate" not in response.headers


async def test_login_refuses_accounts_without_a_password_and_unknown_names():
    await UserRepository.create("kiosk", "kiosk", provider="system")
    await UserRepository.create(ADMIN, "admin")
    async with make_client() as client:
        for username, password in (("kiosk", "anything at all"), ("nobody", PASSWORD)):
            response = await client.post(
                "/api/auth/login", json={"username": username, "password": password}
            )
            assert response.status_code == 401
            assert _code(response) == "auth_invalid_credentials"
            assert set_cookie_headers(response) == []


async def test_setup_is_refused_until_first_run_preparation_succeeded(monkeypatch):
    calls = 0
    real_prepare = setup_code._prepare

    async def flaky(*args):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise RuntimeError("database is locked")
        await real_prepare(*args)

    monkeypatch.setattr(setup_code, "_prepare", flaky)
    with pytest.raises(RuntimeError):
        await setup_code.prepare_first_run(existing_database=False)
    async with make_client() as client:
        # The setup route retries once; while it keeps failing, 503 and nothing created.
        monkeypatch.setattr(setup_code, "_prepare", _always_fail)
        refused = await client.post("/api/auth/setup", json=_setup_body())
        assert refused.status_code == 503
        assert _code(refused) == "auth_setup_unavailable"
        assert await UserRepository.list_all() == []

        # Once a retry succeeds, setup opens (fresh database: no code).
        monkeypatch.setattr(setup_code, "_prepare", real_prepare)
        response = await client.post("/api/auth/setup", json=_setup_body())
        assert response.status_code == 200, response.text


async def _always_fail(*args):
    raise RuntimeError("database is locked")


async def test_setup_is_refused_when_never_prepared():
    async with make_client() as client:
        response = await client.post("/api/auth/setup", json=_setup_body())
    assert response.status_code == 503
    assert _code(response) == "auth_setup_unavailable"


async def test_existing_database_without_a_stored_code_never_opens_setup():
    await setup_code.prepare_first_run(existing_database=True)
    await setup_code.clear_setup_code()  # lost by hand or by a failed write
    async with make_client() as client:
        session = (await client.get("/api/auth/session")).json()
        assert session["setup_code_required"] is True
        for body in (_setup_body(), _setup_body(setup_code="12345678")):
            response = await client.post("/api/auth/setup", json=body)
            assert response.status_code == 503
            assert _code(response) == "auth_setup_unavailable"
    assert await UserRepository.list_all() == []


async def test_setup_through_ingress_is_refused(monkeypatch):
    """Home Assistant creates the administrator there (tests/test_auth_ingress.py)."""
    monkeypatch.setenv("MESHLOOM_INSTALL_KIND", "addon")
    await setup_code.prepare_first_run(existing_database=False)
    async with make_client(client=("172.30.32.2", 40000)) as client:
        response = await client.post("/api/auth/setup", json=_setup_body())
        assert response.status_code == 403
        assert _code(response) == "auth_ingress_only"
        assert set_cookie_headers(response) == []
    assert await UserRepository.list_all() == []


async def test_the_setup_retry_keeps_a_code_issued_by_the_cli(monkeypatch):
    """Startup preparation fails on an existing database, the operator runs
    ``meshloom-admin setup-code`` and types that code: the route's retry must not
    replace it with a new one (MSHL-288)."""
    real_prepare = setup_code._prepare
    monkeypatch.setattr(setup_code, "_prepare", _always_fail)
    with pytest.raises(RuntimeError):
        await setup_code.prepare_first_run(existing_database=True)
    monkeypatch.setattr(setup_code, "_prepare", real_prepare)
    cli_code = await setup_code.issue_setup_code()
    async with make_client() as client:
        response = await client.post("/api/auth/setup", json=_setup_body(setup_code=cli_code))
        assert response.status_code == 200, response.text


async def test_the_setup_retry_issues_a_code_when_none_is_stored(monkeypatch):
    real_prepare = setup_code._prepare
    monkeypatch.setattr(setup_code, "_prepare", _always_fail)
    with pytest.raises(RuntimeError):
        await setup_code.prepare_first_run(existing_database=True)
    monkeypatch.setattr(setup_code, "_prepare", real_prepare)
    issued: list[str] = []
    real_issue = setup_code.issue_setup_code

    async def recording_issue() -> str:
        issued.append(await real_issue())
        return issued[-1]

    monkeypatch.setattr(setup_code, "issue_setup_code", recording_issue)
    async with make_client() as client:
        refused = await client.post("/api/auth/setup", json=_setup_body(setup_code="00000000"))
        assert refused.status_code == 403
        assert len(issued) == 1
        response = await client.post("/api/auth/setup", json=_setup_body(setup_code=issued[0]))
        assert response.status_code == 200, response.text


# --- Exposed first run (MSHL-424) --------------------------------------------------

PUBLIC = ("8.8.8.8", 40000)


@pytest.fixture(autouse=True)
def _app_imported():
    """Importing app.main configures logging, which replaces the root handlers:
    do it before a test captures the code logged during a request."""
    import app.main  # noqa: F401


async def _fresh() -> None:
    await setup_code.prepare_first_run(existing_database=False)


def _logged_code(caplog) -> str:
    [line] = [r.getMessage() for r in caplog.records if "installation code" in r.getMessage()]
    return re.search(r"\b(\d{8})\b", line).group(1)


async def test_a_public_peer_on_a_fresh_database_needs_the_logged_code(caplog):
    await _fresh()
    with caplog.at_level(logging.WARNING, logger="app.auth.setup_code"):
        async with make_client(client=PUBLIC) as client:
            session = (await client.get("/api/auth/session")).json()
            assert session["setup_code_required"] is True
            code = _logged_code(caplog)

            for body in (
                _setup_body(),
                _setup_body(setup_code="00000000" if code != "00000000" else "1"),
            ):
                refused = await client.post("/api/auth/setup", json=body)
                assert refused.status_code == 403
                assert _code(refused) == "auth_setup_code_invalid"
            assert await UserRepository.list_all() == []

            ok = await client.post("/api/auth/setup", json=_setup_body(setup_code=code))
            assert ok.status_code == 200, ok.text
    assert not (await AuthSettingsRepository.get()).setup_code_set


async def test_a_public_setup_without_a_session_call_is_refused_and_issues_a_code(caplog):
    await _fresh()
    with caplog.at_level(logging.WARNING, logger="app.auth.setup_code"):
        async with make_client(client=PUBLIC) as client:
            refused = await client.post("/api/auth/setup", json=_setup_body())
    assert refused.status_code == 403
    assert _code(refused) == "auth_setup_code_invalid"
    assert _logged_code(caplog)
    assert (await AuthSettingsRepository.get()).setup_code_set


async def test_the_lan_owner_is_not_blocked_after_an_internet_visitor():
    await _fresh()
    async with make_client(client=PUBLIC) as client:
        assert (await client.get("/api/auth/session")).json()["setup_code_required"] is True
    async with make_client(client=("192.168.1.10", 40000)) as client:
        assert (await client.get("/api/auth/session")).json()["setup_code_required"] is False
        response = await client.post("/api/auth/setup", json=_setup_body())
        assert response.status_code == 200, response.text


@pytest.mark.parametrize(
    "address",
    ["127.0.0.1", "192.168.1.10", "10.1.2.3", "172.17.0.1", "100.64.0.7", "fd00::1", "::1"],
)
async def test_local_peers_set_up_without_a_code(address, caplog):
    await _fresh()
    with caplog.at_level(logging.WARNING, logger="app.auth.setup_code"):
        async with make_client(client=(address, 40000)) as client:
            session = (await client.get("/api/auth/session")).json()
            assert session["setup_code_required"] is False
            response = await client.post("/api/auth/setup", json=_setup_body())
            assert response.status_code == 200, response.text
    assert not any("installation code" in r.getMessage() for r in caplog.records)


async def test_forwarded_headers_from_an_undeclared_proxy_need_the_code():
    await _fresh()
    headers = {"X-Forwarded-For": "192.168.1.20"}
    async with make_client(client=("192.168.1.2", 40000)) as client:
        session = (await client.get("/api/auth/session", headers=headers)).json()
        assert session["setup_code_required"] is True
        refused = await client.post("/api/auth/setup", json=_setup_body(), headers=headers)
        assert refused.status_code == 403
        assert _code(refused) == "auth_setup_code_invalid"


@pytest.mark.parametrize(("forwarded", "required"), [("192.168.1.20", False), ("8.8.4.4", True)])
async def test_a_declared_proxy_is_judged_by_the_client_it_forwards(forwarded, required):
    await AuthSettingsRepository.update(trusted_proxies=["192.168.1.2/32"])
    await _fresh()
    headers = {"X-Forwarded-For": forwarded}
    async with make_client(client=("192.168.1.2", 40000)) as client:
        session = (await client.get("/api/auth/session", headers=headers)).json()
        assert session["setup_code_required"] is required
        response = await client.post("/api/auth/setup", json=_setup_body(), headers=headers)
        if required:
            assert response.status_code == 403
            assert _code(response) == "auth_setup_code_invalid"
        else:
            assert response.status_code == 200, response.text


async def test_a_declared_public_url_requires_the_code_from_the_lan_too():
    await AuthSettingsRepository.update(public_urls=["https://mesh.example.com"])
    await _fresh()
    async with make_client(client=("192.168.1.10", 40000)) as client:
        assert (await client.get("/api/auth/session")).json()["setup_code_required"] is True
        refused = await client.post("/api/auth/setup", json=_setup_body())
        assert refused.status_code == 403


async def test_a_cli_code_is_required_from_the_lan_even_after_an_exposure_code():
    await _fresh()
    async with make_client(client=PUBLIC) as client:
        await client.get("/api/auth/session")
    cli_code = await setup_code.issue_setup_code()
    async with make_client(client=("192.168.1.10", 40000)) as client:
        assert (await client.get("/api/auth/session")).json()["setup_code_required"] is True
        refused = await client.post("/api/auth/setup", json=_setup_body())
        assert refused.status_code == 403
        ok = await client.post("/api/auth/setup", json=_setup_body(setup_code=cli_code))
        assert ok.status_code == 200, ok.text


async def test_an_existing_database_without_a_code_stays_closed_to_public_peers(caplog):
    await setup_code.prepare_first_run(existing_database=True)
    await setup_code.clear_setup_code()
    caplog.clear()
    with caplog.at_level(logging.WARNING, logger="app.auth.setup_code"):
        async with make_client(client=PUBLIC) as client:
            assert (await client.get("/api/auth/session")).json()["setup_code_required"] is True
            response = await client.post("/api/auth/setup", json=_setup_body())
    assert response.status_code == 503
    assert not any("installation code" in r.getMessage() for r in caplog.records)
    assert not (await AuthSettingsRepository.get()).setup_code_set


async def test_a_relayed_request_is_exposed_even_from_loopback():
    """The relay connects from the host itself: its auth_via alone marks it."""
    scope = {"type": "http", "auth_via": "relay", "client": ("127.0.0.1", 1), "headers": []}
    assert await setup_code.request_exposed(scope) is True
    assert await setup_code.request_exposed({**scope, "auth_via": "lan"}) is False


@pytest.mark.parametrize(
    "client", ["127.0.0.1%x", "10.0.0.1%eth0", " 10.0.0.1", "::ffff:127.0.0.1%x"]
)
async def test_a_client_that_is_not_exactly_a_local_address_is_exposed(client):
    """MSHL-431: a trusted proxy's X-Forwarded-For lands in scope["client"] verbatim."""
    scope = {"type": "http", "auth_via": "lan", "client": (client, 1), "headers": []}
    assert await setup_code.request_exposed(scope) is True
    exact = client.strip().split("%")[0].removeprefix("::ffff:")
    assert await setup_code.request_exposed({**scope, "client": (exact, 1)}) is False


async def test_a_session_under_ingress_issues_no_code(monkeypatch, caplog):
    """Setup is refused under ingress: an anonymous session there must not issue
    a code nor log a warning about an Internet visitor."""
    monkeypatch.setenv("MESHLOOM_INSTALL_KIND", "addon")
    await _fresh()
    with caplog.at_level(logging.WARNING, logger="app.auth.setup_code"):
        async with make_client(client=("172.30.32.2", 40000)) as client:
            session = (await client.get("/api/auth/session")).json()
    assert session["setup_required"] is True
    assert session["setup_code_required"] is False
    assert not any("installation code" in r.getMessage() for r in caplog.records)
    assert not (await AuthSettingsRepository.get()).setup_code_set


async def test_an_ipv4_mapped_declared_proxy_forwarding_a_lan_client_needs_no_code():
    await AuthSettingsRepository.update(trusted_proxies=["192.168.1.2/32"])
    await _fresh()
    headers = {"X-Forwarded-For": "192.168.1.20"}
    async with make_client(client=("::ffff:192.168.1.2", 40000)) as client:
        session = (await client.get("/api/auth/session", headers=headers)).json()
        assert session["setup_code_required"] is False
        response = await client.post("/api/auth/setup", json=_setup_body(), headers=headers)
        assert response.status_code == 200, response.text


async def test_a_public_peer_needs_a_code_while_first_run_was_never_prepared():
    """Startup preparation failed (``_existing_database`` is None, no code stored):
    the session must still tell a public visitor that a code is required."""
    setup_code.reset_first_run_state()
    assert setup_code._existing_database is None
    assert not (await AuthSettingsRepository.get()).setup_code_set
    async with make_client(client=PUBLIC) as client:
        assert (await client.get("/api/auth/session")).json()["setup_code_required"] is True
    async with make_client(client=("192.168.1.10", 40000)) as client:
        assert (await client.get("/api/auth/session")).json()["setup_code_required"] is False
