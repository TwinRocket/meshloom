"""First-run setup (app/auth/first_run.py, POST /api/auth/setup); no Basic import (MSHL-100).

The 4.x Basic gate, the public IP literal ``Host`` and the health version while setup
is pending are in ``tests/test_first_run_hardening.py``."""

import logging

import pytest

from app.auth import first_run
from app.middleware import proxy_headers
from app.repository.auth import AuthAuditRepository, AuthSettingsRepository, UserRepository
from app.services import install_kind
from tests.auth_helpers import ADMIN, PASSWORD, basic, make_client, set_cookie_headers

pytestmark = [pytest.mark.real_auth, pytest.mark.usefixtures("test_db", "fast_argon2")]

PUBLIC = ("8.8.8.8", 40000)
LAN = ("192.168.1.10", 40000)


def _code(response) -> str:
    return response.json()["detail"]["code"]


def _setup_body(**extra):
    return {"username": ADMIN, "password": PASSWORD, **extra}


async def _prepared() -> None:
    await first_run.prepare_first_run()


async def _refused(client, **kwargs) -> None:
    response = await client.post("/api/auth/setup", json=_setup_body(), **kwargs)
    assert response.status_code == 403
    assert _code(response) == "auth_setup_local_only"
    assert set_cookie_headers(response) == []
    assert not await UserRepository.any_exist()


async def test_setup_from_the_lan_works_once_without_any_code():
    await _prepared()
    async with make_client() as client:
        session = (await client.get("/api/auth/session")).json()
        assert session["setup_required"] is True
        assert session["setup_local_only"] is False

        response = await client.post("/api/auth/setup", json=_setup_body(display_name="Owner"))
        assert response.status_code == 200, response.text
        assert response.json()["user"]["role"] == "admin"
        assert set_cookie_headers(response)[0].startswith("meshloom_session=")
        assert (await client.get("/api/auth/me")).json()["display_name"] == "Owner"

        again = await client.post("/api/auth/setup", json=_setup_body(username="other"))
        assert again.status_code == 409
        assert _code(again) == "auth_setup_done"
    assert [user.username for user in await UserRepository.list_all()] == [ADMIN]


async def test_an_existing_database_without_an_account_opens_from_the_lan(test_db):
    """A 4.x upgrade: its data is there, no account yet, and nobody fetches a code."""
    async with test_db.tx() as conn:
        await conn.execute(
            "INSERT INTO contacts (public_key, name) VALUES (?, 'old contact')", ("ab" * 32,)
        )
    await _prepared()
    async with make_client(client=LAN) as client:
        session = (await client.get("/api/auth/session")).json()
        assert session["setup_required"] is True
        assert session["setup_local_only"] is False
        response = await client.post("/api/auth/setup", json=_setup_body())
        assert response.status_code == 200, response.text


async def test_a_stray_setup_code_in_the_body_is_harmless():
    """A browser still running the previous page sends one."""
    await _prepared()
    async with make_client(client=LAN) as client:
        response = await client.post("/api/auth/setup", json=_setup_body(setup_code="12345678"))
        assert response.status_code == 200, response.text


async def test_a_stray_setup_code_does_not_open_setup_when_exposed():
    await _prepared()
    async with make_client(client=PUBLIC) as client:
        response = await client.post("/api/auth/setup", json=_setup_body(setup_code="12345678"))
    assert response.status_code == 403
    assert _code(response) == "auth_setup_local_only"


async def test_setup_applies_the_password_policy():
    await _prepared()
    async with make_client() as client:
        short = await client.post("/api/auth/setup", json=_setup_body(password="short"))
        assert short.status_code == 400
        assert short.json()["detail"]["code"] == "auth_password_policy"
        assert short.json()["detail"]["params"] == {"reason": "too_short", "min_length": 10}

        same = await client.post(
            "/api/auth/setup", json={"username": "LongUsername12", "password": "longusername12"}
        )
        assert same.status_code == 400
        assert same.json()["detail"]["params"]["reason"] == "same_as_username"
    assert await UserRepository.list_all() == []


async def test_retired_basic_variables_import_nothing_and_only_warn(caplog, monkeypatch):
    """MSHL-100: no account comes from MESHCORE_BASIC_AUTH_*, and the header signs nobody in."""
    from app.services.legacy_env_settings import warn_about_retired_env

    monkeypatch.setenv("MESHCORE_BASIC_AUTH_USERNAME", "mesh")
    monkeypatch.setenv("MESHCORE_BASIC_AUTH_PASSWORD", "secret")
    with caplog.at_level(logging.WARNING):
        await _prepared()
        warn_about_retired_env()
    assert await UserRepository.list_all() == []
    assert "secret" not in caplog.text
    assert any("HTTP Basic auth is removed" in r.getMessage() for r in caplog.records)
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


# --- Fail closed ------------------------------------------------------------------


async def _always_fail(*args):
    raise RuntimeError("database is locked")


async def test_setup_is_refused_until_first_run_preparation_succeeded(monkeypatch):
    real_prepare = first_run._prepare
    monkeypatch.setattr(first_run, "_prepare", _always_fail)
    with pytest.raises(RuntimeError):
        await first_run.prepare_first_run()
    async with make_client() as client:
        # The setup route retries once; while it keeps failing, 503 and nothing created.
        refused = await client.post("/api/auth/setup", json=_setup_body())
        assert refused.status_code == 503
        assert _code(refused) == "auth_setup_unavailable"
        assert await UserRepository.list_all() == []

        # Once a retry succeeds, setup opens.
        monkeypatch.setattr(first_run, "_prepare", real_prepare)
        response = await client.post("/api/auth/setup", json=_setup_body())
        assert response.status_code == 200, response.text


async def test_setup_is_refused_when_never_prepared():
    async with make_client() as client:
        response = await client.post("/api/auth/setup", json=_setup_body())
    assert response.status_code == 503
    assert _code(response) == "auth_setup_unavailable"


async def test_an_exposed_request_gets_the_503_first_while_preparation_fails(monkeypatch):
    monkeypatch.setattr(first_run, "_prepare", _always_fail)
    with pytest.raises(RuntimeError):
        await first_run.prepare_first_run()
    async with make_client(client=PUBLIC) as client:
        response = await client.post("/api/auth/setup", json=_setup_body())
    assert response.status_code == 503
    assert not await UserRepository.any_exist()


async def test_preparation_stays_good_when_only_the_kiosk_account_fails(monkeypatch):
    from app.services import kiosk

    monkeypatch.setattr(kiosk, "ensure_kiosk_user", _always_fail)
    await _prepared()
    async with make_client() as client:
        response = await client.post("/api/auth/setup", json=_setup_body())
        assert response.status_code == 200, response.text


# --- Ingress ----------------------------------------------------------------------


async def test_setup_through_ingress_is_refused_as_ingress(monkeypatch):
    """Home Assistant creates the administrator there (tests/test_auth_ingress.py):
    ``auth_ingress_only`` wins, whatever the request looks like."""
    monkeypatch.setenv("MESHLOOM_INSTALL_KIND", "addon")
    await _prepared()
    async with make_client(client=("172.30.32.2", 40000)) as client:
        session = (await client.get("/api/auth/session")).json()
        assert session["setup_required"] is True
        assert session["setup_local_only"] is False
        assert "setup_local_url" not in session
        response = await client.post("/api/auth/setup", json=_setup_body())
        assert response.status_code == 403
        assert _code(response) == "auth_ingress_only"
        assert set_cookie_headers(response) == []
        proxied = await client.post(
            "/api/auth/setup", json=_setup_body(), headers={"Host": "attacker.example"}
        )
        assert _code(proxied) == "auth_ingress_only"
    assert await UserRepository.list_all() == []


# --- Exposed first run (MSHL-424) --------------------------------------------------


@pytest.fixture(autouse=True)
def _app_imported():
    """Importing app.main configures logging, which replaces the root handlers."""
    import app.main  # noqa: F401


async def test_a_public_peer_is_refused():
    await _prepared()
    async with make_client(client=PUBLIC) as client:
        assert (await client.get("/api/auth/session")).json()["setup_local_only"] is True
        await _refused(client)


async def test_a_refusal_is_audited_once_a_minute_and_writes_no_auth_fail_line(caplog):
    await _prepared()
    with caplog.at_level(logging.INFO):
        async with make_client(client=PUBLIC) as client:
            await _refused(client)
            await _refused(client)
    rows = [e for e in await AuthAuditRepository.list_recent() if e.action == "setup"]
    assert [(row.outcome, row.details) for row in rows] == [
        ("failure", {"reason": "setup_local_only"})
    ]
    assert "AUTH_FAIL" not in caplog.text


def test_the_refusal_audit_window():
    assert first_run.refusal_audit_due(now=100.0) is True
    assert first_run.refusal_audit_due(now=100.0 + first_run.REFUSAL_AUDIT_INTERVAL_S - 1) is False
    assert first_run.refusal_audit_due(now=100.0 + first_run.REFUSAL_AUDIT_INTERVAL_S) is True


async def test_the_lan_owner_is_not_blocked_after_an_internet_visitor():
    await _prepared()
    async with make_client(client=PUBLIC) as client:
        await _refused(client)
    async with make_client(client=LAN) as client:
        assert (await client.get("/api/auth/session")).json()["setup_local_only"] is False
        response = await client.post("/api/auth/setup", json=_setup_body())
        assert response.status_code == 200, response.text


@pytest.mark.parametrize(
    "address",
    ["127.0.0.1", "192.168.1.10", "10.1.2.3", "172.17.0.1", "100.64.0.7", "fd00::1", "::1"],
)
async def test_local_peers_set_up(address):
    await _prepared()
    async with make_client(client=(address, 40000)) as client:
        session = (await client.get("/api/auth/session")).json()
        assert session["setup_local_only"] is False
        response = await client.post("/api/auth/setup", json=_setup_body())
        assert response.status_code == 200, response.text


async def test_forwarded_headers_from_an_undeclared_proxy_are_refused():
    await _prepared()
    headers = {"X-Forwarded-For": "192.168.1.20"}
    async with make_client(client=("192.168.1.2", 40000)) as client:
        session = (await client.get("/api/auth/session", headers=headers)).json()
        assert session["setup_local_only"] is True
        await _refused(client, headers=headers)


@pytest.mark.parametrize(("forwarded", "exposed"), [("192.168.1.20", False), ("8.8.4.4", True)])
async def test_a_declared_proxy_is_judged_by_the_client_it_forwards(forwarded, exposed):
    await AuthSettingsRepository.update(trusted_proxies=["192.168.1.2/32"])
    await _prepared()
    headers = {"X-Forwarded-For": forwarded}
    async with make_client(client=("192.168.1.2", 40000)) as client:
        session = (await client.get("/api/auth/session", headers=headers)).json()
        assert session["setup_local_only"] is exposed
        if exposed:
            await _refused(client, headers=headers)
        else:
            response = await client.post("/api/auth/setup", json=_setup_body(), headers=headers)
            assert response.status_code == 200, response.text


async def test_a_declared_public_url_refuses_the_lan_too():
    await AuthSettingsRepository.update(public_urls=["https://mesh.example.com"])
    await _prepared()
    async with make_client(client=LAN) as client:
        assert (await client.get("/api/auth/session")).json()["setup_local_only"] is True
        await _refused(client)


async def test_a_relayed_request_is_exposed_even_from_loopback():
    """The relay connects from the host itself: its auth_via alone marks it."""
    scope = {
        "type": "http",
        "auth_via": "relay",
        "client": ("127.0.0.1", 1),
        "headers": [(b"host", b"127.0.0.1:8000")],
    }
    assert await first_run.request_exposed(scope) is True
    assert await first_run.request_exposed({**scope, "auth_via": "lan"}) is False


@pytest.mark.parametrize(
    "client", ["127.0.0.1%x", "10.0.0.1%eth0", " 10.0.0.1", "::ffff:127.0.0.1%x"]
)
async def test_a_client_that_is_not_exactly_a_local_address_is_exposed(client):
    """MSHL-431: a trusted proxy's X-Forwarded-For lands in scope["client"] verbatim."""
    scope = {
        "type": "http",
        "auth_via": "lan",
        "client": (client, 1),
        "headers": [(b"host", b"192.168.1.5:8000")],
    }
    assert await first_run.request_exposed(scope) is True
    exact = client.strip().split("%")[0].removeprefix("::ffff:")
    assert await first_run.request_exposed({**scope, "client": (exact, 1)}) is False


async def test_an_ipv4_mapped_declared_proxy_forwarding_a_lan_client_opens():
    await AuthSettingsRepository.update(trusted_proxies=["192.168.1.2/32"])
    await _prepared()
    headers = {"X-Forwarded-For": "192.168.1.20"}
    async with make_client(client=("::ffff:192.168.1.2", 40000)) as client:
        session = (await client.get("/api/auth/session", headers=headers)).json()
        assert session["setup_local_only"] is False
        response = await client.post("/api/auth/setup", json=_setup_body(), headers=headers)
        assert response.status_code == 200, response.text


async def test_a_public_peer_is_told_while_first_run_was_never_prepared():
    async with make_client(client=PUBLIC) as client:
        assert (await client.get("/api/auth/session")).json()["setup_local_only"] is True
    async with make_client(client=LAN) as client:
        assert (await client.get("/api/auth/session")).json()["setup_local_only"] is False


# ---------------------------------------------------------------- the Host of a first-run request


@pytest.mark.parametrize(
    "host",
    [
        "attacker.example",
        "attacker.example:8000",
        "attacker.example.",
        "192.168.1.5.nip.io",
        "meshloom.my-home.example.org",
    ],
)
async def test_a_name_that_may_be_public_is_refused_even_from_the_lan(host):
    """A public DNS name may point at this instance for one page's lifetime."""
    await _prepared()
    headers = {"Host": host, "Origin": f"http://{host}", "Sec-Fetch-Site": "same-origin"}
    async with make_client(client=LAN) as client:
        session = (await client.get("/api/auth/session", headers=headers)).json()
        assert session["setup_local_only"] is True
        await _refused(client, headers=headers)


@pytest.mark.parametrize(
    "host",
    [
        "192.168.1.5",
        "192.168.1.5:8000",
        "[fd00::5]:8000",
        "localhost:8000",
        "meshloom.local",
        "meshloom.lan:8000",
        "meshloom.home.arpa",
        "meshloom.internal",
        "mypi",  # a bare name: Docker's host name is not the machine's
        "mypi:8000",
    ],
)
async def test_lan_names_set_up(host):
    await _prepared()
    headers = {"Host": host}
    async with make_client(client=LAN) as client:
        session = (await client.get("/api/auth/session", headers=headers)).json()
        assert session["setup_local_only"] is False
        done = await client.post("/api/auth/setup", json=_setup_body(), headers=headers)
        assert done.status_code == 200, done.text


async def test_a_public_name_does_not_lock_out_the_owner_on_the_lan_address():
    await _prepared()
    async with make_client(client=LAN) as client:
        foreign = await client.get("/api/auth/session", headers={"Host": "attacker.example"})
        assert foreign.json()["setup_local_only"] is True
        owner = {"Host": "192.168.1.5:8000"}
        session = (await client.get("/api/auth/session", headers=owner)).json()
        assert session["setup_local_only"] is False
        done = await client.post("/api/auth/setup", json=_setup_body(), headers=owner)
        assert done.status_code == 200, done.text


async def test_a_request_without_a_host_header_is_exposed():
    scope = {"type": "http", "auth_via": "lan", "client": ("192.168.1.10", 1), "headers": []}
    assert await first_run.request_exposed(scope) is True


# --- The local address offered to a refused visitor ---------------------------------


@pytest.fixture
def on_a_lan(monkeypatch):
    """A machine outside any container, 192.168.1.5 on its default route, listening."""

    async def listening(address, port):
        return True

    monkeypatch.setattr(install_kind, "in_container", lambda: False)
    monkeypatch.setattr(first_run, "_primary_private_ipv4", lambda: "192.168.1.5")
    monkeypatch.setattr(first_run, "_accepts_connections", listening)


def _scope(**extra):
    return {
        "type": "http",
        "client": ("192.168.1.10", 1),
        "server": ("192.168.1.5", 8000),
        "scheme": "http",
        "state": {proxy_headers.PEER_STATE_KEY: "192.168.1.10"},
        "headers": [],
        **extra,
    }


@pytest.mark.usefixtures("on_a_lan")
async def test_the_session_offers_the_local_address_to_an_exposed_visitor():
    """A LAN visitor under a public name; an Internet visitor is never told it."""
    await _prepared()
    async with make_client(client=LAN, base_url="http://mesh.example.com:8000") as client:
        session = (await client.get("/api/auth/session")).json()
    assert session["setup_local_only"] is True
    assert session["setup_local_url"] == "http://192.168.1.5:8000"


@pytest.mark.usefixtures("on_a_lan")
async def test_no_local_address_for_a_request_that_is_not_exposed():
    await _prepared()
    async with make_client(client=LAN) as client:
        session = (await client.get("/api/auth/session")).json()
    assert session["setup_local_only"] is False
    assert "setup_local_url" not in session


@pytest.mark.usefixtures("on_a_lan")
async def test_no_local_address_once_an_account_exists():
    await _prepared()
    await UserRepository.create(ADMIN, "admin")
    async with make_client(client=PUBLIC) as client:
        session = (await client.get("/api/auth/session")).json()
    assert session["setup_required"] is False
    assert session["setup_local_only"] is False
    assert "setup_local_url" not in session


@pytest.mark.usefixtures("on_a_lan")
async def test_a_kiosk_account_alone_still_offers_it():
    await _prepared()
    await UserRepository.create("kiosk", "kiosk", provider="system")
    async with make_client(client=LAN, base_url="http://mesh.example.com:8000") as client:
        session = (await client.get("/api/auth/session")).json()
    assert session["setup_required"] is True
    assert session["setup_local_url"].startswith("http://192.168.1.5:")


@pytest.mark.usefixtures("on_a_lan")
async def test_no_local_address_in_a_container(monkeypatch):
    monkeypatch.setattr(install_kind, "in_container", lambda: True)
    assert await first_run.local_setup_url(_scope()) is None
    await _prepared()
    async with make_client(client=PUBLIC) as client:
        session = (await client.get("/api/auth/session")).json()
    assert session["setup_local_only"] is True
    assert "setup_local_url" not in session


@pytest.mark.parametrize("kind", ["compose", "container", "addon"])
def test_a_declared_container_kind_counts_as_a_container(monkeypatch, kind):
    monkeypatch.setenv("MESHLOOM_INSTALL_KIND", kind)
    assert install_kind.in_container() is True


def test_a_package_install_is_not_a_container(monkeypatch):
    monkeypatch.setenv("MESHLOOM_INSTALL_KIND", "package")
    monkeypatch.setattr(install_kind, "_in_container", lambda: False)
    assert install_kind.in_container() is False


@pytest.mark.usefixtures("on_a_lan")
async def test_the_local_address_is_built_from_the_real_port_and_scheme():
    assert await first_run.local_setup_url(_scope()) == "http://192.168.1.5:8000"
    assert await first_run.local_setup_url(_scope(server=("127.0.0.1", 9000))) == (
        "http://192.168.1.5:9000"
    )
    assert await first_run.local_setup_url(_scope(scheme="https")) == "https://192.168.1.5:8000"


@pytest.mark.usefixtures("on_a_lan")
@pytest.mark.parametrize(
    "client", ["192.168.1.20", "192.168.1.2"]
)  # with and without X-Forwarded-For
async def test_a_trusted_proxys_scheme_says_nothing_about_the_lan_address(monkeypatch, client):
    monkeypatch.setattr(proxy_headers, "current", lambda: ["192.168.1.2/32"])
    scope = _scope(
        scheme="https",
        client=(client, 1),
        state={proxy_headers.PEER_STATE_KEY: "192.168.1.2"},
    )
    assert await first_run.local_setup_url(scope) == "http://192.168.1.5:8000"


@pytest.mark.usefixtures("on_a_lan")
async def test_no_local_address_through_the_relay_or_without_a_port():
    assert await first_run.local_setup_url(_scope(auth_via="relay")) is None
    assert await first_run.local_setup_url(_scope(server=("/run/x.sock", None))) is None
    scope = _scope()
    del scope["server"]
    assert await first_run.local_setup_url(scope) is None


@pytest.mark.usefixtures("on_a_lan")
async def test_no_local_address_on_any_failure(monkeypatch):
    def boom():
        raise OSError("no route")

    monkeypatch.setattr(first_run, "_primary_private_ipv4", boom)
    assert await first_run.local_setup_url(_scope()) is None


@pytest.mark.usefixtures("on_a_lan")
async def test_no_local_address_when_nothing_listens_there(monkeypatch):
    async def refused(address, port):
        raise ConnectionRefusedError

    monkeypatch.setattr(first_run, "_accepts_connections", refused)
    assert await first_run.local_setup_url(_scope()) is None


@pytest.mark.parametrize(
    ("route", "expected"),
    [
        ("192.168.1.5", "192.168.1.5"),
        ("10.0.0.7", "10.0.0.7"),
        ("172.16.0.2", "172.16.0.2"),
        ("8.8.8.8", None),
        ("100.64.0.7", None),
        ("169.254.0.3", None),
        ("127.0.0.1", None),
    ],
)
def test_only_a_private_address_is_offered(monkeypatch, route, expected):
    class FakeSocket:
        def __init__(self, *args):
            pass

        def connect(self, target):
            pass

        def getsockname(self):
            return (route, 1234)

        def close(self):
            pass

    monkeypatch.setattr(first_run.socket, "socket", FakeSocket)
    assert first_run._primary_private_ipv4() == expected


async def test_the_listening_check_answers_for_a_real_socket():
    import asyncio

    async def handler(reader, writer):
        writer.close()

    server = await asyncio.start_server(handler, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    try:
        assert await first_run._accepts_connections("127.0.0.1", port) is True
    finally:
        server.close()
        await server.wait_closed()
    with pytest.raises(OSError):
        await first_run._accepts_connections("127.0.0.1", port)
