"""Keep the channels on the radio (MSHL-41): the setting, the connect sync, the
send-slot policy (one scratch slot) and the delete that empties a kept slot."""

from __future__ import annotations

import asyncio
import logging
from unittest.mock import patch

import pytest

from app import radio_workarounds
from app.radio import radio_manager
from app.radio_sync import (
    _store_pending_channel_message,
    audit_channel_send_cache,
    clear_channel_from_radio_slots,
    sync_and_offload_channels,
)
from app.repository import ChannelRepository, MessageRepository
from app.repository.settings import AppSettingsRepository
from app.routers.channels import delete_channel
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


class _SendingNode(FakeNode):
    """A fake node that also accepts SEND_CHANNEL_TXT."""

    def _cmd_03(self, body: bytes) -> list[bytes]:
        return [b"\x00"]


async def _send_once(client, key: str = KEY_A) -> None:
    response = await client.post("/api/messages/channel", json={"channel_key": key, "text": "hi"})
    assert response.status_code == 200, response.text


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["serial", "tcp"])
async def test_off_a_deleted_channel_does_not_come_back_at_the_next_connect(test_db, client, kind):
    radio_manager._connection_info = "Serial: x" if kind == "serial" else "TCP: x:1"
    await ChannelRepository.upsert(key=KEY_A, name="ops")
    node = _SendingNode()
    async with connected_node(node, kind=kind) as (loop, _):
        await _send_once(client)
        assert node.channels[0] == ("ops", bytes.fromhex(KEY_A))

        assert (await client.delete(f"/api/channels/{KEY_A}")).status_code == 200
        assert node.channels[0] == ("", bytes(16))

        await sync_and_offload_channels(loop.mc)
    assert await ChannelRepository.get_by_key(KEY_A) is None


@pytest.mark.asyncio
async def test_off_deleting_leaves_the_slots_of_other_channels_alone(test_db):
    node = FakeNode()
    node.channels = {1: ("#far", bytes.fromhex(KEY_B))}
    async with connected_node(node, kind="serial"):
        assert await clear_channel_from_radio_slots(KEY_A) == 0
    assert node.channels[1] == ("#far", bytes.fromhex(KEY_B))


@pytest.mark.asyncio
async def test_deleting_a_channel_does_not_trip_the_hourly_audit(test_db, client):
    await ChannelRepository.upsert(key=KEY_A, name="ops")
    node = _SendingNode()
    async with connected_node(node, kind="serial") as (loop, _):
        await _send_once(client)
        assert await audit_channel_send_cache(loop.mc) is True
        assert (await client.delete(f"/api/channels/{KEY_A}")).status_code == 200
        assert radio_manager.get_cached_channel_slot(KEY_A) is None
        assert await audit_channel_send_cache(loop.mc) is True


@pytest.mark.asyncio
@pytest.mark.parametrize("keep", [False, True])
async def test_deleted_while_disconnected_the_next_sync_clears_it_without_adopting(
    test_db, client, keep
):
    _keep(keep)
    await ChannelRepository.upsert(key=KEY_A, name="ops")
    # No radio connected: the delete cannot reach the slot.
    assert (await client.delete(f"/api/channels/{KEY_A}")).status_code == 200
    assert await ChannelRepository.pending_slot_clear_keys() == {KEY_A}

    node = FakeNode()
    node.channels = {1: ("ops", bytes.fromhex(KEY_A)), 2: ("far", bytes.fromhex(KEY_B))}
    async with connected_node(node, kind="serial") as (loop, _):
        result = await sync_and_offload_channels(loop.mc)

    assert await ChannelRepository.get_by_key(KEY_A) is None
    assert node.channels[1] == ("", bytes(16))
    assert await ChannelRepository.get_by_key(KEY_B) is not None
    assert result["synced"] == 1
    assert radio_manager.get_cached_channel_slot(KEY_A) is None
    assert await ChannelRepository.pending_slot_clear_keys() == set()


@pytest.mark.asyncio
async def test_a_failed_clear_stays_pending_for_the_next_sync(test_db, client):
    class _Refusing(FakeNode):
        refuse = True

        def _cmd_20(self, body: bytes) -> list[bytes]:
            if self.refuse:
                return [b"\x01\x02"]
            return super()._cmd_20(body)

    await ChannelRepository.upsert(key=KEY_A, name="ops")
    node = _Refusing()
    node.channels = {1: ("ops", bytes.fromhex(KEY_A))}
    async with connected_node(node, kind="serial") as (loop, _):
        assert (await client.delete(f"/api/channels/{KEY_A}")).status_code == 200

        await sync_and_offload_channels(loop.mc)
        assert await ChannelRepository.get_by_key(KEY_A) is None

        node.refuse = False
        await sync_and_offload_channels(loop.mc)
    assert await ChannelRepository.get_by_key(KEY_A) is None
    assert node.channels[1] == ("", bytes(16))


@pytest.mark.asyncio
async def test_a_channel_added_back_before_the_sync_is_not_cleared(test_db, client):
    await ChannelRepository.upsert(key=KEY_A, name="ops")
    assert (await client.delete(f"/api/channels/{KEY_A}")).status_code == 200
    await ChannelRepository.upsert(key=KEY_A, name="ops")

    node = FakeNode()
    node.channels = {1: ("ops", bytes.fromhex(KEY_A))}
    async with connected_node(node, kind="serial") as (loop, _):
        result = await sync_and_offload_channels(loop.mc)

    assert result["synced"] == 1
    assert await ChannelRepository.get_by_key(KEY_A) is not None
    assert await ChannelRepository.pending_slot_clear_keys() == set()


class _RefusingSlotWrites(FakeNode):
    refuse = True

    def _cmd_20(self, body: bytes) -> list[bytes]:
        if self.refuse:
            return [b"\x01\x02"]
        return super()._cmd_20(body)


@pytest.mark.asyncio
async def test_a_queued_message_does_not_bring_back_a_channel_pending_a_clear(test_db, client):
    await ChannelRepository.upsert(key=KEY_A, name="ops")
    assert (await client.delete(f"/api/channels/{KEY_A}")).status_code == 200  # radio away

    node = _RefusingSlotWrites()
    node.channels = {1: ("ops", bytes.fromhex(KEY_A))}
    async with connected_node(node, kind="serial") as (loop, _):
        await sync_and_offload_channels(loop.mc)  # the clear is refused
        await _store_pending_channel_message(loop.mc, {"channel_idx": 1, "text": "bob: hi"})

        assert await ChannelRepository.get_by_key(KEY_A) is None
        assert await ChannelRepository.pending_slot_clear_keys() == {KEY_A}
        assert await MessageRepository.get_all(conversation_key=KEY_A) == []

        node.refuse = False
        await sync_and_offload_channels(loop.mc)
    assert await ChannelRepository.get_by_key(KEY_A) is None
    assert node.channels[1] == ("", bytes(16))


@pytest.mark.asyncio
async def test_a_queued_message_on_an_added_back_channel_is_still_stored(test_db, client):
    await ChannelRepository.upsert(key=KEY_A, name="ops")
    assert (await client.delete(f"/api/channels/{KEY_A}")).status_code == 200
    await ChannelRepository.upsert(key=KEY_A, name="ops")

    node = FakeNode()
    node.channels = {1: ("ops", bytes.fromhex(KEY_A))}
    async with connected_node(node, kind="serial") as (loop, _):
        await _store_pending_channel_message(loop.mc, {"channel_idx": 1, "text": "bob: hi"})
    assert len(await MessageRepository.get_all(conversation_key=KEY_A)) == 1


@pytest.mark.asyncio
async def test_a_silent_radio_stops_the_slot_scan_at_the_first_timeout(test_db, client):
    class _Silent(FakeNode):
        reads = 0

        def _cmd_1f(self, body: bytes) -> list[bytes]:
            self.reads += 1
            return []

    await ChannelRepository.upsert(key=KEY_A, name="ops")
    node = _Silent()
    async with connected_node(node, kind="serial") as (loop, _):
        loop.mc.commands.default_timeout = 0.05
        assert (await client.delete(f"/api/channels/{KEY_A}")).status_code == 200
    assert node.reads == 1
    assert await ChannelRepository.pending_slot_clear_keys() == {KEY_A}


@pytest.mark.asyncio
async def test_the_pending_clear_is_written_before_the_radio_is_touched(test_db):
    await ChannelRepository.upsert(key=KEY_A, name="ops")
    seen: dict = {}

    async def cancelled(_key: str) -> int:
        seen["pending"] = await ChannelRepository.pending_slot_clear_keys()
        seen["row"] = await ChannelRepository.get_by_key(KEY_A)
        raise asyncio.CancelledError

    with (
        patch("app.radio_sync.clear_channel_from_radio_slots", cancelled),
        pytest.raises(asyncio.CancelledError),
    ):
        await delete_channel(KEY_A)

    assert seen == {"pending": {KEY_A}, "row": None}
    assert await ChannelRepository.pending_slot_clear_keys() == {KEY_A}


@pytest.mark.asyncio
async def test_a_completed_clear_leaves_nothing_pending(test_db, client):
    await ChannelRepository.upsert(key=KEY_A, name="ops")
    node = FakeNode()
    node.channels = {1: ("ops", bytes.fromhex(KEY_A))}
    async with connected_node(node, kind="serial"):
        assert (await client.delete(f"/api/channels/{KEY_A}")).status_code == 200
    assert node.channels[1] == ("", bytes(16))
    assert await ChannelRepository.pending_slot_clear_keys() == set()


@pytest.mark.asyncio
async def test_the_ui_hears_of_the_delete_before_the_radio_is_touched(test_db):
    await ChannelRepository.upsert(key=KEY_A, name="ops")
    events: list[str] = []

    async def clear(_key: str) -> int:
        events.append("radio")
        return 0

    with (
        patch("app.radio_sync.clear_channel_from_radio_slots", clear),
        patch("app.routers.channels.broadcast_event", lambda *_a, **_k: events.append("ws")),
    ):
        await delete_channel(KEY_A)
    assert events == ["ws", "radio"]


@pytest.mark.asyncio
@pytest.mark.parametrize("key", ["nothex", "AB", "G" * 32, "A" * 34])
async def test_deleting_a_malformed_key_is_refused_and_stores_nothing(test_db, client, key):
    response = await client.delete(f"/api/channels/{key}")
    assert response.status_code == 400
    assert await ChannelRepository.pending_slot_clear_keys() == set()


@pytest.mark.asyncio
async def test_deleting_an_unknown_key_stores_nothing(test_db, client):
    assert (await client.delete(f"/api/channels/{KEY_C}")).status_code == 200
    assert await ChannelRepository.pending_slot_clear_keys() == set()


@pytest.mark.asyncio
async def test_a_delete_does_not_log_the_channel_key(test_db, client, caplog):
    await ChannelRepository.upsert(key=KEY_A, name="ops")
    with caplog.at_level(logging.DEBUG):
        assert (await client.delete(f"/api/channels/{KEY_A}")).status_code == 200
    # The test client's own request line (logger httpx) is not the app's.
    app_lines = [r.getMessage() for r in caplog.records if r.name.startswith("app")]
    assert app_lines and not any(KEY_A[:8] in line for line in app_lines)


class _TruncatingNode(_SendingNode):
    """Keeps at most 31 bytes of a name, as the firmware does."""

    def _cmd_20(self, body: bytes) -> list[bytes]:
        return super()._cmd_20(body[:32] + b"\x00" + body[33:])


@pytest.mark.asyncio
@pytest.mark.parametrize("name", ["a" * 32, "é" * 16, "é" * 15 + "ab"])
async def test_a_long_name_keeps_the_audit_quiet_and_survives_the_sync(test_db, client, name):
    await ChannelRepository.upsert(key=KEY_A, name=name)
    node = _TruncatingNode()
    async with connected_node(node, kind="serial") as (loop, _):
        await _send_once(client)
        assert await audit_channel_send_cache(loop.mc) is True
        await sync_and_offload_channels(loop.mc)
    stored = await ChannelRepository.get_by_key(KEY_A)
    assert stored is not None and stored.name == name


class _LateReplies(FakeNode):
    """A read gets the reply of an earlier one that had timed out, then its own too late."""

    def __init__(self) -> None:
        super().__init__()
        self.silent: set[int] = set()
        self.late: dict[int, int] = {}  # slot being read -> slot whose reply arrives instead

    def _cmd_1f(self, body: bytes) -> list[bytes]:
        idx = body[0]
        if idx in self.silent:
            return []
        if idx in self.late:
            return [super()._cmd_1f(bytes([self.late[idx]]))[0], super()._cmd_1f(body)[0]]
        return super()._cmd_1f(body)


@pytest.mark.asyncio
async def test_a_late_reply_does_not_make_the_sync_clear_the_wrong_slot(test_db, client):
    await ChannelRepository.upsert(key=KEY_A, name="gone")
    assert (await client.delete(f"/api/channels/{KEY_A}")).status_code == 200  # radio away

    node = _LateReplies()
    node.channels = {3: ("gone", bytes.fromhex(KEY_A)), 4: ("far", bytes.fromhex(KEY_B))}
    node.silent = {3}
    node.late = {4: 3}
    async with connected_node(node, kind="serial") as (loop, _):
        loop.mc.commands.default_timeout = 0.05
        await sync_and_offload_channels(loop.mc)

    assert node.channels[4] == ("far", bytes.fromhex(KEY_B))
    assert await ChannelRepository.pending_slot_clear_keys() == {KEY_A}


@pytest.mark.asyncio
async def test_a_late_reply_does_not_make_the_sync_offload_the_wrong_slot(test_db):
    node = _LateReplies()
    node.channels = {3: ("near", bytes.fromhex(KEY_A)), 4: ("far", bytes.fromhex(KEY_B))}
    node.silent = {3}
    node.late = {4: 3}
    async with connected_node(node, kind="serial") as (loop, _):
        loop.mc.commands.default_timeout = 0.05
        await sync_and_offload_channels(loop.mc)

    assert node.channels[4] == ("far", bytes.fromhex(KEY_B))


@pytest.mark.asyncio
async def test_a_late_reply_does_not_make_a_delete_clear_the_wrong_slot(test_db, client):
    await ChannelRepository.upsert(key=KEY_A, name="gone")
    node = _LateReplies()
    node.channels = {0: ("far", bytes.fromhex(KEY_B)), 1: ("gone", bytes.fromhex(KEY_A))}
    node.late = {0: 1}
    async with connected_node(node, kind="serial"):
        assert (await client.delete(f"/api/channels/{KEY_A}")).status_code == 200

    assert node.channels[0] == ("far", bytes.fromhex(KEY_B))
    assert node.channels[1] == ("", bytes(16))  # its own read still found it
    # One read was not answered for its slot, so the scan does not count as complete.
    assert await ChannelRepository.pending_slot_clear_keys() == {KEY_A}


@pytest.mark.asyncio
async def test_a_late_reply_does_not_file_a_queued_message_under_another_channel(test_db):
    await ChannelRepository.upsert(key=KEY_A, name="near")
    await ChannelRepository.upsert(key=KEY_B, name="far")
    node = _LateReplies()
    node.channels = {3: ("near", bytes.fromhex(KEY_A)), 4: ("far", bytes.fromhex(KEY_B))}
    node.late = {4: 3}
    async with connected_node(node, kind="serial") as (loop, _):
        await _store_pending_channel_message(loop.mc, {"channel_idx": 4, "text": "bob: hi"})

    assert await MessageRepository.get_all(conversation_key=KEY_A) == []


@pytest.mark.asyncio
async def test_a_failed_fanout_channel_lookup_does_not_log_the_key(test_db, caplog):
    from app.fanout.manager import FanoutManager

    with (
        caplog.at_level(logging.DEBUG, logger="app.fanout.manager"),
        patch.object(ChannelRepository, "get_by_key", side_effect=RuntimeError("db")),
    ):
        await FanoutManager().broadcast_message({"type": "CHAN", "conversation_key": KEY_A})
    assert KEY_A[:8] not in caplog.text
    assert "pending check" in caplog.text


@pytest.mark.asyncio
@pytest.mark.parametrize("name", ["ops", None])
async def test_storing_a_channel_message_does_not_log_the_key(test_db, caplog, name):
    from app.repository import RawPacketRepository
    from app.services.messages import create_message_from_decrypted

    packet_id, _ = await RawPacketRepository.create(b"test_packet_data", 1700000000)
    with caplog.at_level(logging.INFO, logger="app.services.messages"):
        stored = await create_message_from_decrypted(
            packet_id=packet_id,
            channel_key=KEY_A,
            channel_name=name,
            sender="bob",
            message_text="hi",
            timestamp=1700000000,
            received_at=1700000000,
            broadcast_fn=lambda *_a, **_k: None,
        )
    assert stored is not None
    assert "Stored channel message" in caplog.text
    assert KEY_A[:8] not in caplog.text
