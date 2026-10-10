# Fanout bus (`app/fanout/`)

The fanout bus sends mesh events to outside integrations. Each integration is a row in `fanout_configs` plus a `FanoutModule` instance owned by `FanoutManager`. Read this file only when you work on fanout.

## Core

- `base.py` — `FanoutModule(config_id, config, *, name="")`. Hooks: `start`/`stop`, `on_message`, `on_raw`, `on_contact`, `on_telemetry`, `on_health`. Each hook is a no-op by default. Subclasses must override `status` (`"connected"` / `"disconnected"` / `"error"`). `_set_last_error()` keeps an operator-facing error and pushes a health update.
- `manager.py` — `fanout_manager` singleton. `_MODULE_TYPES` (filled in `_register_module_types()`) maps `type` to a class. The manager builds every module as `cls(config_id, config, name=cfg.get("name", ""))`.
  - `load_from_db()` starts the enabled rows, then `sync_system_modules()`.
  - `reload_config(id)` stops the module and restarts it if the row is still enabled (one lock per id). `remove_config(id)` stops it.
  - Each handler runs under `asyncio.wait_for(..., 30 s)` (`_DISPATCH_TIMEOUT_SECONDS`). A timeout sets the module error and restarts the module. An exception sets the error. Modules run concurrently (`asyncio.gather`), and a failure in one module never affects the others.
  - `broadcast_message` drops `CHAN` messages for pending channels (`is_pending_channel`).
  - `get_statuses()` feeds `/api/health`. It skips disabled rows and reserved `system:*` ids.
  - Bots are skipped when `MESHCORE_DISABLE_BOTS` is set (`source="env"`), or after `POST /api/fanout/bots/disable-until-restart` (`source="until_restart"`).

## Event flow

```
broadcast_event(type, data, realtime=True)        app/websocket.py
  -> WebSocket (always)
  -> if realtime: radio proxy notify, then spawn(...)
       "message"    -> broadcast_message  (scope) + push_manager.dispatch_message
       "raw_packet" -> broadcast_raw      (scope)
       "contact"    -> broadcast_contact  (all modules)
dispatch_telemetry_event(data)                    app/websocket.py (no WS event)
  <- radio_sync.py (repeater and contact auto-collect), routers/repeaters.py, routers/contacts.py
  -> broadcast_telemetry (all modules)
radio_stats._stats_sampling_loop (every 60 s)
  -> broadcast_health_fanout (all modules)
```

`realtime=False` (historical decrypt) skips fanout and push.

## Scope

`scope` is JSON in the row. It gates `on_message` and `on_raw` only. The other three hooks reach every module.

- `messages`: `"all"` | `"none"` | `{"channels": F, "contacts": F}`, where `F` = `"all"` | `"none"` | `[keys]` | `{"except": [keys]}`. An omitted sub-key means `"none"`.
- `raw_packets`: `"all"` | `"none"`.

`_enforce_scope()` in `app/routers/fanout.py` sets or validates the scope per type:

| type | scope |
|------|-------|
| `mqtt_community`, `map_upload` | forced `{"messages":"none","raw_packets":"all"}` |
| `bot` | forced `{"messages":"all","raw_packets":"none"}` |
| `webhook`, `apprise`, `mqtt_ha` | `messages` configurable, `raw_packets` forced `none` |
| `mqtt_private`, `sqs` | both configurable |

## Payloads

- `on_message`: `Message.model_dump()` (`app/models.py`). Includes `type` (`PRIV`/`CHAN`), `conversation_key`, `text`, `sender_name`, `sender_key`, `outgoing`, `acked`, `paths`, `sender_timestamp`, `received_at`, `channel_name`, `packet_hash`, `transport_code`, `region`. `CHAN` text is stored as `"Sender: body"`. Use `base.get_fanout_message_text()` to strip that prefix.
- `on_raw`: `RawPacketBroadcast` (`app/models.py`). Includes `id` (storage row), `observation_id` (one per RF arrival), `timestamp`, `data` (packet hex), `payload_type`, `snr`, `rssi`, `decrypted`, `decrypted_info` (`channel_key`, `contact_key`, `sender`, `message`, ...), `transport_code`, `region`.
- `on_contact`: a contact dict (`Contact` fields: `public_key`, `name`, `type`, `lat`, `lon`, `last_seen`, `on_radio`, ...).
- `on_telemetry`: `{public_key, name, timestamp, **snapshot}`. The snapshot holds repeater status fields or contact LPP data, depending on the source.
- `on_health`: `radio_stats._build_fanout_payload()` — `connected`, `connection_info`, `public_key`, `name`. When stats are available it adds `noise_floor_dbm`, `battery_mv`, `uptime_secs`, `last_rssi`, `last_snr`, `tx_air_secs`, `rx_air_secs`, `packets_recv`, `packets_sent`, `flood_tx`, `direct_tx`, `flood_rx`, `direct_rx`.

## Module types

The router's validators (`_validate_*_config`) are the source of truth for config fields and defaults.

- `mqtt_private` (`mqtt_private.py` → `MqttPublisher` in `mqtt.py`, on top of `mqtt_base.BaseMqttPublisher`). Fields: `broker_host`, `broker_port` (1883), `username`, `password`, `use_tls`, `tls_insecure`, `topic_prefix` (`meshcore`). Publishes messages and raw packets.
- `mqtt_ha` (`mqtt_ha.py`). Home Assistant MQTT Discovery. Fields: the same broker fields as `mqtt_private`, plus `topic_prefix`, `tracked_contacts`, and `tracked_repeaters` (lists of public keys). Uses `on_health` (local radio sensors), `on_contact` (device_tracker), `on_telemetry` (repeater sensors), and `on_message` (event entity).
- `mqtt_community` (`mqtt_community.py` → `CommunityMqttPublisher` in `community_mqtt.py`). This is a user-configured raw-packet uplink. The create UI offers it as generic, MeshRank, or LetsMesh US/EU presets. Fields: `broker_host` (`mqtt-us-v1.letsmesh.net`), `broker_port` (443), `transport` (`websockets`|`tcp`), `use_tls`, `tls_verify`, `auth_mode` (`token`|`password`|`none`), `username`/`password` (required when `auth_mode=password`), `token_audience`, `iata` (required, `^[A-Z]{3}$`), `email`, `websocket_path`, `topic_template` (`meshcore/{IATA}/{PUBLIC_KEY}/packets`). Token auth is an Ed25519 JWT signed with the radio key. `on_message` is a no-op. The payload follows the meshcore-packet-capture format (`community_mqtt._format_raw_packet`): `raw` is the upper-case packet hex. Direct-route packets also carry `path`, which is the hop identifiers joined by commas, so the token width follows the path hash mode.
- `bot` (`bot.py`, `bot_exec.py`). Field: `code`. The code must define `bot(...)`. The router checks the syntax and signature (400 on error). Creating or patching a bot answers 403 while bots are disabled. Execution model:
  - The code runs through `exec()` with full `__builtins__` in a dedicated thread pool. Concurrency is capped at 100 (`LoopBoundSemaphore`), and each run times out after 10 s (`BOT_EXECUTION_TIMEOUT`). `_bot_globals` persists between runs. This is arbitrary code execution, by design.
  - The bot waits 2 s before it runs, so echoes can dedupe. Bot sends are serialized at least 2 s apart (`BOT_MESSAGE_SPACING`).
  - Positional arguments: `sender_name, sender_key, message_text, is_dm, channel_key, channel_name, sender_timestamp, path`. The optional `is_outgoing`, `path_bytes_per_hop`, and `packet_hash` are passed when the signature accepts them. `region` and `scoped` go only to bots that name them or take `**kwargs` (`_analyze_bot_signature`). `scoped` = `transport_code is not None`. It is also set on scoped DMs. `region` is `None` both when the message is unscoped and when the region is unknown, so check `scoped` first.
  - For channel messages, `message_text` drops the `"sender_name: "` prefix.
  - Return values: `None`, a `str`, a `list[str]`, or `{"region": name|None, "message": str|list[str]}` (`BotReply`). In the dict form, `region` scopes that one channel reply: a name scopes it, `None` sends it unscoped, and an absent key keeps the channel's `flood_scope_override`. Scoping goes through `services/message_send.send_channel_message_with_effective_scope`. DM replies ignore `region`.
- `webhook` (`webhook.py`). Messages only. Fields: `url` (http/https), `method` (`POST`/`PUT`/`PATCH`), `headers` (object), `hmac_secret`, and `hmac_header` (default `X-Webhook-Signature`). The body is `Message` JSON (compact, sorted keys). The signature is `sha256=<hex HMAC of the body>`. Timeout: 10 s.
- `apprise` (`apprise_mod.py`). Messages only. Fields: `urls` (newline-separated, at least one), `preserve_identity` (default true; keeps the Discord name and avatar), `include_outgoing` (default false), `markdown_format` (default true), and `body_format_dm` / `body_format_channel` (format strings, checked against `FORMAT_VARIABLES`). Legacy `include_path` is used only when no format string is set (migration 060).
- `sqs` (`sqs.py`). Fields: `queue_url` (required), `region_name` (taken from an `*.amazonaws.com` URL when empty), `endpoint_url`, and `access_key_id` + `secret_access_key` (both or neither) + `session_token` (needs the key pair). With no keys, the module uses the default AWS credential chain. The body is `{"event_type":"message"|"raw_packet","data":...}`.
- `map_upload` (`map_upload.py`). Uploads repeater and room adverts (roles 2 and 3) to `https://map.meshcore.io/api/v1/uploader/node` (`_DEFAULT_API_URL`). Each upload is signed with the radio's private key from `keystore`. Without that key the upload is skipped with a warning. The same key is not re-uploaded within 3600 s. Fields: `api_url` (empty = default), `dry_run` (default **true**: logs only), `geofence_enabled`, and `geofence_radius_km`. The geofence center is the radio's live `self_info` `adv_lat`/`adv_lon`. When those are `(0,0)` or unavailable, the geofence check is skipped.

### System module (not a user type)

`meshloom_stats.py` (`MeshloomStatsModule`, id `system:meshloom-stats`) uploads raw packets to the Meshloom Community Stats broker. `sync_system_modules()` starts it from the Community state (`services/meshloom_community.get_community_effective()`), not from `fanout_configs`. It is hidden from `/api/fanout` and from fanout statuses. `PATCH`/`DELETE` on any `system:*` id answer 403. It can run next to a user `mqtt_community` row.

## REST (`app/routers/fanout.py`)

| Method | Path | Notes |
|--------|------|-------|
| GET | `/api/fanout` | All user rows (without `system:*`) |
| POST | `/api/fanout` | 400 for an unknown type or an invalid config/scope. 403 for a bot while bots are disabled. Starts the module if enabled |
| PATCH | `/api/fanout/{id}` | 404 / 403 (reserved id or bot disabled). Re-validates the config every time, then reloads the module |
| DELETE | `/api/fanout/{id}` | Stops the module, then deletes the row |
| POST | `/api/fanout/bots/disable-until-restart` | Stops the bot modules and blocks them until the process restarts |

Configs are validated on every create and update, whether they are enabled or not.

## Adding a type

1. Write `app/fanout/<type>.py`: subclass `FanoutModule`, keep the constructor signature, forward `name` to `super()`, and implement `status`.
2. Register it in `manager._register_module_types()`.
3. In `app/routers/fanout.py`: add it to `_VALID_TYPES`, add a `_validate_<type>_config` wired into `_validate_and_normalize_config`, and add an `_enforce_scope` branch if the scope is fixed. Without that branch it gets the `mqtt_private`/`sqs` behaviour.
4. Frontend (`frontend/src/components/settings/SettingsFanoutSection.tsx`): add the type to `FANOUT_TYPE_ORDER`, the `DraftType` union, and `CREATE_INTEGRATION_DEFINITIONS` (`savedType`, section, default config and scope). Add a `<Type>ConfigEditor` and its `detailType === '<type>'` branch. Use `ScopeSelector` (`showRawPackets` when raw packets are configurable). Add the i18n keys `settings.fanout.types.<type>` and `settings.fanout.create.<draft>.{label,description,defaultName}` in `frontend/src/i18n/locales/{en,fr}.json`.
5. Tests: `tests/test_fanout.py` (CRUD, scope, manager), `tests/test_fanout_integration.py` (lifecycle and delivery), `tests/test_fanout_hitlist.py`, a dedicated file if needed (for example `test_sqs_fanout.py`, `test_mqtt_ha.py`), and `frontend/src/test/fanoutSection.test.tsx`.

## Storage

The `fanout_configs` table (`app/database.py`) has the columns `id` TEXT PK, `type`, `name`, `enabled`, `config` (JSON), `scope` (JSON), `sort_order`, and `created_at`. CRUD and the in-memory `_configs_cache` live in `app/repository/fanout.py`.
