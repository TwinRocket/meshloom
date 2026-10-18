# Notifications (`app/notifications/`)

The event catalogue, the one FR/EN renderer, and the delivery helpers
(`apprise_medium.py`, `executor.py`, `radio_watch.py`). Delivery to a
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
  `types_schema()` is what `GET /api/notifications/types` serves (the route comes with its
  own ticket) and the shared schema-driven form renders. A registry entry also carries
  `default_limits` (per minute, hour, day), `cloud` (default `include_message_text`
  `public_only`, else `always`), `quiet_hours_exempt_default` and `required` fields.
  `ntfy`, `discord` and the presets add entries later.
- `app/repository/notification_channels.py`: `NotificationChannelRepository` (CRUD, the
  in-memory cache of the public channels, status updates) and
  `NotificationJournalRepository` (deliveries, counters, state).

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
- This version only stores and reads: delivery still follows the legacy columns until the
  dispatcher ticket. The legacy columns are no longer written by the new model.

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
