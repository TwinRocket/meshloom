# Multi-Radio Architecture & Design Note (Phase 0 Audit)

**Status:** Phase 0 Complete / Architecture Proposal  
**Date:** October 2026  
**Target:** `TwinRocket/meshloom`  
**Reference Invariant:** Every radio-dependent piece of state, event, packet, message, contact, channel, bot action and outbound operation must keep an unambiguous association with its originating or target radio. Nothing may select a radio implicitly.

---

## 1. Executive Summary & Stance

This document provides the foundational audit and target architectural design for introducing first-class multi-radio support to Meshloom.

The core design stance for this release is **strict isolation by default**:
- Each radio operates as an independent instance (`RadioInstance`) managed by a central registry (`RadioRegistry`).
- Each radio owns its contacts, channels, packets, messages, routing tables, statistics, telemetry history, bots, and fanout configs.
- No implicit cross-radio data leakage or implicit routing. Data originating from Radio A is never displayed as or mixed with data from Radio B.
- Outbound sends target an explicit radio.
- Legacy single-radio deployments automatically migrate without data loss, manual intervention, or behavior change. When only one radio is configured, the user experience remains visually and operationally identical to single-radio Meshloom today.
- Cross-radio aggregated views (e.g. read-time deduplicated global mesh views) are layered on top of explicit radio provenance in Phase 7.

---

## 2. Radio Lifecycle Today (Single-Radio Analysis)

### 2.1 Configuration
- **Storage:** Persisted in SQLite `app_settings` (row `id = 1`) as columns added in migration `_068_radio_transport.py`:
  - `radio_transport` (`serial`, `tcp`, `ble`, or `NULL` if unconfigured)
  - `radio_serial_port`, `radio_serial_baudrate`
  - `radio_tcp_host`, `radio_tcp_port`
  - `radio_ble_address`, `radio_ble_pin`
  - `radio_bound_public_key`, `radio_identity_state`
  - `radio_previous_transport`, `radio_mismatch_*`
- **Legacy Env Fallback:** `MESHCORE_SERIAL_PORT`, `MESHCORE_TCP_HOST`, `MESHCORE_BLE_ADDRESS` are read once on initial start via `maybe_import_legacy_env()` in `app/services/radio_transport.py` and saved into `app_settings`.
- **Identity Gating:** `app/services/radio_identity.py` checks that the connected radio's public key matches `radio_bound_public_key`. If unbound on a new install, it binds. If unbound on a DB with history, or if key mismatches, it enters `identity_unbound_legacy` or `identity_mismatch` gate state, closing ingest until user adopts or rejects.

### 2.2 Creation & Connection
- **Global Manager:** Process-global singleton `radio_manager = RadioManager()` in `app/radio.py`.
- **Access Seam:** `app/services/radio_runtime.py` (`radio_runtime`), which forwards attribute and method calls to `app.radio.radio_manager`.
- **Startup:** In `app/main.py`:
  1. `lifespan` connects SQLite database `db.connect()`.
  2. Runs migrations via `app.migrations.run_migrations()`.
  3. Checks `maybe_import_legacy_env()`.
  4. Starts background connection monitor: `radio_manager.start_connection_monitor()`.
  5. Spawns startup connect task: `_startup_radio_connect_and_setup()`.
- **Connection Method:** `RadioManager.connect()` inspects snapshot from `get_transport()`. Dispatches to `_connect_serial()`, `_connect_tcp()`, or `_connect_ble()`, calling `meshcore.MeshCore.create_serial()`, `create_tcp()`, or `create_ble()`.
- **Disconnect Hook:** Installs `_install_library_reconnect_gate` on `mc.connection_manager.handle_disconnect` to deny ingest immediately if the underlying transport drops.

### 2.3 Post-Connect Setup (`run_post_connect_setup`)
Defined in `app/services/radio_lifecycle.py`:
1. Acquires `radio_manager._operation_lock("post_connect_setup")`.
2. Evaluates identity: `evaluate_connected_identity(mc)` (reads public key via `CMD_APPSTART`).
3. If valid: calls `allow_ingest(session)` in `app/services/radio_ingest_gate.py`.
4. Subscribes event handlers via `register_event_handlers(mc)` in `app/event_handlers.py`.
5. Exports and caches Ed25519 private key in memory via `export_and_store_private_key(mc)` in `app/keystore.py`.
6. Synchronizes radio clock with host system time via `sync_radio_time(mc)` in `app/radio_sync.py`.
7. Queries device information via `mc.commands.send_device_query()` (model, firmware build/version, max contacts, max channels, `path_hash_mode`).
8. Configures default 2-byte path hash mode if supported (`apply_default_path_hash_mode`).
9. Verifies radio proxy loop prevention (`remote_proxy_id == radio_proxy_manager.instance_id`).
10. Sets configured regional flood scope via `set_radio_flood_scope(mc, scope)`.
11. Runs initial sync and offload via `sync_and_offload_all(mc)` in `app/radio_sync.py`.
12. Sends presence announcement advertisement (`send_advertisement(mc)`).
13. Drains pre-connection queued messages (`drain_pending_messages(mc)`).
14. Starts auto message fetching: `mc.start_auto_message_fetching()`.
15. Starts periodic background loops in `app/radio_sync.py`:
    - `start_periodic_sync()`
    - `start_periodic_advert()`
    - `start_message_polling()`
    - `start_telemetry_collect()`
16. Starts local radio stats 60s sampling in `app/services/radio_stats.py`: `start_radio_stats_sampling()`.
17. Marks `radio_manager._setup_complete = True`.

### 2.4 Event Ingestion & Packet Processing
- MeshCore subscriptions in `app/event_handlers.py`:
  - `EventType.RX_LOG_DATA` -> `on_rx_log_data()` -> `process_raw_packet()` in `app/packet_processor.py`.
  - `EventType.CONTACT_MSG_RECV` -> `on_contact_message()` -> `ingest_fallback_direct_message()` in `app/services/dm_ingest.py`.
  - `EventType.PATH_UPDATE` -> `on_path_update()` -> updates `contacts.direct_path*` in `ContactRepository`.
  - `EventType.NEW_CONTACT` -> `on_new_contact()` -> `ContactRepository.upsert_reporting_insert()`.
  - `EventType.ACK` -> `on_ack()` -> `apply_dm_ack_code()` in `app/services/dm_ack_apply.py`.
- **Packet Processor Pipeline (`process_raw_packet`):**
  - Dedupes payload by `payload_hash` in `raw_packets` table via `RawPacketRepository.create()`.
  - If `GROUP_TEXT`: decrypts against all channels in `ChannelRepository.get_all()`. Creates or merges message via `create_message_from_decrypted()`.
  - If `ADVERT`: parses contact info, checks Ed25519 signature, upserts into `contacts`, records path in `contact_advert_paths`.
  - If `TEXT_MESSAGE`: decrypts using private key from `app/keystore.py` and contacts from `ContactRepository`. Creates message via `create_dm_message_from_decrypted()`.
  - If `PATH`: decrypts path packet and updates contact route in `ContactRepository`.
  - If `ACK`: applies 4-byte ACK code to pending DM via `apply_dm_ack_code()`.
  - Broadcasts `raw_packet` WebSocket event via `broadcast_event()` in `app/websocket.py`.

### 2.5 Outgoing Message Flow
- Client sends `POST /api/messages/direct` or `POST /api/messages/channel`.
- Route calls `radio_manager.require_connected()`.
- Delegates to `app/services/message_send.py`:
  - Acquires `radio_manager.radio_operation("send_direct_message" / "send_channel_message")`.
  - Stages route or channel slot on radio.
  - Sends via MeshCore command (`send_msg`, `send_channel_msg`).
  - Stores message in SQLite `messages` with `outgoing = 1`.
  - Tracks expected ACK in `app/services/dm_ack_tracker.py`.
  - Broadcasts `message` event over WebSocket.

### 2.6 Shutdown & Reconnection
- `RadioManager.disconnect()` clears in-memory keystore, denies ingest, unregisters event handlers, acquires operation lock, disables library auto reconnect, disconnects `MeshCore`, clears channel slot cache, and resets runtime state.
- `connection_monitor_loop()` runs every 5 seconds, detecting loss of connection, auto-retrying reconnect via `reconnect_and_prepare_radio()`.

---

## 3. Single-Radio Assumption Inventory

| Location | Symbol / Construct | Current Behavior | Multi-Radio Required Change |
|---|---|---|---|
| `app/radio.py` | `radio_manager = RadioManager()` | Global singleton managing one radio connection | Replace with `RadioRegistry` managing a map `dict[str, RadioInstance]`. Provide default instance access for legacy compatibility. |
| `app/radio.py` | `RadioManager._operation_lock` | Single `asyncio.Lock()` serializing all operations | Move inside `RadioInstance`. Each radio locks independently. |
| `app/radio.py` | `RadioManager._channel_slot_by_key`, `_channel_key_by_slot`, `_pending_message_channel_key_by_slot` | Global LRU channel slot cache | Move inside `RadioInstance`. Each radio has its own slot allocation. |
| `app/radio.py` | `RadioManager._reconnect_lock`, `_setup_lock`, `_lifecycle_lock` | Global locks for lifecycle transitions | Move inside `RadioInstance`. A reconnecting radio never blocks another radio. |
| `app/radio.py` | `RadioManager._transport_snapshot`, `_connection_info`, `_meshcore`, `device_info_loaded`, `max_contacts`, `max_channels`, `path_hash_mode` | Global instance state attributes | Move into `RadioInstance`. |
| `app/services/radio_runtime.py` | `RadioRuntime` / `radio_runtime` | Thin forwarder pointing to `radio_module.radio_manager` | Update `RadioRuntime` to support resolving radio by ID: `radio_runtime.get(radio_id=None)` returning the targeted `RadioInstance` (defaulting to default radio). |
| `app/services/radio_lifecycle.py` | `connection_monitor_loop` | Loops over single `radio_manager` | Run one monitor loop per active `RadioInstance` inside its lifecycle. |
| `app/services/radio_lifecycle.py` | `run_post_connect_setup` | Post connect setup assumes global radio | Takes explicit `RadioInstance`. Registers per-radio handlers and sync tasks. |
| `app/services/radio_ingest_gate.py` | `_ingest_allowed`, `_session_generation` | Global boolean flag. When closed, blocks all packet ingest! | Move ingest gate inside `RadioInstance`. Dropping Radio B does not deny ingest for Radio A. |
| `app/keystore.py` | `_private_key`, `_public_key` | Global in-memory bytes holding one radio key | Key by radio ID: `dict[str, tuple[bytes, bytes]]` inside `Keystore` or per `RadioInstance`. |
| `app/services/dm_ack_tracker.py` | `_pending_acks`, `_buffered_acks` | Global dictionary mapping ACK code to `message_id` | Scope by `radio_id`: `dict[tuple[str, str], PendingAck]` or per `RadioInstance`. |
| `app/services/radio_stats.py` | `_stats_task`, `_noise_floor_samples`, `_latest_stats` | Global sampling task and deque | Move into `RadioInstance`. Each radio tracks its own noise floor and statistics. |
| `app/radio_sync.py` | `_sync_task`, `_advert_task`, `_telemetry_collect_task`, `_message_poll_task`, `_contact_reconcile_task` | Global `asyncio.Task` handles | Move tasks into `RadioInstance`. Each radio runs its own periodic sync/advert/telemetry. |
| `app/radio_sync.py` | `_polling_pause_count`, `_clock_reboot_attempted`, `_last_contact_sync` | Global sync throttle and state variables | Move into `RadioInstance`. |
| `app/event_handlers.py` | `_active_subscriptions: list[Subscription]` | Global subscription list | Scoped per `RadioInstance`. Handlers receive radio context. |
| `app/event_handlers.py` | Event callback functions (`on_rx_log_data`, `on_contact_message`, etc.) | Do not know which radio fired the event | Attach handler with bound `radio_id` (e.g. `functools.partial` or method on `RadioInstance`). |
| `app/packet_processor.py` | `process_raw_packet(raw_bytes, ...)` | Processes packets without radio identity | Add explicit `radio_id: str` parameter. Pass `radio_id` to storage, channel/contact lookup, dedup, and broadcasts. |
| `app/packet_processor.py` | Decryption lookups (`ChannelRepository.get_all()`, `ContactRepository`) | Reads all global channels/contacts | Scope channel/contact lookups to `radio_id`. |
| `app/fanout/bot.py` & `app/fanout/bot_exec.py` | `_last_bot_send_time`, `_bot_send_lock` | Global bot send rate limit and lock | Rate limit per radio. Bots reply only to their owning radio. |
| `app/fanout/bot.py` | `on_message` dispatch | Bot has no radio association | `fanout_configs` specifies `radio_id`. Bot only triggers for its assigned radio. |
| `app/fanout/mqtt_ha.py` | HA Discovery publisher | Publishes one local radio `meshcore_{node_id}` | Keep default radio as `meshcore_{node_id}`. Additional radios namespaced with radio ID to prevent HA entity collision. |
| `app/services/meshloom_community.py` | `mint_radio_jwt` | Uses single `keystore.get_private_key()` | Community feature associated with designated radio. |
| `app/repository/radio_transport.py` | Read/write transport in `app_settings` | Single transport configuration in table `app_settings` | Move radio transport configurations to dedicated `radios` table. |
| `app/routers/radio.py` | Endpoints (`GET /radio/config`, `POST /radio/connect`, etc.) | Direct calls to global `radio_manager` | Accept optional `radio_id` query parameter; default to default radio. Add dedicated `/api/radios` CRUD endpoints. |
| `app/routers/contacts.py` | Endpoints (`GET /contacts`, `POST /contacts`, etc.) | Operates on global contacts | Accept `radio_id` query parameter; filter/store contacts per `radio_id`. |
| `app/routers/channels.py` | Endpoints (`GET /channels`, `POST /channels`, etc.) | Operates on global channels | Accept `radio_id` query parameter; filter/store channels per `radio_id`. |
| `app/routers/messages.py` | `send_direct_message`, `send_channel_message`, `list_messages` | Global sends and queries | Accept `radio_id`; query/send strictly through that radio. |
| `app/routers/health.py` | `GET /health` | Returns single health payload | Return default radio health for backward compatibility + map or list of all radios. |
| `app/websocket.py` | `broadcast_event`, `broadcast_health` | Emits untagged events | Tag every radio-dependent event with `radio_id: str`. Broadcast per-radio health. |
| `app/events.py` | `dump_ws_event` | Serializes event payloads | Ensure `radio_id` is included in payloads or envelope. |
| `frontend/src/useWebSocket.ts` & `wsEvents.ts` | Dispatches events globally | Dispatches without radio context | Include `radio_id` in WS events; dispatch to radio-scoped stores/hooks. |
| `frontend/src/hooks/useRadioControl.ts` | Manages single radio config/health | Single health and config state | Support active selected radio + radio registry query. |
| `frontend/src/hooks/useContactsAndChannels.ts` | Loads all contacts and channels | Fetches without `radio_id` | Parameterize by `activeRadioId`. Switching radio reloads contacts/channels. |
| `frontend/src/hooks/useConversationMessages.ts` | Conversation message history | Single cache by conversation key | Scope cache by `(radio_id, conversation_key)`. |
| `frontend/src/components/RadioStatusChip.tsx` | Shows single radio connection status | Displays single status | Show active radio status + selector dropdown when multiple radios exist. |
| `frontend/src/components/RadioSetupBanner.tsx` | `radioSetupBannerVisible` | Checks `health.transport_configured` | Do not show banner if at least one radio is configured. |
| `frontend/src/components/settings/SettingsRadioSection.tsx` | Single radio configuration UI | Only edits one transport | Edit active radio, and add a multi-radio management panel. |

---

## 4. Data Model Inventory & Migration Plan

### 4.1 Schema Inventory & Radio-Scoping Classification

| Table Name | Current Primary Key / Uniqueness | Current Role | Scoping Classification | Justification & Strategy |
|---|---|---|---|---|
| **`radios` (NEW)** | `id TEXT PRIMARY KEY` | Radio definitions and state | **Core Registry** | Stores configuration for each radio (id, name, transport type, transport config, enabled, bound public key, timestamps). |
| **`contacts`** | `public_key TEXT PRIMARY KEY` | Stored contacts from radio sync & adverts | **Radio-specific** | The same physical node may be heard by multiple radios (with different direct routes, SNRs, and hear times). Identity becomes `(radio_id, public_key)`. |
| **`channels`** | `key TEXT PRIMARY KEY` | Stored channel keys & names | **Radio-specific** | A `#general` channel or slot on Radio A is independent from Radio B. Identity becomes `(radio_id, key)`. |
| **`messages`** | `id INTEGER PRIMARY KEY AUTOINCREMENT`, dedup indexes | Plaintext & decrypted messages | **Radio-specific** | Message reception and transmission belongs to the specific radio that heard or sent it. Add `radio_id TEXT NOT NULL`. |
| **`raw_packets`** | `id INTEGER PRIMARY KEY AUTOINCREMENT`, `UNIQUE(payload_hash)` | Raw packet captures | **Radio-specific** | Dedup by payload hash must be scoped per radio `UNIQUE(radio_id, payload_hash)` so Radio A hearing a packet does not block Radio B from recording its reception. |
| **`contact_advert_paths`** | `id PK`, `UNIQUE(public_key, path_hex, path_len)` | Heard advert paths | **Radio-specific** | Advert paths reflect routes to a specific receiver radio. Add `radio_id`. `UNIQUE(radio_id, public_key, path_hex, path_len)`. |
| **`contact_name_history`** | `id PK`, `UNIQUE(public_key, name)` | Name history | **Radio-specific** | Add `radio_id`. `UNIQUE(radio_id, public_key, name)`. |
| **`repeater_telemetry_history`**| `id PK`, `idx(public_key, timestamp)` | Time series telemetry for repeaters | **Radio-specific** | Telemetry gathered by a specific radio. Add `radio_id`. |
| **`contact_telemetry_history`** | `id PK`, `idx(public_key, timestamp)` | Time series LPP telemetry for contacts | **Radio-specific** | Telemetry gathered by a specific radio. Add `radio_id`. |
| **`repeater_pane_cache`** | `(public_key, pane) PRIMARY KEY` | Cached dashboard panes | **Radio-specific** | Scoped to querying radio: `PRIMARY KEY (radio_id, public_key, pane)`. |
| **`contact_groups`** | `id INTEGER PRIMARY KEY AUTOINCREMENT` | User-defined contact groups | **Global** | Groups are user organizational units across the application. |
| **`contact_group_members`** | `PRIMARY KEY (group_id, public_key)` | Members of contact groups | **Radio-specific** | Links group to `(radio_id, public_key)`. `PRIMARY KEY (group_id, radio_id, public_key)`. |
| **`directory_hop_cache`** | `PRIMARY KEY (prefix, hash_width)` | Hop cache from Community | **Global** | Shared cache from external Community API, not radio-dependent. |
| **`fanout_configs`** | `id TEXT PRIMARY KEY` | Integrations (MQTT, bots, etc.) | **Radio-specific / Global** | Add nullable `radio_id TEXT`. Bots and HA MQTT require explicit radio targeting. NULL means global fanout (e.g. webhook for all traffic). |
| **`push_subscriptions`** | `id PK`, `UNIQUE(endpoint)` | Web Push browser subscriptions | **Global** | Tied to browser endpoints. Notifications carry radio metadata. |
| **`app_settings`** | `id PK CHECK (id = 1)` | App settings & preferences | **Global** | App preferences (VAPID, UI settings) stay here. Transport fields migrate to `radios`. |

### 4.2 Migration Strategy (`_086_multi_radio_support.py`)
1. **Create `radios` table:**
   ```sql
   CREATE TABLE IF NOT EXISTS radios (
       id TEXT PRIMARY KEY,
       name TEXT NOT NULL,
       transport TEXT,                 -- 'serial', 'tcp', 'ble', or NULL
       serial_port TEXT DEFAULT '',
       serial_baudrate INTEGER DEFAULT 115200,
       tcp_host TEXT DEFAULT '',
       tcp_port INTEGER DEFAULT 5000,
       ble_address TEXT DEFAULT '',
       ble_pin TEXT DEFAULT '',
       enabled INTEGER NOT NULL DEFAULT 1,
       auto_connect INTEGER NOT NULL DEFAULT 1,
       bound_public_key TEXT,
       identity_state TEXT DEFAULT 'normal',
       created_at INTEGER NOT NULL,
       last_connected_at INTEGER,
       sort_order INTEGER NOT NULL DEFAULT 0
   );
   ```
2. **Seed Default Radio from `app_settings`:**
   Migrate existing transport settings from `app_settings` into a default radio with `id = 'default'`, `name = 'Primary Radio'`. If `app_settings` had `radio_transport`, copy all parameters and `radio_bound_public_key`.
3. **Table Rebuilds with `radio_id`:**
   In SQLite, altering a primary key or unique constraint requires table recreation (standard SQLite migration pattern):
   - For `contacts`: create `contacts_new` with `PRIMARY KEY (radio_id, public_key)` and `radio_id TEXT NOT NULL DEFAULT 'default'`. Copy data with `radio_id = 'default'`. Drop old, rename `contacts_new` to `contacts`.
   - For `channels`: create `channels_new` with `PRIMARY KEY (radio_id, key)` and `radio_id TEXT NOT NULL DEFAULT 'default'`. Copy data with `radio_id = 'default'`. Drop old, rename.
   - For `messages`: add column `radio_id TEXT NOT NULL DEFAULT 'default'`. Recreate unique indexes to include `radio_id`.
   - For `raw_packets`: add column `radio_id TEXT NOT NULL DEFAULT 'default'`. Replace unique index on `payload_hash` with `UNIQUE(radio_id, payload_hash)`.
   - For child tables (`contact_advert_paths`, `contact_name_history`, `repeater_telemetry_history`, `contact_telemetry_history`, `repeater_pane_cache`, `contact_group_members`): add `radio_id` and update unique constraints/indexes.
4. **Idempotency:**
   Check `sqlite_master` and column names before running updates so that running the migration twice succeeds safely with zero data modification.

---

## 5. Packet Processing & Event Dispatch Architecture

### 5.1 Explicit Context Pipeline
Every incoming packet or event is tagged with its originating `radio_id` from the transport listener all the way through to WebSocket and fanout:
```text
Radio Transport Listener (RadioInstance: id="home")
  │ (RX_LOG_DATA event)
  ▼
on_rx_log_data(event, radio_id="home")
  │
  ▼
process_raw_packet(raw_bytes, radio_id="home", snr=..., rssi=...)
  ├── RawPacketRepository.create(raw_bytes, timestamp, radio_id="home")
  ├── Decrypt with ChannelRepository.get_all_for_radio("home")
  ├── Decrypt DM with Keystore.get_private_key("home") & ContactRepository.get_for_radio("home")
  ├── Parse Advert -> ContactRepository.upsert(..., radio_id="home")
  └── broadcast_event("raw_packet", data_with_radio_id, radio_id="home")
        ├── WebSocketManager.broadcast (envelope contains radio_id="home")
        └── FanoutManager.dispatch(..., radio_id="home")
```

### 5.2 Echo and Ack Tracking Isolation
- **ACK tracking:** `dm_ack_tracker` tracks ACKs keyed by `(radio_id, ack_code)`. An ACK received on Radio B will not satisfy a pending DM sent on Radio A.
- **Echo dedup:** Messages are deduplicated on `(radio_id, type, conversation_key, text, timestamp)`. If Radio B overhears Radio A's transmission over RF, it is processed as an overheard packet on Radio B, not conflated with Radio A's sent message state.

---

## 6. Outbound Sending & Radio Targeting

### 6.1 Explicit Targeting
Every outbound API request specifies `radio_id`:
- `POST /api/messages/direct?radio_id=home`
- `POST /api/messages/channel?radio_id=home`
- `POST /api/radio/advertise?radio_id=home`

### 6.2 Backward Compatibility
If `radio_id` is omitted in any existing endpoint:
- The system automatically targets the default/primary radio (`radio_id = 'default'`).
- Legacy clients or scripts continue to work without modification.

---

## 7. API & WebSocket Specifications

### 7.1 Radio Management Endpoints (New)
- `GET /api/radios`: List all configured radios with live status (`connected`, `connecting`, `disconnected`, `unreachable`, `error`, `disabled`), transport type, and summary metrics.
- `POST /api/radios`: Create a new radio configuration.
- `GET /api/radios/{radio_id}`: Detailed configuration and status of a radio.
- `PATCH /api/radios/{radio_id}`: Update configuration (name, host, port, serial, enabled, auto-connect).
- `DELETE /api/radios/{radio_id}?purge_data=false`: Remove radio configuration. Default keeps archived data (`purge_data=false`).
- `POST /api/radios/{radio_id}/connect`: Manually connect.
- `POST /api/radios/{radio_id}/disconnect`: Manually disconnect.
- `POST /api/radios/{radio_id}/reconnect`: Trigger reconnection attempt.
- `POST /api/radios/test`: Test connection to specified transport target without saving.

### 7.2 WebSocket Contracts
- Every broadcast envelope includes `radio_id`:
  ```json
  {
    "type": "message",
    "radio_id": "home",
    "data": { ... }
  }
  ```
- `health` event emitted per radio when state changes:
  ```json
  {
    "type": "health",
    "radio_id": "home",
    "data": { "radio_state": "connected", ... }
  }
  ```

---

## 8. Integrations: Bots, MQTT, Home Assistant, Community

### 8.1 Bots
- In `fanout_configs`, bot configurations include `radio_id`.
- A bot assigned to Radio A triggers only on messages heard by Radio A.
- Bot replies route strictly through the owning `RadioInstance`.
- Bot send rate-limiting is per-radio so that activity on Radio A does not throttle Radio B.

### 8.2 MQTT & Home Assistant Discovery
- **Default Radio:** Retains existing topic layout (`meshcore/<node_id>/...`) and existing Home Assistant discovery identifiers (`meshcore_<node_id>_*`). Zero breaking changes for existing Home Assistant dashboards.
- **Additional Radios:** Namespaced by `meshcore_<radio_id>_<node_id>` to guarantee no entity ID or topic collisions in Home Assistant.

### 8.3 Meshloom Community
- Community live packet relay and contribution stats associate with a designated primary radio or allow explicit selection.

---

## 9. Frontend Architecture

### 9.1 Radio Context & State Management
- `RadioContext`: Global React context providing `activeRadioId`, list of configured radios, and connection statuses.
- **Header / Navigation:**
  - With **single radio**: UI looks identical to current design. No selector clutter.
  - With **multiple radios**: Clean radio selector pill/dropdown in the header/rail displaying current radio and status dots for all configured radios.
- **Views Scoping:**
  - Contacts, Channels, Messages, Live Feed, Visualizer, Map, and Settings are scoped to `activeRadioId`.
  - Switching `activeRadioId` refreshes conversations, messages, and packet feeds immediately from cache/REST.

### 9.2 Settings UI
- Dedicated **Radios** panel in Settings:
  - List of radios with status indicator, transport summary, and actions (Edit, Reconnect, Disable, Delete).
  - "Add Radio" dialog with transport picker (TCP, Serial, BLE), host/port input, validation, and "Test Connection" button.

---

## 10. Remote Link (TCP) Hardening

For remote radios reached over WireGuard/Tailscale/cellular links:
- **Exponential Backoff with Jitter:** Reconnect attempts back off from 2s to 60s with random jitter to prevent reconnect storms.
- **Connection & Command Timeouts:** Default TCP connect timeout 10s, command timeout 15s to tolerate cellular latency spikes.
- **Half-Open Detection:** Periodic heartbeat (`commands.get_time()` or ping) detects silent connection drops without waiting for TCP socket timeout.
- **Failure Isolation:** A down remote radio logs warnings with backoff without consuming CPU or spamming logs, and never impacts local radios.

---

## 11. Minimal Change Set & Phased Execution

To ensure absolute stability, the implementation proceeds in strictly bounded phases:
- **Phase 1: Registry & Instance Abstraction (Single-Radio Behavior Preserved):** Introduce `RadioInstance` and `RadioRegistry`. Wrap single radio. Ensure 100% test pass.
- **Phase 2: Concurrent Multi-Radio Engine:** Support connecting multiple `RadioInstance`s simultaneously in background.
- **Phase 3: Schema Migration & Persistence Isolation:** Migration `_086`, `radio_id` columns, repository updates.
- **Phase 4: API & WebSocket Radio-Scoping:** Add `/api/radios`, parameterize endpoints by `radio_id`, tag WS events.
- **Phase 5: Outbound Sending, Bots & Fanout Scoping:** Explicit send targeting, bot isolation, HA topic stability.
- **Phase 6: Frontend Multi-Radio Experience:** Radio selector, scoped views, Settings radio management.
- **Phase 7: Remote Link Hardening & Aggregated "All Radios" View:** Backoff, half-open detection, read-time dedup view.
- **Phase 8: Documentation, Changelog & Packaging:** Multi-language documentation and packaging updates.
