"""Vague B: GET/PATCH /push/preferences and PUT conversation override."""

import pytest

from app.push.vapid import get_vapid_claims, set_cached_vapid_subject
from app.repository.settings import DEFAULT_PUSH_DEFAULTS


@pytest.fixture(autouse=True)
def _reset_vapid_subject_cache():
    set_cached_vapid_subject("")
    yield
    set_cached_vapid_subject("")


@pytest.mark.asyncio
async def test_get_preferences_defaults(test_db, client):
    response = await client.get("/api/push/preferences")
    assert response.status_code == 200
    data = response.json()
    assert data["defaults"] == DEFAULT_PUSH_DEFAULTS
    assert data["overrides"] == {}
    assert data["vapid_subject"] == ""


@pytest.mark.asyncio
async def test_patch_preferences_partial_defaults_and_vapid(test_db, client, monkeypatch):
    monkeypatch.setattr("app.config.settings.vapid_subject", "mailto:env@example.net")
    response = await client.patch(
        "/api/push/preferences",
        json={
            "defaults": {"new_dm": False},
            "vapid_subject": "mailto:ops@example.com",
        },
    )
    assert response.status_code == 200
    data = response.json()
    assert data["defaults"]["new_dm"] is False
    assert data["defaults"]["new_contact"] is True
    assert data["vapid_subject"] == "mailto:ops@example.com"
    assert get_vapid_claims() == {"sub": "mailto:ops@example.com"}

    cleared = await client.patch("/api/push/preferences", json={"vapid_subject": ""})
    assert cleared.status_code == 200
    assert cleared.json()["vapid_subject"] == ""
    assert get_vapid_claims() == {"sub": "mailto:env@example.net"}


@pytest.mark.asyncio
async def test_patch_preferences_rejects_invalid_vapid_subject(test_db, client):
    response = await client.patch(
        "/api/push/preferences",
        json={"vapid_subject": "ops@example.com"},
    )
    assert response.status_code == 400
    assert "mailto" in response.json()["detail"]


@pytest.mark.asyncio
async def test_patch_preferences_normalizes_https_instance_url(test_db, client):
    response = await client.patch(
        "/api/push/preferences",
        json={"vapid_subject": "https://radio.meshloom.app/"},
    )
    assert response.status_code == 200
    assert response.json()["vapid_subject"] == "https://radio.meshloom.app"
    assert get_vapid_claims() == {"sub": "https://radio.meshloom.app"}


@pytest.mark.asyncio
async def test_put_conversation_override_true_false_null(test_db, client):
    key = "contact-" + "aa" * 32
    enabled = await client.put(
        f"/api/push/preferences/conversations/{key}",
        json={"override": True},
    )
    assert enabled.status_code == 200
    assert enabled.json()["overrides"][key] is True

    disabled = await client.put(
        f"/api/push/preferences/conversations/{key}",
        json={"override": False},
    )
    assert disabled.json()["overrides"][key] is False

    cleared = await client.put(
        f"/api/push/preferences/conversations/{key}",
        json={"override": None},
    )
    assert key not in cleared.json()["overrides"]


@pytest.mark.asyncio
async def test_legacy_conversation_endpoints_are_gone(test_db, client):
    listed = await client.get("/api/push/conversations")
    assert listed.status_code == 404

    toggled = await client.post("/api/push/conversations/toggle", json={"key": "contact-aa"})
    assert toggled.status_code == 404
