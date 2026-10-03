import logging
import time

import aiosqlite

logger = logging.getLogger(__name__)


async def migrate(conn: aiosqlite.Connection) -> None:
    """Multi-radio persistence isolation: radios table and radio_id on all radio-scoped tables."""
    # Ensure foreign keys are OFF during table rebuilds
    await conn.execute("PRAGMA foreign_keys = OFF")

    tables_cursor = await conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
    existing_tables = {row[0] for row in await tables_cursor.fetchall()}

    # 1. Create radios table
    await conn.execute(
        """
        CREATE TABLE IF NOT EXISTS radios (
            id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            transport TEXT,
            serial_port TEXT,
            serial_baudrate INTEGER DEFAULT 115200,
            tcp_host TEXT,
            tcp_port INTEGER,
            ble_address TEXT,
            ble_pin TEXT,
            enabled INTEGER NOT NULL DEFAULT 1,
            auto_connect INTEGER NOT NULL DEFAULT 1,
            bound_public_key TEXT,
            identity_state TEXT,
            created_at INTEGER NOT NULL,
            updated_at INTEGER NOT NULL,
            last_connected_at INTEGER,
            sort_order INTEGER NOT NULL DEFAULT 0
        )
        """
    )

    # 2. Seed default radio if radios table is empty
    count_cur = await conn.execute("SELECT COUNT(*) FROM radios")
    row = await count_cur.fetchone()
    radios_count = row[0] if row else 0
    if radios_count == 0:
        transport = None
        serial_port = ""
        serial_baudrate = 115200
        tcp_host = ""
        tcp_port = 5000
        ble_address = ""
        ble_pin = ""
        bound_public_key = None
        identity_state = "normal"

        if "app_settings" in existing_tables:
            col_cursor = await conn.execute("PRAGMA table_info(app_settings)")
            app_cols = {row[1] for row in await col_cursor.fetchall()}
            if "radio_transport" in app_cols:
                async with conn.execute("SELECT * FROM app_settings WHERE id = 1") as cursor:
                    row = await cursor.fetchone()
                    if row:
                        row_dict = dict(row)
                        transport = row_dict.get("radio_transport")
                        serial_port = row_dict.get("radio_serial_port") or ""
                        serial_baudrate = row_dict.get("radio_serial_baudrate") or 115200
                        tcp_host = row_dict.get("radio_tcp_host") or ""
                        tcp_port = row_dict.get("radio_tcp_port") or 5000
                        ble_address = row_dict.get("radio_ble_address") or ""
                        ble_pin = row_dict.get("radio_ble_pin") or ""
                        bound_public_key = row_dict.get("radio_bound_public_key")
                        identity_state = row_dict.get("radio_identity_state") or "normal"

        now = int(time.time())
        await conn.execute(
            """
            INSERT INTO radios (
                id, name, transport, serial_port, serial_baudrate,
                tcp_host, tcp_port, ble_address, ble_pin,
                enabled, auto_connect, bound_public_key, identity_state,
                created_at, updated_at, last_connected_at, sort_order
            ) VALUES (
                'default', 'Primary Radio', ?, ?, ?,
                ?, ?, ?, ?,
                1, 1, ?, ?,
                ?, ?, NULL, 0
            )
            """,
            (
                transport,
                serial_port,
                serial_baudrate,
                tcp_host,
                tcp_port,
                ble_address,
                ble_pin,
                bound_public_key,
                identity_state,
                now,
                now,
            ),
        )
        logger.info("Seeded default radio into radios table")

    # Clean orphaned child rows before rebuilding tables to avoid foreign key violations
    if "contacts" in existing_tables:
        for child_table in (
            "contact_advert_paths",
            "contact_name_history",
            "repeater_telemetry_history",
            "contact_telemetry_history",
            "repeater_pane_cache",
            "contact_group_members",
        ):
            if child_table in existing_tables:
                await conn.execute(
                    f"DELETE FROM {child_table} WHERE public_key NOT IN (SELECT public_key FROM contacts)"
                )

    # 3. Rebuild contacts table: PK (radio_id, public_key)
    if "contacts" in existing_tables:
        col_cur = await conn.execute("PRAGMA table_info(contacts)")
        cols_info = {row[1]: row for row in await col_cur.fetchall()}
        needs_contacts_rebuild = not ("radio_id" in cols_info and cols_info["radio_id"][5] > 0)
        if needs_contacts_rebuild:
            await conn.execute(
                """
                CREATE TABLE contacts_v86 (
                    radio_id TEXT NOT NULL DEFAULT 'default',
                    public_key TEXT NOT NULL,
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
                    pinned INTEGER DEFAULT 0,
                    PRIMARY KEY (radio_id, public_key)
                )
                """
            )
            target_cols = [
                "public_key",
                "name",
                "type",
                "flags",
                "direct_path",
                "direct_path_len",
                "direct_path_hash_mode",
                "direct_path_updated_at",
                "route_override_path",
                "route_override_len",
                "route_override_hash_mode",
                "last_advert",
                "lat",
                "lon",
                "last_seen",
                "on_radio",
                "last_contacted",
                "first_seen",
                "last_read_at",
                "favorite",
                "pinned",
            ]
            select_exprs = [
                "COALESCE(radio_id, 'default') AS radio_id"
                if "radio_id" in cols_info
                else "'default' AS radio_id"
            ]
            insert_cols = ["radio_id"]
            for col in target_cols:
                if col in cols_info:
                    select_exprs.append(col)
                    insert_cols.append(col)

            await conn.execute(
                f"""
                INSERT INTO contacts_v86 ({", ".join(insert_cols)})
                SELECT {", ".join(select_exprs)} FROM contacts
                """
            )
            await conn.execute("DROP TABLE contacts")
            await conn.execute("ALTER TABLE contacts_v86 RENAME TO contacts")
            await conn.execute("DROP INDEX IF EXISTS idx_contacts_type_last_seen")
            await conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_contacts_type_last_seen ON contacts(radio_id, type, last_seen)"
            )
            logger.info("Rebuilt contacts table with PRIMARY KEY (radio_id, public_key)")

    # 4. Rebuild channels table: PK (radio_id, key)
    if "channels" in existing_tables:
        col_cur = await conn.execute("PRAGMA table_info(channels)")
        cols_info = {row[1]: row for row in await col_cur.fetchall()}
        needs_channels_rebuild = not ("radio_id" in cols_info and cols_info["radio_id"][5] > 0)
        if needs_channels_rebuild:
            await conn.execute(
                """
                CREATE TABLE channels_v86 (
                    radio_id TEXT NOT NULL DEFAULT 'default',
                    key TEXT NOT NULL,
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
                    membership TEXT NOT NULL DEFAULT 'adopted',
                    PRIMARY KEY (radio_id, key)
                )
                """
            )
            target_cols = [
                "key",
                "name",
                "is_hashtag",
                "on_radio",
                "flood_scope_override",
                "path_hash_mode_override",
                "last_read_at",
                "favorite",
                "pinned",
                "muted",
                "muted_until",
                "membership",
            ]
            select_exprs = [
                "COALESCE(radio_id, 'default') AS radio_id"
                if "radio_id" in cols_info
                else "'default' AS radio_id"
            ]
            insert_cols = ["radio_id"]
            for col in target_cols:
                if col in cols_info:
                    select_exprs.append(col)
                    insert_cols.append(col)

            await conn.execute(
                f"""
                INSERT INTO channels_v86 ({", ".join(insert_cols)})
                SELECT {", ".join(select_exprs)} FROM channels
                """
            )
            await conn.execute("DROP TABLE channels")
            await conn.execute("ALTER TABLE channels_v86 RENAME TO channels")
            logger.info("Rebuilt channels table with PRIMARY KEY (radio_id, key)")

    # 5. messages table: add radio_id and update deduplication indices
    if "messages" in existing_tables:
        col_cur = await conn.execute("PRAGMA table_info(messages)")
        msg_cols = {row[1] for row in await col_cur.fetchall()}
        if "radio_id" not in msg_cols:
            await conn.execute(
                "ALTER TABLE messages ADD COLUMN radio_id TEXT NOT NULL DEFAULT 'default'"
            )
            logger.info("Added radio_id to messages")

        await conn.execute("DROP INDEX IF EXISTS idx_messages_dedup_null_safe")
        await conn.execute("DROP INDEX IF EXISTS idx_messages_incoming_priv_dedup")
        await conn.execute("DROP INDEX IF EXISTS idx_messages_pagination")
        await conn.execute("DROP INDEX IF EXISTS idx_messages_unread_covering")

        if {"type", "conversation_key", "text"}.issubset(msg_cols):
            await conn.execute(
                """
                CREATE UNIQUE INDEX IF NOT EXISTS idx_messages_dedup_null_safe
                    ON messages(radio_id, type, conversation_key, text, COALESCE(sender_timestamp, 0))
                    WHERE type = 'CHAN'
                """
            )
        if {"type", "conversation_key", "text", "sender_key", "outgoing"}.issubset(msg_cols):
            await conn.execute(
                """
                CREATE UNIQUE INDEX IF NOT EXISTS idx_messages_incoming_priv_dedup
                    ON messages(radio_id, type, conversation_key, text, COALESCE(sender_timestamp, 0), COALESCE(sender_key, ''))
                    WHERE type = 'PRIV' AND outgoing = 0
                """
            )
        if {"type", "conversation_key", "received_at"}.issubset(msg_cols):
            await conn.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_messages_pagination
                    ON messages(radio_id, type, conversation_key, received_at DESC, id DESC)
                """
            )
        if {"type", "conversation_key", "outgoing", "received_at"}.issubset(msg_cols):
            await conn.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_messages_unread_covering
                    ON messages(radio_id, type, conversation_key, outgoing, received_at)
                """
            )
        logger.info("Updated messages deduplication and pagination indexes")

    # 6. raw_packets table: add radio_id and unique index on (radio_id, payload_hash)
    if "raw_packets" in existing_tables:
        col_cur = await conn.execute("PRAGMA table_info(raw_packets)")
        pkt_cols = {row[1] for row in await col_cur.fetchall()}
        if "radio_id" not in pkt_cols:
            await conn.execute(
                "ALTER TABLE raw_packets ADD COLUMN radio_id TEXT NOT NULL DEFAULT 'default'"
            )
            logger.info("Added radio_id to raw_packets")

        await conn.execute("DROP INDEX IF EXISTS idx_raw_packets_payload_hash")
        if "payload_hash" in pkt_cols:
            await conn.execute(
                """
                CREATE UNIQUE INDEX IF NOT EXISTS idx_raw_packets_payload_hash
                    ON raw_packets(radio_id, payload_hash)
                """
            )
            logger.info("Updated raw_packets unique index")

    # 7. Child tables
    # 7.1 contact_advert_paths
    if "contact_advert_paths" in existing_tables:
        col_cur = await conn.execute("PRAGMA table_info(contact_advert_paths)")
        cap_cols = {row[1] for row in await col_cur.fetchall()}
        if "radio_id" not in cap_cols:
            await conn.execute(
                """
                CREATE TABLE contact_advert_paths_v86 (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    radio_id TEXT NOT NULL DEFAULT 'default',
                    public_key TEXT NOT NULL,
                    path_hex TEXT NOT NULL,
                    path_len INTEGER NOT NULL,
                    first_seen INTEGER NOT NULL,
                    last_seen INTEGER NOT NULL,
                    heard_count INTEGER NOT NULL DEFAULT 1,
                    UNIQUE(radio_id, public_key, path_hex, path_len),
                    FOREIGN KEY (radio_id, public_key) REFERENCES contacts(radio_id, public_key) ON DELETE CASCADE
                )
                """
            )
            await conn.execute(
                """
                INSERT INTO contact_advert_paths_v86
                    (id, radio_id, public_key, path_hex, path_len, first_seen, last_seen, heard_count)
                SELECT id, 'default', public_key, path_hex, path_len, first_seen, last_seen, heard_count
                FROM contact_advert_paths
                """
            )
            await conn.execute("DROP TABLE contact_advert_paths")
            await conn.execute(
                "ALTER TABLE contact_advert_paths_v86 RENAME TO contact_advert_paths"
            )
            await conn.execute("DROP INDEX IF EXISTS idx_contact_advert_paths_recent")
            await conn.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_contact_advert_paths_recent
                    ON contact_advert_paths(radio_id, public_key, last_seen DESC)
                """
            )
            logger.info("Rebuilt contact_advert_paths with radio_id")

    # 7.2 contact_name_history
    if "contact_name_history" in existing_tables:
        col_cur = await conn.execute("PRAGMA table_info(contact_name_history)")
        cnh_cols = {row[1] for row in await col_cur.fetchall()}
        if "radio_id" not in cnh_cols:
            await conn.execute(
                """
                CREATE TABLE contact_name_history_v86 (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    radio_id TEXT NOT NULL DEFAULT 'default',
                    public_key TEXT NOT NULL,
                    name TEXT NOT NULL,
                    first_seen INTEGER NOT NULL,
                    last_seen INTEGER NOT NULL,
                    UNIQUE(radio_id, public_key, name),
                    FOREIGN KEY (radio_id, public_key) REFERENCES contacts(radio_id, public_key) ON DELETE CASCADE
                )
                """
            )
            await conn.execute(
                """
                INSERT INTO contact_name_history_v86
                    (id, radio_id, public_key, name, first_seen, last_seen)
                SELECT id, 'default', public_key, name, first_seen, last_seen
                FROM contact_name_history
                """
            )
            await conn.execute("DROP TABLE contact_name_history")
            await conn.execute(
                "ALTER TABLE contact_name_history_v86 RENAME TO contact_name_history"
            )
            await conn.execute("DROP INDEX IF EXISTS idx_contact_name_history_key")
            await conn.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_contact_name_history_key
                    ON contact_name_history(radio_id, public_key, last_seen DESC)
                """
            )
            logger.info("Rebuilt contact_name_history with radio_id")

    # 7.3 repeater_telemetry_history
    if "repeater_telemetry_history" in existing_tables:
        col_cur = await conn.execute("PRAGMA table_info(repeater_telemetry_history)")
        rth_cols = {row[1] for row in await col_cur.fetchall()}
        if "radio_id" not in rth_cols:
            await conn.execute(
                """
                CREATE TABLE repeater_telemetry_history_v86 (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    radio_id TEXT NOT NULL DEFAULT 'default',
                    public_key TEXT NOT NULL,
                    timestamp INTEGER NOT NULL,
                    data TEXT NOT NULL,
                    FOREIGN KEY (radio_id, public_key) REFERENCES contacts(radio_id, public_key) ON DELETE CASCADE
                )
                """
            )
            await conn.execute(
                """
                INSERT INTO repeater_telemetry_history_v86
                    (id, radio_id, public_key, timestamp, data)
                SELECT id, 'default', public_key, timestamp, data
                FROM repeater_telemetry_history
                """
            )
            await conn.execute("DROP TABLE repeater_telemetry_history")
            await conn.execute(
                "ALTER TABLE repeater_telemetry_history_v86 RENAME TO repeater_telemetry_history"
            )
            await conn.execute("DROP INDEX IF EXISTS idx_repeater_telemetry_pk_ts")
            await conn.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_repeater_telemetry_pk_ts
                    ON repeater_telemetry_history(radio_id, public_key, timestamp)
                """
            )
            logger.info("Rebuilt repeater_telemetry_history with radio_id")

    # 7.4 contact_telemetry_history
    if "contact_telemetry_history" in existing_tables:
        col_cur = await conn.execute("PRAGMA table_info(contact_telemetry_history)")
        cth_cols = {row[1] for row in await col_cur.fetchall()}
        if "radio_id" not in cth_cols:
            await conn.execute(
                """
                CREATE TABLE contact_telemetry_history_v86 (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    radio_id TEXT NOT NULL DEFAULT 'default',
                    public_key TEXT NOT NULL,
                    timestamp INTEGER NOT NULL,
                    data TEXT NOT NULL,
                    FOREIGN KEY (radio_id, public_key) REFERENCES contacts(radio_id, public_key) ON DELETE CASCADE
                )
                """
            )
            await conn.execute(
                """
                INSERT INTO contact_telemetry_history_v86
                    (id, radio_id, public_key, timestamp, data)
                SELECT id, 'default', public_key, timestamp, data
                FROM contact_telemetry_history
                """
            )
            await conn.execute("DROP TABLE contact_telemetry_history")
            await conn.execute(
                "ALTER TABLE contact_telemetry_history_v86 RENAME TO contact_telemetry_history"
            )
            await conn.execute("DROP INDEX IF EXISTS idx_contact_telemetry_pk_ts")
            await conn.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_contact_telemetry_pk_ts
                    ON contact_telemetry_history(radio_id, public_key, timestamp)
                """
            )
            logger.info("Rebuilt contact_telemetry_history with radio_id")

    # 7.5 repeater_pane_cache
    if "repeater_pane_cache" in existing_tables:
        col_cur = await conn.execute("PRAGMA table_info(repeater_pane_cache)")
        rpc_cols = {row[1] for row in await col_cur.fetchall()}
        if "radio_id" not in rpc_cols:
            await conn.execute(
                """
                CREATE TABLE repeater_pane_cache_v86 (
                    radio_id TEXT NOT NULL DEFAULT 'default',
                    public_key TEXT NOT NULL,
                    pane TEXT NOT NULL,
                    data TEXT NOT NULL,
                    fetched_at INTEGER NOT NULL,
                    PRIMARY KEY (radio_id, public_key, pane),
                    FOREIGN KEY (radio_id, public_key) REFERENCES contacts(radio_id, public_key) ON DELETE CASCADE
                )
                """
            )
            await conn.execute(
                """
                INSERT INTO repeater_pane_cache_v86
                    (radio_id, public_key, pane, data, fetched_at)
                SELECT 'default', public_key, pane, data, fetched_at
                FROM repeater_pane_cache
                """
            )
            await conn.execute("DROP TABLE repeater_pane_cache")
            await conn.execute("ALTER TABLE repeater_pane_cache_v86 RENAME TO repeater_pane_cache")
            await conn.execute("DROP INDEX IF EXISTS idx_repeater_pane_cache_pk")
            await conn.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_repeater_pane_cache_pk
                    ON repeater_pane_cache (radio_id, public_key)
                """
            )
            logger.info("Rebuilt repeater_pane_cache with radio_id")

    # 7.6 contact_group_members
    if "contact_group_members" in existing_tables:
        col_cur = await conn.execute("PRAGMA table_info(contact_group_members)")
        cgm_cols = {row[1] for row in await col_cur.fetchall()}
        if "radio_id" not in cgm_cols:
            await conn.execute(
                """
                CREATE TABLE contact_group_members_v86 (
                    group_id INTEGER NOT NULL,
                    radio_id TEXT NOT NULL DEFAULT 'default',
                    public_key TEXT NOT NULL,
                    PRIMARY KEY (group_id, radio_id, public_key),
                    FOREIGN KEY (group_id) REFERENCES contact_groups(id) ON DELETE CASCADE,
                    FOREIGN KEY (radio_id, public_key) REFERENCES contacts(radio_id, public_key) ON DELETE CASCADE
                )
                """
            )
            await conn.execute(
                """
                INSERT INTO contact_group_members_v86
                    (group_id, radio_id, public_key)
                SELECT group_id, 'default', public_key
                FROM contact_group_members
                """
            )
            await conn.execute("DROP TABLE contact_group_members")
            await conn.execute(
                "ALTER TABLE contact_group_members_v86 RENAME TO contact_group_members"
            )
            await conn.execute("DROP INDEX IF EXISTS idx_contact_group_members_key")
            await conn.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_contact_group_members_key
                    ON contact_group_members(radio_id, public_key)
                """
            )
            logger.info("Rebuilt contact_group_members with radio_id")

    # 7.7 telemetry_alert_state
    if "telemetry_alert_state" in existing_tables:
        col_cur = await conn.execute("PRAGMA table_info(telemetry_alert_state)")
        tas_cols = {row[1] for row in await col_cur.fetchall()}
        if "radio_id" not in tas_cols:
            await conn.execute(
                """
                CREATE TABLE telemetry_alert_state_v86 (
                    radio_id TEXT NOT NULL DEFAULT 'default',
                    public_key TEXT NOT NULL,
                    rule_id TEXT NOT NULL,
                    consecutive_misses INTEGER NOT NULL DEFAULT 0,
                    last_fired_at INTEGER,
                    last_value REAL,
                    PRIMARY KEY (radio_id, public_key, rule_id),
                    FOREIGN KEY (radio_id, public_key) REFERENCES contacts(radio_id, public_key) ON DELETE CASCADE
                )
                """
            )
            await conn.execute(
                """
                INSERT INTO telemetry_alert_state_v86
                    (radio_id, public_key, rule_id, consecutive_misses, last_fired_at, last_value)
                SELECT 'default', public_key, rule_id, consecutive_misses, last_fired_at, last_value
                FROM telemetry_alert_state
                """
            )
            await conn.execute("DROP TABLE telemetry_alert_state")
            await conn.execute(
                "ALTER TABLE telemetry_alert_state_v86 RENAME TO telemetry_alert_state"
            )
            logger.info("Rebuilt telemetry_alert_state with radio_id")

    await conn.commit()
