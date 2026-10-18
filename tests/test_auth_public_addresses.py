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
    assert rows[0].target == "auth_settings.public_urls"


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
