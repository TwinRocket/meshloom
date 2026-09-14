"""Radio proxy: TCP companion server, policy hooks, and API."""

from __future__ import annotations

import asyncio
import socket
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, MagicMock, patch

import aiosqlite
import pytest
from meshcore import EventType, MeshCore
from meshcore.packets import CommandType

from app.channel_constants import PUBLIC_CHANNEL_KEY, PUBLIC_CHANNEL_NAME
from app.models import ContactUpsert, RadioProxyUpdate
from app.radio import radio_manager
from app.radio_proxy.manager import (
    LOG_QUEUE_MAX,
    MESSAGE_QUEUE_MAX,
    ProxySession,
    ProxySettings,
    QueuedMessage,
    RadioProxyManager,
    hosts_overlap,
    is_local_proxy_target,
    project_host_channels,
    radio_proxy_manager,
)
from app.radio_proxy.protocol import (
    encode_ok,
    parse_proxy_instance_id,
    proxy_model_string,
)
from app.repository import (
    ChannelRepository,
    ContactRepository,
    MessageRepository,
    RadioProxyRepository,
)
from app.routers.radio import get_radio_proxy, patch_radio_proxy
from app.services.radio_lifecycle import run_post_connect_setup


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


class _FakeWriter:
    def __init__(self) -> None:
        self.writes: list[bytes] = []

    def write(self, data: bytes) -> None:
        self.writes.append(data)

    async def drain(self) -> None:
        return None

    def close(self) -> None:
        return None

    async def wait_closed(self) -> None:
        return None


def _make_host_radio(name: str = "HostRadio") -> MagicMock:
    mc = MagicMock()
    mc.is_connected = True
    mc.self_info = {"name": name, "public_key": "aa" * 32}
    mc.commands = MagicMock()
    mc.commands.get_msg = AsyncMock()
    mc.commands.get_channel = AsyncMock()
    mc.commands.set_channel = AsyncMock()
    mc.commands.add_contact = AsyncMock(return_value=MagicMock(type=EventType.OK, payload={}))
    mc.commands.remove_contact = AsyncMock()
    mc.commands.send_msg = AsyncMock(
        return_value=MagicMock(
            type=EventType.MSG_SENT,
            payload={"expected_ack": b"\x11\x22\x33\x44", "suggested_timeout": 2500},
        )
    )
    mc.commands.send_chan_msg = AsyncMock(return_value=MagicMock(type=EventType.OK, payload={}))
    mc.commands.set_flood_scope = AsyncMock(return_value=MagicMock(type=EventType.OK, payload={}))
    mc.commands.set_path_hash_mode = AsyncMock(
        return_value=MagicMock(type=EventType.OK, payload={})
    )
    mc.get_contact_by_key_prefix = MagicMock(return_value=None)
    return mc


# A real Meshloom always knows its own key by the time it is proxying; the proxy
# now refuses to answer with a placeholder when it does not, which is what a
# client needs in order not to mistake a booting node for a different radio.
PROXY_TEST_PUBLIC_KEY = bytes.fromhex("ab" * 32)


@asynccontextmanager
async def _started_proxy(test_db, *, bind: str = "127.0.0.1", port: int | None = None):
    import app.keystore as keystore

    previous_key = keystore._public_key
    keystore._public_key = PROXY_TEST_PUBLIC_KEY
    manager = RadioProxyManager()
    settings = ProxySettings(enabled=True, bind=bind, port=port if port is not None else 0)
    await manager.apply_settings(settings)
    try:
        listen = manager.listen_port
        assert listen is not None
        yield manager, listen
    finally:
        await manager.stop()
        keystore._public_key = previous_key


async def _connect_client(port: int) -> MeshCore:
    mc = await MeshCore.create_tcp(host="127.0.0.1", port=port, default_timeout=3)
    assert mc is not None
    return mc


@pytest.mark.asyncio
async def test_repository_get_defaults_when_proxy_columns_missing():
    from app.database import Database
    from app.repository import radio_proxy as radio_proxy_repo

    isolated = Database(":memory:")
    isolated._connection = await aiosqlite.connect(":memory:")
    isolated._connection.row_factory = aiosqlite.Row
    await isolated._connection.execute(
        "CREATE TABLE app_settings (id INTEGER PRIMARY KEY CHECK (id = 1))"
    )
    await isolated._connection.execute("INSERT INTO app_settings (id) VALUES (1)")
    await isolated._connection.commit()

    original = radio_proxy_repo.db
    radio_proxy_repo.db = isolated
    try:
        stored = await RadioProxyRepository.get()
        assert stored.enabled is False
        assert stored.bind == "0.0.0.0"
        assert stored.port == 5001
        assert stored.max_clients == 8
    finally:
        radio_proxy_repo.db = original
        await isolated._connection.close()


@pytest.mark.asyncio
async def test_migration_074_columns_exist(test_db):
    async with test_db.readonly() as conn:
        cursor = await conn.execute("PRAGMA table_info(app_settings)")
        columns = {row[1] for row in await cursor.fetchall()}
    assert {
        "radio_proxy_enabled",
        "radio_proxy_bind",
        "radio_proxy_port",
        "radio_proxy_max_clients",
    } <= columns
    stored = await RadioProxyRepository.get()
    assert stored.enabled is False
    assert stored.bind == "0.0.0.0"
    assert stored.port == 5001
    assert stored.max_clients == 8


@pytest.mark.asyncio
async def test_proxy_api_get_and_patch(test_db):
    status = await get_radio_proxy()
    assert status.enabled is False
    assert status.port == 5001
    assert status.instance_id

    port = _free_port()
    try:
        updated = await patch_radio_proxy(
            RadioProxyUpdate(enabled=True, bind="127.0.0.1", port=port, max_clients=3)
        )
        assert updated.enabled is True
        assert updated.listening is True
        assert updated.port == port
        stored = await RadioProxyRepository.get()
        assert stored.enabled is True
        assert stored.port == port
        assert stored.max_clients == 3
    finally:
        await radio_proxy_manager.stop()
        await RadioProxyRepository.update(enabled=False)
        await radio_proxy_manager.apply_settings(ProxySettings())


def test_loop_guard_helpers():
    assert hosts_overlap("127.0.0.1", "localhost")
    assert is_local_proxy_target(
        "127.0.0.1",
        5001,
        bind="0.0.0.0",
        listen_port=5001,
        listening=True,
    )
    assert not is_local_proxy_target(
        "10.0.0.8",
        5001,
        bind="0.0.0.0",
        listen_port=5001,
        listening=True,
    )
    assert not is_local_proxy_target(
        "127.0.0.1",
        5001,
        bind="0.0.0.0",
        listen_port=5001,
        listening=False,
    )


@pytest.mark.asyncio
async def test_meshcore_create_tcp_setup_and_disabled_export(test_db):
    async with _started_proxy(test_db) as (manager, port):
        mc = await _connect_client(port)
        try:
            assert mc.self_info is not None
            device = await mc.commands.send_device_query()
            assert device.type == EventType.DEVICE_INFO
            assert parse_proxy_instance_id(device.payload["model"]) == manager.instance_id
            assert device.payload["fw ver"] == 10
            assert device.payload["max_channels"] == 40

            export = await mc.commands.export_private_key()
            assert export.type == EventType.DISABLED

            empty = await mc.commands.get_msg()
            assert empty.type == EventType.NO_MORE_MSGS

            contacts = await mc.commands.get_contacts()
            assert contacts.type == EventType.CONTACTS
        finally:
            await mc.disconnect()


@pytest.mark.asyncio
async def test_two_clients_and_host_radio_untouched(test_db):
    host = _make_host_radio()
    async with _started_proxy(test_db) as (_manager, port):
        with patch.object(radio_manager, "_meshcore", host):
            first = await _connect_client(port)
            second = await _connect_client(port)
            try:
                assert first.self_info is not None
                assert second.self_info is not None
                empty = await first.commands.get_msg()
                assert empty.type == EventType.NO_MORE_MSGS
                channel = await first.commands.get_channel(0)
                assert channel.type == EventType.CHANNEL_INFO
                await first.commands.set_channel(7, "", b"\x00" * 16)
                await first.commands.add_contact(
                    {
                        "public_key": "bb" * 32,
                        "type": 1,
                        "flags": 0,
                        "out_path_len": -1,
                        "out_path": "",
                        "out_path_hash_mode": 0,
                        "adv_name": "Overlay",
                        "last_advert": 0,
                        "adv_lat": 0.0,
                        "adv_lon": 0.0,
                    }
                )
                await first.commands.remove_contact("cc" * 32)
            finally:
                await first.disconnect()
                await second.disconnect()

    host.commands.get_msg.assert_not_called()
    host.commands.get_channel.assert_not_called()
    host.commands.set_channel.assert_not_called()
    host.commands.add_contact.assert_not_called()
    host.commands.remove_contact.assert_not_called()


@pytest.mark.asyncio
async def test_proxy_dm_is_single_host_send_without_retries(test_db):
    dest = "ab" * 32
    await ContactRepository.upsert(ContactUpsert(public_key=dest, name="Alice", type=1))
    host = _make_host_radio()
    async with _started_proxy(test_db) as (_manager, port):
        with patch.object(radio_manager, "_meshcore", host):
            mc = await _connect_client(port)
            try:
                sent = await mc.commands.send_msg(dest, "hello-proxy")
                assert sent.type == EventType.MSG_SENT
                assert sent.payload["expected_ack"] == b"\x11\x22\x33\x44"
                assert sent.payload["suggested_timeout"] == 2500
            finally:
                await mc.disconnect()

    assert host.commands.send_msg.await_count == 1
    stored = await MessageRepository.get_all()
    texts = [row.text for row in stored if row.type == "PRIV"]
    assert texts == ["hello-proxy"]


@pytest.mark.asyncio
async def test_channel_body_has_single_radio_prefix(test_db):
    await ChannelRepository.upsert(PUBLIC_CHANNEL_KEY, PUBLIC_CHANNEL_NAME, on_radio=True)
    host = _make_host_radio("HostRadio")
    async with _started_proxy(test_db) as (_manager, port):
        with patch.object(radio_manager, "_meshcore", host):
            mc = await _connect_client(port)
            try:
                slot = await mc.commands.get_channel(0)
                assert slot.payload["channel_name"] == PUBLIC_CHANNEL_NAME
                prefixed = await mc.commands.send_chan_msg(0, "HostRadio: body")
                assert prefixed.type == EventType.OK
                plain = await mc.commands.send_chan_msg(0, "second")
                assert plain.type == EventType.OK
            finally:
                await mc.disconnect()

    messages = [row for row in await MessageRepository.get_all() if row.type == "CHAN"]
    texts = [row.text for row in messages]
    assert set(texts) == {"HostRadio: body", "HostRadio: second"}
    assert all(text.count("HostRadio: ") == 1 for text in texts)


@pytest.mark.asyncio
async def test_set_channel_empty_does_not_mutate_host(test_db):
    await ChannelRepository.upsert(
        PUBLIC_CHANNEL_KEY, PUBLIC_CHANNEL_NAME, is_hashtag=False, on_radio=True
    )
    async with test_db.tx() as conn:
        await conn.execute(
            """
            UPDATE channels
            SET flood_scope_override = 'eu', path_hash_mode_override = 1, last_read_at = 99
            WHERE key = ?
            """,
            (PUBLIC_CHANNEL_KEY,),
        )
    async with _started_proxy(test_db) as (_manager, port):
        mc = await _connect_client(port)
        try:
            first = await mc.commands.get_channel(0)
            assert first.payload["channel_name"] == PUBLIC_CHANNEL_NAME
            cleared = await mc.commands.set_channel(0, "", b"\x00" * 16)
            assert cleared.type == EventType.OK
            empty = await mc.commands.get_channel(0)
            assert empty.payload["channel_name"] == ""
        finally:
            await mc.disconnect()

    host = await ChannelRepository.get_by_key(PUBLIC_CHANNEL_KEY)
    assert host is not None
    assert host.name == PUBLIC_CHANNEL_NAME
    assert host.on_radio is True
    assert host.flood_scope_override == "eu"
    assert host.path_hash_mode_override == 1
    assert host.last_read_at == 99


@pytest.mark.asyncio
async def test_set_channel_upserts_name_only(test_db):
    secret = bytes(range(16))
    key = secret.hex().upper()
    await ChannelRepository.upsert(key, "Old", is_hashtag=False, on_radio=True)
    async with test_db.tx() as conn:
        await conn.execute(
            "UPDATE channels SET flood_scope_override = 'fr', last_read_at = 7 WHERE key = ?",
            (key,),
        )
    async with _started_proxy(test_db) as (_manager, port):
        mc = await _connect_client(port)
        try:
            ok = await mc.commands.set_channel(3, "NewName", secret)
            assert ok.type == EventType.OK
            info = await mc.commands.get_channel(3)
            assert info.payload["channel_name"] == "NewName"
        finally:
            await mc.disconnect()

    host = await ChannelRepository.get_by_key(key)
    assert host is not None
    assert host.name == "NewName"
    assert host.on_radio is True
    assert host.flood_scope_override == "fr"
    assert host.last_read_at == 7


@pytest.mark.asyncio
async def test_unbound_channel_send_errors(test_db):
    host = _make_host_radio()
    async with _started_proxy(test_db) as (_manager, port):
        with patch.object(radio_manager, "_meshcore", host):
            mc = await _connect_client(port)
            try:
                result = await mc.commands.send_chan_msg(4, "no-slot")
                assert result.type == EventType.ERROR
            finally:
                await mc.disconnect()
    host.commands.send_chan_msg.assert_not_called()


@pytest.mark.asyncio
async def test_disconnected_radio_errors_sends_but_serves_reads(test_db):
    await ContactRepository.upsert(ContactUpsert(public_key="cd" * 32, name="Bob", type=1))
    async with _started_proxy(test_db) as (_manager, port):
        with patch.object(radio_manager, "_meshcore", None):
            mc = await _connect_client(port)
            try:
                contacts = await mc.commands.get_contacts()
                assert contacts.type == EventType.CONTACTS
                assert "cd" * 32 in contacts.payload
                sent = await mc.commands.send_msg("cd" * 32, "offline")
                assert sent.type == EventType.ERROR
            finally:
                await mc.disconnect()


@pytest.mark.asyncio
async def test_outgoing_dm_reaches_no_client(test_db):
    dest = "ab" * 32
    await ContactRepository.upsert(ContactUpsert(public_key=dest, name="Alice", type=1))
    host = _make_host_radio()
    port = _free_port()
    try:
        await radio_proxy_manager.apply_settings(
            ProxySettings(enabled=True, bind="127.0.0.1", port=port)
        )
        with patch.object(radio_manager, "_meshcore", host):
            origin = await _connect_client(port)
            other = await _connect_client(port)
            try:
                sent = await origin.commands.send_msg(dest, "hello-proxy")
                assert sent.type == EventType.MSG_SENT
                empty = await origin.commands.get_msg()
                assert empty.type == EventType.NO_MORE_MSGS
                # Nor does any other client: CONTACT_MSG_RECV asserts the contact
                # sent it, so relaying an outgoing message files the operator's own
                # words under Alice, in a client that has no way to tell otherwise.
                other_empty = await other.commands.get_msg()
                assert other_empty.type == EventType.NO_MORE_MSGS
            finally:
                await origin.disconnect()
                await other.disconnect()
    finally:
        await radio_proxy_manager.stop()
        await radio_proxy_manager.apply_settings(ProxySettings())


@pytest.mark.asyncio
async def test_two_clients_receive_incoming_dm_not_originator(test_db):
    async with _started_proxy(test_db) as (manager, port):
        first = await _connect_client(port)
        second = await _connect_client(port)
        try:
            manager.notify_broadcast(
                "message",
                {
                    "id": 42,
                    "type": "PRIV",
                    "text": "incoming-dm",
                    "sender_key": "aabbccddeeff" + "00" * 26,
                    "sender_timestamp": 1_700_000_000,
                },
            )
            first_msg = await first.commands.get_msg()
            second_msg = await second.commands.get_msg()
            assert first_msg.type == EventType.CONTACT_MSG_RECV
            assert first_msg.payload["text"] == "incoming-dm"
            assert second_msg.type == EventType.CONTACT_MSG_RECV
            assert second_msg.payload["text"] == "incoming-dm"
        finally:
            await first.disconnect()
            await second.disconnect()


@pytest.mark.asyncio
async def test_host_outgoing_is_not_relayed_as_incoming(test_db):
    """A message this node sent must not reach clients as one it received.

    Reported from a real conversation: a Meshloom connected to another Meshloom's
    proxy stored the operator's own replies as messages from the contact. They were
    recognisable in the database because they carried no path and no packet hash —
    they never crossed the air — yet sat there as incoming, so reading the thread
    back attributed both halves of the dialogue to the other person.
    """
    async with _started_proxy(test_db) as (manager, port):
        mc = await _connect_client(port)
        session = next(iter(manager._sessions))
        try:
            manager.notify_broadcast(
                "message",
                {
                    "id": 1,
                    "type": "PRIV",
                    "text": "something we sent",
                    "sender_key": "cd" * 32,
                    "conversation_key": "cd" * 32,
                    "sender_timestamp": 1_700_000_000,
                    "outgoing": True,
                },
            )
            await asyncio.sleep(0.15)
            assert session.dequeue_message() is None

            # The same payload arriving from the contact is still relayed.
            manager.notify_broadcast(
                "message",
                {
                    "id": 2,
                    "type": "PRIV",
                    "text": "something they sent",
                    "sender_key": "cd" * 32,
                    "conversation_key": "cd" * 32,
                    "sender_timestamp": 1_700_000_001,
                    "outgoing": False,
                },
            )
            await asyncio.sleep(0.15)
            relayed = session.dequeue_message()
            assert relayed is not None
            assert b"something they sent" in relayed.frame
        finally:
            await mc.disconnect()


@pytest.mark.asyncio
async def test_log_data_forwards_every_payload_type(test_db):
    """The radio logs everything it hears; so does the proxy standing in for it."""
    async with _started_proxy(test_db) as (manager, port):
        mc = await _connect_client(port)
        session = next(iter(manager._sessions))
        try:
            for payload_type, data in (
                ("TEXT_MESSAGE", "aabb"),
                ("GROUP_TEXT", "ccdd"),
                ("ADVERT", "eeff"),
                ("PATH", "1122"),
                ("ACK", "3344"),
                ("TRACE", "5566"),
                (None, "7788"),
            ):
                manager.notify_broadcast(
                    "raw_packet",
                    {"payload_type": payload_type, "data": data, "snr": 1.0, "rssi": -70},
                )
            await asyncio_wait_queue(session.log_queue)
            frames = []
            while not session.log_queue.empty():
                frames.append(session.log_queue.get_nowait())
            assert all(frame[0] == 136 for frame in frames)
            for expected in (
                b"\xaa\xbb",
                b"\xcc\xdd",
                b"\xee\xff",
                b"\x11\x22",
                b"\x33\x44",
                b"\x55\x66",
                b"\x77\x88",
            ):
                assert any(expected in frame for frame in frames), expected.hex()
        finally:
            await mc.disconnect()


@pytest.mark.asyncio
async def test_log_data_skips_packets_without_payload(test_db):
    """A packet the radio reported with no bytes is nothing to forward."""
    async with _started_proxy(test_db) as (manager, port):
        mc = await _connect_client(port)
        session = next(iter(manager._sessions))
        try:
            manager.notify_broadcast("raw_packet", {"payload_type": "ADVERT", "data": ""})
            manager.notify_broadcast("raw_packet", {"payload_type": "ADVERT", "data": "zz"})
            await asyncio.sleep(0.1)
            assert session.log_queue.empty()
        finally:
            await mc.disconnect()


async def asyncio_wait_queue(queue, timeout: float = 1.0):
    for _ in range(20):
        if not queue.empty():
            return
        await asyncio.sleep(timeout / 20)


@pytest.mark.asyncio
async def test_backpressure_drops_oldest():
    session = ProxySession(MagicMock(), _FakeWriter())
    for index in range(MESSAGE_QUEUE_MAX + 6):
        session.enqueue_message(QueuedMessage(kind="dm", frame=encode_ok() + bytes([index])))
    assert session.message_queue.qsize() == MESSAGE_QUEUE_MAX
    assert session.dropped_messages == 6

    logs_only = ProxySession(MagicMock(), _FakeWriter())
    for _ in range(LOG_QUEUE_MAX + 4):
        logs_only.push_log(encode_ok())
    assert logs_only.log_queue.qsize() == LOG_QUEUE_MAX
    assert logs_only.dropped_logs == 4


@pytest.mark.asyncio
async def test_self_connect_instance_id_and_local_port(test_db):
    async with _started_proxy(test_db) as (manager, port):
        assert manager.would_loop_transport("127.0.0.1", port)
        assert manager.would_loop_transport("localhost", port)
        assert (
            parse_proxy_instance_id(proxy_model_string(manager.instance_id)) == manager.instance_id
        )


@pytest.mark.asyncio
async def test_post_connect_setup_aborts_own_proxy_model():
    model = proxy_model_string(radio_proxy_manager.instance_id)
    mc = MagicMock()
    mc.commands.send_device_query = AsyncMock(
        return_value=MagicMock(
            payload={
                "fw ver": 10,
                "max_contacts": 350,
                "max_channels": 40,
                "model": model,
                "fw_build": "meshloom",
                "ver": "1.0.0",
                "path_hash_mode": 0,
            }
        )
    )
    mc.commands.get_time = AsyncMock(return_value=MagicMock(payload={"time": 1}))
    mc.commands.set_flood_scope = AsyncMock(return_value=None)
    mc.commands.send = AsyncMock(return_value=None)
    mc._reader = MagicMock()
    mc._reader.handle_rx = AsyncMock()
    mc.start_auto_message_fetching = AsyncMock()

    manager = MagicMock()
    manager.meshcore = mc
    manager._setup_lock = None
    manager._setup_in_progress = False
    manager._setup_complete = False
    manager.device_info_loaded = False
    manager.max_contacts = None
    manager.device_model = None
    manager.firmware_build = None
    manager.firmware_version = None
    manager.max_channels = 40
    manager.path_hash_mode = 0
    manager.path_hash_mode_supported = False
    manager._acquire_operation_lock = AsyncMock()
    manager._release_operation_lock = MagicMock()

    with (
        patch(
            "app.services.radio_identity.evaluate_connected_identity",
            new=AsyncMock(return_value="continue"),
        ),
        patch("app.event_handlers.register_event_handlers"),
        patch("app.keystore.export_and_store_private_key", new=AsyncMock()),
        patch("app.radio_sync.sync_radio_time", new=AsyncMock()),
        patch(
            "app.repository.AppSettingsRepository.get",
            new=AsyncMock(return_value=MagicMock(flood_scope=None)),
        ),
        pytest.raises(RuntimeError, match="own radio proxy"),
    ):
        await run_post_connect_setup(manager)

    assert manager.device_model == model


@pytest.mark.asyncio
async def test_project_host_channels_puts_public_first(test_db):
    await ChannelRepository.upsert("ff" * 16, "#zzz", is_hashtag=True)
    await ChannelRepository.upsert(PUBLIC_CHANNEL_KEY, PUBLIC_CHANNEL_NAME)
    await ChannelRepository.upsert("00" * 16, "#aaa", is_hashtag=True)
    projected = project_host_channels(await ChannelRepository.get_all())
    assert projected[0].key.upper() == PUBLIC_CHANNEL_KEY
    rest = [ch.key.upper() for ch in projected[1:]]
    assert rest == sorted(rest)
    assert "00" * 16 in rest
    assert "FF" * 16 in rest


@pytest.mark.asyncio
async def test_denied_commands_error(test_db):
    async with _started_proxy(test_db) as (_manager, port):
        mc = await _connect_client(port)
        try:
            advert = await mc.commands.send_advert()
            assert advert.type == EventType.ERROR
            imported = await mc.commands.import_private_key(b"\x00" * 64)
            assert imported.type == EventType.ERROR
        finally:
            await mc.disconnect()


def test_command_codes_match_plan():
    assert CommandType.SYNC_NEXT_MESSAGE.value == 10
    assert CommandType.EXPORT_PRIVATE_KEY.value == 23
    assert CommandType.DEVICE_QEURY.value == 22


@pytest.mark.asyncio
async def test_self_info_refuses_rather_than_inventing_an_identity(monkeypatch):
    """A proxy that does not know its own key must say so, not answer with zeros.

    It used to fall back to 32 zero bytes and the name "Meshloom". A client reads
    that as a well-formed identity, finds it different from the one it is bound to,
    and offers to erase every mesh contact and message to adopt it.
    """
    manager = RadioProxyManager()
    monkeypatch.setattr("app.keystore.get_public_key", lambda: None)
    runtime = MagicMock()
    runtime.meshcore = None
    monkeypatch.setattr("app.services.radio_runtime.radio_runtime", runtime)

    assert manager._self_public_key() is None
    assert await manager._self_info_frame() is None


@pytest.mark.asyncio
async def test_self_info_uses_the_stored_key_when_the_radio_is_silent(monkeypatch):
    manager = RadioProxyManager()
    stored = bytes.fromhex("ab" * 32)
    monkeypatch.setattr("app.keystore.get_public_key", lambda: stored)
    runtime = MagicMock()
    runtime.meshcore = None
    monkeypatch.setattr("app.services.radio_runtime.radio_runtime", runtime)

    assert manager._self_public_key() == stored
    frame = await manager._self_info_frame()
    assert frame is not None
    assert stored in frame
