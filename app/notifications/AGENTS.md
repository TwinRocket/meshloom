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
