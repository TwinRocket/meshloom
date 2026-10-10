# Backend (`app/`)

Backend-only facts. The repo map, commands, quality gate, cross-cutting conventions (packet
identities, message dedup, path hash modes, channel keys), environment variables, the release
chain, the updater security principle and the working rules are in the root `AGENTS.md`.
Like everything else here, this file is a hypothesis: check it against the code.

Stack: FastAPI, aiosqlite, Pydantic v2 (+ pydantic-settings), `meshcore` (PyPI), PyCryptodome, httpx.

## Module map

```text
app/
├── main.py              # lifespan, middleware (CORS, gzip, Basic auth), router registration, static mount
├── config.py            # pydantic-settings, env prefix MESHCORE_ (see root env table)
├── database.py          # SQLite connection, base schema and indexes
├── migrations/          # _NNN_name.py, each exposes `async def migrate(conn)`; tracked by PRAGMA user_version
├── models.py            # Pydantic API models and typed write contracts (e.g. ContactUpsert)
├── events.py            # WsEventType + typed WS payload serialization
├── websocket.py         # WS manager, broadcast_event(), broadcast_error()
├── background_tasks.py  # spawn(): fire-and-forget with a strong ref, logged errors, drained on shutdown
├── radio.py             # RadioManager: transport/session state, radio_operation() lock, slot cache
├── radio_sync.py        # periodic loops: contact sync/offload, adverts, message poll/audit, telemetry
├── radio_proxy/         # virtual companion TCP radio (protocol, policy, manager)
├── decoder.py           # packet parsing and decryption
├── packet_processor.py  # RX_LOG_DATA pipeline: store raw, decrypt, dedup, paths, ACK codes
├── event_handlers.py    # MeshCore event subscriptions (on_rx_log_data, on_ack, CONTACT_MSG_RECV fallback)
├── region_resolver.py   # name a packet's region by recomputing transport codes per known region
├── security.py          # optional app-wide HTTP Basic auth (HTTP + WS)
├── frontend_static.py   # serve frontend/dist, else frontend/prebuilt
├── keystore.py          # in-memory private key for DM decryption
├── push/                # Web Push (not a fanout module)
├── fanout/              # fanout bus, see fanout/AGENTS_fanout.md
├── repository/          # data access, one module per aggregate
├── services/            # orchestration/domain services (selection below)
└── routers/             # one APIRouter per file; server_control.py holds shared repeater/room CLI helpers
```

Services worth knowing before touching a flow:

| Module | Owns |
|---|---|
| `radio_runtime.py` | The seam routers, lifespan, fanout and `radio_sync.py` use to reach the global `RadioManager` |
| `radio_lifecycle.py` | Post-connect setup (timeout 300 s), connection monitor loop (every 5 s) |
| `radio_identity.py`, `radio_ingest_gate.py` | Bound identity (`radio_bound_public_key`) and the process-wide ingest gate |
| `radio_transport.py` | Transport snapshot read from `app_settings` (serial/TCP/BLE are UI settings, never env) |
| `dm_ingest.py`, `dm_ack_apply.py`, `dm_ack_tracker.py` | DM storage and ACK application, shared by packet and fallback paths |
| `messages.py`, `message_send.py` | Message creation, `handle_duplicate_message`, send/resend workflows |
| `meshloom_community.py` | Community settings, JWT mint, HTTP client and circuit breaker |
| `community_live.py` | Community Live relay (one upstream socket) |
| `directory.py`, `observer_reach.py`, `rf_locate.py` | Community directory, observer reach, RF locate |
| `hashtag_catalogue.py`, `channel_membership.py` | Unlocking unknown GroupText; pending/adopted/refused channels |
| `install_kind.py`, `update_apply.py`, `oss_updates.py`, `update_window.py` | In-app updater, see "Updates" |
| `stale_contacts.py` | Hourly housekeeping: stale contacts, raw-packet retention (`raw_packet_retention_days`, 0 = off) |

## Migrations

- One file per version: `app/migrations/_NNN_description.py`. The runner discovers files by numeric prefix and runs pending ones in order.
- Each migration runs in one transaction with the `user_version` bump. `conn.commit()` inside a migration is deferred to the runner. A migration that cannot run in a transaction (`VACUUM`, `journal_mode`) sets `TRANSACTIONAL = False` and must be idempotent.
- New indexes go in both `database.py` (fresh DBs) and a migration (existing DBs). Example: `idx_messages_chan_unattributed_sender` (migration 086).
- Tests: `tests/test_migrations/`.

## Radio lifecycle

- Transport lives in `app_settings` (`radio_transport`, `radio_serial_port` empty = auto-detect, `radio_tcp_*`, `radio_ble_*`). Until `radio_transport` is set, the radio stays paused.
- A live key that differs from `radio_bound_public_key`, or mesh history with no bound key (`identity_unbound_legacy`), closes ingest and pauses setup until the user adopts or rejects (`POST /radio/identity/adopt|reject`). Do not call `pause_connection()` while the post-connect operation lock is held.
- The connection monitor checks every 5 s. If post-connect setup fails, `setup_complete` stays false and the monitor retries indefinitely. This is intentional: the radio may be rebooting.
- If setup exceeds 300 s, the backend logs the failure and broadcasts an `error` toast asking the operator to reboot the radio and restart the server.
- The message poll task always runs: an hourly audit by default, or every 10 s with `MESHCORE_ENABLE_MESSAGE_POLL_FALLBACK=true`. It also detects channel-slot cache drift and resets the send-slot cache when it finds any.
- Periodic adverts: `app_settings.advert_interval` in seconds, `0` = off. Any non-zero value is floored at 3600 s (`MIN_ADVERT_INTERVAL`).
- `sync_recent_contacts_to_radio()` sets `_last_contact_sync` before the sync finishes, so a failed sync stays throttled (no retry storm).
- Contact capacity: `max_radio_contacts`. Favorites load first, non-favorites refill to 80 %, and a full offload/reload triggers at 95 % (`RADIO_CONTACT_*_RATIO` in `radio_sync.py`).

## Sending

- Routers validate, then delegate to `services/message_send.py`. Radio access goes through `radio_operation()`.
- Channel slots: the count comes from firmware `max_channels` (fallback 40). Slots are reused through a session LRU cache, except on TCP (`connection_info` starts with `TCP:`) or with `MESHCORE_FORCE_CHANNEL_SLOT_RECONFIGURE=true`; then every send calls `set_channel(...)`.
- DM retries mirror `meshcore_py` `send_msg_with_retry`: stage the effective route with `add_contact`, send, and retry up to 2 more times only when `MSG_SENT` returned an expected ACK code. Timing follows the radio's `suggested_timeout`. The last retry is flood (`reset_path`). The first ACK is terminal: sibling ACK codes are cleared.
- ACKs are matched from the host `ACK` frame (`event_handlers.on_ack`) and from RF packets (PATH-embedded and standalone `ACK` payloads, in `packet_processor`). ACKs never update routes.
- Channel resend: byte-perfect within 30 s; `?new_timestamp=true` has no time limit and creates a new row. The sender prefix is stripped by exact match on the current radio name.
- Repeater/room login (`routers/server_control.py: prepare_authenticated_contact_connection`): one try on the effective route, then exactly one flood retry (`reset_path`), only on timeout and only when the route was not already flood. This is deliberate. The server relearns the return path on a flood login. Do not reduce it to single-shot.

## Read state

`GET /read-state/unreads` never windows the whole `messages` table. It enumerates conversations with an index skip-scan and reads through `idx_messages_pagination` / `idx_messages_unread_covering`. `tests/test_unreads_equivalence.py` keeps the old `ROW_NUMBER()` query as an oracle: both outputs must stay identical. `first_unread_ids` is the first unread row by `(received_at, id)`. Using `MIN(id)` would be wrong, because historical decryption inserts old messages with new ids.

## Region-scope stats (`GET /statistics` → `region_scope_24h`)

Two views with different denominators that are not meant to agree. Traffic (`path_utils.bucket_region_scope`) counts flood GroupText, including undecryptable packets. Senders (`StatisticsRepository._region_scope_senders_24h`, in `repository/settings.py`) counts distinct decrypted senders. `false_positive_floor` comes from undefined payload types (`0x0C`–`0x0E`) and measures corrupt captures that claim `TRANSPORT_FLOOD`. Show `scoped_messages` next to the floor, never the percentage alone, and do not remove the floor.

## WebSocket (server side)

- Events (`events.py: WsEventType`): `health`, `message`, `message_acked`, `message_deleted`, `contact`, `contact_resolved`, `contact_deleted`, `channel`, `channel_deleted`, `raw_packet`, `community_packet`, `community_live`, `error`, `success`.
- On connect the server sends `health` only. Contacts and channels load over REST. The client sends the text `ping`, and the server replies `{"type":"pong"}`.
- Each client has a bounded queue (`CLIENT_QUEUE_MAX` = 2048) drained by one writer task, so `broadcast_event()` never awaits socket I/O. `raw_packet` frames are dropped for a client whose backlog reaches 1024. A send timeout (5 s), a send error or an overflow evicts the client with close code 1013. Never call `websocket.send_text` on a managed socket: use `ws_manager.send_raw`.
- `broadcast_event()` also notifies the radio proxy and, when `realtime=True`, dispatches `message` → fanout + Web Push, `raw_packet` → fanout and `contact` → fanout. Historical decryption passes `realtime=False`.

## Community client

### HTTP (`services/meshloom_community.py`)

- Effective config = env first, then DB. `MESHLOOM_COMMUNITY_IATA`, `_BROKER_HOST` and `_API_BASE` override the stored value on every read (`get_community_effective()`); they are not only seeds. `MESHLOOM_COMMUNITY` only seeds a brand-new DB. `MESHLOOM_COMMUNITY_LOCKED=1` makes enabling answer 403.
- Upstream mapping: transport error/timeout/5xx → **503**; 429 → 429; 400 → 400; 401 → 502 (with a clock hint when `classify_auth_rejection` reports `clock_skew`, drift > 60 s); other non-200 → 502 (`CommunityUpstreamError.upstream_status`); an unexpected body → 502.
- `CommunityBreaker`: 3 consecutive failures open it for 30 s (503 without network), then one trial call decides.
- Observer reach falls back to one GET per hash only when the batch route is missing upstream (404/405).
- The hashtag catalogue is global: `GET /community/hashtags` proxies `GET /v1/hashtags`. There is no per-IATA catalogue (`/community/iata/{code}/hashtags` was removed; Community answers 410 on its old `/v1/iata/{code}/hashtags`).
- Hashtag keys hash the exact name everywhere; the official-app normalisation is UI input only (see root `AGENTS.md`, "Hashtag rule").

### Live relay (`services/community_live.py`)

- One process-wide upstream socket to `/v1/live/packets`. The reader is claimed under the relay lock, so concurrent `subscribe()` calls cannot open two sockets. Sessions expire after 90 s without a heartbeat; the upstream closes 1.5 s after the last session leaves.
- Frames go out as `community_packet` through `ws_manager.broadcast`, i.e. to **every** connected WS client, not only to subscribed tabs.
- Reconnect backoff: 0.5 s doubling up to 30 s. It resets only after a socket stayed up ≥ 10 s (`JWT_EXPIRY_MIN_UPTIME_S`). The JWT is reminted locally on every attempt. A 4001 close on such a socket reconnects at once.
- A handshake 401 is not a 4001: retry at 2 s, doubling, and give up after 5 refusals (`AUTH_REJECT_MAX_ATTEMPTS`), or at once for `AUTH_FATAL_CODES`. The relay then reports `state: auth_rejected`, with `auth_error` = `clock_skew` or `token_rejected` (`auth_code` keeps the raw code). `POST /community/live/relancer` or `PATCH /community` clears it.
- 4002: the reader stops that generation and does not reconnect. 4003/4004/4005 are never shown to the user (`CommunityLiveStatus` hides them).
- `community_live.state` values actually produced: `connected`, `reconnecting`, `opted_out`, `idle`, `auth_rejected`. `gate` is in the type, but nothing sets `_gate_blocked = True` (dead code, tracked as a code issue).

## Updates

Security principle (root side never reads anything the app writes): see the root `AGENTS.md`.

- `services/install_kind.py`: `MESHLOOM_INSTALL_KIND` (`package|compose|addon|container|source`) wins. Otherwise detection order: package helper (`/usr/lib/meshloom/apply-update` or `meshloom-update.service`), compose helper (`MESHLOOM_UPDATE_HELPER=compose` or `/var/lib/meshloom/update-helper-compose`), container, source. Apply is supported only for package/compose with a helper present. `addon` is declared only, never inferred, and never applies. `MESHLOOM_UPDATE_HELPER=none` forces apply off.
- `services/oss_updates.py`: polls every 300 s (once at start). It reads GitHub `releases/latest` (redirect, strict `X.Y.Z`) and falls back to the Community mirror `GET /v1/meshloom/latest` (no JWT, works with Community off, skipped while the breaker is open). `latest_source` says which one answered.
- `services/update_apply.py`: the app only writes the trigger `request-update` and its own `update-attempt.json`. The root helper publishes `status.json` (package: `/var/lib/meshloom-update/status.json`; compose: `MESHLOOM_UPDATE_STATUS_PATH`, a read-only mount). `read_job()` shows the attempt until the helper's `started_at` is at or after the request (±2 s), then the helper status. Helper `cooldown` shows as `failed`. The pre-4.18 `update-job.json` is read only as a fallback (`legacy_update_helper`). When the package `.path` unit is not active, the app falls back to `systemctl start --no-block meshloom-update.service` and checks that the unit really started.
- Routes: `GET /updates`, `POST /updates/refresh`, `POST /updates/apply` (202; 409 `apply_not_supported` / `update_not_available` / `apply_in_progress`), `PATCH /updates/settings` (`auto_update` and window; not through `PATCH /settings`). Auto-apply waits 6 h after a failed job.
- Tests: `tests/test_update_apply.py`, `tests/test_install_kind.py`, `tests/test_oss_updates.py`, `tests/test_update_helpers_exec.py` (runs the real helpers against hostile inputs).

## Web Push (`app/push/`)

Per-browser subscriptions in `push_subscriptions` (`UNIQUE(endpoint)`). VAPID keys are generated on first start and stored in `app_settings`. The subject is `app_settings.vapid_subject`, falling back to `MESHCORE_VAPID_SUBJECT`; Apple rejects `.local`. Enablement: `policy.conversation_is_enabled` (overrides > `new_dm` for PRIV and rooms > public/hashtag ON > private-key channels OFF). Muted channels are a separate breaker. First-seen alerts (`dispatch_first_seen`) fire only on a new contact insert after setup. A push service answering 404/410 deletes the subscription. Requires HTTPS and outbound internet.

## Security posture: facts, not endorsements

- CORS is `allow_origins=["*"]` **with** `allow_credentials=True` (`main.py`). No accounts or sessions; optional app-wide Basic auth (`security.py`).
- Bots run user Python through `exec()` (`MESHCORE_DISABLE_BOTS=true` turns this off).
- Changing these belongs to the out-of-scope Access and Plugins workstreams (root `AGENTS.md`, "Working rules"). Do not change them in passing.

## API routes (all under `/api`)

Source of truth: `app/routers/*.py` and `app.include_router(...)` in `main.py`. The OpenAPI docs are served by `api_docs.py`.

| Router | Routes |
|---|---|
| health | `GET /health` |
| debug | `GET /debug` |
| radio | `GET,PATCH /radio/config` · `GET,PUT /radio/private-key` (GET needs `MESHCORE_ENABLE_LOCAL_PRIVATE_KEY_EXPORT=true`) · `POST /radio/advertise` · `POST /radio/discover` · `POST /radio/regions/verify` · `POST /radio/discover-regions` · `POST /radio/trace` · `GET,PUT /radio/transport` · `POST /radio/transport/ble-scan` · `GET,PATCH /radio/proxy` (409 on port change when `MESHCORE_MANAGED_PORTS`) · `POST /radio/identity/adopt` · `POST /radio/identity/reject` · `POST /radio/disconnect` · `POST /radio/reboot` · `POST /radio/reconnect` |
| contacts | `GET,POST /contacts` · `GET /contacts/analytics` · `GET /contacts/repeaters/advert-paths` · `POST /contacts/bulk-delete` · `DELETE /contacts/{pk}` · `POST /contacts/{pk}/mark-read` · `POST /contacts/{pk}/trace` · `POST /contacts/{pk}/path-discovery` · `POST /contacts/{pk}/routing-override` · `POST /contacts/{pk}/telemetry` · `GET /contacts/{pk}/telemetry-history` |
| contact_groups | `GET,POST /contact-groups` · `PATCH,DELETE /contact-groups/{id}` · `PUT /contact-groups/{id}/members` |
| repeaters | `POST /contacts/{pk}/repeater/{login,status,lpp-telemetry,neighbors,acl,node-info,radio-settings,advert-intervals,owner-info,regions}` · `GET /contacts/{pk}/repeater/cache` · `GET /contacts/{pk}/repeater/telemetry-history` · `POST /contacts/{pk}/command` |
| rooms | `POST /contacts/{pk}/room/{login,status,lpp-telemetry,acl}` |
| channels | `GET,POST /channels` · `GET /channels/rejected` · `GET /channels/{key}/detail` · `POST /channels/bulk-hashtag` · `POST /channels/{key}/{mark-read,flood-scope-override,path-hash-mode-override,adopt,refuse}` · `DELETE /channels/{key}` (200 even if absent) |
| messages | `GET /messages` (`q`, `after`/`after_id`) · `GET /messages/around/{id}` · `POST /messages/direct` · `POST /messages/channel` · `POST /messages/channel/{id}/resend` · `DELETE /messages/{id}` (local only) |
| packets | `GET /packets/undecrypted/count` · `GET /packets/undecrypted/group-text-samples` · `GET /packets/history` · `GET /packets/{id}` · `POST /packets/region-backfill` · `POST /packets/decrypt/historical` · `POST /packets/maintenance` |
| read_state | `GET /read-state/unreads` · `POST /read-state/mark-all-read` |
| settings | `GET,PATCH /settings` · `POST /settings/{favorites,pins,muted-channels,blocked-keys,blocked-names}/toggle` · `POST /settings/tracked-telemetry/toggle` · `GET /settings/tracked-telemetry/schedule` · `POST /settings/tracked-telemetry-contacts/toggle` · `GET /settings/tracked-telemetry-contacts/schedule` (max 8 tracked each) · `GET /settings/telemetry-alert-catalog` · `POST /settings/notification-destinations/test` · `GET /settings/backup/database` · `GET /settings/backup/json` · `POST /settings/backup/restore` |
| fanout | `GET,POST /fanout` · `PATCH,DELETE /fanout/{id}` · `POST /fanout/bots/disable-until-restart` |
| statistics | `GET /statistics` |
| tools | `POST /tools/mesh-test` (404 when Community is off, 400 when the scope is not in `known_regions`; stores nothing) |
| locate | `GET /locate?q=` (409 when ambiguous; never writes GPS) |
| directory | `POST /directory/resolve-hops` (1-byte prefixes → 400) · `GET /directory/nodes` · `GET /directory/nodes/live` · `GET /directory/nodes/search` · `GET /directory/nodes/{pk}/reach` · `GET /directory/nodes/{pk}/neighbors` · `GET /directory/packets/{hash}/reach` · `POST /directory/packets/reach-counts` (≤ 20) · `POST /directory/cache/reset`. Community only: empty payloads when it is off |
| community | `GET,PATCH /community` · `GET /community/airports` · `GET /community/me/stats` · `PUT /community/me/iata` · `POST /community/me/iata/override` · `GET /community/stats` · `GET /community/hashtags` · `PUT /community/me/hashtags` · `POST /community/live/subscribe` · `DELETE /community/live/subscribe/{session_id}` · `POST /community/live/relancer` |
| updates | see "Updates" |
| push | `GET /push/vapid-public-key` · `POST /push/subscribe` · `GET /push/subscriptions` · `PATCH,DELETE /push/subscriptions/{id}` · `POST /push/subscriptions/{id}/test` · `GET,PATCH /push/preferences` · `PUT /push/preferences/conversations/{key}` |
| ws | `WS /ws` |

## Tests

`PYTHONPATH=. uv run pytest tests/ -q`. One file per area (`tests/test_<module>.py`), plus `tests/test_migrations/`, shared fixtures in `tests/conftest.py`, Playwright e2e in `tests/e2e/` (hardware, not part of the gate). Find the right file with `ls tests | grep <area>` instead of relying on a list here.

When a change touches the API or WS payloads, update `frontend/src/types.ts` and the frontend tests in the same PR.

## Known non-issues

- `sender_timestamp` is 4-byte Unix seconds on the wire. Dedup is per second, and switching to milliseconds would break echo dedup.
- Our own DM heard back over RF (`try_decrypt_dm` with `is_outbound`) attaches `packet_hash` to the plaintext row stored by the send endpoint. It does not create a second row.
- Contact lat/lon `0.0` is MeshCore's "no GPS" value and overwrites older coordinates on purpose.
- `meshcore_py` can raise `IndexError` on a truncated advert `LOG_DATA` frame. This is a one-off parser failure, not DB corruption.
