"""Radio workarounds are settings (Settings → Radio → Advanced), read by the radio code."""

from unittest.mock import AsyncMock, patch

import pytest

from app import radio_workarounds
from app.radio import radio_manager
from app.repository.settings import AppSettingsRepository


@pytest.mark.asyncio
async def test_defaults_are_off(test_db):
    stored = await AppSettingsRepository.get()
    assert stored.enable_message_poll_fallback is False
    assert stored.force_channel_slot_reconfigure is False
    assert stored.load_with_autoevict is False
    assert await radio_workarounds.load() == radio_workarounds.RadioWorkarounds()


@pytest.mark.asyncio
async def test_repository_update_refreshes_what_the_radio_reads(test_db, monkeypatch):
    monkeypatch.setattr(radio_manager, "_connection_info", "Serial: /dev/ttyUSB0")
    assert radio_manager.channel_slot_reuse_enabled() is True

    await AppSettingsRepository.update(force_channel_slot_reconfigure=True)
    assert radio_workarounds.current().force_channel_slot_reconfigure is True
    assert radio_manager.channel_slot_reuse_enabled() is False

    await AppSettingsRepository.update(force_channel_slot_reconfigure=False)
    assert radio_manager.channel_slot_reuse_enabled() is True


@pytest.mark.asyncio
async def test_patch_settings_round_trip(test_db, client):
    response = await client.patch(
        "/api/settings",
        json={"enable_message_poll_fallback": True, "load_with_autoevict": True},
    )
    assert response.status_code == 200
    data = response.json()
    assert data["enable_message_poll_fallback"] is True
    assert data["load_with_autoevict"] is True
    assert data["force_channel_slot_reconfigure"] is False
    assert radio_workarounds.current().load_with_autoevict is True

    again = await client.get("/api/settings")
    assert again.json()["enable_message_poll_fallback"] is True


@pytest.mark.asyncio
async def test_switching_the_poll_fallback_restarts_the_loop(test_db, client):
    with patch(
        "app.radio_sync.restart_message_polling_if_running", new_callable=AsyncMock
    ) as restart:
        await client.patch("/api/settings", json={"enable_message_poll_fallback": True})
        restart.assert_awaited_once()
        restart.reset_mock()
        # Same value again: nothing to restart.
        await client.patch("/api/settings", json={"enable_message_poll_fallback": True})
        restart.assert_not_awaited()


@pytest.mark.asyncio
async def test_sync_reads_autoevict_from_the_database(test_db):
    from app import radio_sync

    await AppSettingsRepository.update(load_with_autoevict=True)
    mc = AsyncMock()
    with (
        patch.object(
            radio_sync, "_enable_autoevict_on_radio", new=AsyncMock(return_value=True)
        ) as enable,
        patch.object(radio_sync, "sync_contacts_from_radio", new=AsyncMock(return_value={})),
        patch.object(radio_sync, "sync_and_offload_channels", new=AsyncMock(return_value={})),
        patch.object(radio_sync, "start_background_contact_reconciliation") as reconcile,
    ):
        await radio_sync.sync_and_offload_all(mc)
    enable.assert_awaited_once_with(mc)
    assert reconcile.call_args.kwargs["autoevict"] is True
