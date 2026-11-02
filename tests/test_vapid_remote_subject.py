"""MSHL-446: the Meshloom Remote address is the VAPID subject when none is typed.

Precedence: the subject typed in Settings → Notifications, then ``https://<slug>.meshloom.link``
while Remote is on and registered, then the built-in default. The Remote part is derived
(never written to ``app_settings.vapid_subject``) and recomputed on every Remote change.
"""

from __future__ import annotations

import pytest

from app.auth.audit import SYSTEM_ACTOR
from app.push import vapid
from app.push.vapid import (
    DEFAULT_VAPID_SUBJECT,
    ensure_vapid_keys,
    get_vapid_claims,
    remote_vapid_subject,
    resolve_vapid_subject_with_source,
    set_cached_remote_vapid_subject,
    set_cached_vapid_subject,
)
from app.repository.remote import RemoteRepository
from app.repository.settings import AppSettingsRepository
from app.services import remote
from tests.test_remote_service import SLUG, SLUG2, relay  # noqa: F401  (fixture)

HOST = f"{SLUG}.meshloom.link"


@pytest.fixture(autouse=True)
def _reset_subject_cache():
    set_cached_vapid_subject("")
    set_cached_remote_vapid_subject("")
    yield
    set_cached_vapid_subject("")
    set_cached_remote_vapid_subject("")


def test_precedence_stored_then_remote_then_default():
    remote_sub = f"https://{HOST}"
    assert resolve_vapid_subject_with_source("mailto:me@example.org", remote_sub) == (
        "mailto:me@example.org",
        "stored",
    )
    assert resolve_vapid_subject_with_source("", remote_sub) == (remote_sub, "remote")
    assert resolve_vapid_subject_with_source("https://", remote_sub) == (remote_sub, "remote")
    assert resolve_vapid_subject_with_source("", "") == (DEFAULT_VAPID_SUBJECT, "default")


def test_remote_subject_needs_remote_on_and_a_hostname():
    assert remote_vapid_subject(True, HOST) == f"https://{HOST}"
    assert remote_vapid_subject(True, f" {HOST.upper()} ") == f"https://{HOST}"
    assert remote_vapid_subject(False, HOST) == ""
    assert remote_vapid_subject(True, "") == ""
    assert remote_vapid_subject(True, "bad host/path") == ""


def test_claims_use_the_cached_remote_subject_unless_one_is_typed():
    from py_vapid.__init__ import _check_sub

    set_cached_remote_vapid_subject(f"https://{HOST}")
    claims = get_vapid_claims()
    assert claims == {"sub": f"https://{HOST}"}
    assert _check_sub(claims["sub"]) is True
    set_cached_vapid_subject("mailto:me@example.org")
    assert get_vapid_claims() == {"sub": "mailto:me@example.org"}


@pytest.mark.asyncio
async def test_without_remote_behaviour_is_unchanged(test_db):
    await ensure_vapid_keys()
    assert get_vapid_claims() == {"sub": DEFAULT_VAPID_SUBJECT}


@pytest.mark.asyncio
async def test_startup_reads_an_already_enabled_remote(test_db):
    await RemoteRepository.update_state(enabled=True, slug=SLUG, hostname=HOST)
    await ensure_vapid_keys()
    assert get_vapid_claims() == {"sub": f"https://{HOST}"}
    # Derived only: nothing is written to app_settings.
    assert await AppSettingsRepository.get_vapid_subject() == ""


@pytest.mark.asyncio
async def test_recomputed_on_enable_disable_rotate_unregister(relay):  # noqa: F811
    from app.repository.auth import UserRepository

    await UserRepository.create("test-admin", "admin")
    await ensure_vapid_keys()
    assert get_vapid_claims() == {"sub": DEFAULT_VAPID_SUBJECT}

    await remote.enable(SYSTEM_ACTOR)
    assert get_vapid_claims() == {"sub": f"https://{HOST}"}

    await remote.disable(SYSTEM_ACTOR)
    assert get_vapid_claims() == {"sub": DEFAULT_VAPID_SUBJECT}

    await remote.enable(SYSTEM_ACTOR)
    await remote.rotate(SYSTEM_ACTOR)
    assert get_vapid_claims() == {"sub": f"https://{SLUG2}.meshloom.link"}

    await remote.unregister(SYSTEM_ACTOR)
    assert get_vapid_claims() == {"sub": DEFAULT_VAPID_SUBJECT}
    assert await AppSettingsRepository.get_vapid_subject() == ""


@pytest.mark.asyncio
async def test_typed_subject_wins_over_remote(relay):  # noqa: F811
    await AppSettingsRepository.set_vapid_subject("mailto:me@example.org")
    await ensure_vapid_keys()
    await remote.enable(SYSTEM_ACTOR)
    assert get_vapid_claims() == {"sub": "mailto:me@example.org"}


@pytest.mark.asyncio
async def test_unreadable_remote_state_falls_back(test_db, monkeypatch):
    async def boom():
        raise RuntimeError("no table")

    set_cached_remote_vapid_subject(f"https://{HOST}")
    monkeypatch.setattr(RemoteRepository, "get_state", staticmethod(boom))
    assert await vapid.refresh_remote_vapid_subject() == ""
    assert get_vapid_claims() == {"sub": DEFAULT_VAPID_SUBJECT}


@pytest.mark.asyncio
async def test_preferences_report_the_subject_in_use_and_its_source(test_db, client):
    data = (await client.get("/api/push/preferences")).json()
    assert data["vapid_subject"] == ""
    assert data["vapid_subject_effective"] == DEFAULT_VAPID_SUBJECT
    assert data["vapid_subject_source"] == "default"

    set_cached_remote_vapid_subject(f"https://{HOST}")
    data = (await client.get("/api/push/preferences")).json()
    assert data["vapid_subject"] == ""
    assert data["vapid_subject_effective"] == f"https://{HOST}"
    assert data["vapid_subject_source"] == "remote"

    data = (
        await client.patch("/api/push/preferences", json={"vapid_subject": "mailto:me@example.org"})
    ).json()
    assert data["vapid_subject_effective"] == "mailto:me@example.org"
    assert data["vapid_subject_source"] == "stored"
