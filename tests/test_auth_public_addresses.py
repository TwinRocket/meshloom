"""Public addresses and the Host guard mode through ``PATCH /api/auth/settings`` (MSHL-65)."""

from __future__ import annotations

import contextlib
from contextlib import asynccontextmanager

import pytest

from app.middleware import host_guard
from app.repository.auth import (
    AuthAuditRepository,
    AuthSettingsRepository,
    UsernameTakenError,
)
from tests.auth_helpers import create_user, login, make_client

pytestmark = [pytest.mark.real_auth, pytest.mark.usefixtures("test_db", "fast_argon2")]

LAN_URL = "http://192.168.1.10:8000"
PEER = ("10.9.9.9", 40000)


@pytest.fixture(autouse=True)
def _guard_state(monkeypatch):
    monkeypatch.setenv("MESHLOOM_INSTALL_KIND", "package")
    host_guard.reset()
    yield
    host_guard.reset()


@asynccontextmanager
async def _admin(base_url: str = LAN_URL):
    with contextlib.suppress(UsernameTakenError):
        await create_user()
    async with make_client(base_url, PEER) as client:
        await login(client)
        yield client


def _code(response) -> str:
    return response.json()["detail"]["code"]


async def test_public_urls_are_saved_canonical_and_applied():
    async with _admin() as client:
        response = await client.patch(
            "/api/auth/settings",
            json={"public_urls": ["https://Mesh.Example.com:443/", "https://mesh.example.com"]},
        )
        assert response.status_code == 200, response.text
        assert response.json()["public_urls"] == ["https://mesh.example.com"]
        assert (await client.get("/api/auth/settings")).json()["public_urls"] == [
            "https://mesh.example.com"
        ]
    assert (await AuthSettingsRepository.get()).public_urls == ["https://mesh.example.com"]
    assert host_guard.host_allowed("mesh.example.com", ["https://mesh.example.com"])
    rows = [r for r in await AuthAuditRepository.list_recent() if r.action == "settings.changed"]
    assert {r.target for r in rows[:2]} == {
        "auth_settings.public_urls",
        "auth_settings.host_guard_mode",
    }


async def test_the_first_public_address_switches_the_guard_to_enforce():
    async with _admin() as client:
        response = await client.patch(
            "/api/auth/settings", json={"public_urls": ["https://mesh.example.com"]}
        )
        assert response.status_code == 200, response.text
        assert response.json()["host_guard_mode"] == "enforce"
    assert (await AuthSettingsRepository.get()).host_guard_mode == "enforce"
    assert host_guard.mode() == "enforce"
    mode_rows = [
        r
        for r in await AuthAuditRepository.list_recent()
        if r.target == "auth_settings.host_guard_mode"
    ]
    assert mode_rows[0].details == {"host_guard_mode": "enforce", "automatic": True}
    async with make_client("http://attacker.example", PEER) as outside:
        refused = await outside.get("/api/auth/session")
        assert refused.status_code == 421


async def test_no_public_address_keeps_observation():
    async with _admin() as client:
        response = await client.patch("/api/auth/settings", json={"session_lifetime_days": 7})
        assert response.status_code == 200, response.text
        assert response.json()["host_guard_mode"] == "observe"
        assert (await client.patch("/api/auth/settings", json={"public_urls": []})).json()[
            "host_guard_mode"
        ] == "observe"
    async with make_client("http://attacker.example", PEER) as outside:
        assert (await outside.get("/api/auth/session")).status_code == 200


async def test_an_explicit_mode_in_the_same_request_wins():
    async with _admin() as client:
        response = await client.patch(
            "/api/auth/settings",
            json={"public_urls": ["https://mesh.example.com"], "host_guard_mode": "observe"},
        )
        assert response.status_code == 200, response.text
        assert response.json()["host_guard_mode"] == "observe"


async def test_observe_chosen_after_the_first_address_is_kept_on_the_next_one():
    async with _admin() as client:
        await client.patch("/api/auth/settings", json={"public_urls": ["https://a.example.com"]})
        await client.patch("/api/auth/settings", json={"host_guard_mode": "observe"})
        response = await client.patch(
            "/api/auth/settings",
            json={"public_urls": ["https://a.example.com", "https://b.example.com"]},
        )
        assert response.json()["host_guard_mode"] == "observe"


async def test_the_switch_is_skipped_when_it_would_reject_the_address_in_use():
    async with _admin("http://proxy.example.com") as client:
        response = await client.patch(
            "/api/auth/settings", json={"public_urls": ["https://other.example.com"]}
        )
        assert response.status_code == 200, response.text
        assert response.json()["host_guard_mode"] == "observe"


@pytest.mark.parametrize(
    "value",
    [
        ["http://mesh.example.com"],
        ["mesh.example.com"],
        ["https://user@mesh.example.com"],
        ["https://mesh.example.com/?x=1"],
        ["https://mesh_example.com"],
        [f"https://h{i}.example.com" for i in range(host_guard.MAX_PUBLIC_URLS + 1)],
    ],
)
async def test_invalid_public_urls_are_refused_with_a_code(value):
    async with _admin() as client:
        response = await client.patch("/api/auth/settings", json={"public_urls": value})
        assert response.status_code == 400
        assert _code(response) == "auth_public_urls_invalid"
    assert (await AuthSettingsRepository.get()).public_urls == []


async def test_a_declared_host_passes_enforce_and_removing_it_rejects_it_again():
    async with _admin() as client:
        response = await client.patch(
            "/api/auth/settings",
            json={"public_urls": ["https://mesh.example.com"], "host_guard_mode": "enforce"},
        )
        assert response.status_code == 200, response.text
        assert response.json()["host_guard_mode"] == "enforce"
    async with make_client("http://mesh.example.com", PEER) as outside:
        assert (await outside.get("/api/auth/session")).status_code == 200
    async with _admin() as client:
        assert (
            await client.patch("/api/auth/settings", json={"public_urls": []})
        ).status_code == 200
    async with make_client("http://mesh.example.com", PEER) as outside:
        refused = await outside.get("/api/auth/session")
        assert refused.status_code == 421
        assert _code(refused) == "auth_host_rejected"


async def test_enforce_is_refused_when_it_would_lock_out_the_current_host():
    async with _admin("http://mesh.example.com") as client:
        response = await client.patch("/api/auth/settings", json={"host_guard_mode": "enforce"})
        assert response.status_code == 400
        assert _code(response) == "auth_host_guard_would_lock_out"
        assert response.json()["detail"]["params"] == {"host": "mesh.example.com"}
        assert (await AuthSettingsRepository.get()).host_guard_mode == "observe"
        # Declared in the same request: accepted.
        ok = await client.patch(
            "/api/auth/settings",
            json={"host_guard_mode": "enforce", "public_urls": ["https://mesh.example.com"]},
        )
        assert ok.status_code == 200, ok.text
        # Already enforcing: removing the host it is reached by is refused too.
        refused = await client.patch("/api/auth/settings", json={"public_urls": []})
        assert refused.status_code == 400
        assert _code(refused) == "auth_host_guard_would_lock_out"
        assert (await AuthSettingsRepository.get()).public_urls == ["https://mesh.example.com"]
        # Going back to observe is always possible.
        back = await client.patch("/api/auth/settings", json={"host_guard_mode": "observe"})
        assert back.status_code == 200


async def test_allowing_an_observed_host_declares_it_and_clears_it_from_the_list():
    await create_user()
    async with make_client("http://proxy.example.com", PEER) as seen:
        await login(seen)
        before = (await seen.get("/api/auth/settings")).json()
        assert [h["host"] for h in before["rejected_hosts"]] == ["proxy.example.com"]
        response = await seen.patch(
            "/api/auth/settings", json={"public_urls": ["https://proxy.example.com"]}
        )
        assert response.status_code == 200, response.text
        assert response.json()["rejected_hosts"] == []
        assert (await seen.get("/api/auth/settings")).json()["rejected_hosts"] == []


async def test_only_an_admin_changes_the_public_addresses():
    await create_user()
    from app.auth.passwords import hash_password_async
    from app.repository.auth import UserRepository
    from tests.auth_helpers import PASSWORD

    await UserRepository.create("bob", "member", password_hash=await hash_password_async(PASSWORD))
    async with make_client(LAN_URL, PEER) as client:
        await login(client, "bob")
        response = await client.patch(
            "/api/auth/settings", json={"public_urls": ["https://mesh.example.com"]}
        )
        assert response.status_code == 403
    assert (await AuthSettingsRepository.get()).public_urls == []


# --- HTTPS from the internet (MSHL-437) -------------------------------------------

PUBLIC_PEER = ("203.0.113.9", 40000)


@pytest.fixture
def _https_state():
    from app.middleware import https_required, proxy_headers

    proxy_headers.reset()
    https_required.reset()
    yield
    proxy_headers.reset()
    https_required.reset()


@pytest.mark.usefixtures("_https_state")
async def test_a_public_plain_http_admin_cannot_declare_a_public_address():
    """Direct exposure over plain http: saving would refuse every later request."""
    await create_user()
    async with make_client("http://mesh.example.com", PUBLIC_PEER) as remote:
        await login(remote)
        response = await remote.patch(
            "/api/auth/settings", json={"public_urls": ["https://mesh.example.com"]}
        )
        assert response.status_code == 400
        assert _code(response) == "auth_https_would_lock_out"
        stored = await AuthSettingsRepository.get()
        assert stored.public_urls == []
        assert stored.host_guard_mode == "observe"
        # Still reachable: nothing was saved.
        assert (await remote.get("/api/auth/settings")).status_code == 200
        assert (await remote.get("/")).status_code != 403


@pytest.mark.usefixtures("_https_state")
async def test_a_public_admin_over_https_and_a_lan_admin_can_declare_one():
    await create_user()
    async with make_client("https://mesh.example.com", PUBLIC_PEER) as remote:
        await login(remote)
        response = await remote.patch(
            "/api/auth/settings", json={"public_urls": ["https://mesh.example.com"]}
        )
        assert response.status_code == 200, response.text
        assert (await remote.get("/api/auth/settings")).status_code == 200
        # Removing every address is never refused by this rule.
        cleared = await remote.patch(
            "/api/auth/settings", json={"public_urls": [], "host_guard_mode": "observe"}
        )
        assert cleared.status_code == 200, cleared.text
    async with _admin("http://mesh.example.com") as lan:
        response = await lan.patch(
            "/api/auth/settings", json={"public_urls": ["https://mesh.example.com"]}
        )
        assert response.status_code == 200, response.text
        assert (await lan.get("/api/auth/settings")).status_code == 200


@pytest.mark.usefixtures("_https_state")
async def test_the_shipped_app_refuses_plain_http_from_a_public_peer():
    """End to end through ``app.main``: the middleware is registered and applied."""
    async with _admin("http://mesh.example.com") as lan:
        response = await lan.patch(
            "/api/auth/settings",
            json={"public_urls": ["https://mesh.example.com"], "trusted_proxies": ["10.0.0.5"]},
        )
        assert response.status_code == 200, response.text
    async with make_client("http://mesh.example.com", PUBLIC_PEER) as outside:
        refused = await outside.get("/api/auth/session")
        assert refused.status_code == 403
        assert _code(refused) == "auth_https_required"
        page = await outside.get("/")
        assert page.status_code == 403
    async with make_client("https://mesh.example.com", PUBLIC_PEER) as secure:
        assert (await secure.get("/api/auth/session")).status_code == 200
    async with make_client("http://mesh.example.com", PEER) as lan_peer:
        assert (await lan_peer.get("/api/auth/session")).status_code == 200
    # A listed proxy's X-Forwarded-Proto is read before the HTTPS rule (ordering).
    proxy_peer = ("10.0.0.5", 40000)
    forwarded = {"X-Forwarded-For": "203.0.113.9"}
    async with make_client("http://mesh.example.com", proxy_peer) as proxy:
        over_https = await proxy.get(
            "/api/auth/session", headers={**forwarded, "X-Forwarded-Proto": "https"}
        )
        assert over_https.status_code == 200
        over_http = await proxy.get(
            "/api/auth/session", headers={**forwarded, "X-Forwarded-Proto": "http"}
        )
        assert over_http.status_code == 403
        assert _code(over_http) == "auth_https_required"
