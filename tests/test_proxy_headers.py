"""Trusted proxies (app/middleware/proxy_headers.py): the one interpreter of
``X-Forwarded-For`` and ``X-Forwarded-Proto`` (MSHL-63)."""

import subprocess
from pathlib import Path

import httpx
import pytest

from app.auth.models import AuthSettings
from app.middleware import proxy_headers
from app.middleware.proxy_headers import (
    MAX_TRUSTED_PROXIES,
    TrustedProxyHeadersMiddleware,
    normalize_proxies,
    normalize_proxy,
    peer_address,
)
from app.repository.auth import AuthAuditRepository, AuthSettingsRepository
from tests.auth_helpers import ADMIN, PASSWORD, create_user, set_cookie_headers

REPO = Path(__file__).resolve().parents[1]


@pytest.fixture(autouse=True)
def _no_trusted_proxy():
    proxy_headers.reset()
    yield
    proxy_headers.reset()


def trust(*proxies: str) -> None:
    proxy_headers.apply(AuthSettings(trusted_proxies=list(proxies)))


async def run_scope(
    peer: str | None,
    headers: dict[str, str],
    *,
    scope_type: str = "http",
    scheme: str = "http",
) -> dict:
    """Run a crafted scope through the middleware; return the scope the app saw."""
    seen: dict = {}

    async def app(scope, receive, send):
        seen.update(scope)

    scope = {
        "type": scope_type,
        "scheme": scheme,
        "path": "/",
        "headers": [(k.lower().encode("latin-1"), v.encode("latin-1")) for k, v in headers.items()],
        "client": (peer, 50000) if peer else None,
    }

    async def receive():  # pragma: no cover - never called
        return {}

    async def send(message):  # pragma: no cover - never called
        pass

    await TrustedProxyHeadersMiddleware(app)(scope, receive, send)
    return seen


FORWARDED = {"X-Forwarded-For": "203.0.113.7", "X-Forwarded-Proto": "https"}


class TestUntrustedPeers:
    async def test_nothing_is_trusted_by_default_not_even_loopback(self):
        seen = await run_scope("127.0.0.1", FORWARDED)
        assert seen["client"] == ("127.0.0.1", 50000)
        assert seen["scheme"] == "http"

    async def test_loopback_is_ignored_when_not_listed(self):
        trust("10.0.0.0/8")
        seen = await run_scope("127.0.0.1", FORWARDED)
        assert seen["client"] == ("127.0.0.1", 50000)
        assert seen["scheme"] == "http"

    async def test_a_lan_client_cannot_forge_its_address(self):
        trust("127.0.0.1")
        seen = await run_scope("192.168.1.20", {**FORWARDED, "X-Forwarded-For": "172.30.32.2"})
        assert seen["client"] == ("192.168.1.20", 50000)
        assert seen["scheme"] == "http"

    async def test_websocket_scheme_is_kept(self):
        seen = await run_scope("127.0.0.1", FORWARDED, scope_type="websocket", scheme="ws")
        assert seen["scheme"] == "ws"


class TestTrustedPeers:
    async def test_the_forwarded_client_and_scheme_are_used(self):
        trust("127.0.0.1")
        seen = await run_scope("127.0.0.1", FORWARDED)
        assert seen["client"] == ("203.0.113.7", 0)
        assert seen["scheme"] == "https"

    async def test_the_rightmost_untrusted_address_is_used(self):
        """A client-written entry on the left never wins over what the proxies appended."""
        trust("10.0.0.0/8")
        seen = await run_scope("10.0.0.2", {"X-Forwarded-For": "6.6.6.6, 203.0.113.7, 10.0.0.9"})
        assert seen["client"] == ("203.0.113.7", 0)

    async def test_repeated_headers_are_joined_in_order(self):
        trust("10.0.0.0/8")
        seen = await run_scope("10.0.0.2", {"X-Forwarded-For": "6.6.6.6"})
        assert seen["client"] == ("6.6.6.6", 0)

    async def test_ipv6_proxy_and_client(self):
        trust("fd00:1::/64")
        seen = await run_scope("fd00:1::1", {"X-Forwarded-For": "[2001:db8::7]:4711"})
        assert seen["client"] == ("2001:db8::7", 4711)

    async def test_an_ipv4_entry_matches_its_mapped_peer(self):
        """A dual-stack listener (``--host ::``) sees IPv4 peers as ``::ffff:a.b.c.d``."""
        trust("10.0.0.2")
        seen = await run_scope("::ffff:10.0.0.2", {"X-Forwarded-For": "203.0.113.7"})
        assert seen["client"] == ("203.0.113.7", 0)
        other = await run_scope("::ffff:10.0.0.3", {"X-Forwarded-For": "203.0.113.7"})
        assert other["client"] == ("::ffff:10.0.0.3", 50000)

    async def test_websocket_becomes_wss(self):
        trust("127.0.0.1")
        seen = await run_scope("127.0.0.1", FORWARDED, scope_type="websocket", scheme="ws")
        assert seen["scheme"] == "wss"

    @pytest.mark.parametrize("proto", ["javascript", "https, http", "ftp", ""])
    async def test_an_unusable_proto_is_ignored(self, proto):
        trust("127.0.0.1")
        seen = await run_scope("127.0.0.1", {"X-Forwarded-Proto": proto})
        assert seen["scheme"] == "http"

    async def test_no_forwarded_for_keeps_the_peer(self):
        trust("127.0.0.1")
        seen = await run_scope("127.0.0.1", {"X-Forwarded-Proto": "https"})
        assert seen["client"] == ("127.0.0.1", 50000)
        assert seen["scheme"] == "https"

    async def test_the_tcp_peer_stays_readable(self):
        trust("127.0.0.1")
        seen = await run_scope("127.0.0.1", FORWARDED)
        assert peer_address(seen) == "127.0.0.1"
        assert seen["client"][0] == "203.0.113.7"

    async def test_lifespan_passes_through(self):
        seen: dict = {}

        async def app(scope, receive, send):
            seen.update(scope)

        await TrustedProxyHeadersMiddleware(app)({"type": "lifespan"}, None, None)  # type: ignore[arg-type]
        assert seen == {"type": "lifespan"}


class TestNormalisation:
    def test_addresses_become_networks(self):
        assert normalize_proxy(" 10.0.0.1 ") == "10.0.0.1/32"
        assert normalize_proxy("10.1.2.3/8") == "10.0.0.0/8"
        assert normalize_proxy("::1") == "::1/128"

    @pytest.mark.parametrize(
        "raw",
        [
            "0.0.0.0/0",
            "::/0",
            "0.0.0.0/1",
            "10.0.0.0/7",
            "::/1",
            "fd00::/8",
            "::ffff:10.0.0.1",
            "::ffff:0:0/96",
            "*",
            "proxy.lan",
            "10.0.0.0/33",
            "",
        ],
    )
    def test_refused(self, raw):
        with pytest.raises(ValueError):
            normalize_proxy(raw)

    def test_blanks_and_duplicates_dropped(self):
        assert normalize_proxies(["10.0.0.1", " ", "10.0.0.1/32", "::1"]) == [
            "10.0.0.1/32",
            "::1/128",
        ]

    def test_limit(self):
        many = [f"10.0.{i}.1" for i in range(MAX_TRUSTED_PROXIES + 1)]
        with pytest.raises(ValueError):
            normalize_proxies(many)

    def test_invalid_stored_entries_are_dropped(self):
        assert proxy_headers.valid_stored_proxies(["10.0.0.1", "0.0.0.0/0", "nope"]) == (
            "10.0.0.1/32",
        )


@pytest.mark.usefixtures("test_db")
class TestSetting:
    async def test_empty_by_default_and_applied_on_update(self):
        assert (await AuthSettingsRepository.get()).trusted_proxies == []
        await proxy_headers.load()
        assert proxy_headers.current() == ()
        await AuthSettingsRepository.update(trusted_proxies=["127.0.0.1"])
        assert proxy_headers.current() == ("127.0.0.1/32",)
        seen = await run_scope("127.0.0.1", FORWARDED)
        assert seen["client"][0] == "203.0.113.7"

    async def test_a_bad_stored_entry_is_skipped_on_read(self, test_db):
        async with test_db.tx() as conn:
            await conn.execute(
                "UPDATE auth_settings SET trusted_proxies = ? WHERE id = 1",
                ('["0.0.0.0/0", "nope", "10.0.0.1"]',),
            )
        assert (await AuthSettingsRepository.get()).trusted_proxies == ["10.0.0.1/32"]
        assert await proxy_headers.load() == ("10.0.0.1/32",)


def _client(app, base_url: str, peer: tuple[str, int]) -> httpx.AsyncClient:
    transport = httpx.ASGITransport(app=app, client=peer)
    return httpx.AsyncClient(transport=transport, base_url=base_url)


def test_registered_outside_the_gate_and_cors():
    from app.auth.middleware import AuthMiddleware
    from app.cors import AllowedOriginsCORSMiddleware
    from app.main import app

    classes = [m.cls for m in app.user_middleware]  # outermost first
    position = classes.index(TrustedProxyHeadersMiddleware)
    assert position < classes.index(AllowedOriginsCORSMiddleware)
    assert position < classes.index(AuthMiddleware)


async def test_a_second_instance_keeps_the_first_peer():
    trust("127.0.0.1")
    seen: dict = {}

    async def app(scope, receive, send):
        seen.update(scope)

    scope = {
        "type": "http",
        "scheme": "http",
        "headers": [(b"x-forwarded-for", b"203.0.113.7")],
        "client": ("127.0.0.1", 1),
    }
    await TrustedProxyHeadersMiddleware(TrustedProxyHeadersMiddleware(app))(scope, None, None)  # type: ignore[arg-type]
    assert seen["client"][0] == "203.0.113.7"
    assert peer_address(seen) == "127.0.0.1"


@pytest.mark.real_auth
@pytest.mark.usefixtures("test_db", "fast_argon2")
class TestThroughTheApp:
    """The real app, with the middleware app.main registers."""

    @pytest.mark.parametrize(
        ("trusted", "secure"), [((), False), (("10.0.0.0/8",), False), (("127.0.0.1",), True)]
    )
    async def test_cookie_flavour_follows_a_trusted_proto_only(self, trusted, secure):
        from app.main import app

        trust(*trusted)
        await create_user()
        async with _client(app, "http://test", ("127.0.0.1", 50000)) as client:
            response = await client.post(
                "/api/auth/login",
                json={"username": ADMIN, "password": PASSWORD},
                headers=FORWARDED,
            )
        assert response.status_code == 200
        [cookie] = set_cookie_headers(response)
        attributes = [part.strip().lower() for part in cookie.split(";")]
        assert cookie.startswith("__Host-meshloom_session=") is secure
        assert ("secure" in attributes) is secure

    async def test_audit_and_sessions_get_the_forwarded_client(self):
        from app.main import app

        trust("127.0.0.1")
        await create_user()
        async with _client(app, "http://test", ("127.0.0.1", 50000)) as client:
            response = await client.post(
                "/api/auth/login",
                json={"username": ADMIN, "password": PASSWORD},
                headers={"X-Forwarded-For": "6.6.6.6, 203.0.113.7"},
            )
            assert response.status_code == 200
            [session] = (await client.get("/api/auth/sessions")).json()
        assert session["ip"] == "203.0.113.7"
        rows = await AuthAuditRepository.list_recent(limit=5)
        assert rows[0].ip == "203.0.113.7"


@pytest.mark.usefixtures("test_db")
class TestSettingsRoute:
    """``GET/PATCH /api/auth/settings`` (admin; conftest signs tests in as one)."""

    async def test_read_and_write(self, client):
        assert (await client.get("/api/auth/settings")).json() == {
            "trusted_proxies": [],
            "audit_retention_days": 366,
            "ingress_default_role": "member",
            "trusted_networks": [],
            "security_notices": [],
        }
        response = await client.patch(
            "/api/auth/settings", json={"trusted_proxies": [" 10.0.0.1 ", "fd00:1::/64", ""]}
        )
        assert response.status_code == 200
        assert response.json()["trusted_proxies"] == ["10.0.0.1/32", "fd00:1::/64"]
        assert proxy_headers.current() == ("10.0.0.1/32", "fd00:1::/64")
        rows = await AuthAuditRepository.list_recent(limit=1)
        assert rows[0].action == "settings.changed"
        assert rows[0].target == "auth_settings.trusted_proxies"

    @pytest.mark.parametrize("value", [["0.0.0.0/0"], ["proxy.lan"], ["10.0.0.0/33"]])
    async def test_invalid_entries_are_refused(self, client, value):
        response = await client.patch("/api/auth/settings", json={"trusted_proxies": value})
        assert response.status_code == 400
        assert response.json()["detail"]["code"] == "auth_trusted_proxies_invalid"
        assert (await AuthSettingsRepository.get()).trusted_proxies == []

    async def test_member_is_refused(self, client, sign_in_as):
        sign_in_as("member")
        response = await client.patch("/api/auth/settings", json={"trusted_proxies": []})
        assert response.status_code == 403


# --- Launchers: uvicorn must never interpret the headers itself ---------------


def _tracked_files() -> list[str]:
    out = subprocess.run(
        ["git", "ls-files"], cwd=REPO, capture_output=True, text=True, check=True
    ).stdout
    return out.splitlines()


def test_every_documented_or_scripted_uvicorn_command_disables_proxy_headers():
    """Any ``uvicorn app.main:app`` command line in the repository (launchers,
    scripts, docs) carries ``--no-proxy-headers``. The four launchers are parsed
    by uvicorn itself in ``tests/test_auth_ingress.py``."""
    offenders = []
    for path in _tracked_files():
        # app/ holds no launcher (app/main.py only prints a Windows hint).
        if path.startswith(("tests/", "app/")) or path == "CHANGELOG.md":
            continue
        file = REPO / path
        if file.suffix not in {"", ".md", ".sh", ".py", ".service", ".yaml", ".yml"} and not (
            file.name.startswith("Dockerfile")
        ):
            continue
        try:
            text = file.read_text(encoding="utf-8")
        except (UnicodeDecodeError, IsADirectoryError, FileNotFoundError):
            continue
        for number, line in enumerate(text.splitlines(), 1):
            compact = line.replace('", "', " ").replace('"', "")
            if "uvicorn app.main:app" in compact and "--no-proxy-headers" not in line:
                offenders.append(f"{path}:{number}")
    assert offenders == []


@pytest.mark.real_auth
@pytest.mark.usefixtures("test_db", "fast_argon2")
@pytest.mark.parametrize(("base_url", "secure"), [("http://test", False), ("https://test", True)])
async def test_session_says_whether_the_server_saw_https(base_url, secure):
    """The frontend compares it with ``location.protocol`` to spot an untrusted proxy."""
    from app.main import app

    await create_user()
    async with _client(app, base_url, ("127.0.0.1", 50000)) as client:
        # An unlisted proxy's X-Forwarded-Proto changes nothing.
        body = (await client.get("/api/auth/session", headers=FORWARDED)).json()
    assert body["secure"] is secure
