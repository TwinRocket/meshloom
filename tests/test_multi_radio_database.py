"""Comprehensive multi-radio database tests: migration 086, RadioRepository, and persistence isolation."""

import time

import aiosqlite
import pytest

from app.migrations._086_multi_radio_support import migrate as migrate_086
from app.models import (
    ContactUpsert,
    RadioCreate,
    RadioUpdate,
)
from app.repository import (
    ChannelRepository,
    ContactGroupRepository,
    ContactRepository,
    ContactTelemetryRepository,
    MessageRepository,
    RadioRepository,
    RawPacketRepository,
    RepeaterPaneCacheRepository,
)


class TestMigration086:
    """Test migration 086 schema upgrade, data preservation, and idempotency."""

    @pytest.mark.asyncio
    async def test_migration_086_upgrades_v85_database_with_data(self):
        conn = await aiosqlite.connect(":memory:")
        conn.row_factory = aiosqlite.Row

        # Set up a pre-086 database (v85)
        await conn.execute("PRAGMA user_version = 85")
        await conn.execute(
            """
            CREATE TABLE app_settings (
                id INTEGER PRIMARY KEY CHECK (id = 1),
                radio_transport TEXT DEFAULT 'serial',
                radio_serial_port TEXT DEFAULT '/dev/ttyUSB0',
                radio_serial_baudrate INTEGER DEFAULT 115200,
                radio_tcp_host TEXT DEFAULT '192.168.1.100',
                radio_tcp_port INTEGER DEFAULT 4000,
                radio_ble_address TEXT DEFAULT '',
                radio_ble_pin TEXT DEFAULT ''
            )
            """
        )
        await conn.execute(
            """
            INSERT INTO app_settings (
                id, radio_transport, radio_serial_port, radio_serial_baudrate,
                radio_tcp_host, radio_tcp_port
            ) VALUES (1, 'tcp', '/dev/ttyUSB0', 115200, '192.168.1.100', 4000)
            """
        )
        await conn.execute(
            """
            CREATE TABLE contacts (
                public_key TEXT PRIMARY KEY,
                name TEXT,
                type INTEGER DEFAULT 0,
                flags INTEGER DEFAULT 0,
                direct_path TEXT,
                direct_path_len INTEGER,
                direct_path_hash_mode INTEGER,
                direct_path_updated_at INTEGER,
                route_override_path TEXT,
                route_override_len INTEGER,
                route_override_hash_mode INTEGER,
                last_advert INTEGER,
                lat REAL,
                lon REAL,
                last_seen INTEGER,
                on_radio INTEGER DEFAULT 0,
                last_contacted INTEGER,
                first_seen INTEGER,
                last_read_at INTEGER,
                favorite INTEGER DEFAULT 0,
                pinned INTEGER DEFAULT 0
            )
            """
        )
        contact_key = "0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef"
        await conn.execute(
            "INSERT INTO contacts (public_key, name, type) VALUES (?, 'Node Alice', 1)",
            (contact_key,),
        )

        await conn.execute(
            """
            CREATE TABLE channels (
                key TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                is_hashtag INTEGER DEFAULT 0,
                on_radio INTEGER DEFAULT 0,
                flood_scope_override TEXT,
                path_hash_mode_override INTEGER,
                last_read_at INTEGER,
                favorite INTEGER DEFAULT 0,
                pinned INTEGER DEFAULT 0,
                muted INTEGER DEFAULT 0,
                muted_until INTEGER,
                membership TEXT NOT NULL DEFAULT 'adopted'
            )
            """
        )
        channel_key = "abcdef0123456789abcdef0123456789"
        await conn.execute(
            "INSERT INTO channels (key, name) VALUES (?, '#general')",
            (channel_key,),
        )

        await conn.execute(
            """
            CREATE TABLE messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                type TEXT NOT NULL,
                conversation_key TEXT NOT NULL,
                text TEXT NOT NULL,
                sender_timestamp INTEGER,
                received_at INTEGER NOT NULL,
                paths TEXT,
                txt_type INTEGER DEFAULT 0,
                signature TEXT,
                outgoing INTEGER DEFAULT 0,
                acked INTEGER DEFAULT 0,
                sender_name TEXT,
                sender_key TEXT,
                transport_code INTEGER,
                region TEXT,
                packet_hash TEXT,
                observer_reach_eligible INTEGER
            )
            """
        )
        await conn.execute(
            """
            INSERT INTO messages (type, conversation_key, text, received_at)
            VALUES ('CHAN', ?, 'hello world', 1700000000)
            """,
            (channel_key,),
        )

        await conn.execute(
            """
            CREATE TABLE raw_packets (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp INTEGER NOT NULL,
                data BLOB NOT NULL,
                message_id INTEGER,
                payload_hash BLOB
            )
            """
        )
        await conn.execute(
            "INSERT INTO raw_packets (timestamp, data, payload_hash) VALUES (1700000000, X'112233', X'AABB')",
        )

        await conn.execute(
            """
            CREATE TABLE contact_advert_paths (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                public_key TEXT NOT NULL,
                path_hex TEXT NOT NULL,
                path_len INTEGER NOT NULL,
                first_seen INTEGER NOT NULL,
                last_seen INTEGER NOT NULL,
                heard_count INTEGER NOT NULL DEFAULT 1,
                FOREIGN KEY (public_key) REFERENCES contacts(public_key) ON DELETE CASCADE
            )
            """
        )
        await conn.execute(
            """
            INSERT INTO contact_advert_paths (public_key, path_hex, path_len, first_seen, last_seen)
            VALUES (?, 'AABB', 1, 1700000000, 1700000000)
            """,
            (contact_key,),
        )

        await conn.commit()

        # Run migration 086
        await migrate_086(conn)

        # Check radios table exists and seeded from app_settings
        async with conn.execute("SELECT * FROM radios WHERE id = 'default'") as cursor:
            radio = await cursor.fetchone()
        assert radio is not None
        assert radio["name"] == "Primary Radio"
        assert radio["transport"] == "tcp"
        assert radio["tcp_host"] == "192.168.1.100"
        assert radio["tcp_port"] == 4000
        assert radio["enabled"] == 1
        assert radio["auto_connect"] == 1

        # Check contacts table rebuilt with radio_id
        async with conn.execute(
            "SELECT * FROM contacts WHERE public_key = ?", (contact_key,)
        ) as cursor:
            c = await cursor.fetchone()
        assert c is not None
        assert c["radio_id"] == "default"
        assert c["name"] == "Node Alice"

        # Check channels table rebuilt with radio_id
        async with conn.execute("SELECT * FROM channels WHERE key = ?", (channel_key,)) as cursor:
            ch = await cursor.fetchone()
        assert ch is not None
        assert ch["radio_id"] == "default"
        assert ch["name"] == "#general"

        # Check messages table has radio_id
        async with conn.execute(
            "SELECT * FROM messages WHERE conversation_key = ?", (channel_key,)
        ) as cursor:
            msg = await cursor.fetchone()
        assert msg is not None
        assert msg["radio_id"] == "default"

        # Check raw_packets table has radio_id
        async with conn.execute("SELECT * FROM raw_packets WHERE id = 1") as cursor:
            rp = await cursor.fetchone()
        assert rp is not None
        assert rp["radio_id"] == "default"

        # Check child table contact_advert_paths has radio_id
        async with conn.execute(
            "SELECT * FROM contact_advert_paths WHERE public_key = ?", (contact_key,)
        ) as cursor:
            cap = await cursor.fetchone()
        assert cap is not None
        assert cap["radio_id"] == "default"

        # Verify idempotency by running migration 086 again
        await migrate_086(conn)
        async with conn.execute("SELECT COUNT(*) AS cnt FROM radios") as cursor:
            row = await cursor.fetchone()
        assert row["cnt"] == 1

        await conn.close()


class TestRadioRepository:
    """Test RadioRepository CRUD operations and cascades."""

    @pytest.mark.asyncio
    async def test_radio_crud(self, test_db):
        # Default radio already seeded on init
        default_radio = await RadioRepository.get_default()
        assert default_radio is not None
        assert default_radio.id == "default"

        # Create a new secondary radio
        new_radio = await RadioRepository.create(
            RadioCreate(
                id="radio_ble",
                name="BLE Companion",
                transport="ble",
                ble_address="AA:BB:CC:DD:EE:FF",
                enabled=True,
                auto_connect=False,
            )
        )
        assert new_radio.id == "radio_ble"
        assert new_radio.name == "BLE Companion"
        assert new_radio.transport == "ble"
        assert new_radio.ble_address == "AA:BB:CC:DD:EE:FF"
        assert not new_radio.auto_connect

        # List all radios
        radios = await RadioRepository.list_all()
        ids = [r.id for r in radios]
        assert "default" in ids
        assert "radio_ble" in ids

        # Update secondary radio
        updated = await RadioRepository.update(
            "radio_ble",
            RadioUpdate(name="BLE Companion Updated", auto_connect=True),
        )
        assert updated is not None
        assert updated.name == "BLE Companion Updated"
        assert updated.auto_connect is True

        # Cannot delete default radio
        with pytest.raises(ValueError, match="Cannot delete the default radio"):
            await RadioRepository.delete("default")

        # Delete secondary radio
        deleted = await RadioRepository.delete("radio_ble")
        assert deleted is True

        # Radio is gone
        assert await RadioRepository.get("radio_ble") is None

    @pytest.mark.asyncio
    async def test_radio_delete_with_purge(self, test_db):
        await RadioRepository.create(
            RadioCreate(id="radio_purged", name="Purge Test", transport="serial")
        )

        # Create data on radio_purged
        key = "ee" * 32
        await ContactRepository.upsert(
            ContactUpsert(public_key=key, name="Purge Contact", type=1),
            radio_id="radio_purged",
        )
        chan_key = "11" * 16
        await ChannelRepository.upsert(
            key=chan_key,
            name="#purged",
            radio_id="radio_purged",
        )
        await MessageRepository.create(
            msg_type="CHAN",
            conversation_key=chan_key,
            text="farewell",
            received_at=int(time.time()),
            radio_id="radio_purged",
        )
        await RawPacketRepository.create(
            data=b"test packet data for purge",
            radio_id="radio_purged",
        )

        assert await ContactRepository.get(key, radio_id="radio_purged") is not None
        assert await ChannelRepository.get_by_key(chan_key, radio_id="radio_purged") is not None
        assert len(await MessageRepository.get_all(radio_id="radio_purged")) == 1
        assert await RawPacketRepository.get_undecrypted_count(radio_id="radio_purged") == 1

        # Delete with purge_data=True
        await RadioRepository.delete("radio_purged", purge_data=True)

        assert await ContactRepository.get(key, radio_id="radio_purged") is None
        assert await ChannelRepository.get_by_key(chan_key, radio_id="radio_purged") is None
        assert len(await MessageRepository.get_all(radio_id="radio_purged")) == 0
        assert await RawPacketRepository.get_undecrypted_count(radio_id="radio_purged") == 0


class TestMultiRadioIsolation:
    """Test multi-radio isolation across contacts, channels, messages, packets, cache, telemetry."""

    @pytest.fixture(autouse=True)
    async def setup_secondary_radio(self, test_db):
        await RadioRepository.create(
            RadioCreate(id="radio_sec", name="Secondary Radio", transport="serial")
        )

    @pytest.mark.asyncio
    async def test_contact_isolation(self, test_db):
        key = "ab" * 32

        # Create same public_key on default and secondary radio with different names
        await ContactRepository.upsert(
            ContactUpsert(public_key=key, name="Alice Default", type=1),
            radio_id="default",
        )
        await ContactRepository.upsert(
            ContactUpsert(public_key=key, name="Alice Secondary", type=2),
            radio_id="radio_sec",
        )

        c1 = await ContactRepository.get(key, radio_id="default")
        c2 = await ContactRepository.get(key, radio_id="radio_sec")
        assert c1 is not None and c1.name == "Alice Default"
        assert c2 is not None and c2.name == "Alice Secondary"

        # List contacts on each radio
        all_def = await ContactRepository.get_all(radio_id="default")
        all_sec = await ContactRepository.get_all(radio_id="radio_sec")
        assert any(c.name == "Alice Default" for c in all_def)
        assert not any(c.name == "Alice Secondary" for c in all_def)
        assert any(c.name == "Alice Secondary" for c in all_sec)
        assert not any(c.name == "Alice Default" for c in all_sec)

        # Deleting on default does not delete on secondary
        await ContactRepository.delete(key, radio_id="default")
        assert await ContactRepository.get(key, radio_id="default") is None
        assert await ContactRepository.get(key, radio_id="radio_sec") is not None

    @pytest.mark.asyncio
    async def test_channel_isolation(self, test_db):
        ch_key = "cd" * 16

        await ChannelRepository.upsert(
            key=ch_key,
            name="#primary-chan",
            radio_id="default",
        )
        await ChannelRepository.upsert(
            key=ch_key,
            name="#secondary-chan",
            radio_id="radio_sec",
        )

        ch1 = await ChannelRepository.get_by_key(ch_key, radio_id="default")
        ch2 = await ChannelRepository.get_by_key(ch_key, radio_id="radio_sec")
        assert ch1 is not None and ch1.name == "#primary-chan"
        assert ch2 is not None and ch2.name == "#secondary-chan"

        # Update last read on default does not affect secondary
        await ChannelRepository.update_last_read_at(ch_key, timestamp=12345, radio_id="default")
        ch1 = await ChannelRepository.get_by_key(ch_key, radio_id="default")
        ch2 = await ChannelRepository.get_by_key(ch_key, radio_id="radio_sec")
        assert ch1.last_read_at == 12345
        assert ch2.last_read_at is None

    @pytest.mark.asyncio
    async def test_message_isolation(self, test_db):
        ch_key = ("ef" * 16).upper()
        now = int(time.time())

        # Messages on default
        id1 = await MessageRepository.create(
            msg_type="CHAN",
            conversation_key=ch_key,
            text="Message on default radio",
            received_at=now,
            radio_id="default",
        )
        # Messages on secondary
        id2 = await MessageRepository.create(
            msg_type="CHAN",
            conversation_key=ch_key,
            text="Message on secondary radio",
            received_at=now + 1,
            radio_id="radio_sec",
        )

        assert id1 is not None
        assert id2 is not None

        msgs_def = await MessageRepository.get_all(conversation_key=ch_key, radio_id="default")
        msgs_sec = await MessageRepository.get_all(conversation_key=ch_key, radio_id="radio_sec")

        assert len(msgs_def) == 1 and msgs_def[0].text == "Message on default radio"
        assert len(msgs_sec) == 1 and msgs_sec[0].text == "Message on secondary radio"

        # Unread counts are scoped
        await ChannelRepository.upsert(key=ch_key, name="#room", radio_id="default")
        await ChannelRepository.upsert(key=ch_key, name="#room", radio_id="radio_sec")

        unreads_def = await MessageRepository.get_unread_counts(radio_id="default")
        unreads_sec = await MessageRepository.get_unread_counts(radio_id="radio_sec")
        assert unreads_def["counts"].get(f"channel-{ch_key}") == 1
        assert unreads_sec["counts"].get(f"channel-{ch_key}") == 1

    @pytest.mark.asyncio
    async def test_raw_packet_deduplication_isolation(self, test_db):
        packet_bytes = b"identical raw packet received on both radios"

        # First radio receives it
        id1, is_new1 = await RawPacketRepository.create(packet_bytes, radio_id="default")
        assert is_new1 is True

        # Second radio receives the exact same packet payload
        id2, is_new2 = await RawPacketRepository.create(packet_bytes, radio_id="radio_sec")
        assert is_new2 is True
        assert id1 != id2

        # Duplicate on default returns existing id1
        dup_id1, is_new_dup = await RawPacketRepository.create(packet_bytes, radio_id="default")
        assert is_new_dup is False
        assert dup_id1 == id1

        # Count is 1 per radio
        assert await RawPacketRepository.get_undecrypted_count(radio_id="default") == 1
        assert await RawPacketRepository.get_undecrypted_count(radio_id="radio_sec") == 1

    @pytest.mark.asyncio
    async def test_repeater_pane_cache_isolation(self, test_db):
        rep_key = "12" * 32
        await ContactRepository.upsert(
            ContactUpsert(public_key=rep_key, name="Repeater 1", type=2),
            radio_id="default",
        )
        await ContactRepository.upsert(
            ContactUpsert(public_key=rep_key, name="Repeater 1", type=2),
            radio_id="radio_sec",
        )

        await RepeaterPaneCacheRepository.put(
            rep_key, "status", {"uptime": 100}, radio_id="default"
        )
        await RepeaterPaneCacheRepository.put(
            rep_key, "status", {"uptime": 500}, radio_id="radio_sec"
        )

        panes_def = await RepeaterPaneCacheRepository.get_all(rep_key, radio_id="default")
        panes_sec = await RepeaterPaneCacheRepository.get_all(rep_key, radio_id="radio_sec")

        assert panes_def["status"]["data"]["uptime"] == 100
        assert panes_sec["status"]["data"]["uptime"] == 500

    @pytest.mark.asyncio
    async def test_telemetry_isolation(self, test_db):
        node_key = "34" * 32
        now = int(time.time())

        await ContactRepository.upsert(
            ContactUpsert(public_key=node_key, name="Telemetry Node", type=1),
            radio_id="default",
        )
        await ContactRepository.upsert(
            ContactUpsert(public_key=node_key, name="Telemetry Node", type=1),
            radio_id="radio_sec",
        )

        await ContactTelemetryRepository.record(node_key, now, {"voltage": 3.7}, radio_id="default")
        await ContactTelemetryRepository.record(
            node_key, now, {"voltage": 4.1}, radio_id="radio_sec"
        )

        latest_def = await ContactTelemetryRepository.get_latest(node_key, radio_id="default")
        latest_sec = await ContactTelemetryRepository.get_latest(node_key, radio_id="radio_sec")

        assert latest_def["data"]["voltage"] == 3.7
        assert latest_sec["data"]["voltage"] == 4.1

    @pytest.mark.asyncio
    async def test_contact_group_membership_isolation(self, test_db):
        key1 = "55" * 32
        key2 = "66" * 32

        await ContactRepository.upsert(
            ContactUpsert(public_key=key1, name="Alice", type=1),
            radio_id="default",
        )
        await ContactRepository.upsert(
            ContactUpsert(public_key=key2, name="Bob", type=1),
            radio_id="radio_sec",
        )

        group = await ContactGroupRepository.create("Field Ops")

        # Set members on default radio
        await ContactGroupRepository.set_members(group.id, [key1], radio_id="default")
        # Set members on secondary radio
        await ContactGroupRepository.set_members(group.id, [key2], radio_id="radio_sec")

        g_def = await ContactGroupRepository.get_by_id(group.id, radio_id="default")
        g_sec = await ContactGroupRepository.get_by_id(group.id, radio_id="radio_sec")

        assert g_def.public_keys == [key1]
        assert g_sec.public_keys == [key2]
