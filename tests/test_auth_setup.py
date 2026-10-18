"""First-run setup (app/auth/setup_code.py, POST /api/auth/setup) and the Basic import."""

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
    await setup_code.prepare_first_run(
        existing_database=False, basic_username="", basic_password=""
    )
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
        await setup_code.prepare_first_run(
            existing_database=True, basic_username="", basic_password=""
        )
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
    await setup_code.prepare_first_run(existing_database=True, basic_username="", basic_password="")
    assert await AuthSettingsRepository.get_setup_code_hash() not in (None, first_hash)
    await UserRepository.create(ADMIN, "admin")
    await setup_code.prepare_first_run(existing_database=True, basic_username="", basic_password="")
    assert not (await AuthSettingsRepository.get()).setup_code_set


async def test_setup_applies_the_password_policy():
    await setup_code.prepare_first_run(
        existing_database=False, basic_username="", basic_password=""
    )
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


async def test_basic_credentials_are_imported_once_without_code_or_policy(caplog):
    with caplog.at_level(logging.WARNING):
        await setup_code.prepare_first_run(
            existing_database=True, basic_username="mesh", basic_password="secret"
        )
    [user] = await UserRepository.list_all()
    assert (user.username, user.role, user.has_password) == ("mesh", "admin", True)
    settings = await AuthSettingsRepository.get()
    assert settings.basic_imported and not settings.setup_code_set
    assert "secret" not in caplog.text
    assert any("retired" in r.getMessage() for r in caplog.records)

    # The next start does not import again, even with other values; it only warns.
    caplog.clear()
    with caplog.at_level(logging.WARNING):
        await setup_code.prepare_first_run(
            existing_database=True, basic_username="other", basic_password="changed"
        )
    assert [u.username for u in await UserRepository.list_all()] == ["mesh"]
    assert any("retired" in r.getMessage() for r in caplog.records)

    # Existing scripts keep working with the header, browsers with the challenge.
    async with make_client() as client:
        assert (
            await client.get("/api/contacts", headers=basic("mesh", "secret"))
        ).status_code == 200
        anonymous = await client.get("/api/contacts")
        assert anonymous.status_code == 401
        assert anonymous.headers["www-authenticate"].startswith("Basic ")
        # And the imported account can sign in to a session too.
        login = await client.post(
            "/api/auth/login", json={"username": "mesh", "password": "secret"}
        )
        assert login.status_code == 200


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
        await setup_code.prepare_first_run(
            existing_database=False, basic_username="", basic_password=""
        )
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
    await setup_code.prepare_first_run(existing_database=True, basic_username="", basic_password="")
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
    await setup_code.prepare_first_run(
        existing_database=False, basic_username="", basic_password=""
    )
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
        await setup_code.prepare_first_run(
            existing_database=True, basic_username="", basic_password=""
        )
    monkeypatch.setattr(setup_code, "_prepare", real_prepare)
    cli_code = await setup_code.issue_setup_code()
    async with make_client() as client:
        response = await client.post("/api/auth/setup", json=_setup_body(setup_code=cli_code))
        assert response.status_code == 200, response.text


async def test_the_setup_retry_issues_a_code_when_none_is_stored(monkeypatch):
    real_prepare = setup_code._prepare
    monkeypatch.setattr(setup_code, "_prepare", _always_fail)
    with pytest.raises(RuntimeError):
        await setup_code.prepare_first_run(
            existing_database=True, basic_username="", basic_password=""
        )
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
