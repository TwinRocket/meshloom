-- A fresh database made by Meshloom 4.18.3 (schema 95, PRAGMA user_version = 95),
-- dumped with sqlite3 .dump. Loaded by tests/test_first_run_hardening.py, which
-- sets user_version (a dump does not keep it) and lets this code migrate it.
PRAGMA foreign_keys=OFF;
BEGIN TRANSACTION;
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
);
INSERT INTO channels VALUES('D8F3E005453E56988DE0A853348640F7','#meshloom',1,0,NULL,NULL,NULL,0,0,0,NULL,'adopted');
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
    packet_hash TEXT,
    observer_reach_eligible INTEGER
    -- Deduplication: channel echoes/repeats use a content/time unique index so
    -- duplicate observations reconcile onto a single stored row. Legacy
    -- databases may also gain an incoming-DM content index via migration 44.
    -- Enforced via idx_messages_dedup_null_safe (unique index) rather than a table constraint
    -- to avoid the storage overhead of SQLite's autoindex duplicating every message text.
, transport_code INTEGER, region TEXT);
CREATE TABLE app_settings (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    max_radio_contacts INTEGER DEFAULT 200,
    auto_decrypt_dm_on_advert INTEGER DEFAULT 1,
    last_message_times TEXT DEFAULT '{}',
    preferences_migrated INTEGER DEFAULT 0,
    advert_interval INTEGER DEFAULT 0,
    last_advert_time INTEGER DEFAULT 0,
    flood_scope TEXT DEFAULT '',
    blocked_keys TEXT DEFAULT '[]',
    blocked_names TEXT DEFAULT '[]',
    discovery_blocked_types TEXT DEFAULT '[]',
    tracked_telemetry_repeaters TEXT DEFAULT '[]',
    auto_resend_channel INTEGER DEFAULT 0,
    telemetry_interval_hours INTEGER DEFAULT 8,
    vapid_private_key TEXT DEFAULT '',
    vapid_public_key TEXT DEFAULT '',
    push_conversations TEXT DEFAULT '[]',
    stale_contact_days INTEGER DEFAULT 0,
    raw_packet_retention_days INTEGER DEFAULT 0,
    community_enabled INTEGER DEFAULT 0,
    community_iata TEXT DEFAULT '',
    community_broker_host TEXT DEFAULT '',
    community_api_base TEXT DEFAULT '',
    push_defaults TEXT DEFAULT '{"new_contact": true, "new_dm": true, "advert_repeater": true, "advert_companion": true, "advert_sensor": true}',
    push_conversation_overrides TEXT DEFAULT '{}',
    vapid_subject TEXT DEFAULT '',
    radio_proxy_enabled INTEGER DEFAULT 0,
    radio_proxy_bind TEXT DEFAULT '0.0.0.0',
    radio_proxy_port INTEGER DEFAULT 5001,
    radio_proxy_max_clients INTEGER DEFAULT 8,
    rejected_channels TEXT DEFAULT '[]'
, telemetry_routed_hourly INTEGER DEFAULT 0, tracked_telemetry_contacts TEXT DEFAULT '[]', known_regions TEXT, radio_transport TEXT, radio_serial_port TEXT DEFAULT '', radio_serial_baudrate INTEGER DEFAULT 115200, radio_tcp_host TEXT DEFAULT '', radio_tcp_port INTEGER DEFAULT 5000, radio_ble_address TEXT DEFAULT '', radio_ble_pin TEXT DEFAULT '', radio_bound_public_key TEXT, radio_identity_state TEXT, radio_previous_transport TEXT, radio_mismatch_previous_public_key TEXT, radio_mismatch_new_public_key TEXT, radio_mismatch_new_name TEXT, radio_transport_env_imported INTEGER DEFAULT 0, ui_preferences TEXT DEFAULT '{}', telemetry_alert_rules TEXT DEFAULT '{"battery_volts_min": 3.5, "noise_floor_max_dbm": -90, "misses_before_alert": 2, "overrides": {}}', auto_update INTEGER NOT NULL DEFAULT 0, auto_update_window_start TEXT NOT NULL DEFAULT '00:00', auto_update_window_end TEXT NOT NULL DEFAULT '00:00', auto_update_weekdays TEXT NOT NULL DEFAULT '[0,1,2,3,4,5,6]', last_notified_update_version TEXT, path_hash_one_byte_opt_in INTEGER NOT NULL DEFAULT 0, notification_destinations TEXT DEFAULT '{"email": {"host": "", "port": 587, "mode": "starttls", "user": "", "password": "", "from": "", "to": ""}, "webhook": {"url": "", "hmac_secret": ""}}', community_status_clear TEXT DEFAULT '[]', community_status_last TEXT DEFAULT '', enable_message_poll_fallback INTEGER DEFAULT 0, force_channel_slot_reconfigure INTEGER DEFAULT 0, load_with_autoevict INTEGER DEFAULT 0, legacy_env_settings_imported INTEGER DEFAULT 0, community_env_imported INTEGER DEFAULT 0, community_waiting_for_position INTEGER DEFAULT 0, community_opt_out_pending TEXT DEFAULT '', auto_clock_wraparound INTEGER DEFAULT 1, wifi_prompt_state TEXT DEFAULT '{}');
INSERT INTO app_settings VALUES(1,200,1,'{}',0,0,0,'','[]','[]','[]','[]',0,8,'','','[]',0,0,0,'','','','{"new_contact": true, "new_dm": true, "advert_repeater": true, "advert_companion": true, "advert_sensor": true}','{}','',0,'0.0.0.0',5001,8,'[]',0,'[]','[]',NULL,'',115200,'',5000,'','',NULL,NULL,NULL,NULL,NULL,NULL,0,'{}','{"channels": {"push": true, "email": false, "webhook": false}, "rules": {"battery": {"enabled": true, "op": "lt", "threshold": 3.5, "hysteresis": 0.2}, "noise": {"enabled": true, "op": "gt", "threshold": -90.0, "hysteresis": 3.0}, "rssi": {"enabled": false, "op": "lt", "threshold": -120.0, "hysteresis": 5.0}, "snr": {"enabled": false, "op": "lt", "threshold": 0.0, "hysteresis": 2.0}, "tx_queue": {"enabled": false, "op": "gt", "threshold": 10.0, "hysteresis": 2.0}, "silence": {"enabled": true, "threshold": 2.0}, "gps_lost": {"enabled": true}, "lpp:temperature": {"enabled": false, "op": "gt", "threshold": 50.0, "hysteresis": 1.0}}, "overrides": {}}',0,'00:00','00:00','[0,1,2,3,4,5,6]',NULL,0,'{"email": {"host": "", "port": 587, "mode": "starttls", "user": "", "password": "", "from": "", "to": ""}, "webhook": {"url": "", "hmac_secret": ""}}','[]','',0,0,0,0,0,0,'',1,'{}');
CREATE TABLE contact_groups (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    sort_order INTEGER NOT NULL DEFAULT 0,
    created_at INTEGER NOT NULL
);
CREATE TABLE contact_group_members (
    group_id INTEGER NOT NULL,
    public_key TEXT NOT NULL,
    PRIMARY KEY (group_id, public_key),
    FOREIGN KEY (group_id) REFERENCES contact_groups(id) ON DELETE CASCADE,
    FOREIGN KEY (public_key) REFERENCES contacts(public_key) ON DELETE CASCADE
);
CREATE TABLE directory_hop_cache (
    prefix TEXT NOT NULL,
    hash_width INTEGER NOT NULL,
    name TEXT,
    source TEXT NOT NULL,
    expires_at INTEGER NOT NULL,
    public_key TEXT,
    lat REAL,
    lon REAL,
    PRIMARY KEY (prefix, hash_width)
);
CREATE TABLE fanout_configs (
    id TEXT PRIMARY KEY,
    type TEXT NOT NULL,
    name TEXT NOT NULL,
    enabled INTEGER DEFAULT 0,
    config TEXT NOT NULL DEFAULT '{}',
    scope TEXT NOT NULL DEFAULT '{}',
    sort_order INTEGER DEFAULT 0,
    created_at INTEGER NOT NULL
);
CREATE TABLE repeater_telemetry_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    public_key TEXT NOT NULL,
    timestamp INTEGER NOT NULL,
    data TEXT NOT NULL,
    FOREIGN KEY (public_key) REFERENCES contacts(public_key) ON DELETE CASCADE
);
CREATE TABLE push_subscriptions (
    id TEXT PRIMARY KEY,
    endpoint TEXT NOT NULL,
    p256dh TEXT NOT NULL,
    auth TEXT NOT NULL,
    label TEXT NOT NULL DEFAULT '',
    language TEXT NOT NULL DEFAULT 'fr',
    created_at INTEGER NOT NULL,
    last_success_at INTEGER,
    failure_count INTEGER DEFAULT 0,
    UNIQUE(endpoint)
);
CREATE TABLE IF NOT EXISTS "contacts" (
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
            last_read_at INTEGER
        , favorite INTEGER DEFAULT 0, pinned INTEGER DEFAULT 0);
CREATE TABLE IF NOT EXISTS "raw_packets" (id INTEGER PRIMARY KEY AUTOINCREMENT, timestamp INTEGER NOT NULL, data BLOB NOT NULL, message_id INTEGER, payload_hash BLOB, FOREIGN KEY (message_id) REFERENCES messages(id) ON DELETE SET NULL);
CREATE TABLE IF NOT EXISTS "contact_advert_paths" (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                public_key TEXT NOT NULL,
                path_hex TEXT NOT NULL,
                path_len INTEGER NOT NULL,
                first_seen INTEGER NOT NULL,
                last_seen INTEGER NOT NULL,
                heard_count INTEGER NOT NULL DEFAULT 1,
                UNIQUE(public_key, path_hex, path_len),
                FOREIGN KEY (public_key) REFERENCES contacts(public_key) ON DELETE CASCADE
            );
CREATE TABLE IF NOT EXISTS "contact_name_history" (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                public_key TEXT NOT NULL,
                name TEXT NOT NULL,
                first_seen INTEGER NOT NULL,
                last_seen INTEGER NOT NULL,
                UNIQUE(public_key, name),
                FOREIGN KEY (public_key) REFERENCES contacts(public_key) ON DELETE CASCADE
            );
CREATE TABLE contact_telemetry_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                public_key TEXT NOT NULL,
                timestamp INTEGER NOT NULL,
                data TEXT NOT NULL,
                FOREIGN KEY (public_key) REFERENCES contacts(public_key) ON DELETE CASCADE
            );
CREATE TABLE repeater_pane_cache (
            public_key TEXT NOT NULL,
            pane TEXT NOT NULL,
            data TEXT NOT NULL,
            fetched_at INTEGER NOT NULL,
            PRIMARY KEY (public_key, pane),
            FOREIGN KEY (public_key) REFERENCES contacts(public_key) ON DELETE CASCADE
        );
CREATE TABLE telemetry_alert_state (
                public_key TEXT NOT NULL,
                rule_id TEXT NOT NULL,
                consecutive_misses INTEGER NOT NULL DEFAULT 0,
                last_fired_at INTEGER,
                last_value REAL,
                PRIMARY KEY (public_key, rule_id),
                FOREIGN KEY (public_key) REFERENCES contacts(public_key) ON DELETE CASCADE
            );
INSERT INTO sqlite_sequence VALUES('raw_packets',0);
INSERT INTO sqlite_sequence VALUES('contact_advert_paths',0);
INSERT INTO sqlite_sequence VALUES('contact_name_history',0);
CREATE INDEX idx_messages_sender_key ON messages(sender_key);
CREATE INDEX idx_messages_unread_covering ON messages(type, conversation_key, outgoing, received_at);
CREATE INDEX idx_messages_pagination ON messages(type, conversation_key, received_at DESC, id DESC);
CREATE UNIQUE INDEX idx_messages_dedup_null_safe
           ON messages(type, conversation_key, text, COALESCE(sender_timestamp, 0))
           WHERE type = 'CHAN';
CREATE INDEX idx_contacts_type_last_seen ON contacts(type, last_seen);
CREATE INDEX idx_messages_type_received_conversation
                ON messages(type, received_at, conversation_key)
                ;
CREATE INDEX idx_raw_packets_message_id ON raw_packets(message_id);
CREATE INDEX idx_raw_packets_timestamp ON raw_packets(timestamp);
CREATE UNIQUE INDEX idx_raw_packets_payload_hash ON raw_packets(payload_hash);
CREATE INDEX idx_contact_advert_paths_recent ON contact_advert_paths(public_key, last_seen DESC);
CREATE INDEX idx_contact_name_history_key ON contact_name_history(public_key, last_seen DESC);
CREATE INDEX idx_repeater_telemetry_pk_ts
            ON repeater_telemetry_history (public_key, timestamp)
        ;
CREATE UNIQUE INDEX idx_messages_incoming_priv_dedup
           ON messages(type, conversation_key, text, COALESCE(sender_timestamp, 0),
                       COALESCE(sender_key, ''))
           WHERE type = 'PRIV' AND outgoing = 0;
CREATE INDEX idx_contact_telemetry_pk_ts
                ON contact_telemetry_history(public_key, timestamp)
            ;
CREATE INDEX idx_contact_group_members_key
        ON contact_group_members(public_key)
        ;
CREATE INDEX idx_messages_packet_hash ON messages(packet_hash) WHERE packet_hash IS NOT NULL;
CREATE INDEX idx_repeater_pane_cache_pk
            ON repeater_pane_cache (public_key)
        ;
CREATE INDEX idx_messages_chan_unattributed_sender
            ON messages(sender_name) WHERE type = 'CHAN' AND sender_key IS NULL
            ;
CREATE INDEX idx_messages_received ON messages(received_at);
COMMIT;
