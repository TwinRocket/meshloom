"""Vague B: GET/PATCH /push/preferences and PUT conversation override."""

import pytest

from app.push.vapid import get_vapid_claims, set_cached_vapid_subject
from app.repository import AppSettingsRepository
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
    monkeypatch.setenv("MESHCORE_VAPID_SUBJECT", "mailto:env@example.net")
    response = await client.patch(
        "/api/push/preferences",
        json={
            "defaults": {"new_dm": False},
            "vapid_subject": "mailto:ops@example.com",
        },
    )
    assert response.status_code == 200
    data = response.json()
    assert data["defaults"]["new_dm"] == {
        "push": False,
        "email": False,
        "webhook": False,
        "apprise": False,
    }
    assert data["defaults"]["new_contact"] == {
        "push": True,
        "email": False,
        "webhook": False,
        "apprise": False,
    }
    assert data["vapid_subject"] == "mailto:ops@example.com"
    assert get_vapid_claims() == {"sub": "mailto:ops@example.com"}

    cleared = await client.patch("/api/push/preferences", json={"vapid_subject": ""})
    assert cleared.status_code == 200
    assert cleared.json()["vapid_subject"] == ""
    # Cleared: the built-in default, never the retired environment variable.
    assert get_vapid_claims() == {"sub": "mailto:noreply@meshcore.local"}


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
    assert enabled.json()["overrides"][key] == {"push": True}

    disabled = await client.put(
        f"/api/push/preferences/conversations/{key}",
        json={"override": False},
    )
    assert disabled.json()["overrides"][key] == {"push": False}

    cleared = await client.put(
        f"/api/push/preferences/conversations/{key}",
        json={"override": None},
    )
    assert key not in cleared.json()["overrides"]


@pytest.mark.asyncio
async def test_put_conversation_override_merges_media(test_db, client):
    key = "contact-" + "bb" * 32
    first = await client.put(
        f"/api/push/preferences/conversations/{key}",
        json={"override": {"email": True}},
    )
    assert first.json()["overrides"][key] == {"email": True}
    second = await client.put(
        f"/api/push/preferences/conversations/{key}",
        json={"override": {"push": False}},
    )
    assert second.json()["overrides"][key] == {"email": True, "push": False}


@pytest.mark.asyncio
async def test_legacy_conversation_endpoints_are_gone(test_db, client):
    listed = await client.get("/api/push/conversations")
    assert listed.status_code == 404

    toggled = await client.post("/api/push/conversations/toggle", json={"key": "contact-aa"})
    assert toggled.status_code == 404


# ── POST /push/subscribe and the egress guard ────────────────────────────

_KEYS = {"p256dh": "p256", "auth": "auth"}


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "endpoint",
    ["http://192.168.1.10/up", "https://127.0.0.1:8443/push", "http://[fd00::5]/up"],
)
async def test_subscribe_private_endpoint_is_400_by_default(test_db, client, endpoint):
    response = await client.post("/api/push/subscribe", json={"endpoint": endpoint, **_KEYS})
    assert response.status_code == 400
    assert response.json()["detail"]["code"] == "egress_private_address"
    assert (await client.get("/api/push/subscriptions")).json() == []


@pytest.mark.asyncio
async def test_subscribe_private_endpoint_allowed_by_the_instance_setting(test_db, client):
    patch = await client.patch("/api/settings", json={"push_allow_private_endpoints": True})
    assert patch.status_code == 200
    assert patch.json()["push_allow_private_endpoints"] is True
    response = await client.post(
        "/api/push/subscribe", json={"endpoint": "http://192.168.1.10/up", **_KEYS}
    )
    assert response.status_code == 200
    # The metadata service stays refused whatever the setting.
    response = await client.post(
        "/api/push/subscribe", json={"endpoint": "http://169.254.169.254/latest", **_KEYS}
    )
    assert response.status_code == 400


@pytest.mark.asyncio
async def test_subscribe_public_endpoint_and_bad_scheme(test_db, client):
    ok = await client.post(
        "/api/push/subscribe", json={"endpoint": "https://93.184.215.14/push/abc", **_KEYS}
    )
    assert ok.status_code == 200
    bad = await client.post(
        "/api/push/subscribe", json={"endpoint": "ftp://93.184.215.14/x", **_KEYS}
    )
    assert bad.status_code == 400
    assert bad.json()["detail"]["code"] == "egress_scheme"


@pytest.mark.asyncio
async def test_push_allow_private_endpoints_defaults_off(test_db, client):
    assert (await client.get("/api/settings")).json()["push_allow_private_endpoints"] is False


@pytest.mark.asyncio
async def test_grandfathered_notice_is_dismissed_or_cleared_by_a_choice(test_db, client):
    await AppSettingsRepository.update(
        push_allow_private_endpoints=True, push_private_endpoints_grandfathered=True
    )
    settings = (await client.get("/api/settings")).json()
    assert settings["push_private_endpoints_grandfathered"] is True
    dismissed = await client.patch(
        "/api/settings", json={"push_private_endpoints_grandfathered": False}
    )
    assert dismissed.json()["push_private_endpoints_grandfathered"] is False
    assert dismissed.json()["push_allow_private_endpoints"] is True

    await AppSettingsRepository.update(push_private_endpoints_grandfathered=True)
    chosen = await client.patch("/api/settings", json={"push_allow_private_endpoints": True})
    assert chosen.json()["push_private_endpoints_grandfathered"] is False
