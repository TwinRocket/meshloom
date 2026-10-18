# Database (SQLite)

One SQLite file (`MESHCORE_DATABASE_PATH`, default `data/meshcore.db`) opened by
`app/database.py` (`Database`, module singleton `db`). Like every doc here this is
a hypothesis: the code wins. The table and index lists below are checked against a
freshly migrated database by `tests/test_database_doc.py`; column details are not.

## Connection and pragmas

Set on every connect (`Database._connect_unlocked`): `journal_mode = WAL`,
`synchronous = NORMAL`, `busy_timeout = 5000`, `cache_size = -64000`,
`temp_store = MEMORY`, `auto_vacuum = INCREMENTAL`. `foreign_keys` is OFF while the
schema is created and migrations run (historical table rebuilds), then switched ON
for all application queries. All access goes through one connection and one lock:
`db.tx()` for writes (commit on clean exit, rollback on error) and
`db.readonly()` for reads (no commit). Consume or close cursors before leaving
either block.

Startup order: `SCHEMA_TABLES` (`CREATE TABLE IF NOT EXISTS`), then
`run_migrations`, then `SCHEMA_INDEXES`. A fresh database therefore gets `SCHEMA_TABLES`
(which already includes many columns that migrations added later, for example
`messages.sender_key`, `channels.membership`, `contacts.favorite`) and then every
migration runs on top of it. The final shape is "base schema + all migrations", so
a migration must tolerate columns that `SCHEMA_TABLES` already created. Never read
`SCHEMA_TABLES` alone as the current schema: it is not complete.

## Migrations

- One file per version: `app/migrations/_NNN_description.py`, exposing
  `async def migrate(conn)`. The runner (`app/migrations/__init__.py`) discovers
  them by numeric prefix and runs those above `PRAGMA user_version`, in order.
- Numbering is contiguous from 001. The current head is 103 (the value of
  `LATEST_SCHEMA_VERSION` in `tests/test_migrations/conftest.py`).
- Each migration runs in one transaction together with the `user_version` bump; a
  failure rolls both back. `await conn.commit()` inside a migration is a no-op
  (deferred to the runner). A migration that cannot run in a transaction (`VACUUM`,
  `journal_mode`) sets `TRANSACTIONAL = False` and must be idempotent.
- Never rename or delete a migration file. Two files with one number make the
  runner refuse to start (`_refuse_duplicate_numbers`): deployments that keep
  deleted files would otherwise run both.
- Migrations must tolerate a database where the target table does not exist yet
  (check `sqlite_master`) and a column that is already there (check
  `PRAGMA table_info`), as migration 093 does.

### Adding a migration

1. Create `app/migrations/_104_what_it_does.py` (next number, no gap) with
   `async def migrate(conn)`. Make it idempotent as above.
2. New table or column: the migration creates it for existing databases. Recent
   practice (082-084) also adds it to `SCHEMA_TABLES` in `app/database.py` so fresh
   databases get it directly; either way the migration must check
   `PRAGMA table_info` / `sqlite_master` first, because on a fresh database
   `SCHEMA_TABLES` may already have created it (otherwise: `duplicate column name`
   and the app refuses to start).
3. New index: add it to `SCHEMA_INDEXES` in `database.py` (fresh databases) **and**
   create it in the migration (existing databases).
4. Bump `LATEST_SCHEMA_VERSION` in `tests/test_migrations/conftest.py` and add
   `tests/test_migrations/test_migration_NNN.py` (see the existing ones for the
   build-an-old-schema-then-migrate pattern).
5. Update this file: new table or index entry, columns, writers. The drift test
   fails until the table and index lists match.

## Tables

Timestamps are Unix seconds (INTEGER). Public keys are lowercase hex (migration
014). JSON columns are TEXT. "Written by" names the repository module in
`app/repository/`; the services and routers that call it are the real writers.

### `app_settings`

Single row (`id = 1`, `CHECK (id = 1)`). Holds every user setting, the radio
transport and bound identity, Community state, push/VAPID keys, notification
destinations, telemetry alert rules, auto-update window and the one-shot
"imported legacy env" flags. Many columns hold JSON (blocked lists, push
defaults and overrides, `ui_preferences`, `notification_destinations`,
`telemetry_alert_rules`, tracked telemetry repeaters/contacts, `known_regions`,
`auth_allowed_origins`: the cross-origin sites CORS lets in, kept in memory by
`app/origin_policy.py`; `wifi_prompt_state`: the Wi-Fi prompt state per radio public key, never a
password, outside `AppSettings`; `radio_outage_started_at`: Unix time of the radio outage
that was notified, NULL when none, written by `notifications/radio_watch.py`, outside
`AppSettings`; `radio_offline_grace_s`: the delay before `radio_offline`;
`share_enabled`, `share_shorten_mode`, `share_preview_policy`: link and file sharing
(migration 102); `share_instance_seed`: the sharing identity seed, outside `AppSettings`,
never exported by a backup; `store_online`: the Store egress consent, off by default,
migration 103, not in `AppSettings` yet, excluded from backups and reset to 0 in a
database copy).
Most columns were added by migrations (`ALTER TABLE ... ADD COLUMN`). Read and
written through `AppSettingsRepository` (`settings.py`), plus
`RadioTransportRepository` (`radio_transport.py`) and `RadioProxyRepository`
(`radio_proxy.py`) for the radio columns. `StatisticsRepository` (`settings.py`)
only reads, across most tables (messages, raw_packets, contacts, ...). The full column list is
`PRAGMA table_info(app_settings)`; the typed view is `AppSettings` in
`app/models.py`. Settings belong here and in the UI, never in new env vars.

### `contacts`

PK `public_key`. Name, node `type`, `flags`, advertised position (`lat`, `lon`),
`last_advert`, `last_seen`, `last_contacted`, `first_seen`, `on_radio`,
`last_read_at`, `favorite`, `pinned`. Routing: `direct_path*` (learned) and
`route_override_*` (user forced). Written by `ContactRepository` (`contacts.py`)
from advert handling, `radio_sync.py`, the contacts router and backup restore.
Deleted by the user, by the stale-contact purge, and wiped on identity change.
Parent of most child tables below.

### `contact_advert_paths`

Paths over which a contact's adverts were heard: `(public_key, path_hex, path_len)`
unique, `first_seen`, `last_seen`, `heard_count`. FK to `contacts` (cascade).
Written by `ContactAdvertPathRepository.record_observation` (`contacts.py`) from
`packet_processor.py`,
which keeps only the 10 most recent paths per contact.

### `contact_name_history`

Names a contact has used: `(public_key, name)` unique, `first_seen`, `last_seen`.
FK to `contacts` (cascade). Written by `ContactNameHistoryRepository`
(`contacts.py`).

### `contact_groups` and `contact_group_members`

User-defined contact groups (`id`, `name`, `sort_order`, `created_at`) and their
membership (composite PK `(group_id, public_key)`; FKs to `contact_groups` and
`contacts`, both cascade). `ContactGroupRepository` (`contact_groups.py`), used by
the contact-groups router and backup restore.

### `channels`

PK `key`. `name`, `is_hashtag`, `on_radio`, overrides (`flood_scope_override`,
`path_hash_mode_override`), `last_read_at`, `favorite`, `pinned`, `muted`,
`muted_until`, and `membership` (`adopted` or `pending`, see
`services/channel_membership.py`). `ChannelRepository` (`channels.py`). The
radio slot cache is memory-only; `on_radio` is its persisted hint.

### `messages`

One row per stored message. `type` is `PRIV` (direct message, `conversation_key`
= the contact's public key) or `CHAN` (channel message, `conversation_key` = the
channel key). `text`, `sender_timestamp`, `received_at`, `paths` (JSON),
`txt_type`, `signature`, `outgoing`, `acked`, `sender_name`, `sender_key`,
`packet_hash`, `observer_reach_eligible`, `transport_code`, `region`.
Deduplication is by unique partial indexes, not table constraints
(`idx_messages_dedup_null_safe` for channels, `idx_messages_incoming_priv_dedup`
for incoming DMs). Written by `MessageRepository` (`messages.py`) through
`services/messages.py` and `services/dm_ingest.py`, fed by `packet_processor.py`
and the send routes. No automatic retention. Deleted one message at a time by
the user, and wiped on identity change.

### `raw_packets`

Every received packet, kept so it can be decrypted later: `timestamp`, `data`
(BLOB), `payload_hash` (BLOB, unique), `message_id` (FK to `messages`, `ON DELETE
SET NULL`). Written by `RawPacketRepository` (`raw_packets.py`) in
`packet_processor.py`. Retention: opt-in `raw_packet_retention_days` prunes
undecrypted rows (`message_id IS NULL`) older than that in batches; decrypted
rows are never pruned by it. The packets maintenance route can also prune undecrypted rows on request and
purge rows linked to messages. Wiped on identity change.

### `repeater_telemetry_history` and `contact_telemetry_history`

Time series of telemetry snapshots: `public_key`, `timestamp`, `data` (JSON).
FK to `contacts` (cascade). Written by `RepeaterTelemetryRepository` and
`ContactTelemetryRepository` (`repeater_telemetry.py`, `contact_telemetry.py`).
Each insert prunes that key's rows older than 30 days and keeps at most 1000
rows per key. Wiped on identity change.

### `repeater_pane_cache`

Last answer of each repeater admin pane (`status`, `neighbors`, `acl`, ...):
PK `(public_key, pane)`, `data` (JSON), `fetched_at`. FK to `contacts`
(cascade). `RepeaterPaneCacheRepository`, written by the repeaters router. Reads
ignore entries older than 7 days; rows are replaced, and deleted per contact.

### `telemetry_alert_state`

Per-contact, per-rule alert bookkeeping: PK `(public_key, rule_id)`,
`consecutive_misses`, `last_fired_at`, `last_value`. FK to `contacts` (cascade).
`TelemetryAlertStateRepository`, used by `app/telemetry_alerts.py`.

### `directory_hop_cache`

Cache of resolved path-hop prefixes (Community directory and others): PK
`(prefix, hash_width)`, `name`, `source`, `expires_at`, `public_key`, `lat`,
`lon`. `DirectoryHopCacheRepository` (`directory.py`), filled by `services/directory.py`.
Rows are ignored once `expires_at` has passed; the whole table is wiped on
demand and on identity change.

### `fanout_configs`

One row per fanout integration (MQTT, bots, webhooks, Apprise, SQS, map upload):
PK `id` (text), `type`, `name`, `enabled`, `config` (JSON), `scope` (JSON),
`sort_order`, `created_at`. `FanoutConfigRepository` (`fanout.py`), used by the fanout
manager and its router. See `app/fanout/AGENTS.md`.

### `push_subscriptions`

Web Push subscriptions: PK `id`, `endpoint` (unique), `p256dh`, `auth`, `label`,
`language`, `created_at`, `last_success_at`, `failure_count`.
`PushSubscriptionRepository` (`push_subscriptions.py`), used by `app/push/`.
A subscription is deleted once `failure_count` reaches 15 consecutive failures.

### `users`

Local accounts (migration 101; model and rules in `docs/dev/auth.md`): PK `id`
(`AUTOINCREMENT`, never reused, so audit rows never point at a later account),
`username` (display form), `username_key` (unique: NFKC + case folding, so two
names that differ only by case clash), `display_name`, `password_hash` (argon2id
encoded string, NULL for ingress and kiosk identities), `role`
(`admin`/`member`/`viewer`/`kiosk`, CHECK), `provider` (`local`/`ingress`/`system`,
CHECK), `external_id` (unique per provider), `disabled`, `must_change_password`,
`totp_secret`, `totp_enabled_at`, `totp_last_counter` (unused until 2FA),
`created_at`, `updated_at`. `UserRepository` (`auth.py`).

### `sessions`

Login sessions: PK `id` (random, not a secret), `user_id` (FK `users(id)`,
cascade), `token_hash` (unique, SHA-256 hex of the cookie secret; the secret is
never stored), `created_at`, `last_seen_at` (written at most once a minute),
`expires_at`, `ip`, `user_agent`, `via`, `revoked_at`. At most 50 active per user
(creating one more revokes the oldest). `AuthSessionRepository` (`auth.py`).

### `auth_audit`

Authentication audit trail: PK `id`, `ts`, `actor_user_id`, `actor_token_id`
(no foreign keys: a row outlives its actor), `action`, `target`, `ip`, `via`,
`outcome`, `details` (JSON; the repository refuses keys that name a secret).
Retention `auth_settings.audit_retention_days` (366 by default).
`AuthAuditRepository` (`auth.py`).

### `auth_settings`

Single row (`id = 1`) of access settings, kept out of `app_settings` so no auth
secret reaches `AppSettings`: `trusted_networks` (JSON `[{cidr, user_id}]`),
`trusted_proxies` (JSON CIDR list), `public_urls` (JSON), `host_guard_mode`
(`observe`/`enforce`), `ingress_default_role` (`member`), `session_lifetime_days`
(30), `audit_retention_days` (366), `basic_imported`, `setup_code_hash` (SHA-256 of
the installation code). The CORS list stays `app_settings.auth_allowed_origins`.
`AuthSettingsRepository` (`auth.py`).

### `plugins`

Plugin instances (migration 103; model and rules in `docs/dev/plugins.md`, "Data
model"): PK `instance_id` (uuid), `plugin_id` (the manifest id; several instances
share one), `label`, `kind` (`bundled`/`store`/`custom`/`local`, CHECK), `version`,
`enabled`, `manifest`, `config` (never a secret), `scope`, `granted_permissions`,
`quotas` (JSON), `auto_update`, `source` (a local plugin's code), `archive_sha256`,
`repository`, `installed_at`, `updated_at`, `status`, `last_error`, `attention`
(JSON, NULL when nothing to review), `sort_order`. `PluginRepository` (`plugins.py`).
Nothing reads it yet: the bots stay in `fanout_configs` until the switch (Store T9).

### `plugin_kv`

Per-instance key-value store: PK `(instance_id, key)`, `value` (JSON), `updated_at`.
No foreign key: uninstalling with "keep data" leaves the rows. `PluginKvRepository`
(`plugins.py`).

### `plugin_secrets`

Values of the config schema's `writeOnly` fields: PK `(instance_id, field)`, `value`,
`updated_at`. Stored in clear until the Notifications key file exists; never returned
by the repository except `config_for_init()`, never exported, emptied in the database
download. `PluginRepository` (`plugins.py`).

## Relationships

All foreign keys point at `contacts(public_key)` (children: advert paths, name
history, both telemetry histories, pane cache, alert state, group members, all
`ON DELETE CASCADE`), at `contact_groups(id)` (members, cascade) or at
`messages(id)` (`raw_packets.message_id`, `SET NULL`), plus `sessions.user_id` on
`users(id)` (cascade). `messages.conversation_key`
and `messages.sender_key` are plain text, not foreign keys: a message outlives its
contact or channel. Enforcement requires `PRAGMA foreign_keys = ON`, which
`Database` sets after migrations (migration 049 rebuilt the tables with cascades).

## Indexes

Created by `SCHEMA_INDEXES` after migrations (and by some migrations for existing
databases). The current set:

| Index | On |
|---|---|
| `idx_contact_advert_paths_recent` | `contact_advert_paths(public_key, last_seen DESC)` |
| `idx_contact_group_members_key` | `contact_group_members(public_key)` |
| `idx_contact_name_history_key` | `contact_name_history(public_key, last_seen DESC)` |
| `idx_contact_telemetry_pk_ts` | `contact_telemetry_history(public_key, timestamp)` |
| `idx_auth_audit_ts` | `auth_audit(ts)` |
| `idx_contacts_type_last_seen` | `contacts(type, last_seen)` |
| `idx_messages_chan_unattributed_sender` | `messages(sender_name)` where `CHAN` and `sender_key IS NULL` |
| `idx_messages_dedup_null_safe` | unique, `messages(type, conversation_key, text, COALESCE(sender_timestamp, 0))` where `CHAN` |
| `idx_messages_incoming_priv_dedup` | unique, `messages(type, conversation_key, text, COALESCE(sender_timestamp, 0), COALESCE(sender_key, ''))` where incoming `PRIV` |
| `idx_messages_packet_hash` | `messages(packet_hash)` where not null |
| `idx_messages_pagination` | `messages(type, conversation_key, received_at DESC, id DESC)` |
| `idx_messages_received` | `messages(received_at)` |
| `idx_messages_sender_key` | `messages(sender_key)` |
| `idx_messages_type_received_conversation` | `messages(type, received_at, conversation_key)` |
| `idx_messages_feed_type` | `messages(type, received_at)` |
| `idx_messages_feed_outgoing` | `messages(outgoing, received_at)` |
| `idx_messages_feed_type_outgoing` | `messages(type, outgoing, received_at)` |
| `idx_messages_unread_covering` | `messages(type, conversation_key, outgoing, received_at)` |
| `idx_plugins_plugin_id` | `plugins(plugin_id)` |
| `idx_raw_packets_message_id` | `raw_packets(message_id)` |
| `idx_raw_packets_payload_hash` | unique, `raw_packets(payload_hash)` |
| `idx_raw_packets_timestamp` | `raw_packets(timestamp)` |
| `idx_repeater_pane_cache_pk` | `repeater_pane_cache(public_key)` |
| `idx_repeater_telemetry_pk_ts` | `repeater_telemetry_history(public_key, timestamp)` |
| `idx_sessions_expires` | `sessions(expires_at)` |
| `idx_sessions_user` | `sessions(user_id, created_at)` |

## Retention and maintenance

- Housekeeping loop (`services/stale_contacts.py`, hourly): purge non-favorite
  contacts not heard for `stale_contact_days` (off at 0); prune undecrypted raw
  packets older than `raw_packet_retention_days` (off at 0); then a bounded
  `PRAGMA incremental_vacuum` (2048 pages) so freed pages return to the OS.
- Inline caps: telemetry histories (30 days, 1000 rows per key), advert paths (10
  per contact), push subscriptions (15 failures), pane cache (7 days on read),
  hop cache (`expires_at` on read).
- Identity change (`wipe_mesh_identity_data` in `services/radio_identity.py`, when the user
  adopts a different radio key and confirms the wipe) wipes `raw_packets`, `messages`, `contacts` (and so their children),
  `contact_groups`, `directory_hop_cache` and both telemetry histories. In the same
  transaction it resets `channels.last_read_at` and `channels.on_radio`, and in
  `app_settings` resets `last_message_times`, `last_advert_time`, empties
  `tracked_telemetry_repeaters` and `tracked_telemetry_contacts`, and strips the
  `contact-*` entries from `push_conversations` and `push_conversation_overrides`.
  A new setting keyed by contact must be added to that reset.
- Expired and revoked `sessions` (`AuthSessionRepository.purge`) and `auth_audit`
  rows past `audit_retention_days` (`AuthAuditRepository.purge_older_than`) have
  purge helpers, but no loop calls them yet: the auth core and the audit log
  tickets schedule them.
- Everything else (messages, channels, fanout configs) is kept until the user
  deletes it.

## Backups

Three kinds, all in `services/backup.py` (details, coverage and the restore
order in `app/AGENTS.md`, "## Backups"):

- An SQLite file snapshot via the SQLite backup API (`write_sqlite_backup`,
  `GET /settings/backup/database`), never restored from the UI. The private key
  is not in the database, so not in the snapshot. In the copy (never the live
  database) every `app_settings.*_seed` column, the BLE PIN, the
  previous-transport snapshot, the password and TOTP secrets of `users` and
  `auth_settings.setup_code_hash` are set to NULL, `sessions` and `plugin_secrets` are
  emptied, the consents `share_enabled` and `store_online` are set to 0, and a one-row
  `meshloom_backup_snapshot` marker table is added: a database
  that starts with it is a restored snapshot, so startup runs the restore hooks
  and drops it (`docs/dev/backup.md`). A fresh database never has it.
- `meshloom-backup-v1` (`export_json` / `restore_json`, `GET
  /settings/backup/json`): contacts, channels, settings and groups, Meshloom
  data only. It never holds the radio private key.
- The full backup `meshloom-backup-v2` (`POST /settings/backup/export`): the v1
  fields plus the radio identity (private key), the radio settings, the Wi-Fi
  credentials (password in clear, or an open network Meshloom recorded in
  `app_settings.wifi_prompt_state`) and `meshloom_extra` (integrations, VAPID keys
  and push subscriptions, the radio proxy, settings outside v1). It may be wrapped in a passphrase envelope
  (`services/backup_crypto.py`). Radio reads and writes live in
  `services/backup_radio.py`, the identity decision, inspect and the restore job
  in `services/backup_restore.py`. Optional named `sections` added by other
  features, and the restore hooks: `docs/dev/backup.md`.

## Keeping this file honest

`tests/test_database_doc.py` builds a fresh database, then fails when a table or
an index exists in the schema but not in this file (a `### ` heading with the table
name, an index row in the table above), or the other way round. It does not check
columns: when you add one, update the table's prose by hand.
