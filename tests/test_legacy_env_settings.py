"""One-shot import of the retired MESHCORE_* settings (VAPID contact, radio workarounds)."""

import logging

import pytest

from app import radio_workarounds
from app.radio import radio_manager
from app.repository.settings import AppSettingsRepository
from app.services.legacy_env_settings import (
    RETIRED_ENV,
    import_legacy_community_iata,
    import_legacy_env_settings,
    warn_about_retired_env,
)
from app.services.meshloom_community import (
    DEFAULT_API_BASE,
    DEFAULT_BROKER_HOST,
    get_community_effective,
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


@pytest.mark.asyncio
async def test_clock_wraparound_variable_is_warned_about_never_imported(
    test_db, monkeypatch, caplog
):
    await AppSettingsRepository.update(auto_clock_wraparound=False)
    monkeypatch.setenv("__CLOWNTOWN_DO_CLOCK_WRAPAROUND", "true")
    with caplog.at_level(logging.WARNING, logger="app.services.legacy_env_settings"):
        assert await import_legacy_env_settings() == {}
    assert "__CLOWNTOWN_DO_CLOCK_WRAPAROUND is set but no longer read" in caplog.text
    assert "Settings → Radio → Advanced" in caplog.text
    # The user's choice in the UI wins: the variable does not switch it back on.
    assert (await AppSettingsRepository.get()).auto_clock_wraparound is False


def test_warn_lists_only_the_variables_that_are_set(monkeypatch):
    monkeypatch.setenv("MESHCORE_VAPID_SUBJECT", "mailto:x@example.org")
    monkeypatch.setenv("MESHCORE_LOAD_WITH_AUTOEVICT", "   ")  # blank: not a leftover
    assert warn_about_retired_env() == ["MESHCORE_VAPID_SUBJECT"]


def test_private_key_export_switch_is_retired(monkeypatch, caplog):
    from app.config import Settings

    monkeypatch.setenv("MESHCORE_ENABLE_LOCAL_PRIVATE_KEY_EXPORT", "true")
    with caplog.at_level(logging.WARNING, logger="app.services.legacy_env_settings"):
        assert warn_about_retired_env() == ["MESHCORE_ENABLE_LOCAL_PRIVATE_KEY_EXPORT"]
    assert caplog.messages == [
        "MESHCORE_ENABLE_LOCAL_PRIVATE_KEY_EXPORT is set but no longer read: this setting "
        "lives in no setting at all (GET /api/radio/private-key and the full backup in "
        "Settings → Database always include the key). Remove the variable from your "
        "environment."
    ]
    assert "enable_local_private_key_export" not in Settings.model_fields


@pytest.mark.asyncio
async def test_the_imported_workarounds_reach_the_radio_code(test_db, monkeypatch):
    """Startup imports, then loads the in-memory copy the radio code reads."""
    monkeypatch.setenv("MESHCORE_FORCE_CHANNEL_SLOT_RECONFIGURE", "1")
    await import_legacy_env_settings()
    await radio_workarounds.load()
    assert radio_workarounds.current().force_channel_slot_reconfigure is True
    assert radio_manager.channel_slot_reuse_enabled() is False


class TestLegacyCommunityEnv:
    """The retired MESHLOOM_COMMUNITY* variables: imported once, then only warned about."""

    @pytest.mark.asyncio
    async def test_new_database_starts_off_and_ignores_env(self, test_db, monkeypatch):
        monkeypatch.setenv("MESHLOOM_COMMUNITY", "1")
        monkeypatch.setenv("MESHLOOM_COMMUNITY_IATA", "CDG")
        await import_legacy_community_iata(existing_database=False)
        state = await get_community_effective()
        assert state.enabled is False  # Community is an explicit opt-in in the UI
        assert state.iata == ""
        from app.repository.settings import AppSettingsRepository

        assert await AppSettingsRepository.get_community_env_imported() is True

    @pytest.mark.asyncio
    async def test_new_database_without_env_starts_off(self, test_db, monkeypatch):
        for name in ("MESHLOOM_COMMUNITY", "MESHLOOM_COMMUNITY_IATA"):
            monkeypatch.delenv(name, raising=False)
        await import_legacy_community_iata(existing_database=False)
        assert (await get_community_effective()).enabled is False

    @pytest.mark.asyncio
    async def test_existing_database_imports_the_iata_once(self, test_db, monkeypatch, caplog):
        from app.repository.settings import AppSettingsRepository

        await AppSettingsRepository.update_community(enabled=True, iata="")
        monkeypatch.setenv("MESHLOOM_COMMUNITY", "0")  # only ever seeded a new database
        monkeypatch.setenv("MESHLOOM_COMMUNITY_IATA", "lys")
        assert await import_legacy_community_iata(existing_database=True) == "LYS"
        state = await get_community_effective()
        assert state.enabled is True  # the stored opt-in is kept
        assert state.iata == "LYS"

        # Then never read again: a later value (or a UI change) is not overwritten.
        await AppSettingsRepository.update_community(iata="CDG")
        monkeypatch.setenv("MESHLOOM_COMMUNITY_IATA", "BOD")
        await import_legacy_community_iata(existing_database=True)
        assert (await get_community_effective()).iata == "CDG"

    @pytest.mark.asyncio
    async def test_existing_database_env_iata_replaces_the_stored_one(self, test_db, monkeypatch):
        """The env IATA overrode the stored one on every read: keep what was in effect."""
        from app.repository.settings import AppSettingsRepository

        await AppSettingsRepository.update_community(enabled=True, iata="CDG")
        monkeypatch.setenv("MESHLOOM_COMMUNITY_IATA", "NCE")
        await import_legacy_community_iata(existing_database=True)
        assert (await get_community_effective()).iata == "NCE"

    @pytest.mark.asyncio
    async def test_existing_database_ignores_an_invalid_iata(self, test_db, monkeypatch):
        from app.repository.settings import AppSettingsRepository

        await AppSettingsRepository.update_community(iata="CDG")
        monkeypatch.setenv("MESHLOOM_COMMUNITY_IATA", "nope")
        await import_legacy_community_iata(existing_database=True)
        assert (await get_community_effective()).iata == "CDG"

    @pytest.mark.asyncio
    async def test_existing_off_database_stays_off(self, test_db, monkeypatch):
        monkeypatch.setenv("MESHLOOM_COMMUNITY", "1")
        await import_legacy_community_iata(existing_database=True)
        assert (await get_community_effective()).enabled is False

    @pytest.mark.asyncio
    async def test_every_community_variable_is_warned_about(self, test_db, monkeypatch, caplog):
        names = [
            "MESHLOOM_COMMUNITY",
            "MESHLOOM_COMMUNITY_IATA",
            "MESHLOOM_COMMUNITY_BROKER_HOST",
            "MESHLOOM_COMMUNITY_API_BASE",
        ]
        for name in names:
            monkeypatch.setenv(name, "x")
        assert set(names) <= set(RETIRED_ENV)
        with caplog.at_level(logging.WARNING):
            assert set(warn_about_retired_env()) == set(names)
            # Warned at every start, even once imported.
            await import_legacy_env_settings()
            await import_legacy_env_settings()
        for name in names:
            assert caplog.text.count(f"{name} is set but no longer read") == 3

    @pytest.mark.asyncio
    async def test_address_variables_are_ignored(self, test_db, monkeypatch):
        monkeypatch.setenv("MESHLOOM_COMMUNITY_BROKER_HOST", "mqtt.internal.test")
        monkeypatch.setenv("MESHLOOM_COMMUNITY_API_BASE", "https://api.internal.test")
        await import_legacy_community_iata(existing_database=True)
        state = await get_community_effective()
        assert state.broker_host == DEFAULT_BROKER_HOST
        assert state.api_base == DEFAULT_API_BASE


@pytest.mark.parametrize("value", ["false", "0"])
def test_clock_wraparound_variable_set_off_gets_an_explicit_warning(monkeypatch, caplog, value):
    monkeypatch.setenv("__CLOWNTOWN_DO_CLOCK_WRAPAROUND", value)
    with caplog.at_level(logging.WARNING, logger="app.services.legacy_env_settings"):
        assert "__CLOWNTOWN_DO_CLOCK_WRAPAROUND" in warn_about_retired_env()
    assert "used to keep the radio clock wraparound off" in caplog.text


def test_clock_wraparound_variable_set_on_gets_no_off_warning(monkeypatch, caplog):
    monkeypatch.setenv("__CLOWNTOWN_DO_CLOCK_WRAPAROUND", "true")
    with caplog.at_level(logging.WARNING, logger="app.services.legacy_env_settings"):
        warn_about_retired_env()
    assert "used to keep the radio clock wraparound off" not in caplog.text
