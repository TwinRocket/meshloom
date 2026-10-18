# Community client (backend)

Developer reference for `app/services/meshloom_community.py`, `community_live.py`,
`community_ban.py`, `community_status_clear.py`, `observer_position.py` and the
Stats publisher (`app/fanout/meshloom_stats.py`). The rules an agent must not
break are in `app/AGENTS.md`, "Community client"; this page holds the mechanics
behind them. The Community side of every call is in
`meshloom-community/docs/contracts/` (`identity.md`, `errors.md`,
`live-events.md`); this page does not repeat it. Like every doc, it is a
hypothesis: check it against the code. Constants are named so you can grep them.

## Configuration and state

- Effective config is the DB (`community_enabled`, `community_iata`) plus the fixed
  `DEFAULT_BROKER_HOST` / `DEFAULT_API_BASE`, read at call time by
  `get_community_effective()`. No env var is read, and a new DB starts off.
  `services/legacy_env_settings.py` handles the retired `MESHLOOM_COMMUNITY*`
  variables: `import_legacy_community_iata()` copies a valid IATA into an existing
  DB once (marker `community_env_imported`, migration 091), and
  `warn_about_retired_env()` warns at every start while one is set.
- `GET /community` returns `enabled`, `iata`, `publisher_configured`,
  `publisher_connected`, `state` (`off`, `active`, `waiting_for_position`),
  `banned` and `opt_out_pending`. `PATCH` accepts `enabled`, `iata` and
  `opt_out_reason`.
- An unknown `position_precision` (bind result, `/me/stats`) parses as `null`.

## HTTP (`stats_request`)

- Upstream mapping: transport error, timeout or 5xx gives 503; 429 gives 429; 400
  gives 400; 401 gives 502 (with a clock hint when `classify_auth_rejection` reports
  `clock_skew`); 403 `code: banned` gives 403 `community_banned` (see Banned key);
  any other non-200 gives 502 (`CommunityUpstreamError.upstream_status`); an
  unexpected body gives 502.
- `CommunityBreaker` (the shared `CircuitBreaker` of `app/net/breaker.py`): 3 consecutive failures (`BREAKER_FAILURE_THRESHOLD`) open it
  for 30 s (`BREAKER_OPEN_SECONDS`, 503 without network), then one trial call
  decides. The IATA suggestion and the release mirror never touch it.
- The hashtag catalogue is global (`GET /community/hashtags` proxies
  `GET /v1/hashtags`). Hashtag names go out (`PUT /v1/me/hashtags`) only with
  Community on and an IATA saved, and only for a `#` channel whose key is
  `SHA256(name)[:16]` (`is_public_hashtag_channel`); resolve and sample uploads
  (`services/hashtag_catalogue.py`) also wait for the IATA. Keys hash the exact
  name; the official-app normalisation is UI input only (root `AGENTS.md`,
  "Hashtag rule").
- `GET /community/iata/active` (`active_iata_codes`, `community_live.py`) is cached
  60 s (`ACTIVE_IATA_CACHE_S`); off, waiting or a Community without the route
  (404/405) gives `available: false`.

## Egress guard

Opted out means no connection to the Community hosts. `community_egress_state()` is
the single check, made by `stats_request`, the Live socket (before every
reconnect, and through `community_active()` for `subscribe`/`restart`/
`unsubscribe`), the release mirror, `active_iata_codes`, the hashtag publish and
resolve, the directory, observer reach and the update poll cadence. It answers
None while off **and while waiting for a position**. `community_enrolled_state()`
(on, waiting or not) is for `FanoutManager.sync_system_modules()`, the Stats
publisher (`_pre_connect`) and the `PUT /v1/me/iata` binds only.

Four exceptions, each a context variable read by `stats_request` for one path
(do not add a fifth without an owner decision):

| Exception | Variable | Allowed |
|---|---|---|
| Join or IATA bind | `_bind_state` | `PUT /v1/me/iata`, also while waiting |
| Opt-out call and its manual retry | `_opt_out_state` | `POST /v1/me/opt-out` only |
| IATA suggestion | `_iata_suggest_state` | `GET /v1/iata/suggest` only; not during an opt-out |
| Leftover status clear | its own MQTT connection | one retained clear (below) |

While waiting for a position the Stats publisher (status only, no packets) and the
binds keep working; every other `/v1/me/*` call, including
`POST /me/iata/override`, stays cut. `tests/test_community_off_no_egress.py` sweeps
the routes: a new call path must be covered there (`_NOT_SWEPT` lists the
exceptions).

## Waiting for a position

- `community_state_of()` gives `waiting_for_position` when on and `_position_missing`
  is set. `note_radio_position()` sets it when an accepted identity
  (`ingest_allowed()`) reports a `self_info` without a publishable position (none,
  `0, 0`, out of bounds). It runs right after the identity gate in
  `run_post_connect_setup`, before `export_and_store_private_key`, so nothing leaves
  first; then on every `SELF_INFO` through `observer_position.note_radio_self_info()`.
  No radio, or an identity not yet accepted, keeps the last state.
- A position alone does not clear it. Only a successful `PUT /v1/me/iata` that
  carried a position does (`note_position_bound()`), or a `self_info` this process
  already bound (`position_already_bound`). A refused bind keeps waiting.
- The flag is persisted in `app_settings.community_waiting_for_position` (migration
  092) and loaded by `load_position_state()` in the lifespan before the update poll
  and the fanout start, so a restart while waiting makes no call at all (the IATA
  suggestion excepted). A new DB starts not waiting. Joining without a position is
  allowed and lands in this state.
- A change spawns `_apply_position_state()`: save the flag (also while off), then
  under `_update_lock` re-read the state. To waiting: bump `_egress_generation`,
  cancel requests in flight, close Live. Back to active: reopen Live and nudge the
  update poll. Both ways, the Stats publisher re-publishes its retained status
  (`FanoutManager.republish_system_status()`). Tested by `TestWaitingForPosition`.

## Joining, IATA change, opt-out

All three run under `_update_lock`.

- **The PATCH never turns Community on or changes the IATA.** From off,
  `PATCH {enabled: true}` gives 409 `join_requires_bind`; another `iata` gives 409
  `iata_requires_bind` (`enable_through_bind`, `iata_through_bind`). Nothing is
  written or sent. The frontend only PATCHes `{enabled: false}`.
- **Join or IATA change** is `PUT /community/me/iata` (`bind_community_iata()`,
  `enable: true` to join). Order, because `/authz` ties a status topic to the
  session's IATA: clear the old retained status from the running session and stop
  the publisher, call `PUT /v1/me/iata`, save the IATA, start the publisher with a
  JWT for the new one. A failed or cancelled bind restarts the publisher on the old
  topic and keeps the stored IATA. When off, the bind goes out first (through
  `_bind_state`) and only success writes `enabled`; a refusal (429 cap, 503 no
  radio key) leaves Community off. The write and the publisher/Live reload then run
  through `_run_to_completion()`, so a cancelled caller never leaves Community on
  without its publisher. The first bind of a key is free (`identity.md`).
- **Position in the bind** (`observer_position.py`). `put_iata_bind()` is the only
  builder: `publishable_position(self_info)` gives 6 decimals with
  `position_precision: "exact"` when the adverts broadcast the position
  (`advert_location_broadcast`: `adv_loc_policy` is an int other than 0), else 2
  decimals (about 1 km) with `"rounded"`; no position field when there is none. The
  browser cannot supply one (`CommunityIataBindRequest` has no `lat`/`lon`).
  `GET /radio/config` reports an untrusted or missing policy as `"off"`.
- **Automatic re-bind.** `note_radio_self_info()` compares `(public key,
  publishable_position)` with the last successful bind (in memory, so the first
  `self_info` after a start re-binds once); on a difference a worker waits
  `REBIND_DEBOUNCE_S` (5 s) and calls `rebind_current_iata()` under the lock (same
  IATA, no retire, no DB write; nothing when off). Failures retry after
  `REBIND_RETRY_DELAYS_S`, then every `REBIND_WAITING_RETRY_S` while waiting. Tested
  by `tests/test_observer_position.py`, `TestPositionRebindOff`.
- **Opt-out** (`PATCH {enabled: false, opt_out_reason?}`: `privacy`, `not_useful`,
  `technical_issue`, `other`). `_update_community_locked()` sets `_leaving`
  (`opt_out_in_progress()`): the guard answers None until the teardown ends, so the
  publisher neither reconnects nor toasts when Community kicks its session. Then
  `_announce_opt_out()` writes the pending record first
  (`app_settings.community_opt_out_pending`, migration 093: `{pubkey, reason,
  requested_at, iata}`) and sends `POST /v1/me/opt-out` within
  `OPT_OUT_LAST_CALL_TIMEOUT_S` (5 s). The `OptOutResult` is `confirmed` (any 2xx),
  `maybe` (timeout after the request left) or `failed`. `confirmed` empties the
  record and drops every pending status clear for the key
  (`community_status_clear.drop_pubkey`); `failed` arms the leftover clear; `maybe`
  arms nothing. A failure never blocks the local opt-out (fail closed). The record
  shows as `opt_out_pending` while off. Nothing retries automatically, even after
  a restart: `POST /community/opt-out/retry` (`retry_opt_out()`, up to
  `OPT_OUT_RETRY_TIMEOUT_S`, 20 s) is the only way. It answers 409
  `no_pending_opt_out`, 409 `opt_out_key_unknown`, 409 `opt_out_other_radio`, 503
  without a radio key, or the mapped Community error (record kept). Coming back on is
  a join, whose bind goes out even with an unchanged position, which lifts
  Community's refusal. Tested by `TestOptOutCall`.
- **Teardown.** `community_teardown()` runs in a `finally` through
  `_run_to_completion()` (a tracked task that holds the caller's cancellation), so a
  failed write, a reset that raises or a dropped request still gets all of it. Each
  step is bounded (`TEARDOWN_STEP_TIMEOUT_S`) and runs even if the previous one
  failed: cancel requests in flight, close Live (`close_stats()`,
  `CLOSE_TIMEOUT_S`), clear the retained status and stop the publisher, reset the
  breaker, the sample quota and the Live queue; it also calls
  `observer_position.forget_last_bind()`. The clear runs before the DISCONNECT
  (`BaseMqttPublisher._before_disconnect`): retained `offline`, then an empty
  retained payload, so a failed clear leaves `offline`, never `online`.

## Retained status left behind

- **Leftover clear** (`community_status_clear.py`). `_retire_status_and_reload()`
  saves the old topic (IATA, broker, audience, pubkey) in
  `app_settings.community_status_clear` (migration 088) before retiring. If the
  publisher acknowledged the empty payload (`retired_status_topic`) the entry goes;
  otherwise a worker retries. Each attempt holds `_update_lock`, opens one MQTT
  connection without a Will (JWT minted with the entry's IATA), publishes `b""`
  retained QoS 1 and disconnects, within `CLEAR_ATTEMPT_TIMEOUT_S` (15 s). At most
  `CLEAR_MAX_ATTEMPTS` (6), spaced by `CLEAR_RETRY_DELAYS_S` (1 min to 8 h), and none
  after `CLEAR_GIVE_UP_AFTER_S` (24 h). It waits for this radio's key without
  spending attempts. The lifespan resumes it (`start_status_clear_worker`) before
  the fanout starts. Tested by `TestRetainedStatusClear`.
- **Orphaned topic.** The publisher records the last retained topic
  (`note_published`, `app_settings.community_status_last`, migration 089). At startup
  `adopt_orphaned_status()` hands it to the same clear when Community is off or the
  IATA or broker differs (the recorded `broker_host` stays on purpose, so an old
  custom broker still gets one clear). Being on again on the same topic drops the
  entry (`drop_current`). Tested by `TestOrphanedStatusAtStartup`.

## IATA suggestion

`suggest_iata()` (`GET /community/iata/suggest`) runs only on the user's click;
nothing is ever assigned automatically. Without a position (`suggestion_position`,
always 2 decimals) it sends nothing and answers both codes null. Otherwise it calls
the public `GET /v1/iata/suggest` (`IATA_SUGGEST_TIMEOUT_SECONDS`, 5 s) through
`_iata_suggest_state`. Any failure or malformed answer falls back to the bundled
list (`nearest_airport_iata`, `source: "local"`). Tested by `TestIataSuggestion`.

## Ear position precision (`observer_reach.py`)

Only `exact` ear positions count in `max_distance_km` and the browser's mesh-test
distances; a `rounded` one still places the ear. A missing field reads `exact` on
`/v1/directory/packets/*` and `rounded` on `/v1/directory/observers` and `/reach`;
`null` and unknown values read `rounded`. `_fill_local_contact_coordinates` completes
ears from local adverts **by full 64-hex public key only** (never a prefix, never a
name): the ear becomes `exact`. An ear with no key match may be placed from the one
contact with its name, but stays `rounded` (map only, no distance). The browser
mirrors this (`placedObserverCoordinates`, `byName`, in `utils/meshTest.ts`).

## Banned key (`community_ban.py`)

Community answers 403 `code: banned` (`errors.md`) on the key's write routes
(`BANNED_REFUSED_ROUTES`: six routes, `PUT /v1/me/iata`,
`POST /v1/me/iata/override`, `PUT /v1/me/hashtags`, `POST /v1/hashtags/resolve`,
`POST /v1/hashtags/samples`, `POST /v1/directory/packets/observations`), on the Live
handshake, and refuses the key at MQTT auth. A retry cannot lift it, so the first
such answer (`note_banned()`, from `stats_request` or Live) starts an in-memory
state for `BANNED_HOLD_S` (12 h):

- `stats_request` refuses those six routes locally with 403 `community_banned`, after
  the egress guard.
- Live stops with state `banned` (`stop_for_ban()`, spawned by `_apply_ban()`) and
  opens nothing.
- The Stats publisher does not connect (`_pre_connect` waits
  `BANNED_RECHECK_INTERVAL_S`, 300 s) and shows no toast (`_error_toast_allowed`).
- Reads (`/v1/me/stats`, directory) and the opt-out keep working.

The broker refusal alone cannot be told from other auth failures. On a CONNACK
refusing the credentials (`is_auth_refusal`), the publisher's `_on_connection_error`
sends one probe, `probe_community_ban()`: `POST /v1/hashtags/resolve` with
`{"hash_bytes": []}`, which has no side effect. Not during an opt-out or when the ban
is known, and at most every `BAN_PROBE_MIN_INTERVAL_S` (1 h, `take_ban_probe()`).

The state ends on restart, on a user action (`PATCH /community`, a join or IATA bind,
`POST /community/live/restart`, the opt-out teardown) or after `BANNED_HOLD_S`, when
the next refused reconnect probes again and restarts it if the ban holds. The
re-bind worker treats the 403 like an opt-out (no retry). Tested by
`tests/test_community_ban.py`.

## Live relay (`community_live.py`)

- One process-wide upstream socket to `/v1/live/packets`, claimed under the relay
  lock. Sessions expire after `SESSION_TTL_S` (90 s) without a heartbeat; the
  upstream closes 1.5 s after the last one leaves.
- Frames go out as `community_packet` through `ws_manager.broadcast` to **every** WS
  client. The upstream reader feeds a queue of `FANOUT_QUEUE_MAX` (32, surplus
  dropped); per client, backlog at `CLIENT_COMMUNITY_PACKET_BACKLOG_MAX` (256,
  `app/websocket.py`) stops `community_packet` until it catches up, without evicting
  the client. Missed "heard by" bumps are reconciled by REST.
- States (`LiveState`): `connected`, `reconnecting`, `paused`, `opted_out` (also
  while waiting for a position), `idle`, `auth_rejected`, `banned`.
- Reconnect backoff 0.5 s doubling to 30 s, reset only after a socket stayed up
  `JWT_EXPIRY_MIN_UPTIME_S` (10 s). The JWT is reminted each attempt; a 4001 close
  reconnects at once. Sleeps are jittered by +/-20 % (`BACKOFF_JITTER`).
- **Feed paused** (`live_disabled`: 1013 close or handshake 503 with that code):
  state `paused`, retry after 30 s then 60 s (`PAUSED_RETRY_MIN_S`/`_MAX_S`). Any
  socket that opens, any other handshake answer, an opt-out and `close_stats()` clear
  it. A 503 with another code is a 4004.
- **Handshake 401** is not a 4001: retry at 2 s doubling, give up after
  `AUTH_REJECT_MAX_ATTEMPTS` (5), or at once for `AUTH_FATAL_CODES`. State
  `auth_rejected`, `auth_error` = `clock_skew` or `token_rejected`.
- Other refusals: 409 maps to 4005, 429 and 503 to 4004, any other status (403 not
  `banned` included) has no close code; all reconnect with the normal backoff. A
  `banned` 403 stops the reader. 4002 is reserved and never sent; if it arrives the
  reader stops. 4003 to 4005 are never shown (`CommunityLiveStatus` hides them).
  `POST /community/live/restart` clears 4002, a given-up 401 and a ban.
- **Region filter.** `POST /community/live/subscribe` takes `iata` (at most
  `LIVE_IATA_SELECTION_MAX` = 64 codes, in `models.py`; placeholders `XXX`, `LOC`,
  `AAA` give 422; empty means every region), on the first call and every
  heartbeat. The socket asks for `?iata=` = union of the sessions' regions **plus the
  radio's IATA**, sorted; one session for every region, or more than
  `UPSTREAM_IATA_MAX` (16) codes, means the global feed. After a union change a check
  runs `FILTER_DEBOUNCE_S` (2 s) later and, if the open socket asked for something
  else, reopens with the same `X-Live-Instance` (guard checked before and after the
  close). A handshake 400 (`live_bad_filter`) switches to the global feed for
  `FILTER_RETRY_S` (1 h); `restart` and `PATCH /community` clear that. Frames still
  reach every client and the browser filters again (`docs/dev/frontend-community.md`,
  "#live"). `CommunityLiveStatus.upstream_iata` reports the open filter (null =
  global).

## Stats publisher (`system:meshloom-stats`)

`MeshloomStatsModule` (`app/fanout/meshloom_stats.py`) uploads raw packets to the
Stats broker. `sync_system_modules()` (serialised) starts it from
`community_enrolled_state()`, not from `fanout_configs`; it is hidden from
`/api/fanout` and `PATCH`/`DELETE` on any `system:*` id answer 403. It can run next
to a user `mqtt_community` row.

- While waiting, `_status_extra()` adds `state: waiting_for_position` to the retained
  `online` payload and `on_raw` publishes no packet. `_status_extra()` returns `{}`
  for user `mqtt_community` rows.
- Every clean `stop()` publishes the Will's retained `offline` first; after
  `retire_system_status()` it also publishes the empty payload.
- Hooks on `BaseMqttPublisher`: `_pre_connect` (re-checks the enrolled guard and the
  ban before every reconnect), `_on_connection_error` (ban probe),
  `_error_toast_allowed` (false during an opt-out or a ban), `_on_status_retained`
  (records the topic for the orphan clear).
