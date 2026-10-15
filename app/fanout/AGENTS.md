# Fanout bus (`app/fanout/`)

Each integration is a row in `fanout_configs` (CRUD and `_configs_cache` in
`app/repository/fanout.py`) plus a `FanoutModule` instance owned by
`FanoutManager`. The router's validators (`_validate_*_config` in
`app/routers/fanout.py`) are the source of truth for config fields and defaults;
configs are validated on every create and update, enabled or not.

## Core rules

- `base.py`: `FanoutModule(config_id, config, *, name="")`. Hooks `start`/`stop`,
  `on_message`, `on_raw`, `on_contact`, `on_telemetry`, `on_health` are no-ops by
  default. Subclasses must override `status` (`"connected"` / `"disconnected"` /
  `"error"`). `_set_last_error()` keeps an operator-facing error and pushes a
  health update.
- `manager.py`: the manager builds every module as
  `cls(config_id, config, name=cfg.get("name", ""))`, so keep that constructor
  signature. Each handler runs under a 30 s `wait_for`; a timeout sets the error
  and restarts the module. Modules run concurrently and a failure in one never
  affects the others.
- `broadcast_message` drops `CHAN` messages for pending channels.
- Bots are skipped when `MESHCORE_DISABLE_BOTS` is set (`source="env"`) or after
  `POST /api/fanout/bots/disable-until-restart` (`source="until_restart"`);
  creating or patching a bot then answers 403.
- `system:*` ids are reserved: hidden from `/api/fanout` and fanout statuses,
  `PATCH`/`DELETE` on them answer 403.

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

`realtime=False` (historical decrypt) skips the proxy, fanout and push.

## Scope

`scope` (JSON in the row) gates `on_message` and `on_raw` only; the other hooks
reach every module.

- `messages`: `"all"` | `"none"` | `{"channels": F, "contacts": F}`, where `F` =
  `"all"` | `"none"` | `[keys]` | `{"except": [keys]}`. An omitted sub-key means `"none"`.
- `raw_packets`: `"all"` | `"none"`.

`_enforce_scope()` in `app/routers/fanout.py` forces it per type:

| type | scope |
|------|-------|
| `mqtt_community`, `map_upload` | forced `{"messages":"none","raw_packets":"all"}` |
| `bot` | forced `{"messages":"all","raw_packets":"none"}` |
| `webhook`, `apprise`, `mqtt_ha` | `messages` configurable, `raw_packets` forced `none` |
| `mqtt_private`, `sqs` | both configurable (also the default for a type with no branch) |

## Payload pitfalls

- `CHAN` message text is stored as `"Sender: body"`: use
  `base.get_fanout_message_text()` to strip the prefix (bots already get it stripped).
- `on_raw` carries both `id` (storage row, repeats) and `observation_id` (one per
  RF arrival).
- `on_health` stats fields are `None` when the firmware sends the legacy 26-byte
  packet-stats frame.

## Module pitfalls

- **MQTT publishers** share `mqtt_base.BaseMqttPublisher`. `stop()` runs
  `_before_disconnect(client)` while still connected, then cancels the loop; both
  are bounded because aiomqtt's `__aexit__` waits for the broker without a timeout.
- **`mqtt_ha`** publishes nothing until the radio identity is known (first health
  frame). Discovery configs are retained and cleared on stop; cached telemetry is
  replayed without `retain`. User docs: `README_HA.md`.
- **`mqtt_community`** (user raw-packet uplink) follows the meshcore-packet-capture
  format: `raw` is upper-case hex, and direct-route `path` is the hop identifiers
  joined by commas, so the token width follows the path hash mode. Token auth is an
  Ed25519 JWT signed with the radio key.
- **`bot`** runs user code through `exec()` with full `__builtins__`, by design
  (arbitrary code execution; changing it is the out-of-scope Plugins work). Runs
  are capped in concurrency and time; `_bot_globals` persists between runs. The
  bot waits 2 s so echoes can dedupe, and bot sends are spaced at least 2 s apart.
  Optional arguments (`region`, `scoped`, …) are passed only to bots whose
  signature names them (`_analyze_bot_signature`). `region` is `None` both when
  unscoped and when unknown: check `scoped` first. In a `BotReply` dict, an absent
  `region` keeps the channel's `flood_scope_override`, `None` sends unscoped.
- **`webhook`** always sends `X-Webhook-Event: message`, and with a secret the
  signature header `sha256=<hex HMAC of the body>` (compact, sorted-keys JSON).
- **`map_upload`** defaults to `dry_run: true` (logs only), signs each upload with
  the radio's private key from `keystore` and skips without it.

## System module (not a user type)

`meshloom_stats.py` (`system:meshloom-stats`) uploads raw packets to the Meshloom
Community broker. It is started by `sync_system_modules()` from
`community_enrolled_state()`, not from `fanout_configs`, and may run next to a user
`mqtt_community` row. While Community waits for a position it publishes its status
with `state: waiting_for_position` and no packets. Every clean `stop()` of a
`CommunityMqttPublisher` first publishes the Will's `offline` payload, retained;
on opt-out or an IATA change the next `stop()` also clears the retained status, and
`services/community_status_clear.py` retries a clear that was not acknowledged.
Detail: `docs/dev/community-client.md`.

## Adding a type

1. Write `app/fanout/<type>.py`: subclass `FanoutModule`, keep the constructor
   signature, forward `name` to `super()`, and implement `status`.
2. Register it in `manager._register_module_types()`.
3. In `app/routers/fanout.py`: add it to `_VALID_TYPES`, add a
   `_validate_<type>_config` wired into `_validate_and_normalize_config`, and an
   `_enforce_scope` branch if the scope is fixed.
4. Frontend (`frontend/src/components/settings/SettingsFanoutSection.tsx`): add the
   type to `FANOUT_TYPE_ORDER`, the `DraftType` union and
   `CREATE_INTEGRATION_DEFINITIONS`, a `<Type>ConfigEditor` and its
   `detailType === '<type>'` branch; use `ScopeSelector`. Add the i18n keys
   `settings.fanout.types.<type>` and
   `settings.fanout.create.<draft>.{label,description,defaultName}` in both
   languages where the other fanout keys live: `frontend/src/i18n/locales/{en,fr}.json`
   and the slices `locales/slices/d.{en,fr}.json`, deep-merged over the base.
5. Tests: `tests/test_fanout.py`, `tests/test_fanout_integration.py`,
   `tests/test_fanout_hitlist.py`, a dedicated file if needed, and
   `frontend/src/test/fanoutSection.test.tsx`.
6. User docs: `docs/user/{en,fr}/deep/fanout.md`.
