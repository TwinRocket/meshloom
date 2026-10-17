"""Wi-Fi prompt state helpers on AppSettingsRepository (migration 095)."""

import asyncio

import pytest

from app.repository import AppSettingsRepository

KEY_A = "AB" * 32
KEY_B = "cd" * 32


@pytest.mark.asyncio
async def test_empty_by_default(test_db):
    assert await AppSettingsRepository.get_wifi_prompt_state() == {}


@pytest.mark.asyncio
async def test_fields_are_independent_and_keyed_by_lowercase_key(test_db):
    await AppSettingsRepository.mark_wifi_prompt_dismissed(KEY_A)
    await AppSettingsRepository.mark_wifi_open_network(KEY_A, "Home Net")
    await AppSettingsRepository.mark_wifi_store_started(KEY_A, "Home Net")

    state = await AppSettingsRepository.get_wifi_prompt_state()
    entry = state[KEY_A.lower()]
    assert isinstance(entry["dismissed_at"], int)
    assert entry["open_network_ssid"] == "Home Net"
    assert isinstance(entry["open_network_at"], int)
    assert entry["store_target_ssid"] == "Home Net"
    assert isinstance(entry["store_started_at"], int)

    await AppSettingsRepository.clear_wifi_open_network(KEY_A)
    await AppSettingsRepository.clear_wifi_store_marker(KEY_A)
    entry = (await AppSettingsRepository.get_wifi_prompt_state())[KEY_A.lower()]
    # The dismissal is never erased by the other helpers.
    assert set(entry) == {"dismissed_at"}


@pytest.mark.asyncio
async def test_one_radio_does_not_affect_another(test_db):
    await AppSettingsRepository.mark_wifi_prompt_dismissed(KEY_A)
    await AppSettingsRepository.mark_wifi_open_network(KEY_B, "Other")
    state = await AppSettingsRepository.get_wifi_prompt_state()
    assert set(state[KEY_A.lower()]) == {"dismissed_at"}
    assert set(state[KEY_B]) == {"open_network_ssid", "open_network_at"}


@pytest.mark.asyncio
async def test_concurrent_writers_keep_every_field(test_db):
    await asyncio.gather(
        AppSettingsRepository.mark_wifi_prompt_dismissed(KEY_A),
        AppSettingsRepository.mark_wifi_open_network(KEY_A, "Net"),
        AppSettingsRepository.mark_wifi_store_started(KEY_A, "Net"),
        AppSettingsRepository.mark_wifi_prompt_dismissed(KEY_B),
    )
    state = await AppSettingsRepository.get_wifi_prompt_state()
    assert set(state[KEY_A.lower()]) == {
        "dismissed_at",
        "open_network_ssid",
        "open_network_at",
        "store_started_at",
        "store_target_ssid",
    }
    assert set(state[KEY_B]) == {"dismissed_at"}


@pytest.mark.asyncio
@pytest.mark.parametrize("bad", ["", "ab" * 31, "zz" * 32, 'a"' * 32, "ab" * 32 + "x"])
async def test_refuses_a_key_that_is_not_64_hex(test_db, bad):
    with pytest.raises(ValueError):
        await AppSettingsRepository.mark_wifi_prompt_dismissed(bad)
    assert await AppSettingsRepository.get_wifi_prompt_state() == {}


@pytest.mark.asyncio
async def test_an_ssid_with_quotes_is_stored_verbatim(test_db):
    ssid = ' "quoted" \\ net '
    await AppSettingsRepository.mark_wifi_open_network(KEY_A, ssid)
    state = await AppSettingsRepository.get_wifi_prompt_state()
    assert state[KEY_A.lower()]["open_network_ssid"] == ssid


@pytest.mark.asyncio
async def test_a_corrupted_column_restarts_from_an_empty_object(test_db):
    await test_db.conn.execute(
        "UPDATE app_settings SET wifi_prompt_state = '{not json' WHERE id = 1"
    )
    await test_db.conn.commit()
    assert await AppSettingsRepository.get_wifi_prompt_state() == {}
    await AppSettingsRepository.mark_wifi_prompt_dismissed(KEY_A)
    assert set((await AppSettingsRepository.get_wifi_prompt_state())[KEY_A.lower()]) == {
        "dismissed_at"
    }
