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
- `render.py`: the only place notification copy lives. `render(event, lang,
  text_policy=...)` returns `Rendered(title, body_text, body_markdown, body_html,
  tag, url_hash)`. The dict helpers (`build_push_payload`, `event_notification_text`,
  `notification_email_lines`, `render_plain_email`, `render_html_email`,
  `enrich_notification_payload`) serve the callers that pass dicts; they were moved
  unchanged from `app/push/manager.py` and `app/email_template.py`, which re-export
  them under their old names (tests import `_build_payload` from `push.manager`).

- `channels.py`: the channel models and the type registry. A type declares a Pydantic
  config model (`WebPushConfig`, `EmailConfig`, `WebhookConfig`, `AppriseConfig`); secret
  fields are `SecretStr` with `writeOnly: true` in `json_schema_extra`, labels and help are
  i18n keys (`label_key`, `help_key`) in the schema, the texts live in the frontend.
  `types_schema()` is what `GET /api/notifications/types` serves and the shared schema-driven form renders. A registry entry also carries
  `default_limits` (per minute, hour, day), `cloud` (default `include_message_text`
  `public_only`, else `always`), `quiet_hours_exempt_default` and `required` fields.
  `ntfy`, `discord` and the presets add entries later.
- `app/repository/notification_channels.py`: `NotificationChannelRepository` (CRUD, the
  in-memory cache of the public channels, status updates, `get_rules()`) and
  `NotificationJournalRepository` (deliveries, batched `add_deliveries()`, counters, state).
- `rules.py`: pure evaluation of `notification_rules` (see "Dispatcher").
- `dispatcher.py`: `emit()`, the queue and worker per channel, the limiter, the retry
  policy, the journal, the counters and `notification_statuses()` (see "Dispatcher").
- `transports/`: `base.py` (`Transport.send(channel, rendered, event, secrets) ->
  SendResult(status, retry_after, error_code)`, `result_from_exception()` for httpx),
  `web_push.py` (wraps `PushManager.dispatch_event`), `email.py`, `webhook.py`,
  `apprise.py` (the native transports of the `email`, `webhook` and `apprise` channel types,
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
  (email host/port/mode, webhook URL): `secret_required`. Plain text in `secrets` until
  the encryption ticket.
- **Destinations are checked** at create and when the destination or
  `allow_private_network` changes (egress guard; Apprise URLs by `validate_apprise_urls`),
  coded `egress_*` / Apprise codes. `allow_private_network` is false for a new channel; the
  import grandfathers every existing destination.
- **Rules** (`app_settings.notification_rules`): `version`, `paused_until`, `events[id]`
  (`channels`, `always_deliver`, and for `contact.first_seen` `contact_types`
  `{companion, repeater, sensor}` since 4.18.x read the three types differently),
  `conversations[key][channel_id] = bool`, `keywords[*].channels`,
  `watched_nodes[*].channels`. The import drops a medium whose channel it did not create
  (no SMTP, no URL): it could not deliver, and a reference would be an orphan.
- **Deletion cascade.** `delete()` removes the channel, its deliveries and counters, and its
  id from `events[*].channels`, `contact_types`, `conversations`, `keywords[*].channels`
  and `watched_nodes[*].channels` (`strip_channel_from_rules`) in one transaction. A
  disabled channel keeps its rules. `resolve(ids)` skips an orphan id and logs it once.
- **Cache.** `list_all()`/`get()`/`resolve()` read a cache invalidated by every write.
- **Journal.** 200 `notification_deliveries` rows per channel (trimmed at insert, plus the
  daily `prune_deliveries()`); successes are counted per channel and hour
  (`notification_counters`); `notification_state` holds dedup timestamps with an expiry.
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

## API (`app/routers/notifications.py`, `api_support.py`)

- All routes are `admin`. Responses show a channel with `config[secret] = "********"` (or `""`
  when unset) and `secret_fields`; `Channel` itself never holds a value. Errors are
  `{code, params}` (`ChannelError.detail()`, `fail()`): `invalid_config`, `config_required`,
  `secret_required`, `type_immutable`, `builtin_channel` (403), `channel_missing` (404, 400 for
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
- **Rules** (`GET/PATCH /rules`): `events[id]` (`channels`, `always_deliver`, `contact_types`),
  `paused_until` (epoch seconds, `null` resumes, a past value is a resume), `mention_whole_word`
  in the rules; `radio_offline_grace_s` and `push_allow_private_endpoints` are the
  `app_settings` columns (one source), `host_timezone` read-only. The summary of the events
  suppressed during a pause (counts per channel on resume) is not implemented: the journal
  keeps one `paused` row per suppressed job.

## Dispatcher

- **Entry.** `emit(event, *, payload=None, targets=None)` never raises and never waits on a
  send. Without `targets` the rules choose the channels (`rules.event_channel_ids`), minus the
  channels in `exclude` (a producer ruling a channel out); `targets` is for a caller that
  already decided (`notify.channel_changed`, tests). Unknown and disabled channels are skipped
  without a journal row. Producers never call `emit` with `targets`: see "Producers".
- **Rules** (`rules.py`). A stored conversation exception wins either way; else a DM follows
  `message.dm`, a Public or `#` channel `message.channel`, a channel with its own key no
  one; `contact.first_seen` follows `contact_types` (1 companion, 2 repeater, 4 sensor; a
  room or an unknown type never). The global `paused_until` and a channel's own
  `paused_until` hold everything but urgent and `always_deliver` events (journal `paused`).
  `conversation_channel_enabled(state_key, message_type, rules, channel_id, is_hashtag, is_public)`
  is the per-channel predicate (plus `muted`, `blocked` and `now` for the pause: they cut
  everything but an `always_deliver` event); its cases are the shared fixture
  `tests/fixtures/notification_policy_cases.json` (the frontend suite reads it too, T19).
  `app/push/policy.py` is a deprecated adapter over it, removed with the shims.
  Block lists and mutes are applied by `producers.emit_message` (MSHL-113 moves them here).

## Producers

`producers.py` is what the rest of the app calls: `emit_message(data)` (the
`broadcast_event("message")` hook in `app/websocket.py`: outgoing messages, pending channels and
muted channels, unless `always_deliver`, never notify; a blocked sender's message skips
`legacy:apprise` only, as in 4.18.x), `emit_payload(payload)` (first seen in
`app/push/first_seen.py`, `hashtag_catalogue._notify_channel_found`, `telemetry_alerts._dispatch`,
`radio_watch._dispatch`) and `emit_oss_update(current, latest)` (`services/oss_updates.py`, which
keeps its `last_notified_update_version` guard: it records only when `emit` queued the event for at least one enabled, unpaused channel). `emit` returns that count and takes `rules` / `is_open` so the producer's lookups are not repeated; the block lists are read only when `legacy:apprise` is a candidate.
The payload dict is what the transports render, so the golden fixtures hold
(`tests/test_notification_producers.py` sends every golden case through the rules). `PushManager`
(`app/push/manager.py`) is only the Web Push transport (`dispatch_event`).
- **Queues.** One `asyncio.Queue(maxsize=200)` and one worker task per channel (`spawn()`),
  started by a job and ended when the queue is empty. One send at a time per channel;
  `min_interval_s` between sends (`channels.min_interval_s`: preset `telegram` 3 s,
  `discord` 2 s, `ntfy` 1 s; type `email` 10 s, `apprise` 2 s, `webhook` and `web_push` 0).
  Past `MAX_PENDING` (20) waiting, a non-urgent job is folded (`aggregated`) into one
  `notifications_grouped` "+N events" sent once the queue is empty; a full queue drops the
  job (`rate_limited`). `contact.first_seen` is capped at 10 an hour per channel
  (`aiolimiter`), the rest summed into one `first_seen_grouped`. The spacing and the retries
  read the injected `clock`/`sleep` (fake-clock tests); token buckets, daily quotas and the
  global cap come with MSHL-125.
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
  secrets). `notification_deliveries` rows only for `failed`, `rate_limited`, `aggregated`,
  `paused`, `aborted` (`suppressed_quiet` with the quiet hours), batched in one transaction;
  a row holds the event id, the hashed `dedup_key`, a code and the attempts, never the text.
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
- **Webhook** (`webhook.py`). `body_bytes()` and `sign()` are the 4.18.x v1 body and signature,
  byte for byte (golden `webhook_body`); `request_headers()` adds `X-Webhook-Id` (the event's
  `dedup_key`, a body hash for a grouped summary) and `X-Webhook-Timestamp` (per attempt). The
  signature does not cover the timestamp (v2, MSHL-137); the id and timestamp are unsigned, so no replay protection. `verify_signature()` is constant-time.
  `app/notify.send_webhook_alert` (legacy) uses the same helpers.
- **Apprise** (`apprise.py`). `apprise_medium.send_to_urls` sends each URL on its own (retries per
  URL); any URL still failing makes the delivery `failed` with `line N (redacted url): code`,
  never `retry`. Discord URLs get `avatar=False`; `verify_tls` maps to `verify_certificate`.
- **TLS.** `verify_tls=false` is honoured only when every resolved address of the destination
  is private (`tls_check_may_be_off`, webhook URL, SMTP host, each Apprise user-chosen host; a
  fixed-host service never): else `verify_tls_requires_private_destination`, at create/update and
  again at send. E-mail `mode=none` with a login is refused outside the private network
  (`smtp_plaintext_auth_refused`, same two places). Cc addresses join the recipients (Apprise
  copies a whole Cc on every per-recipient message).

## Contract, version 1

Consumed by the Feed, the webhook v2 and the plugin `plugin.notification` event
without redefining anything.

- **Id**: `event` is `domain.name`, lower case: `message.dm`, `message.channel`,
  `message.mention` (placeholder, no producer yet), `contact.first_seen`,
  `channel.found`, `telemetry.alert`, `radio.offline`, `radio.online`,
  `app.started`, `app.update`, `notify.destination_changed`, `notify.test`
  (`EVENT_IDS`). These are notification ids, not WebSocket types
  (`app/auth/ws_events.py` stays the only list of those).
- **Common fields**: `event`, `event_id` (random, per occurrence), `occurred_at`
  (epoch seconds), `dedup_key`, `urgent` (default false; true for `telemetry.alert`
  and `radio.offline`; never for `message.*`, whose text is unauthenticated mesh
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
