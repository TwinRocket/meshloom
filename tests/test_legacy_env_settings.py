"""One-shot import of the retired MESHCORE_* settings (VAPID contact, radio workarounds)."""

import logging

import pytest

from app import radio_workarounds
from app.radio import radio_manager
from app.repository.settings import AppSettingsRepository
from app.services.legacy_env_settings import (
    RETIRED_ENV,
    import_legacy_env_settings,
    warn_about_retired_env,
)

WORKAROUND_ENV = {
    "MESHCORE_ENABLE_MESSAGE_POLL_FALLBACK": "enable_message_poll_fallback",
    "MESHCORE_FORCE_CHANNEL_SLOT_RECONFIGURE": "force_channel_slot_reconfigure",
    "MESHCORE_LOAD_WITH_AUTOEVICT": "load_with_autoevict",
}


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    for name in RETIRED_ENV:
        monkeypatch.delenv(name, raising=False)


@pytest.mark.asyncio
async def test_true_env_values_are_imported_once_then_ignored(test_db, monkeypatch):
    for name in WORKAROUND_ENV:
        monkeypatch.setenv(name, "true")
    monkeypatch.setenv("MESHCORE_VAPID_SUBJECT", "  mailto:ops@example.org ")

    imported = await import_legacy_env_settings()

    assert imported == {
        "vapid_subject": "mailto:ops@example.org",
        "enable_message_poll_fallback": True,
        "force_channel_slot_reconfigure": True,
        "load_with_autoevict": True,
    }
    stored = await AppSettingsRepository.get()
    for column in WORKAROUND_ENV.values():
        assert getattr(stored, column) is True
    assert await AppSettingsRepository.get_vapid_subject() == "mailto:ops@example.org"
    assert await AppSettingsRepository.get_legacy_env_settings_imported() is True

    # The user turns a workaround off and clears the contact in the UI; the env
    # is still set, but a restart must not bring the old values back.
    await AppSettingsRepository.update(load_with_autoevict=False)
    await AppSettingsRepository.set_vapid_subject("")
    monkeypatch.setenv("MESHCORE_VAPID_SUBJECT", "mailto:other@example.org")

    assert await import_legacy_env_settings() == {}
    assert (await AppSettingsRepository.get()).load_with_autoevict is False
    assert await AppSettingsRepository.get_vapid_subject() == ""


@pytest.mark.asyncio
async def test_false_or_garbage_values_import_nothing(test_db, monkeypatch):
    monkeypatch.setenv("MESHCORE_ENABLE_MESSAGE_POLL_FALLBACK", "false")
    monkeypatch.setenv("MESHCORE_LOAD_WITH_AUTOEVICT", "maybe")
    monkeypatch.setenv("MESHCORE_VAPID_SUBJECT", "ops@example.org")  # not mailto:

    assert await import_legacy_env_settings() == {}
    stored = await AppSettingsRepository.get()
    assert stored.enable_message_poll_fallback is False
    assert stored.load_with_autoevict is False
    assert await AppSettingsRepository.get_vapid_subject() == ""
    assert await AppSettingsRepository.get_legacy_env_settings_imported() is True


@pytest.mark.asyncio
async def test_a_contact_already_in_the_database_is_not_overwritten(test_db, monkeypatch):
    await AppSettingsRepository.set_vapid_subject("mailto:ui@example.org")
    monkeypatch.setenv("MESHCORE_VAPID_SUBJECT", "mailto:env@example.org")

    imported = await import_legacy_env_settings()

    assert "vapid_subject" not in imported
    assert await AppSettingsRepository.get_vapid_subject() == "mailto:ui@example.org"


@pytest.mark.asyncio
async def test_https_subject_is_normalized_before_import(test_db, monkeypatch):
    monkeypatch.setenv("MESHCORE_VAPID_SUBJECT", "https://radio.example.org/")
    await import_legacy_env_settings()
    assert await AppSettingsRepository.get_vapid_subject() == "https://radio.example.org"


@pytest.mark.asyncio
async def test_without_env_the_marker_is_still_set(test_db):
    assert await import_legacy_env_settings() == {}
    assert await AppSettingsRepository.get_legacy_env_settings_imported() is True


@pytest.mark.asyncio
async def test_a_variable_still_set_after_the_import_is_logged(test_db, monkeypatch, caplog):
    await import_legacy_env_settings()
    monkeypatch.setenv("MESHCORE_FORCE_CHANNEL_SLOT_RECONFIGURE", "true")
    with caplog.at_level(logging.WARNING, logger="app.services.legacy_env_settings"):
        assert await import_legacy_env_settings() == {}
    assert "MESHCORE_FORCE_CHANNEL_SLOT_RECONFIGURE is set but no longer read" in caplog.text
    assert (await AppSettingsRepository.get()).force_channel_slot_reconfigure is False


def test_warn_lists_only_the_variables_that_are_set(monkeypatch):
    monkeypatch.setenv("MESHCORE_VAPID_SUBJECT", "")
    assert warn_about_retired_env() == ["MESHCORE_VAPID_SUBJECT"]


@pytest.mark.asyncio
async def test_the_imported_workarounds_reach_the_radio_code(test_db, monkeypatch):
    """Startup imports, then loads the in-memory copy the radio code reads."""
    monkeypatch.setenv("MESHCORE_FORCE_CHANNEL_SLOT_RECONFIGURE", "1")
    await import_legacy_env_settings()
    await radio_workarounds.load()
    assert radio_workarounds.current().force_channel_slot_reconfigure is True
    assert radio_manager.channel_slot_reuse_enabled() is False
