# Notifications (`app/notifications/`)

The event catalogue, the one FR/EN renderer, the channels, the rules, the dispatcher
and its transports, and the delivery helpers (`apprise_medium.py`, `executor.py`,
`radio_watch.py`). Delivery to a
user-chosen destination goes through the egress guard (`app/net/AGENTS.md`).
This file is a hypothesis: check it against the code.

## Modules

- `events.py`: the catalogue, one frozen dataclass per event, and
  `event_from_payload()`, which turns the legacy payload dicts the producers still
  build (`{"event": "first_seen", ...}`) into catalogue events; `to_payload()` goes
  back. The grouped summaries (`notifications_grouped`, `first_seen_grouped`) are
  dispatcher output, not catalogue events.
- `ws_frame.py`: the `notification_event` WebSocket frame (MSHL-138), see "Dispatcher".
- `render.py`: the only place notification copy lives. `render(event, lang,
  text_policy=...)` returns `Rendered(title, body_text, body_markdown, body_html,
  tag, url_hash)`. The dict helpers (`build_push_payload`, `event_notification_text`,
  `notification_email_lines`, `render_plain_email`, `render_html_email`,
  `enrich_notification_payload`) serve the callers that pass dicts; they were moved
  unchanged from `app/push/manager.py` and `app/email_template.py`, which re-export
  them under their old names (tests import `_build_payload` from `push.manager`).
  Message texts go through `message_body_text()`, the one call site of `app/share_text.py`
  (`share_text_summary`: a share link reads `[image]`, `[encrypted file]` or `[link]`; offline,
  never a URL or a `#fragment`). The webhook transport sends the raw text (v1: the raw payload;
  v2: `data.text` and `rendered.body`, both subject to the channel's text policy).

- `failures.py`: a channel that keeps failing (MSHL-130). Constants (`FAILED_EVENT_AFTER` 3,
  `PAUSE_AFTER` 20, `BACKOFF_S` 300/900/3600/21600), `safe_error_code` (the notice carries a
  bare code such as `http_503` or `timeout`, never `last_error` as stored: no token, password,
  URL or host), `announce()` (the once-a-day `notify.channel_failed`) and
  `announce_push_removed()` (`push_unsubscribed`, `push_evicted`, `push_refused`). The dispatcher owns the counting, the pause and the probes.
- `channels.py`: the channel models and the type registry. A type declares a Pydantic
  config model (`WebPushConfig`, `EmailConfig`, `WebhookConfig`, `AppriseConfig`, `NtfyConfig`, `DiscordConfig`); secret
  fields are `SecretStr` with `writeOnly: true` in `json_schema_extra`, labels and help are
  i18n keys (`label_key`, `help_key`) in the schema, the texts live in the frontend.
  `types_schema()` is what `GET /api/notifications/types` serves and the shared schema-driven form renders. A registry entry also carries
  `default_limits` (per minute, hour, day), `cloud` (default `include_message_text`
  `public_only`, else `always`), `quiet_hours_exempt_default` and `required` fields.
  `limits_check` replaces `default_limits` for a config (`limits_for(public)`; ntfy.sh gets a daily cap). The presets are the entries of `presets.py` (see "Presets").
- `templates.py`: per-channel message templates (MSHL-127). `notification_channels.template_dm` /
  `template_channel` (migration 130; plain columns, NOT in the encrypted secret set) hold a body
  template for the row types `apprise` (and its presets), `ntfy` and `discord`
  (`TEMPLATE_ROW_TYPES`; `/types` carries `templates: true`); any other type refuses a non-empty one
  (`template_unsupported`). Variables are the fanout Apprise ones (`apprise_mod.FORMAT_VARIABLES`) plus
  `title`, substituted by the fanout's single-pass `_apply_format` (a substituted value is never expanded
  again, an unknown `{name}` stays as written). `validate_template`/`check_templates` run on save (create,
  PATCH, backup restore) against dummy variables: at most `MAX_TEMPLATE_LENGTH` (500) characters, no control
  character other than newline and tab (`template_too_long`, `template_invalid`). `render.event_notification_text(template_dm=,
  template_channel=)` applies a template to message events only, after the text policy (`{text}` is empty
  when the policy strips it; an empty result keeps the default body); values get `neutralise_mentions`, and on a
  Markdown channel `escape_markdown` for the mesh-typed ones (`MESH_TEXT_VARIABLES`) while the template's own markup
  stays. The Apprise, ntfy and Discord transports pass the channel's templates. `POST
  /api/notifications/channels/preview-template` renders a sample DM and channel message through the same
  function. Mention events (`message.mention`) use the templates like other messages.
- `heartbeat.py`: the per-channel heartbeat (MSHL-131). `notification_channels.heartbeat_interval_s`
  (migration 131; 0 = off, the default, else `HEARTBEAT_MIN_S`..`HEARTBEAT_MAX_S` = 300..86400) on the
  types with `heartbeat: true` in `/types` (`webhook`, `apprise` and its `gotify`/`home_assistant` presets,
  `ntfy`; any other refuses a non-zero value with `heartbeat_unsupported`). `HeartbeatManager`
  (`dispatcher.heartbeats`) keeps one `background_tasks.spawn` loop per channel (`asyncio.sleep` through the
  dispatcher's injectable `_sleep`), started by `Dispatcher.start` (`start()` sets `_active`; outside the app
  nothing runs) and kept in step by `reconcile()`: after each channel create/PATCH/delete/enable, a backup
  restore and every 60 s in the quiet loop; a changed interval or URL restarts the loop, `stop()` cancels all.
  Two modes. **Message**: the `notify.heartbeat` event (instance name, uptime, version) one interval after the
  start, sent through the channel's transport outside the queue: exempt from the global pause, quiet hours and
  the buckets, counted (`count_sent`) but not journalled; skipped while the channel is auto-paused, and a
  failure counts like any send. **Ping**: when the encrypted `heartbeat_url` secret is set, a plain GET or POST
  (`heartbeat_method`, empty body) through `pinned_async_client` (`follow_redirects=False`, a 3xx is
  `redirect_refused`, `PING_TIMEOUT_S` 10, the answer is never read), at start then every interval, the URL
  validated by the egress guard on save (`check_ping_url`) and again before every ping; it ignores the
  auto-pause and a failure is only logged as a code.
- `app/repository/notification_channels.py`: `NotificationChannelRepository` (CRUD, the
  in-memory cache of the public channels, status updates, `get_rules()`) and
  `NotificationJournalRepository` (deliveries, batched `add_deliveries()`, counters, state).
- `rules.py`: pure evaluation of `notification_rules` (see "Dispatcher").
- `fanout_convert.py`: Apprise fanout row → notification channel, and the duplicate check on
  channel creation (MSHL-134, see "API").
- `quiet_hours.py`: the quiet window, the suppressed-event counters and the digest payload (see "Quiet hours"); also `digestible()` for the per-channel digest mode (see "Digest mode").
- `user_rules.py`: the per-account layer over those rules, for Web Push only (see "Per-account rules").
- `limits.py`: the dispatcher's token buckets (`ClockedLimiter`), the daily quota, `effective_limits()` and the persisted dedup keys (see "Dispatcher", "Limits").
- `dispatcher.py`: `emit()`, the queue and worker per channel, the limiter, the retry
  policy, the journal, the counters and `notification_statuses()` (see "Dispatcher").
- `transports/`: `base.py` (`Transport.send(channel, rendered, event, secrets) ->
  SendResult(status, retry_after, error_code)`, `result_from_exception()` for httpx),
  `web_push.py` (wraps `PushManager.dispatch_event`), `email.py`, `webhook.py`,
  `apprise.py` (also the transport of the presets), `ntfy.py` (the native transports of the `email`, `webhook`, `apprise` and `ntfy` channel types,
  see "Native transports"), `legacy.py` (`legacy:email`, `legacy:webhook`,
  `legacy:apprise` through the `app/notify.py` send functions, which read
  `notification_destinations` at send time; `default_transport` gives a `legacy:*` id the legacy transport, any other channel its
  native one).

## Channels (data model)

- **Ids.** `builtin:push` (type `web_push`, undeletable, one only), `legacy:email`,
  `legacy:webhook`, `legacy:apprise` (the import of 4.18.x data, migration 113), else
  `<type>-<8 hex>`. An id is never reused after a delete.
- **Type is immutable**: `ChannelError("type_immutable")`, HTTP 400. `ChannelError` carries
  a stable `code`, a `status` and `params` (never a secret); the routes answer
  `{code, params}`.
- **Secrets are write-only.** `Channel` (every read path, the cache, the schema) has only
  `secret_fields_set`, the names that hold a value. `get_for_send()` is the one reader of
  the values, for the dispatcher. A PATCH keeps a secret when it is omitted, `null` or
  `********`, and clears it with `""`. A kept secret never follows a destination change
  (email host/port/mode, webhook URL): `secret_required`. Each value in `secrets` is a `v1:`
  AES-256-GCM token (`app/secret_store.py`, `encode_secrets`/`decode_secrets`); an
  unreadable one turns the channel off with `secret_unreadable` (`app/AGENTS.md`,
  "Secrets at rest"). Without a usable key a save that carries a secret is 503
  `secret_store_unavailable`; nothing is stored plain.
- **Destinations are checked** at create and when the destination or
  `allow_private_network` changes (egress guard; Apprise URLs by `validate_apprise_urls`),
  coded `egress_*` / Apprise codes. `allow_private_network` is false for a new channel; the
  import grandfathers every existing destination.
- **Rules** (`app_settings.notification_rules`, the admin defaults, see "Per-account rules"): `version`, `paused_until`, `events[id]`
  (`channels`, `always_deliver`, and for `contact.first_seen` `contact_types`
  `{companion, repeater, sensor}` since 4.18.x read the three types differently),
  `conversations[key][channel_id] = bool`, `keywords[*]` (see "Keywords"),
  `watched_nodes[*]` `{public_key, channels, cooldown_s, silence_s}` (see "Watched nodes"). The import drops a medium whose channel it did not create
  (no SMTP, no URL): it could not deliver, and a reference would be an orphan.
- **Backup.** The `notifications` backup section (`services/backup_notifications.py`) exports
  the channels without secrets and the rules; a restore writes a channel missing a secret
  disabled with `last_error = "secret_missing"`, which `update()` clears once the secret is
  saved. The heartbeat interval and method travel; the `heartbeat_url` secret does not (kept on the same
  channel, else pending), and an interval out of range or on a type without heartbeat restores as 0
  (`notification_heartbeat_dropped:<id>`). The accounts' own choices travel in the `notification_user_rules` section. A 4.18.x file reaches the rules and the `legacy:*` channels through `legacy_sync`
  (the migration 113 builders). See `docs/dev/backup.md`.
- **Deletion cascade.** `delete()` removes the channel, its deliveries and counters, and its
  id from `events[*].channels`, `contact_types`, `conversations`, `keywords[*].channels`
  and `watched_nodes[*].channels` (`strip_channel_from_rules`) in one transaction. A
  disabled channel keeps its rules. `resolve(ids)` skips an orphan id and logs it once.
- **Cache.** `list_all()`/`get()`/`resolve()` read a cache invalidated by every write.
- **Caches assume one process** (carried over from MSHL-113). The channel cache, the block lists
  (`services/message_filters.py`, dropped by `AppSettingsRepository.update` and the block toggles;
  `tests/test_message_filters.py` covers the PATCH, the toggles and a restore), the rules'
  readers and the dispatcher's buckets, quota counts and dedup keys live in the memory of the
  one Meshloom process that owns the radio and the database. A second process on the same
  database (a script writing settings with `meshloom-admin`, a second instance) would not
  invalidate them: it is unsupported, and a change made that way is seen after a restart.
- **Journal.** 200 `notification_deliveries` rows per channel (trimmed at insert, plus the
  daily `prune_deliveries()`); successes are counted per channel and hour
  (`notification_counters`); held-by-quiet-hours and grouped rows are also counted per hour in
  `notification_state` (`stat:<status>:<hour>:<channel>`, same transaction as the journal rows,
  read by `window_totals()` for `GET /deliveries` `totals`); `notification_state` holds the dedup keys (`dedup:*`), the pending secrets of a restored channel, with an expiry.
- **The rules decide, the shims keep the 4.18.x columns in step.** The producers emit events
  (`producers.py`) and the rules choose the channels. The deprecated routes
  (`/api/push/preferences*`, `PATCH /api/settings` with `notification_destinations`) still write
  the legacy columns (`push_defaults`, `push_conversation_overrides`,
  `notification_destinations`), and `AppSettingsRepository` calls `legacy_sync.sync_rules()` /
  `sync_destinations()` after each such write: `legacy:*` channels follow the destinations (one
  that is no longer configured is deleted, with the cascade) and `builtin:push` / `legacy:*` ids
  follow the flags in the rules, leaving the other channels, `message.channel` and every other
  rule key alone. The other way round, `PATCH /rules` (events) and `PUT /rules/conversations/{key}`
  call `legacy_sync.sync_columns()`, which copies the four legacy media of the rules back into the
  two preference columns so `GET /api/push/preferences` shows what is delivered (a companion
  list goes to both `new_contact` and `advert_companion`, which 4.18.x ORs). The one refusal left
  is 409 `legacy_managed`: `PATCH` of `config` / `allow_private_network` / `verify_tls` and
  `DELETE` of a `legacy:*` channel, because the `legacy:*` transports still read
  `notification_destinations` at send time. Media with no channel yet (no SMTP, no URL) keep their stored flags and overrides: `sync_columns` only overwrites the media whose channel exists, so the flags come back as rules when the destination is configured. The telemetry rules' old `channels` field is neither
  read nor written (migration 114 copied it into `push_defaults.telemetry_alert` where that key was missing) (`TelemetryAlertRules.channels` is `exclude=True`, accepted and ignored). The shims
  are removed in 5.2.0 (T34).

## Per-account rules (`user_rules.py`, MSHL-321, 361, 362, 363)

`app_settings.notification_rules` is the **admin defaults**. Web Push is the one channel whose
recipients are accounts (each device has an owner, MSHL-290), so an account may change, for its
own devices only, whether an event reaches them; every other channel is a destination the admin
owns and keeps following the defaults (webhooks, e-mail, Apprise, ntfy, `legacy:*`).

- **Storage.** `user_notification_rules` (migration 123): one row per account that changed
  something, `{"version": 1, "events": {id: bool}, "contact_types": {kind: bool},
  "conversations": {state_key: bool}, "pause": {"until": int | null}}` (`sanitize_override`:
  known events, the three contact kinds, booleans, state keys `channel-<hex>`/`contact-<hex>`
  (at most 256), a pause end in epoch seconds or null). The two later keys (MSHL-362, 363)
  live in the same JSON document, so they needed no migration. No row means "follow
  the defaults", so migration 123 writes none and an upgrade changes nothing. The events that
  prove a destination change (`notify.channel_changed`, `notify.destination_changed`) and
  `notify.test` cannot be overridden. `UserNotificationRulesRepository.all_overrides()` is read
  from memory on every notification.
- **Effective rules.** `push_wanted(rules, override, event, ..., now=)`, in this order: (1) the
  account's **pause** (`is_paused`: set and not over) cuts everything but urgent events,
  `always_deliver` ones and the locked events (MSHL-363, the instance pause's exemptions);
  (2) the account's **conversation exception** for a `message.*` event's state key
  (`conversation_state_key`, the key `rules.py` builds): `false` cuts every message event of
  that conversation, mentions included; `true` lists `builtin:push` as a stored admin exception
  would (so a muted conversation or a blocked sender still cuts it, unless `always_deliver`)
  and wins over the account's own event choice (MSHL-362: more specific); (3) the account's
  event choice: a `false` cuts the event, the admin's conversation exceptions included; a
  `true` lists `builtin:push` on that event (or contact kind) and then goes through the same
  evaluation as the defaults, so the admin's conversation exceptions, mutes, block lists and
  the global pause still apply. `always_deliver`, keywords and watched nodes are instance
  settings: an account has no copy of them.
- **Dispatch.** Without `targets`, `Dispatcher._emit` strips `builtin:push` from the channels the
  defaults chose and asks `build_audience` instead: a `PushAudience` that gives each account's
  devices a payload or none. `builtin:push` is queued when anyone wants the event, even if the
  defaults leave it off, and the `Delivery` carries the audience only when an account has an
  override or a mention version exists (otherwise every device gets the payload, as before). The
  Web Push transport passes it to `PushManager.dispatch_event(audience=)`, which skips the
  devices of an account that wants nothing. A producer that routes Web Push alone with `targets`
  asks for the same with `per_account=True`. A delivery with no audience (a destination-change
  proof) reaches every deliverable device.
- **Grouped summaries (MSHL-361).** On `builtin:push` a held job (`_Held.add(job,
  per_account=True)`, and `Worker.first_seen_audience` for `contact.first_seen`) remembers its
  `PushAudience`: the counts are kept per account (key None: the accounts without an
  override). `_push_summary` builds the summary's own audience at flush time: an account gets
  `notifications_grouped` / `first_seen_grouped` only when at least one held job was wanted by
  its rules, counted from those jobs alone (so a muted conversation never shows up in its
  counts); an account paused at that moment gets none; with nobody left nothing is queued and
  no cap or bucket token is spent. The other channels count every held job, as before.
- **Mentions.** The producer takes `builtin:push` out of the instance-wide mention/plain split
  and hands the dispatcher both versions (`push_mention`). An account whose rules give it
  `message.mention` receives the mention, else the plain `message.channel`, never both.
- **API.** `GET/PATCH/DELETE /api/push/my-rules` (`send`): per overridable event and per contact
  kind `{default, choice}` (`choice` null follows the default), plus `conversations` (state key
  to bool, patched with bool or null) and `pause` (`{active, until}`). `PUT /api/push/my-pause`
  (`{until}`: epoch seconds within a year, or null until the account resumes; 400
  `notification_pause_invalid`) and `DELETE /api/push/my-pause`. `DELETE /my-rules` forgets the
  event, contact type and conversation choices and keeps a pause in force. Unknown ids or keys:
  400 `notification_rule_unknown`; more than 256 conversations: 400
  `notification_conversations_limit`. The admin edits the defaults with
  `/api/notifications/rules` as before. The Settings page shows the account's own pause, rules
  and conversation exceptions next to its devices, and the admin's matrix as "defaults for all
  accounts".
- **Backup.** The `notification_user_rules` section rides with the accounts
  (`docs/dev/backup.md`).
- Tests: `tests/test_user_notification_rules.py`, `tests/test_user_push_summaries.py`,
  `tests/test_migrations/test_migration_123.py`.

## Presets (`presets.py`)

A preset is an Apprise service described as data, no code per service: a `Preset` (`id`,
`template`, `fields`, `format`, `cloud`, `default_limits`, `min_interval_s`, `wiki_url`) in the
`PRESETS` tuple. Telegram, Gotify and Home Assistant are the first three.

- **Stored as `apprise` + `preset`.** A channel row has `type = "apprise"` and `preset = <id>`; its
  public fields are the `config`, its secret fields the `secrets` (`PresetField.secret`), never the
  URL. `get_type(type, preset)` returns the preset's `ChannelType` (generated by
  `preset_channel_type`: a Pydantic model from `create_model`, `required`, `destination_keys` and
  `secret_follows` from the fields flags), refusing a preset on another base type
  (`channel_preset_unknown`). `REGISTRY` stays the natives only.
- **Template.** `{field}` is percent-encoded (`:` kept, a Telegram token needs it), `{url.scheme}` /
  `{url.netloc}` / `{url.path}` / `{url.seg1}`..`seg9` (the n-th path segment) take a `kind="url"` field apart (`scheme_map` turns `http`/`https`
  into `gotify`/`gotifys`), `[...]` is left out when a placeholder inside is empty. A field with a
  `default` takes it when empty. `compile_url()` re-validates every value and raises `PresetError`
  (field names only); the URL exists only inside `_check_destination` and `AppriseTransport.send`.
- **Checks.** `_check_destination` compiles the URL and runs `validate_apprise_urls` (host check of
  `user_host` plugins, the scheme allowlist, the Telegram chat id rule) and the `verify_tls` rule,
  so a LAN Gotify or Home Assistant answers `egress_private_address` until `allow_private_network`
  is ticked. `required` secrets are `config_required`; a missing or malformed public field is
  `invalid_config` naming the field (no value). Migration 124 joins the pre-MSHL-365 Slack `token_a`/`token_b`/`token_c` into `webhook` (an incomplete channel is disabled with `secret_pending`); `secret_fields.scrub_error` also hides the last path segment of a URL secret. A Matrix `credential` (token or password, one secret) is a `required` secret: save and `POST /channels/test` refuse its absence with `config_required` before any request, and no preset does a network credential check at save time (egress only on the explicit test, through the transport). Patterns are ASCII (`[0-9]`, `[A-Za-z0-9_]`, never `\d`/`\w`; `_full` also uses `re.ASCII`); a `url` field refuses `..` and `%` in its path, and `no_path=True` (Home Assistant) refuses any path rather than dropping it; `host` pins a fixed host and `pattern` is also checked on a url value. A `url` field may be `secret` (Slack: one pasted webhook, split by `seg2..seg4`; the whole URL is the secret, schema without `format: uri`). Home Assistant uses `?accesstoken=` so the
  service stays the path (Apprise would otherwise take the last path element for the token).
- **`GET /types`.** A preset is an entry with `preset: true`, `type` its id, `base_type: "apprise"`,
  `help_key`, `wiki_url` and the JSON Schema of its model (`label_key` / `help_key` per field under
  `notifications.presets.<id>.fields|help.<name>`, secrets `writeOnly`, choices `enum`, URLs
  `format: uri`). The frontend creates it as `{type: base_type, preset: id}`.
- **Adding a service** = one `Preset` plus its texts in the frontend slices
  (`settings.notifications.types.<id>.name|description`, `notifications.presets.<id>.about|fields|help`
  in FR and EN) and, if it has an icon, `TYPE_ICONS`. `tests/test_apprise_presets.py` runs its generic
  checks on every preset (URL accepted by the Apprise parser with the `example` values, schema,
  invalid and required fields, secrets apart, scrubbing, transport, texts in both languages).
- **Telegram 429.** Telegram's `retry_after` is in the JSON body, not a header: `apprise_medium`
  reads it (`_body_retry_after`) when there is no `Retry-After`, and the retry waits that long.

## API (`app/routers/notifications.py`, `api_support.py`)

- All routes are `admin`. Responses show a channel with `config[secret] = "********"` (or `""`
  when unset) and `secret_fields`; `Channel` itself never holds a value. Errors are
  `{code, params}` (`ChannelError.detail()`, `fail()`): `invalid_config`, `config_required`,
  `secret_required`, `text_policy_locked` (a preset with `text_policy_locked`, the SMS ones, only accepts its own `never`: `prepare()` and `update()` refuse another `include_message_text`; `/types` carries `include_message_text_locked`); the backup restore forces the fixed value (`notification_text_policy_forced:<id>` warning) and `AppriseTransport.send` clamps it again at send time), `type_immutable`, `builtin_channel` (403), `channel_missing` (404, 400 for
  an id inside a body), `channel_exists` (409), `rate_limited`, the guard's `egress_*`, ...
  Never a host, an IP or an exception text.
- **Tests.** `POST /channels/test` runs `NotificationChannelRepository.prepare()` (the checks of
  a creation) and sends through `Dispatcher.send_test()`: the channel's own transport, so the
  egress guard, with no queue, retry, journal row or counter; for a saved channel the answer
  also updates its status. `api_support.test_failure()` maps a `SendResult.error_code` to
  `(status, code)` (401/403 `auth_failed` 502, timeout 504, 429 `rate_limited`, anything it does
  not know `service_error` 502). Limits: `AsyncLimiter(1, 5)` per channel id, 1 per 2 s for
  the unsaved test (taken before `prepare()`, so before any DNS lookup) and 10 a minute for all
  tests together (`has_capacity()` then `acquire()`), 429 with `Retry-After`; tests reset them
  (`reset_test_limiters`, autouse in `tests/conftest.py`).
- **`notify.channel_changed`** (catalogue event, urgent so a pause never holds it): on create,
  change of destination (public settings, a URL secret, `allow_private_network`; not a
  credential or a rename), enable, disable and delete. `announce_channel_change()` writes the
  audit row (`notify.channel_changed`, 366 days), broadcasts `notification_channel_changed`
  (admin-only `WsEvent`, `realtime=False`) and emits the notification to `builtin:push` (it
  cannot be taken off the rule: 403 `builtin_channel`) and to the rule's channels, every other
  enabled one by default, never to the channel that changed. `notify.destination_changed` stays
  as it was for the legacy settings route (push only).
- **`notify.channel_failed`** (MSHL-130, not urgent, not configurable, locked for accounts): on the
  3rd consecutive failure of a channel (and every later one, but `failures.announce` claims
  `notification_state` key `channel_failed:<id>` with a 1-day TTL through `state_claim`, so at
  most once a day per channel, across restarts), and when `PushManager._record_outcomes` drops
  a subscription its push service expired (403/404/410; key `channel_failed:push:<sub id>`,
  `name` = the device label, error `push_unsubscribed`); MSHL-399 adds the 15-failure eviction
  (`batch_record_outcomes` returns the evicted ids, error `push_evicted`) and the egress-guard
  removals (`_SendResult.invalid`, error `push_refused`), same key. Emitted with `targets` =
  `builtin:push` and every enabled channel, minus the failing one. Payload `channel_id`, `name`,
  `error` (`safe_error_code`). Not digestible (`quiet.digestible` takes `message.*`/`contact.*`
  only), so a channel in digest mode gets it at once; quiet hours and pauses still hold it (not
  urgent).
- **Admin-only notices on Web Push** (MSHL-399): `user_rules.ADMIN_ONLY_EVENTS`
  (`notify.channel_changed`, `notify.channel_failed`, `notify.destination_changed`) name channels
  and devices, admin-level data (`/api/health` details and the `notification_event` frame of
  `notify.*` are admin-only too). `_emit` replaces the `builtin:push` audience with
  `admin_audience()`: the admins owning a deliverable device (`owner_role == "admin"`), `default`
  None so no other account gets it, and held counters are per admin account. No admin device, or
  owners unreadable: `builtin:push` is dropped (fail closed). `PushManager.dispatch_event` checks
  `is_admin_owner` again for these events (a demotion between emit and send, and the legacy
  `notify_destination_changed` that calls it directly). Other channels are admin-configured
  destinations and are not filtered.
- **Fanout duplicates and conversion** (`fanout_convert.py`, MSHL-134): `POST /channels`
  answers `fanout_duplicates: [{id, name}]`, the enabled, unconverted Apprise fanout rows whose
  URLs match the new channel's (presets compiled, generic Apprise `urls`, Discord `webhook_url`).
  The match compares HMAC-SHA256 fingerprints of the scheme-less, slash-trimmed URL under a
  per-process random key with `hmac.compare_digest`; no URL is returned or logged. A failure there
  logs the channel id and returns `[]`. The fanout route
  `POST /api/fanout/{id}/convert-to-notification-channel` (`{channel_id?}`) creates the channel
  (single Telegram or Gotify URL → preset, else generic Apprise with `urls` and `format`), or with
  `channel_id` merges into that one, maps `body_format_dm`/`body_format_channel` to
  `template_dm`/`template_channel` (invalid ones dropped with a note), turns the scope into
  `message.dm`/`message.channel` channels and conversation exceptions (`set_rules` then
  `legacy_sync.sync_columns()`), then disables the row with `config.converted_to` (write-ahead `config.converting_to` before the
  channel is created, so a retry never makes a second channel; `channel_id` (the duplicate prompt)
  on a still-marked row deletes that pending channel first unless it is the target). Answer:
  `{channel: {id, name, type, preset, enabled}, fanout: {id, name, type, enabled, converted_to},
  already_converted, notes}`, no `config`; a converted row turned back on is never reported as a
  duplicate (the owner confirmed keeping both); errors `fanout_missing` (404),
  `fanout_not_apprise`, `secret_unreadable` (409), `channel_missing` (404).
- **Resume** (`POST /channels/{id}/resume`, admin like the whole router): clears the auto-pause
  (`paused_until`, `autopause:<id>` state), `failure_count` and `last_error`, then broadcasts
  `health`; 404 `channel_missing`. No audit row.
- **Rules** (`GET/PATCH /rules`): `events[id]` (`channels`, `always_deliver`, `contact_types`),
  `paused_until` (epoch seconds, `null` resumes, a past value is a resume), `quiet_hours` (partial
  object, see "Quiet hours"; 400 `invalid_config` with `fields: ["quiet_hours.<field>"]`), `mention_whole_word`
  and `emit_ws_events` (bool, default `true`, see "Dispatcher"; a restore keeps it only as a bool)
  in the rules; `radio_offline_grace_s` and `push_allow_private_endpoints` are the
  `app_settings` columns (one source), `host_timezone` read-only. The summary of the events
  suppressed during a pause (counts per channel on resume) is not implemented: the journal
  keeps one `paused` row per suppressed job.

## Dispatcher

- **Entry.** `emit(event, *, payload=None, targets=None)` never raises and never waits on a
  send. Without `targets` the rules choose the channels (`rules.event_channel_ids`), minus the
  channels in `exclude` (a producer ruling a channel out); `targets` is for a caller that
  already decided (`notify.channel_changed`, `notify.channel_failed`, tests). Unknown and disabled
  channels are skipped without a journal row. Producers never call `emit` with `targets`: see "Producers".
- **WebSocket frame** (`ws_frame.py`, MSHL-138). Once the rules, mutes, block lists and
  exclusions leave at least one enabled channel, and before pauses and quiet hours, `_emit`
  broadcasts one `notification_event`: `version`, `event`, `event_id`, `occurred_at`,
  `dedup_key`, `urgent`, `conversation_key`, `conversation_type` (`CHAN`/`PRIV` for a
  message, `CHAN` for `channel.found`), `conversation_open` (CHAN messages: Public or `#`)
  and `data` (the webhook v2 typed data, `webhook_v2.event_data(..., "always")`), never
  rendered text. `rules.emit_ws_events: false` turns it off. No frame for an unmatched or
  suppressed event, a summary, `notify.heartbeat` or a test send (they skip the rules).
  `app/websocket.py` filters it per socket: a kiosk never gets DM data, `notify.*` is admin.
  A mention that reaches only Web Push arrives as `message.channel` (the producers' split);
  a re-emitted event may give two frames, consumers dedup on `dedup_key`.
- **Rules** (`rules.py`). A stored conversation exception wins either way; else a DM follows
  `message.dm`, a Public or `#` channel `message.channel`, a channel with its own key no
  one; `contact.first_seen` follows `contact_types` (1 companion, 2 repeater, 4 sensor; a
  room or an unknown type never). The global `paused_until` holds everything but urgent and
  `always_deliver` events; a channel's own `paused_until` (the auto-pause, MSHL-130) holds
  everything, urgent included (journal `paused`).
  `conversation_channel_enabled(state_key, message_type, rules, channel_id, is_hashtag, is_public)`
  is the per-channel predicate (plus `muted`, `blocked` and `now` for the pause: they cut
  everything but an `always_deliver` event); its cases are the shared fixture
  `tests/fixtures/notification_policy_cases.json` (the frontend suite reads it too, T19).
  `app/push/policy.py` is a deprecated adapter over it, removed with the shims.
  Block lists and mutes are applied here, for every channel: `event_channel_ids(..., muted=, blocked=)`
  (a `message.*` event only) cuts the candidates, stored exceptions included, unless the event is
  `always_deliver`. The producer passes the channel's effective mute (`channel.muted`, `muted_until`
  applied) and `rules.message_blocked(payload)`, which is `services/message_filters.is_blocked` (the one
  block-list rule, the messages listing's case rules) over the lists `message_filters.block_lists()`
  keeps in memory (dropped by `AppSettingsRepository.update` and the block toggles, so no database
  read per message; lists that cannot be read fail closed). The Apprise fanout, outside the engine,
  keeps `message_filters.is_filtered` (same rule plus the mute).

## Quiet hours (`quiet_hours.py`, MSHL-124)

`rules.quiet_hours` = `{enabled, start, end, weekdays, digest_at_end}` (absent = disabled; a malformed
stored value reads as disabled). The window is `services/update_window.in_window` over the dispatcher's
wall clock in host-local time (an overnight window belongs to its opening weekday). The settings UI is
`QuietHoursControl` (rules panel) plus a *Exempt from quiet hours* box on each channel card.

- **Precedence in `Dispatcher._emit`**: (1) the rules, mutes and block lists and, on Web Push, each
  account's own choices, conversation exceptions and pause decide who wants the event: an event nobody
  wants is never counted; (2) the global and the channel `paused_until` (`paused` row, dropped; the
  global one is also counted in `pause:*` counters for the resume summary, below); (3) quiet hours. `urgent` events and `always_deliver` ones skip (2) and (3), except a
  channel's own `paused_until`, which holds them too; a channel with
  `quiet_hours_exempt` (registry default true for `webhook`, false otherwise; the imported `legacy:*`
  rows keep 0 on purpose, MSHL-368: 4.18.x had no quiet hours and they are off until enabled, and the
  card's box flips it, `legacy:*` included) skips (3); an account's *Always notify me* does not skip (3). The synthetic
  catalogue events tied to `targets` follow the same path.
- **Dedup before counting** (MSHL-368): `_first_hold` applies the same per-channel dedup key and TTL as
  `_admit` before a held event is counted, so a re-emitted event is counted once. Quiet hours share
  `_admit`'s keys; the pause uses `dedup:pause:<channel>:<key>` (a paused event is not delivered, so a
  copy after the resume still is).
- **Suppression**: a `suppressed_quiet` journal row and, with `digest_at_end`, one counter in
  `notification_state`, `quiet:<channel>:<event>:<conversation state key>` (value = count, no expiry; one
  `UPDATE`, an `INSERT` only for a new key: `NotificationJournalRepository.counter_incr`). A Web Push job
  that carries a `PushAudience` is counted per wanted account (`builtin:push@<user id>`, `@d` for the
  accounts without an override). No text, sender or name is stored; deleting a channel deletes its counters;
  the backup leaves them out.
- **Digest**: a 60 s task (`_quiet_loop`, started at app startup by `start_notifications()` in the
  lifespan, and idempotently by `emit`; `quiet_tick()` is what tests call) runs once the window is closed
  and no global pause holds: `counters_take` reads and deletes the counters, then each enabled, unpaused channel gets one
  `quiet_digest` delivery through `_admit` (limits apply). `build_digest` sums counts: Public and `#`
  channels are named, DMs and private channels only when the channel's `include_message_text` is `always`,
  else they are `dms` / `private` counts. `render.quiet_digest_text` writes it in FR and EN
  (`scope: "pause"` has its own wording). On Web Push the
  digest audience is the accounts with counters (minus paused ones).
- **Pause summary** (MSHL-368, T10): what the *global* pause held (not a channel's own pause) is counted
  under `pause:<channel>...` (same key shape, same `digest_at_end` gate) and `quiet_tick` sends one
  `quiet_digest` with `scope: "pause"` per channel once the pause is over, whether or not a quiet window
  is open; the pause summary goes first.
- **Digest failures** (MSHL-368): a digest `_admit` drops (limits) or that targets a paused channel is
  put back (`_put_back`, `counter_add`) and retried each tick. Only a real refusal (`_admit` false) spends the
  budget: at most `QUIET_DIGEST_RETRIES` (10) ticks in a row, then given up with a warning. A channel in its
  own pause keeps its counters with no retry spent and no time limit (MSHL-374; the counters are bounded by
  their keys). `_digest_retries` is reset by `_forget_retries` at the end of every tick for each channel with
  no counters waiting (sent, given up, dropped, disabled or deleted), so a later digest gets the full budget
  and the dict only holds channels that are being retried. A disabled or deleted channel gets none. A queued digest that
  still answers `retry` gets the one long retry (`quiet_digest` is in `LONG_RETRY_EVENTS`).
- Tests: `tests/test_quiet_hours.py`.

### Digest mode (`digest_interval_s`, MSHL-136)

A channel column (migration 132; 0 = off, else 300..86400, checked by `check_digest_interval` on the
create and update models and by the backup restore, which drops a bad value to 0 with
`notification_digest_dropped:<id>`). It reuses the quiet-hours counters (`counter_key`, `_count_held`,
`_digest`, `build_digest`, `_put_back`) under their own prefix, `digest:`, and renders with scope `digest`
("Since the last summary").

- **Which events.** `_emit` holds an event for a digest-mode channel when `quiet.digestible(name)` and the
  event is neither `urgent` nor `always_deliver` (the same `subject_to_holds` the pauses use, so "urgent" is
  the event's `urgent` flag, set on `radio.offline`, `telemetry.alert` and `notify.channel_changed`, or
  the rule). Digestible: `message.dm`, `message.channel`, `contact.first_seen`. Bypass, delivered at once:
  `message.mention`, `message.keyword`, `contact.node_seen`, `contact.node_back` (a user opted into each
  one, so a delay defeats it), and everything outside `message.*` / `contact.*`. The WebSocket frame
  (`ws_frame.publish`) fires before this, per event. A keyword alert reaches a digest-mode channel at
  once: `producers._emit_message` does not count a digest channel (unless it gets the mention) as
  already receiving the message, so `_emit_keywords` still targets it; the message itself is counted once in
  the summary and the alert never is.
- **Order.** Rules, then the pauses (`paused`), then quiet hours (a quiet-held event is counted under
  `quiet:`, never `digest:`), then digest mode, then `_admit`. The re-emit dedup is the quiet one
  (`_first_hold`).
- **Window.** The first held event writes `digest_due:<channel>` = now + interval, before its counter
  (`_hold_digest`, `_digest_open` caches it). `digest_tick()` runs from the minute loop: for each channel
  with counters or a due row, it flushes when due (`_digest_flush`: delete the due row, take the counters,
  `_digest`, `_admit`), is a no-op during the global pause, skips a channel in its own pause or (unless
  `quiet_hours_exempt`) in quiet hours, opens a window for counters that lost their due row, flushes at
  once when the interval was switched to 0, and drops the counters of a disabled or deleted channel. A
  window whose end is further than one (new) interval away, because the interval was shortened, is clamped to now + the new interval. A refused summary is put back with the due row set to now (same `QUIET_DIGEST_RETRIES` budget).
- **Restart safety.** Counters and due rows are in `notification_state` (no expiry): a window survives a
  restart and an overdue one is flushed at the first tick. No text, sender or name is stored.
- Tests: `tests/test_digest_mode.py` (fake `wall_clock`, `digest_tick()` by hand),
  `tests/test_migrations/test_migration_132.py`.

## Producers

`producers.py` is what the rest of the app calls: `emit_message(data)` (the
`broadcast_event("message")` hook in `app/websocket.py`: outgoing messages, pending channels and
muted channels, unless `always_deliver`, never notify; a blocked sender's or a muted channel's message
reaches no channel: the rules decide, `emit(..., muted=, blocked=)`), `emit_payload(payload)` (first seen in
`app/push/first_seen.py`, `hashtag_catalogue._notify_channel_found`, `telemetry_alerts._dispatch`,
`radio_watch._dispatch`) and `emit_oss_update(current, latest)` (`services/oss_updates.py`, which
keeps its `last_notified_update_version` guard: it records only when `emit` queued the event for at least one enabled, unpaused channel). `emit` returns that count and takes `rules` / `is_open` so the producer's lookups are not repeated.
The payload dict is what the transports render, so the golden fixtures hold
(`tests/test_notification_producers.py` sends every golden case through the rules). `PushManager`
(`app/push/manager.py`) is only the Web Push transport (`dispatch_event`).

### Mentions (`mention.py`, MSHL-121)

`is_mention(text, radio_name, whole_word=)`: `@[<name>]`, or with `rules.mention_whole_word`
the name as a whole word (`(?<!\w)name(?!\w)`, not `\b`: names may start or end with a
symbol); case-insensitive, name `re.escape`d, one pattern compiled per (name, mode)
(`lru_cache`), so a message costs one regex search and no database read (the radio name is
`radio_runtime.meshcore.self_info`, in memory). The producer decides it for an incoming `CHAN`
message and ignores a `mention` key in the input. The rules then give two disjoint sets: the
channels of `events["message.mention"]` get `message.mention` (title "Mention in #x by Y", the
same push `tag` as the plain message so it replaces it), the other `message.channel` channels
keep the plain event (`emit(..., exclude=mentioned)`): one notification per channel. A mention
reaches the channels of its own rule plus those with a stored `true` exception for the conversation (listed or not); on a private-key
channel it also needs a non-cloud channel (`rules.channel_is_cloud`: registry `cloud` /
`cloud_check`; an Apprise or webhook channel always counts as cloud (their URL is a secret); unknown or unreadable counts as cloud)
or a stored `true` exception for the conversation (Q6: a mention is forged by anyone in range,
never urgent). The channels table is read (`cloud_channel_ids`) only for a mention of a
private-key channel. Mute, block lists and the pauses apply as for `message.channel`. Migration
115 gives an existing install the rule `message.mention` = channels of `message.dm`.
`tests/fixtures/notification_policy_cases.json` carries the `mention`/`cloud` cases; the
frontend mirror (`frontend/src/utils/notificationPolicy.ts`, `test/notificationPolicy.test.ts`) passes every case.
### Keywords (`keywords.py`, MSHL-128)

`rules["keywords"]`: at most 20 rules `{id, pattern, match: contains|word, scope: all|dms|[state keys],
channels, cooldown_s}`, validated by `keywords.clean_rules` (the API, `PATCH /rules` `keywords`, replaces the whole list;
the backup `sanitize_rules` runs `clean_rule` per entry). No regex ever touches the text or the pattern: both are
NFC + `casefold`ed and compared with `str.find` (`word` checks the neighbouring characters, as `mention.py`), so a match is
linear and ReDoS-free. The matched text is the message without the `"Name: "` prefix of a channel message.
`producers._emit_keywords` runs after the mention split, for a conversation that is neither muted nor blocked: each matching
rule emits one `message.keyword` (`KeywordEvent`, never urgent, dedup key per (rule, message), payload carries `keyword` and
`keyword_id`, both dropped from the input like `mention`) with `targets` = the rule's channels minus `receiving` (the channels
of `message.dm`/`message.channel`/`message.mention` for this message, and `builtin:push` when any account wants it), so no
channel gets two notifications. `builtin:push` is refused in a rule (Web Push is per account; deferred). Cooldowns go through
`Dispatcher.claim_cooldown`: keys `dedup:cooldown:kw:<rule>` (`cooldown_s`) and `...:<rule>:<sha of sender key or name>`
(`max(900 s, cooldown_s)`) in the dedup store (`notification_state`), claimed together and only when a channel is reached. The
dispatcher applies the pauses (global and per channel), quiet hours, limits and the text policy as for any message.
`message.keyword` is in `NOT_CONFIGURABLE` (no matrix row). Tests: `tests/test_notification_keywords.py`.

### Watched nodes (`watched_nodes.py`, MSHL-129)

`rules["watched_nodes"]`: at most 50 entries `{public_key (64 lowercase hex), channels, cooldown_s (default 3600, 0 to 30 d),
silence_s (default 86400, 60 s to 365 d)}`, validated by `watched_nodes.clean_entries` (the API: `PATCH /rules` `watched_nodes` replaces
the whole list, unknown channels refused) and, per entry, by `clean_entry` in the backup `sanitize_rules` (cap `MAX_WATCHED`, unique keys). Web Push
(`builtin:push`) is refused, as for keywords. The hook is a service, `observe_sighting()` (never raises), called from the contact paths of
`packet_processor.py`: a new advert packet (after the upsert, `previous_seen` = the contact's `last_seen` before it) and an incoming decoded DM.
It reads the rules, ignores an unwatched key (nothing stored), takes the previous sighting from `notification_state` `watch:heard:<key>`
(else `previous_seen`), records the new one, claims the node's cooldown through `Dispatcher.claim_cooldown` (`dedup:cooldown:watch:<key>`, only
when the entry has a channel, one cooldown for both events), then emits `contact.node_back` (`NodeBackEvent`, previous sighting at least
`silence_s` old, `silent_s` in the data) or `contact.node_seen` (`NodeSeenEvent`) with `targets` = the entry's channels. Dedup key per sighting
(`heard_at`), never urgent. State is bounded because only watched keys are stored (at most 50 + 50 rows, `HEARD_TTL_S` expiry); removing an
entry through the API calls `forget()` (rows and in-memory cooldown). Both events are in `NOT_CONFIGURABLE`. Tests: `tests/test_watched_nodes.py`
(fake clock via `Dispatcher._dedup._wall`).

- **Queues.** One `asyncio.Queue(maxsize=200)` and one worker task per channel (`spawn()`),
  started by a job and ended when the queue is empty. One send at a time per channel;
  `min_interval_s` between sends (`channels.min_interval_s`: preset `telegram` 3 s,
  `discord` 2 s, `ntfy` 1 s; type `email` 10 s, `apprise` 2 s, `webhook` and `web_push` 0).
  A full queue drops the job (`rate_limited`). `contact.first_seen` is capped at 10 an hour
  per channel (`aiolimiter`), the rest summed into one `first_seen_grouped`. The spacing, the
  retries, the buckets and the aggregation window read the injected `clock`/`sleep`/
  `wall_clock` (fake-clock tests); the fake `sleep` is instant, so a test that emits a burst
  holds it (`tests/test_rate_limit.py`, `_burst`) or the window closes between two emits.
- **Limits** (MSHL-125, `limits.py`). `Dispatcher._admit()` decides each (event, channel) at
  `emit` time, in this order: (1) persisted dedup (`DedupStore`, below), (2) the global cap
  (`ClockedLimiter(limits.global_per_hour, 3600)`, the rules' `limits.global_per_hour`, default
  600, a bad value reads as 600; over it `rate_limited`, urgent included, and `notifications_capped`
  in `/api/health`, `Dispatcher.capped`, `broadcast_health` on change), (3) the daily quota
  (`ChannelBuckets`: a count of the UTC day, started from `notification_counters` plus the
  unflushed counts, so a restart keeps it; exhausted: `quota_exceeded` row, no summary, until
  the next UTC midnight), (4) the minute and hour buckets (`ClockedLimiter`, an `AsyncLimiter`
  on the injected clock: `has_capacity()` then `acquire()` with room, never a waiter; the cap is
  charged with `try_acquire()`, test and take in one step, after the only real await of `_admit`,
  the day-counter read). Limits
  are `effective_limits(channel)`: the channel's `rate_per_*` column, else the registry's
  `limits_for(config)` (so ntfy.sh gets its 10/30/200 and a self-hosted ntfy 30/min); editing
  them rebuilds the changed bucket on the next job. A full bucket holds the job as a count
  (`_Held`, `aggregated` row) and `_flush_held` queues one `notifications_grouped` summary after
  `AGGREGATION_WINDOW_S` (60 s) once the buckets and the cap have room (else one more window);
  the payload has `count` and optional `channels` (open channels only, top 3), `dms`,
  `private`, `others`: counts, never a text, a sender or a private channel's name. An urgent
  job takes a token from every bucket that has room but passes a full one once per
  (channel, dedup key) (`dedup:urgent:` key, 24 h). Summaries take a cap token too (the
  wait loop ends only with room in the cap; a summary that still finds none is `rate_limited`),
  so the 600 an hour holds. `used_today` counts admissions, a restart reseeds it from the
  actual sends: the quota is never exceeded. In-memory
  state is per process: one Meshloom process owns the radio, the database and these buckets
  (see "Caches").
- **Dedup keys** (`DedupStore`, `notification_state` rows `dedup:<channel>:<dedup_key>`, value
  `1`, `expires_at`): `contact.first_seen` 24 h, `radio.offline` 24 h (its key carries the
  outage start), `message.*` 24 h (its key carries the message id); `telemetry.alert` has none
  (hysteresis only). Memory is authoritative (loaded once per process, 20 000 entries at most),
  new keys are written in a batch by the counter flush (60 s and at shutdown; the pending batch
  is capped at 20 000 too, the keys closest to expiry dropped first), expired rows pruned
  hourly and the `dedup:` rows trimmed to the 20 000 that expire last (`state_trim_prefix`). A duplicate is dropped without a journal row.
- **Retries** (`tenacity`, `retry_if_result`): `RETRY_WAITS` 0/5/30 s while the transport
  answers `retry` (5xx, timeout, network error, 429 whose `Retry-After` wins, capped at
  120 s); a `failed` answer (other 4xx, refused destination, not configured) is never
  retried. A `message.*` job still failing is dropped as stale; `radio.offline` and
  `telemetry.alert` still answering `retry` get one more attempt 5 min later (a `failed`
  answer is final for them too), requeued from a spawned timer so the
  worker stays free. Web Push, e-mail and the legacy Apprise medium answer `failed`, never
  `retry` (a retry would reach again the subscriptions or URLs that took it; the Apprise
  medium retries each URL itself).
- **Threads.** Every blocking transport (Apprise, e-mail, pywebpush) runs in `executor.py`
  (4 threads), never the default pool; timeouts are the transports' own (httpx, Apprise
  `cto`/`rto`, the push send's connect/read), never `asyncio.timeout` around a thread.
- **Outcomes.** Successes are counted in memory and flushed every 60 s to
  `notification_counters` with the channel's `last_ok_at` (one write a minute, not one per
  send); a success after failures resets `failure_count` at once. A failure updates
  `failure_count` and `last_error` (a code, `secret_fields.scrub_error` with the channel's
  secrets). `notification_deliveries` rows only for `failed`, `rate_limited`, `quota_exceeded`, `aggregated`,
  `paused`, `suppressed_quiet`, `aborted`, batched in one transaction;
  a row holds the event id, the hashed `dedup_key`, a code and the attempts, never the text.
- **Failing channels** (MSHL-130). `_record_failure` counts consecutive failures; at 3 it
  broadcasts `health` and from 3 on calls `failures.announce` (rate-limited, above). At 20
  (`_pause_if_running`) it sets `paused_until` = now + `BACKOFF_S[step]` and stores the step in
  `notification_state` `autopause:<id>` (no expiry; both survive a restart; `delete()` drops the key). `probe_tick()`,
  run by the quiet loop every 60 s, sends a `send_test(record=True)` to each enabled channel
  whose pause has expired and that holds an `autopause:` key (a `paused_until` without that
  key is never probed); a failure re-pauses at the next step (5 min, 15 min, 1 h, then 6 h), a success
  (probe, test button or a real send) clears the pause and the key. `failing()` /
  `notifications_failing()` is the health flag `notifications_failing`: a channel with
  `secret_unreadable`, an enabled one with `failure_count >= 3`, or an auto-paused one.
- **Status.** `notification_statuses()` is the health payload's `notification_statuses`:
  `{channel id: {status, last_error, last_ok_at}}`, `status` one of `connected`, `error`,
  `paused`, `disconnected` (disabled); `secret_unreadable` is added with the encryption at
  rest of the secrets, which is what can make a secret unreadable.
  A change of a channel's status or error broadcasts `health`; below admin `last_error` is
  emptied (`without_admin_details`).
- **Shutdown.** `drain_background_tasks` cancels the workers, the long-retry timers and the
  first-seen flushers; each records what it held as `aborted`, and the counter loop flushes.
- **Tests.** `tests/test_notification_dispatcher.py` (marked `async_notifications`); every
  other test runs with `tests/conftest.py`'s `_settle_notifications`, which waits for the
  queues after each `emit` so a producer test can assert on the send.

## Native transports

- **Rendering.** The dispatcher passes `rendered=None`: each transport renders from
  `event.payload` in `channel_language()` (the channel's own, `auto` = the instance's) and
  under the channel's `include_message_text`.
- **E-mail** (`email.py`). One ephemeral `mailto(s)://` Apprise URL per send (never stored,
  password in `secret_scope`). `to` and `cc` are parsed by `parse_recipients` (newline, comma,
  semicolon). Apprise's mailto plugin sends one message per recipient inside the URL and returns
  one bool, so a refusal fails the delivery while others may be reached: `failed`, never retried.
  A message whose text the policy withholds gets the subject "New message" and no `text` line.
  SMTP runs in smtplib inside Apprise: the host is validated by the guard right before the send
  but the connection is not pinned (documented residual). The default text policy of a new e-mail
  channel is `public_only` when `email_is_cloud(config)` (host not clearly local).
- **Webhook** (`webhook.py`). `config.body_type` picks the body (MSHL-137). **v1**: `body_bytes()`
  and `sign()` are the 4.18.x body and signature, byte for byte (golden `webhook_body`, and
  `tests/fixtures/webhook_v1_golden.json` for the whole request); `request_headers()` adds
  `X-Webhook-Id` (the event's `dedup_key`, a body hash for a grouped summary) and
  `X-Webhook-Timestamp` (per attempt), both unsigned, so no replay protection.
  `verify_signature()` is constant-time. `app/notify.send_webhook_alert` and `legacy:webhook`
  use the same helpers and stay v1 (the API refuses a config PATCH on a legacy channel).
  **v2**: `webhook_v2.py` builds the body (one Pydantic model per event, discriminated on
  `event`; the dispatcher's summaries are `summary.grouped`, `summary.first_seen`,
  `summary.digest`, with an `event_id` hashed from their content), `body_bytes()` there is
  `model_dump_json()`; `sign_v2()` is `sha256=` + HMAC(secret, `timestamp + "." + body`),
  `verify_signature_v2()` is constant-time, `request_headers_v2()` adds `X-Webhook-Version: 2`.
  An event with no v2 model fails with `v2_unsupported_event`. v2 `data` never carries more than
  v1: the text follows `include_message_text` (`null` when held back) and no `conversation_key`.
  The JSON Schema `docs/user/en/deep/notifications-webhook.schema.json` is
  `webhook_v2.schema_text()`, rewritten by `scripts/dev/gen_webhook_schema.py` (`--check`,
  `--examples en|fr` prints the doc pages' examples); `tests/test_webhook_v2.py` fails on drift
  and validates every example of `docs/user/{en,fr}/deep/notifications-webhook.md`.
  **Default**: `WebhookConfig.body_type` defaults to `v2`, so a new channel stores `v2`; a row
  stored without the key reads as `v1` through `ChannelType.stored_defaults` /
  `read_config()` (applied by `repository/notification_channels.py` and the backup's
  `_public_config` / `_same_config`), no migration.
- **Markdown escaping** (`markdown.py`, the only copy; `render.py` and `push/manager.py` re-export
  it). `neutralise_mentions()` breaks `@everyone`, `@here`, `@channel`, `@all`, `@room`, `<@`, `<!`, `<#`
  with a zero-width space, `escape_markdown()` backslash-escapes Markdown punctuation; both apply to
  every title and body (`event_notification_text`, mentions always, Markdown on `markdown=True`: a
  Markdown Apprise channel, Discord). HTML uses `html.escape`. The output is pinned by the golden
  fixture (do not add characters without regenerating it) and `tests/test_render_markdown.py`.
- **Discord** (`discord.py`). Type `discord`: `webhook_url` (secret, `parse_discord_webhook`: https,
  `discord.com`/`discordapp.com`, no port or user info) and `username`; `cloud`, limits 30/min 600/h.
  One POST `{content, username?, allowed_mentions: {"parse": []}, flags: 4}` through the pinned
  client with a `HostAllowlist`; `content` is `**title**\ntext` escaped and cut at 2000. 429 is
  `retry` with the longest of `Retry-After`, `X-RateLimit-Reset-After` and the body's `retry_after`;
  `X-RateLimit-Remaining: 0` makes the next send of that webhook answer `retry` without a request
  (in-process, per webhook id hash; the transport never sleeps). 401/403/404 `auth_failed`, 400
  `invalid_config`. `verify_tls=false` is refused (public host). httpx logs the request path at INFO:
  `discord.py` installs a filter that masks the token.
- **Apprise** (`apprise.py`). `apprise_medium.send_to_urls` sends each URL on its own (retries per
  URL); any URL still failing makes the delivery `failed` with `line N (redacted url): code`,
  never `retry`. Discord URLs get `avatar=False`; `verify_tls` maps to `verify_certificate`.
- **ntfy** (`ntfy.py`). ntfy's JSON publish mode: `POST {server}` with `{topic, title, message,
  priority, tags, click, markdown}` through `pinned_async_client` (no `Title:` header, which httpx
  would refuse for a French title; the topic, the only secret on ntfy.sh, stays out of the URL and
  the logs). `topic`, `token` and `password` are write-only and follow a change of `server`
  (`secret_required`). A token wins over `username` + `password` (Bearer / Basic). The priority is
  `priority_urgent` when the catalogue event is `urgent`, else `priority_default`; `click` is
  `click_url` + the push payload's `url_hash`. Answers: 401/403 `auth_failed`, 413 `service_error`,
  429 `rate_limited` (`retry`, with `Retry-After`) or `quota_exceeded` (`failed`, never
  retried) when the body's JSON `code` is 42908 (daily message quota; a plain rate limit is 42901, checked on v2.11.0), 5xx `service_error`,
  a certificate failure `tls_error`. `server` and `click_url` refuse userinfo and `click_url` a fragment (`click_url()` joins with `urlsplit`). `cloud` and the daily limit (200, 10/min, 30/h) apply when the host is
  `ntfy.sh` (`ntfy_is_hosted`); a self-hosted server gets 30/min and no daily cap. The limits are enforced by the dispatcher (`effective_limits`). `generate_ntfy_topic()` (24 URL-safe characters) is
  a helper for a future form button (no caller yet; the user docs give an `openssl` one-liner). CI: `tests/integration/test_ntfy_e2e.py` against a real
  `binwiederhier/ntfy` container (`scripts/quality/ntfy_integration.sh`, job `ntfy-integration`; skipped
  without `MESHLOOM_NTFY_URL`).
- **TLS.** `verify_tls=false` is honoured only when every resolved address of the destination
  is private (`tls_check_may_be_off`, webhook URL, ntfy server, SMTP host, each Apprise user-chosen host; a
  fixed-host service never): else `verify_tls_requires_private_destination`, at create/update and
  again at send. E-mail `mode=none` with a login is refused outside the private network
  (`smtp_plaintext_auth_refused`, same two places). Cc addresses join the recipients (Apprise
  copies a whole Cc on every per-recipient message).

## Contract, version 1

Consumed by the Feed, the webhook v2 and the plugin `plugin.notification` event
without redefining anything.

- **Id**: `event` is `domain.name`, lower case: `message.dm`, `message.channel`,
  `message.mention` (see "Mentions"), `message.keyword`, `contact.first_seen`, `contact.node_seen`,
  `contact.node_back` (see "Watched nodes"), `channel.found`, `telemetry.alert`, `radio.offline`, `radio.online`,
  `app.started`, `app.update`, `notify.destination_changed`, `notify.channel_changed`, `notify.test`,
  `notify.channel_failed` (`EVENT_IDS`). These are notification ids, not WebSocket types
  (`app/auth/ws_events.py` stays the only list of those).
- **Common fields**: `event`, `event_id` (random, per occurrence), `occurred_at`
  (epoch seconds), `dedup_key`, `urgent` (default false; true for `telemetry.alert`,
  `radio.offline` and `notify.channel_changed`; never for `message.*`, whose text is unauthenticated mesh
  input), `conversation_key` (messages and `channel.found`, else null), and a typed
  `data` dataclass per event.
- **`dedup_key`** is `sha256(...)[:16]` of the event id and what identifies the
  occurrence (`dedup_hash()`): never a public key, a name or text, so it can go in
  a log, a journal or the `X-Webhook-Id` header.
- **Compatibility**: additive only. Add an event, or an optional field with a
  default; never rename, remove or change the meaning of one. A break needs
  `CONTRACT_VERSION = 2` and a `CONTRACTS:`-style call-out in the PR.

## Invariants

- **One renderer.** A new string goes in `render.py` in French and English, never
  in a transport. The text policy (`include_message_text`: `always`,
  `public_only`, `never`) is applied there, once, from the conversation class
  (Public or `#` channel versus DM or private key); room-wide mentions are always
  neutralised and the Markdown output escapes user text.
- **Golden tests.** `tests/fixtures/notifications_golden.json` records the output of
  every transport for every existing event in both languages, byte for byte
  (`tests/test_notifications_golden.py`). A diff is a behaviour change: fix the code.
  Add a new event's cases to `scripts/dev/gen_notifications_golden.py`, and refresh
  the fixture only to add cases, never to absorb a change of an old one.
- **Language.** `resolve_instance_language()` (`app/email_template.py`) returns
  `app_settings.notification_language` when it is `fr` or `en`, else the heuristic:
  English only with a strict majority of English push subscriptions, a tie is
  French. Push payloads use the subscription's own language.
- **No environment variable** for anything here (root `AGENTS.md`).
