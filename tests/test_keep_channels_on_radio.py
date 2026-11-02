"""Keep the channels on the radio (MSHL-41): the setting, the connect sync, the
send-slot policy (one scratch slot) and the delete that empties a kept slot."""

from __future__ import annotations

import pytest

from app import radio_workarounds
from app.radio import radio_manager
from app.radio_sync import clear_channel_from_radio_slots, sync_and_offload_channels
from app.repository import ChannelRepository
from app.repository.settings import AppSettingsRepository
from tests.fixtures.fake_node import FakeNode, connected_node

KEY_A = "A1" * 16
KEY_B = "B2" * 16
KEY_C = "C3" * 16


@pytest.fixture(autouse=True)
def _restore_send_cache():
    """Serial link (slot reuse on), empty send cache, both restored after."""
    saved = (
        radio_manager._channel_slot_by_key.copy(),
        radio_manager._channel_key_by_slot.copy(),
        radio_manager._pending_message_channel_key_by_slot.copy(),
        radio_manager.max_channels,
        radio_manager._connection_info,
    )
    radio_manager.reset_channel_send_cache()
    radio_manager._connection_info = "Serial: /dev/ttyUSB0"
    yield
    (
        radio_manager._channel_slot_by_key,
        radio_manager._channel_key_by_slot,
        radio_manager._pending_message_channel_key_by_slot,
        radio_manager.max_channels,
        radio_manager._connection_info,
    ) = saved


def _keep(on: bool = True, **others) -> None:
    radio_workarounds._current = radio_workarounds.RadioWorkarounds(
        keep_channels_on_radio=on, **others
    )


# -- the setting ---------------------------------------------------------------


@pytest.mark.asyncio
async def test_off_by_default_and_round_trips_through_the_api(test_db, client):
    assert (await AppSettingsRepository.get()).keep_channels_on_radio is False
    assert (await client.get("/api/settings")).json()["keep_channels_on_radio"] is False
    response = await client.patch("/api/settings", json={"keep_channels_on_radio": True})
    assert response.status_code == 200
    assert response.json()["keep_channels_on_radio"] is True
    # The repository refreshed the copy the send path reads.
    assert radio_manager.keep_channels_on_radio() is True
    await client.patch("/api/settings", json={"keep_channels_on_radio": False})
    assert radio_manager.keep_channels_on_radio() is False


def test_the_setting_is_in_the_backup():
    from app.services.backup import V1_SETTING_COLUMNS

    assert "keep_channels_on_radio" in V1_SETTING_COLUMNS


# -- send-slot policy ----------------------------------------------------------


def test_off_keeps_the_lru_eviction_over_every_slot():
    radio_manager.max_channels = 2
    radio_manager.note_channel_slot_loaded(KEY_A, 0)
    radio_manager.note_channel_slot_loaded(KEY_B, 1)
    assert radio_manager.plan_channel_send_slot(KEY_C) == (0, True, KEY_A)


def test_on_reuses_a_kept_slot_and_sends_others_from_the_scratch_slot():
    _keep()
    radio_manager.max_channels = 4
    radio_manager.note_channel_slot_loaded(KEY_A, 0)
    radio_manager.note_channel_slot_loaded(KEY_B, 1)
    assert radio_manager.get_scratch_channel_slot() == 3
    assert radio_manager.get_kept_channel_slot_count() == 3
    # Already on the radio: its slot is reused, nothing written.
    assert radio_manager.plan_channel_send_slot(KEY_A) == (0, False, None)
    # Not on the radio: the scratch slot, never a kept one (slot 2 is free but kept).
    assert radio_manager.plan_channel_send_slot(KEY_C, preferred_slot=0) == (3, True, None)
    radio_manager.note_channel_slot_loaded(KEY_C, 3)
    assert radio_manager.plan_channel_send_slot(KEY_C) == (3, False, None)
    # The next channel evicts only what the scratch slot held.
    other = "D4" * 16
    assert radio_manager.plan_channel_send_slot(other) == (3, True, KEY_C)
    assert radio_manager.get_cached_channel_slot(KEY_A) == 0
    assert radio_manager.get_cached_channel_slot(KEY_B) == 1


def test_on_without_slot_reuse_always_writes_the_scratch_slot():
    _keep(force_channel_slot_reconfigure=True)
    radio_manager.max_channels = 8
    assert radio_manager.plan_channel_send_slot(KEY_A, preferred_slot=0) == (7, True, None)


def test_one_off_sends_borrow_the_scratch_slot_only_in_keep_mode():
    radio_manager.max_channels = 8
    assert radio_manager.temp_channel_slot(0) == 0
    _keep()
    assert radio_manager.temp_channel_slot(0) == 7


# -- connect sync ----------------------------------------------------------------


@pytest.mark.asyncio
async def test_on_the_connect_sync_keeps_the_slots_and_seeds_the_cache(test_db):
    _keep()
    node = FakeNode()
    node.channels = {0: ("#ops", bytes.fromhex(KEY_A)), 2: ("#far", bytes.fromhex(KEY_B))}
    before = dict(node.channels)
    async with connected_node(node, kind="serial") as (loop, _n):
        for _reconnect in range(2):
            result = await sync_and_offload_channels(loop.mc)
            assert result == {"synced": 2, "cleared": 0, "kept": True}
            assert node.channels == before
        assert not any(name == "set_channel" for name, _detail in node.log)
        assert radio_manager.get_cached_channel_slot(KEY_A) == 0
        assert radio_manager.get_cached_channel_slot(KEY_B) == 2
        assert radio_manager.plan_channel_send_slot(KEY_B) == (2, False, None)
    stored = await ChannelRepository.get_by_key(KEY_A)
    assert stored is not None and stored.name == "#ops" and stored.on_radio is True


@pytest.mark.asyncio
async def test_off_the_connect_sync_still_empties_the_slots(test_db):
    node = FakeNode()
    node.channels = {0: ("#ops", bytes.fromhex(KEY_A))}
    async with connected_node(node, kind="serial") as (loop, _n):
        result = await sync_and_offload_channels(loop.mc)
    assert result == {"synced": 1, "cleared": 1}
    assert node.channels[0] == ("", bytes(16))
    assert (await ChannelRepository.get_by_key(KEY_A)) is not None


# -- delete ----------------------------------------------------------------------


@pytest.mark.asyncio
async def test_on_deleting_a_channel_empties_its_kept_slot(test_db, client):
    _keep()
    await ChannelRepository.upsert(key=KEY_A, name="#ops")
    node = FakeNode()
    node.channels = {1: ("#ops", bytes.fromhex(KEY_A)), 2: ("#far", bytes.fromhex(KEY_B))}
    async with connected_node(node, kind="serial"):
        radio_manager.note_channel_slot_loaded(KEY_A, 1)
        response = await client.delete(f"/api/channels/{KEY_A}")
        assert response.status_code == 200
        assert radio_manager.get_cached_channel_slot(KEY_A) is None
    assert node.channels[1] == ("", bytes(16))
    assert node.channels[2] == ("#far", bytes.fromhex(KEY_B))


@pytest.mark.asyncio
async def test_off_deleting_a_channel_leaves_the_radio_alone(test_db):
    node = FakeNode()
    node.channels = {1: ("#ops", bytes.fromhex(KEY_A))}
    async with connected_node(node, kind="serial"):
        assert await clear_channel_from_radio_slots(KEY_A) == 0
    assert node.channels[1] == ("#ops", bytes.fromhex(KEY_A))
