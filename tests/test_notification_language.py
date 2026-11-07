"""``app_settings.notification_language``: default, PATCH, and the e-mail language."""

import pytest
from pydantic import ValidationError

from app.email_template import resolve_instance_language
from app.repository import AppSettingsRepository
from app.repository.push_subscriptions import PushSubscriptionRepository
from app.routers.settings import AppSettingsUpdate, update_settings
from app.services.backup import export_json


async def _subscribe(language: str, n: int, start: int = 0) -> None:
    for i in range(start, start + n):
        await PushSubscriptionRepository.create(
            endpoint=f"https://push.example/{language}-{i}",
            p256dh=f"p{language}{i}",
            auth=f"a{language}{i}",
            language=language,
        )


@pytest.mark.asyncio
async def test_default_is_auto(test_db):
    assert (await AppSettingsRepository.get()).notification_language == "auto"


@pytest.mark.asyncio
async def test_patch_stores_and_rejects_unknown_values(test_db, client):
    result = await update_settings(AppSettingsUpdate(notification_language="en"))
    assert result.notification_language == "en"
    assert (await AppSettingsRepository.get()).notification_language == "en"
    with pytest.raises(ValidationError):
        AppSettingsUpdate(notification_language="de")  # type: ignore[arg-type]
    assert (
        await client.patch("/api/settings", json={"notification_language": "de"})
    ).status_code == 422
    ok = await client.patch("/api/settings", json={"notification_language": "fr"})
    assert ok.status_code == 200 and ok.json()["notification_language"] == "fr"
    assert (await client.get("/api/settings")).json()["notification_language"] == "fr"


@pytest.mark.asyncio
async def test_a_bad_stored_value_reads_as_auto(test_db):
    async with test_db.tx() as conn:
        await conn.execute("UPDATE app_settings SET notification_language = 'xx'")
    assert (await AppSettingsRepository.get()).notification_language == "auto"


@pytest.mark.asyncio
async def test_english_forces_english_with_french_subscriptions(test_db):
    await _subscribe("fr", 3)
    assert await resolve_instance_language() == "fr"
    await AppSettingsRepository.update(notification_language="en")
    assert await resolve_instance_language() == "en"


@pytest.mark.asyncio
async def test_french_forces_french_with_english_subscriptions(test_db):
    await _subscribe("en", 3)
    assert await resolve_instance_language() == "en"
    await AppSettingsRepository.update(notification_language="fr")
    assert await resolve_instance_language() == "fr"


@pytest.mark.asyncio
async def test_auto_is_a_strict_majority_and_a_tie_is_french(test_db):
    await _subscribe("en", 2)
    await _subscribe("fr", 2)
    assert await resolve_instance_language() == "fr"
    await _subscribe("en", 1, start=2)
    assert await resolve_instance_language() == "en"


@pytest.mark.asyncio
async def test_the_language_travels_in_a_backup(test_db):
    await AppSettingsRepository.update(notification_language="en")
    export = await export_json()
    assert export.settings is not None
    assert export.settings.notification_language == "en"


@pytest.mark.asyncio
@pytest.mark.parametrize("role", ["member", "viewer", "kiosk"])
async def test_non_admin_cannot_change_notification_language(test_db, client, sign_in_as, role):
    sign_in_as(role)
    response = await client.patch("/api/settings", json={"notification_language": "en"})
    assert response.status_code == 403
    assert (await AppSettingsRepository.get()).notification_language == "auto"


@pytest.mark.asyncio
async def test_unreadable_settings_fall_back_with_a_logged_warning(test_db, monkeypatch, caplog):
    async def boom():
        raise RuntimeError("db gone")

    monkeypatch.setattr(AppSettingsRepository, "get", boom)
    monkeypatch.setattr(PushSubscriptionRepository, "get_all", boom)
    with caplog.at_level("WARNING", logger="app.email_template"):
        assert await resolve_instance_language() == "fr"
    assert "notification_language unreadable" in caplog.text
    assert "push subscriptions unreadable" in caplog.text
