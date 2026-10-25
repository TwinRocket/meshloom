"""The session lifetime an admin sets through ``PATCH /api/auth/settings`` (MSHL-318)."""

from __future__ import annotations

import pytest

from app.auth import sessions
from app.auth.models import MAX_SESSION_LIFETIME_DAYS, MIN_SESSION_LIFETIME_DAYS
from app.repository.auth import AuthAuditRepository, AuthSessionRepository, AuthSettingsRepository
from tests.auth_helpers import create_user, login, make_client

pytestmark = [pytest.mark.real_auth, pytest.mark.usefixtures("test_db", "fast_argon2")]

DAY = 86400


async def test_the_default_is_30_days_and_is_readable_by_an_admin():
    await create_user()
    async with make_client() as client:
        await login(client)
        assert (await client.get("/api/auth/settings")).json()["session_lifetime_days"] == 30


async def test_an_admin_changes_it_and_the_change_is_audited():
    await create_user()
    async with make_client() as client:
        await login(client)
        response = await client.patch("/api/auth/settings", json={"session_lifetime_days": 7})
        assert response.status_code == 200, response.text
        assert response.json()["session_lifetime_days"] == 7
        assert (await client.get("/api/auth/settings")).json()["session_lifetime_days"] == 7
    assert (await AuthSettingsRepository.get()).session_lifetime_days == 7
    rows = [r for r in await AuthAuditRepository.list_recent() if r.action == "settings.changed"]
    assert rows[0].target == "auth_settings.session_lifetime_days"
    assert rows[0].details == {"lifetime_days": 7}


@pytest.mark.parametrize(
    "days", [0, -1, MIN_SESSION_LIFETIME_DAYS - 1, MAX_SESSION_LIFETIME_DAYS + 1, "x"]
)
async def test_the_value_is_bounded(days):
    await create_user()
    async with make_client() as client:
        await login(client)
        response = await client.patch("/api/auth/settings", json={"session_lifetime_days": days})
        assert response.status_code == 422
    assert (await AuthSettingsRepository.get()).session_lifetime_days == 30


@pytest.mark.parametrize("days", [MIN_SESSION_LIFETIME_DAYS, MAX_SESSION_LIFETIME_DAYS])
async def test_the_bounds_themselves_are_accepted(days):
    await create_user()
    async with make_client() as client:
        await login(client)
        response = await client.patch("/api/auth/settings", json={"session_lifetime_days": days})
        assert response.status_code == 200, response.text
    assert (await AuthSettingsRepository.get()).session_lifetime_days == days


@pytest.mark.parametrize("role", ["member", "viewer"])
async def test_a_non_admin_cannot_read_or_change_it(role):
    await create_user()
    await create_user("other", role=role)
    async with make_client() as client:
        await login(client, "other")
        assert (await client.get("/api/auth/settings")).status_code == 403
        response = await client.patch("/api/auth/settings", json={"session_lifetime_days": 365})
        assert response.status_code == 403
    assert (await AuthSettingsRepository.get()).session_lifetime_days == 30


async def test_new_sessions_use_the_new_lifetime():
    await create_user()
    async with make_client() as admin:
        await login(admin)
        await admin.patch("/api/auth/settings", json={"session_lifetime_days": 3})
        before = {s.id for s in await AuthSessionRepository.list_for_user(1)}
    async with make_client() as long_client, make_client() as short_client:
        await login(long_client)
        await login(short_client, remember=False)
    created = [s for s in await AuthSessionRepository.list_for_user(1) if s.id not in before]
    by_remember = {s.remember: s for s in created}
    assert set(by_remember) == {True, False}
    assert by_remember[True].expires_at - by_remember[True].created_at == 3 * DAY
    # Without "remember me" the 12 h cap still applies.
    assert (
        by_remember[False].expires_at - by_remember[False].created_at
        == sessions.BROWSER_SESSION_LIFETIME_S
    )


async def test_an_existing_session_takes_the_new_lifetime_at_its_next_slide(test_db):
    from tests.conftest import test_db as _unused  # noqa: F401  (fixture module import guard)

    await create_user()
    async with make_client() as client:
        await login(client)
        [session] = await AuthSessionRepository.list_for_user(1)
        assert session.expires_at - session.created_at == 30 * DAY
        await client.patch("/api/auth/settings", json={"session_lifetime_days": 2})
        # Untouched until it is used again: the stored expiry does not move by itself.
        [same] = await AuthSessionRepository.list_for_user(1)
        assert same.expires_at == session.expires_at
        async with test_db.tx() as conn:
            await conn.execute(
                "UPDATE sessions SET last_seen_at = last_seen_at - 120 WHERE id = ?", (session.id,)
            )
        assert await sessions.slide_session(session.id)
        [slid] = await AuthSessionRepository.list_for_user(1)
        assert slid.expires_at - slid.last_seen_at == 2 * DAY
