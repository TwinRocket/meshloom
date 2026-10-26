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
- There is no `bot` fanout type any more (migration 122 converted them into local
  plugins). `POST /api/fanout` with `type: "bot"` and `PATCH` on an archived bot id
  answer 410 `bot_type_retired`; the old "disable bots until restart" route is gone.
- `system:*` ids are reserved: hidden from `/api/fanout` and fanout statuses,
  `PATCH`/`DELETE` on them answer 403.

## Secrets

- Secret config keys are write-only: `FANOUT_SECRET_KEYS` in `app/secret_fields.py`
  (Apprise `urls`, webhook `url`, `headers` and `hmac_secret`, the MQTT
  `password`, SQS `access_key_id`, `secret_access_key` and `session_token`).
  `GET /api/fanout` and `GET /api/fanout/{id}` return them through
  `redact_config()`: `********` when set, `""` when not; Apprise adds the
  derived `urls_redacted` (one `redact_apprise_url()` line per URL), the webhook
  `url_redacted` (`redact_url()`: scheme, host, port), both never stored. The
  webhook `headers` are an object secret (`_OBJECT_SECRET_KEYS`): names in
  clear, each value `********` or `""`. `allow_private_network`
  and `private_network_grandfathered` are plain flags, not secrets.
- At rest these keys are sealed (`v1:` tokens, `app/secret_store.py`, AAD per row and
  key) by `repository.fanout.seal_config()` on every write and opened by
  `open_config()` on read; a plain legacy value is read as is and sealed on the next
  write. A token that does not open is dropped from the config and listed in
  `secret_unreadable`; the manager does not start that module and reports
  `secret_unreadable` until the secret is sent again (an update without it keeps the
  token: name, `enabled`, scope or an unchanged config are saved, another config change
  is 409 `secret_unreadable`). Without a usable key a write that carries a secret raises
  `SecretStoreUnavailable` (503 `secret_store_unavailable`), never stores it plain.
  Details in `app/AGENTS.md`, "Secrets at rest".
- Writes go through `merge_secrets()`: a key omitted, `None` or `********`
  keeps the stored value, `""` clears it, anything else replaces it; derived
  keys are dropped. An object secret merges by name: a `********` value keeps
  that name's stored value (none: the name is dropped), a name left out is
  removed, `""` for the whole object clears it to `{}`. A secret that is its
  own destination (webhook `url`, like Apprise `urls`) is resolved first, so
  keeping it is no move. The routes and the backup restore use it, so a redacted
  config sent back never overwrites a secret.
- A kept secret never follows a destination change. `FANOUT_DESTINATION_KEYS`
  lists, per type, the keys that say where the secret goes (with their
  defaults: an absent key reads as its default): webhook `url` (its
  `headers` values and `hmac_secret` follow it), MQTT
  `broker_host`/`broker_port`/`use_tls`/`tls_insecure` (and the Community
  broker's transport, TLS and path), SQS `queue_url`/`endpoint_url`/`region_name`. When one of them
  changes and a non-empty stored secret would be kept, `merge_secrets()` raises
  `SecretRequiredError`: `PATCH` answers 400 `secret_required` (params
  `fields`), and the restore clears that secret and adds the warning
  `secret_dropped_destination_changed:<name>`. Sending the secret again (or
  `""`) passes. The editor mirrors the table (`SECRET_DESTINATION_KEYS`) and
  marks the kept secret field required once a destination field is edited.
- `get_statuses()` (health, `/api/debug`) passes each `last_error` through
  `scrub_error()` with `config_secret_values()`: for an Apprise URL, only its
  `user:password` and path tokens (`apprise_url_secret_parts()`), for a
  webhook URL its user, path and query parts (`url_secret_parts()`) and the
  header values, so the host stays readable; the Apprise module scrubs its send
  errors before logging them.

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
every broadcast_* above (message: after the pending-channel check)
  -> plugin_manager.dispatch(hook, data)          app/plugins/host.py (queues, never blocks)
```

Plugins get the same events through the plugin host, not as fanout modules:
`message`, `raw_packet`, `contact`, `telemetry`, `health`, each to the running
instances that subscribe that hook (`docs/dev/plugins.md`, "Supervisor"). Converted
bots are plugin instances, not fanout modules.

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
| `webhook`, `apprise`, `mqtt_ha` | `messages` configurable, `raw_packets` forced `none` |
| `mqtt_private`, `sqs` | both configurable (also the default for a type with no branch) |

## Payload pitfalls

- `CHAN` message text is stored as `"Sender: body"`: use
  `base.get_fanout_message_text()` to strip the prefix (plugins already get it stripped).
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
- **Converted bots** (`bot` rows of 4.18, migration 122) live in `plugins` as local
  instances running `bot.py` through the fixed `compat.py` wrapper
  (`meshloom_plugin/compat.py`, `legacy_bot`; `analyze_bot_signature` lives there too),
  inside the plugin sandbox. `region` is `None` both when unscoped and when unknown:
  check `scoped` first. In a `BotReply` dict, an absent `region` keeps the channel's
  `flood_scope_override`, `None` sends unscoped. The originals are archived in
  `fanout_configs_bots_archive` (`docs/dev/plugins.md`, "Legacy bots").
- **Egress guard.** `webhook`, `apprise`, `sqs` and `map_upload` destinations
  go through `app/net/egress_guard.py` with `config.allow_private_network`
  (default false; migration 100 grandfathered existing rows). The validators
  stay network-free; the routes add `_check_destinations` (DNS) for a row that
  is or becomes enabled, and the modules check again before each send. Apprise
  accepts only the allowlist of `app/net/apprise_urls.py`, with Matrix
  discovery and e-mail WKD forced off. Details:
  `app/net/AGENTS.md`.
- **`webhook`** always sends `X-Webhook-Event: message`, and with a secret the
  signature header `sha256=<hex HMAC of the body>` (compact, sorted-keys JSON).
  It sends through `pinned_async_client` (no redirect, no proxy variables);
  `last_error` is a code (`egress_*`, `timeout`, `connect_error`...) or `HTTP <status>`.
- **`apprise`** sends the instances `validate_apprise_urls` returned (redirects
  off); a refused URL sets `last_error` to `<code> (URL <line>)`, never the URL.
  It skips blocked senders and muted channels (`services/message_filters.py`,
  the one block-list rule the notification rules use too); `mqtt_private`, `sqs` and
  `webhook` still forward them (machine integrations,
  `tests/test_message_filters.py`).
  Deprecated since 5.0.0 (removal in 5.2.0, MSHL-134): `POST /api/fanout` still
  accepts it but answers with a `Deprecation: true` header and logs a warning; the
  UI no longer offers it. `POST /api/fanout/{id}/convert-to-notification-channel`
  (`app/notifications/fanout_convert.py`) moves a row into a notification channel:
  the URLs go through `NotificationChannelRepository.create()` (secret store, egress
  guard), the scope becomes rules, the row is disabled and marked
  `config.converted_to = <channel id>`. Before creating the channel it writes
  `config.converting_to = <new channel id>` (write-ahead): a retry after a failure in
  between reuses that channel or creates it under that id, never a second one. If the
  duplicate prompt then attaches the still-marked row to another channel, the pending
  channel is deleted first (with its secrets and rules) while untouched (same settings and
  secrets, no other row marked with it, no hand-made rule), else kept with the note
  `pending_channel_kept`. Clients
  cannot set or clear either key (create drops them, update keeps the stored values);
  backup and restore carry them as part of `config`. A second call is a no-op
  (`already_converted`). The answer has no `config` at all (channel and row by id,
  name, type, state); refusals are `{code, params}` with no config. Never log or
  return a URL. A converted row turned back on is not reported as a duplicate.
- **`map_upload`** defaults to `dry_run: true` (logs only), signs each upload with
  the radio's private key from `keystore` and skips without it. Uploads go
  through `_post()` (guard, then pinned client).
- **`sqs`** checks a custom `endpoint_url` at `start()`; refused, it never
  builds the boto3 client and reports the code.

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

## Sharing links and images (no runtime change)

Bots are gone (extensions replaced them) and the fanout runtime is unchanged by sharing. A fanout module
(webhook, MQTT, SQS, and the fanout Apprise, which formats `{text}` itself) receives the message text as
stored, `📷 <link>` and `🔒 <link>` included. Only the notification renderer summarises it
(`app/share_text.py`, wired once in `app/notifications/render.py` `message_body_text`: Web Push, e-mail,
ntfy and the notification Apprise channels, never the webhook channel). Extensions have no `share` permission and no API token
(`/api/share/*` needs an authenticated principal with `send`): a script outside Meshloom, with a `send`
token, calls `POST /api/share/links` and `POST /api/share/files` (user guide `docs/user/{en,fr}/deep/fanout.md`).

## Adding a type

1. Write `app/fanout/<type>.py`: subclass `FanoutModule`, keep the constructor
   signature, forward `name` to `super()`, and implement `status`.
2. Register it in `manager._register_module_types()`.
3. In `app/routers/fanout.py`: add it to `_VALID_TYPES`, add a
   `_validate_<type>_config` wired into `_validate_and_normalize_config`, and an
   `_enforce_scope` branch if the scope is fixed.
   List its secret keys in `FANOUT_SECRET_KEYS` (`app/secret_fields.py`), even
   when empty, its destination keys in `FANOUT_DESTINATION_KEYS` (and the
   editor's `SECRET_DESTINATION_KEYS`), and use `SecretInput` for the secrets.
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
