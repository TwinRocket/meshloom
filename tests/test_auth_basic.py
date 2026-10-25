"""Authorization: Basic under load: verification cap, per-IP negative cache, cache invalidation."""

import asyncio
import threading

import pytest

from app import security
from app.auth.passwords import hash_password_async
from app.auth.providers import password as provider
from app.repository.auth import UserRepository
from tests.auth_helpers import ADMIN, PASSWORD, basic, create_user, make_client

pytestmark = [pytest.mark.real_auth, pytest.mark.usefixtures("test_db", "fast_argon2")]


def _code(response) -> str:
    return response.json()["detail"]["code"]


async def test_full_verification_queue_answers_503_at_once(monkeypatch):
    await create_user()
    release = threading.Event()
    real_verify = provider._verify_or_dummy

    def slow_verify(*args):
        release.wait(5)
        return real_verify(*args)

    monkeypatch.setattr(provider, "_verify_or_dummy", slow_verify)
    capacity = provider.MAX_CONCURRENT_VERIFICATIONS + provider.MAX_QUEUED_VERIFICATIONS
    async with make_client() as client:
        # Fill the running and waiting slots with checks that block.
        pending = [
            asyncio.create_task(provider.authenticate(ADMIN, f"wrong password {i}"))
            for i in range(capacity)
        ]
        await asyncio.sleep(0.05)
        busy = await client.get("/api/auth/me", headers=basic(ADMIN, "another wrong one"))
        assert busy.status_code == 503
        assert _code(busy) == "auth_busy"
        assert busy.headers["retry-after"] == "1"
        login = await client.post("/api/auth/login", json={"username": ADMIN, "password": PASSWORD})
        assert login.status_code == 503
        assert _code(login) == "auth_busy"
        release.set()
        assert await asyncio.gather(*pending) == [None] * capacity
        assert provider._pending == 0
        ok = await client.get("/api/auth/me", headers=basic(ADMIN, PASSWORD))
        assert ok.status_code == 200


async def test_failed_basic_from_an_ip_is_refused_without_argon2_for_a_few_seconds(monkeypatch):
    await create_user()
    calls = 0
    real_verify = provider.verify_password

    def counting(*args):
        nonlocal calls
        calls += 1
        return real_verify(*args)

    monkeypatch.setattr(provider, "verify_password", counting)
    async with make_client() as client, make_client(client=("10.0.0.9", 1)) as other:
        for _ in range(20):
            response = await client.get("/api/auth/me", headers=basic(ADMIN, "garbage password"))
            assert response.status_code == 401
        assert calls == 1
        # Another IP is not affected.
        assert (await other.get("/api/auth/me", headers=basic(ADMIN, PASSWORD))).status_code == 200
        # Once the cooldown is over, the IP is checked again.
        for ip in list(security._failed_ips):
            security._failed_ips[ip] = 0
        assert (await client.get("/api/auth/me", headers=basic(ADMIN, PASSWORD))).status_code == 200


def test_negative_cache_is_bounded(monkeypatch):
    monkeypatch.setattr(security, "BASIC_FAILED_IPS_MAX", 3)
    for i in range(10):
        security._note_failure(f"10.0.0.{i}", 0.0)
    assert len(security._failed_ips) == 3


async def test_cached_basic_check_ends_with_a_password_reset_in_the_same_second():
    user = await create_user()
    async with make_client() as client:
        assert (await client.get("/api/auth/me", headers=basic(ADMIN, PASSWORD))).status_code == 200
        # Same updated_at second as the check: only the hash changes (a CLI reset).
        await UserRepository.set_password_hash(
            user.id, await hash_password_async("an entirely new one"), now=user.updated_at
        )
        stale = await client.get("/api/auth/me", headers=basic(ADMIN, PASSWORD))
        assert stale.status_code == 401


async def test_basic_is_logged_once_as_deprecated(monkeypatch):
    messages: list[str] = []
    monkeypatch.setattr(security.logger, "warning", lambda msg, *a: messages.append(msg % a))
    await create_user()
    async with make_client() as client:
        for _ in range(3):
            response = await client.get("/api/auth/me", headers=basic(ADMIN, PASSWORD))
            assert response.status_code == 200
    assert [m for m in messages if "Authorization: Basic is deprecated" in m] != []
    assert len(messages) == 1
