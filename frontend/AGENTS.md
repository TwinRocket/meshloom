# Frontend (`frontend/`)

Frontend-only rules. Commands, the quality gate and cross-layer rules are in the
root `AGENTS.md`. This file is a hypothesis: check it against `src/` before you
rely on it. Community and `#live` detail: `docs/dev/frontend-community.md`.

## Stack pitfalls

- `@michaelhart/meshcore-decoder` is an npm alias of
  `meshcore-decoder-multibyte-patch@0.3.0`. Keep packet parsing consistent with
  backend `app/path_utils.py`.
- Two map stacks on purpose: `maplibre-gl` + `@deck.gl/*` for `#live`
  (`components/live/`), Leaflet / react-leaflet for the node map, mini-maps and pickers.
- `vite.config.ts` sets `server.watch.usePolling` so the dev server sees edits
  on WSL and Docker bind mounts. Its `/api` proxy keeps `changeOrigin: false`:
  the backend refuses a WebSocket whose `Origin` does not match `Host`, and
  `rewriteWsOrigin` would defeat that check.
- `npm run build` type-checks only what `src/main.tsx` reaches; `npm run typecheck`
  checks all of `src/` (root `AGENTS.md`, "Deployments that never delete files").

## Invariants that are easy to break

### Packet stream stays out of React ancestors

`stores/rawPacketStore.ts` is read through `useSyncExternalStore`
(`useRawPackets()`, `useRawPacketStatsSession()`). Only leaf views subscribe:
`RawPacketFeedView`, `VisualizerView`, `LiveView`, `CrackerPanel`. `FeedView` reads `feedStore`, which holds its own copy of the control
packets (see below), but `useRawPackets()` only through the activation seed. **No ancestor
of `MessageList` (`App`, `AppShell`,
`ConversationPane`) may subscribe**, or every packet re-renders the whole tree.
`src/test/appPacketIsolation.test.tsx` enforces this.

`stores/feedStore.ts` (the global message stream, sorted by `(received_at, id)`,
capped at `FEED_MAX_ROWS` = 2000, oldest dropped) follows the same rule: only the
feed view reads `useFeedRows()`, no ancestor of `MessageList` may subscribe
(`src/test/appFeedIsolation.test.tsx`). Two further rules:

- **Lazy attach.** Until `activateFeedStore()` (first `FeedView` mount) every
  `recordFeedMessage` / `applyFeedAck` / ... call is a flag check and stores
  nothing. `useRealtimeAppState` calls the store from `onMessage` (after the blocked
  check), `onMessageAcked`, `onMessageDeleted` and `onContactResolved`.
- **Control rows.** `recordFeedControlPacket(packet)` is called by `onRawPacket` right
  after `recordRawPacket`. Active store only (one flag check otherwise); the header byte
  and the server's `payload_type` label select Request / Response / AnonRequest /
  GroupData without decoding, and only those go through the decode cache of
  `utils/controlJournal.ts` (`isControlJournalPacket`), so an observation is decoded once.
  Rows are deduped by `getRawPacketObservationKey`, ordered by `(timestamp, key)` and
  bounded by `FEED_CONTROL_MAX` = 500 **control** packets (the oldest go). They seed from
  `rawPacketStore` at `activateFeedStore()`. `onReconnect` clears `rawPacketStore` and
  **not** these rows; `clearFeedRows()` (a server-filter reload) does not either, only
  `resetFeedStore()` does.
- **Control history.** Control rows are not persisted, so `loadControlHistory('open')`
  (`components/feed/feedHistory.ts`) replays the stored ones once per session, when the first
  message page is in and the Control source is on: one `GET /api/packets/history` with
  `payload_type=Request,Response,AnonRequest,GroupData` (the route takes a comma-separated list,
  any type matches), `limit=200`, `max_scan=20000`. `mergeControlHistory` marks them `replayed`
  (no RSSI/SNR, `observation_id` dropped: one row per packet), skips a database `id` a live row
  already holds, and only fills the room left under `FEED_CONTROL_MAX`, so it never evicts a live row.
  `snapshot.controlHistory` carries `truncated` / `scanned` (the scan-limit hint), the `after_id`
  cursor of the **Load earlier control packets** button (`'more'`) and `exhausted`. A 403 ends it
  silently. The purge `raw_packet_retention_days` removes undecrypted packets (third-party
  Request/AnonRequest), so they may be missing. `resetFeedStore()` clears it.
- **Row memoisation.** `FeedMessageRow` is `memo`, so every prop must keep its identity across
  renders: `labels` comes from `createFeedNameResolver`, which caches per row object, and `time`
  from the per-language cache of `createTimeFormatter`. Do not build a fresh object inline in the
  row's call site (`docs/dev/feed.md`, `feedPerf.test.tsx` guard it).
- **Event rows.** `recordFeedEvent(event)` (active store only; returns `false` otherwise) holds the
  session-only rows of `utils/feedEvents.ts`, sorted by `(at, key)`, deduped by `key`, bounded by
  `FEED_EVENT_MAX` = 200. Like control rows they survive `onReconnect`, `clearFeedRows()` and
  `resetFeedForRefetch()`; only `resetFeedStore()` empties them. Nothing is persisted or fetched.
- **Batched emits.** Mutations edit a private working array; one `queueMicrotask`
  publishes a new immutable snapshot and notifies once, so a burst (historical
  decryption) is a single render. Read the snapshot with `getFeedSnapshot()` after
  a microtask, not right after the call.

### Feed

`#feed` (`components/feed/FeedView.tsx`, `lazy()` behind `LazyBoundary`, not in the
entry chunk) is the only subscriber of `feedStore`. `#control` is not a view of its own: it parses as
`{type: 'feed', feedPreset: 'control'}` and `FeedView` opens with `controlPreset`
(`utils/feedFilters.ts`: only the Control source) through its `preset` prop, which also
opens the Nodes facet sheet on a phone (`FeedNodesFacet` `defaultOpen`; a column on `md`+),
keyed on the preset so switching remounts it. A preset's filters are never written to
`meshloom-feed-filters`. `getConversationHash` emits `#control` for it (never rewritten
to `#feed`); `toolIdOf()` (`feedConversation.ts`) maps it back to the `control` tool so the
rail and the Tools list light the right entry. `RailItemId` `'control'` stays registered
(Settings → Navigation, stored rails) but is not in `DEFAULT_RAIL`. A stored
`{type: 'control'}` (4.18.x `localStorage`) is read as the preset in
`lastViewedConversation.ts`; `ConversationType` no longer has `'control'`.

- **Pagination.** On mount: `activateFeedStore()`, then `loadFeedPage(null)`
  (`GET /api/messages?limit=200`, `FEED_PAGE_SIZE`). Scrolling within ~300 px of the
  top calls `loadFeedPage({received_at, id})` of the oldest held row, sent as
  `before` / `before_id`; that pair is how the server orders and cuts, so no row is
  skipped or repeated. A page shorter than the limit sets `hasOlder=false`
  ("beginning of history"). A re-mount refetches the newest page but never revives
  `hasOlder` on a store that already reached the beginning.
- **Follow mode.** Pinned means within `FOLLOW_THRESHOLD_PX` of the bottom. While
  pinned, the view re-pins on every new row and for a bounded number of frames as
  heights settle. A scroll up turns it off and records the highest id held; the
  Follow button counts rows with a larger id that are not `historical`
  (`feedFollow.ts`). Scrolling back to the bottom re-pins without the button.
- **DOM cap.** `FEED_DOM_MAX` (300) rows are mounted at most, whatever the memory
  window (`FEED_MAX_ROWS`, 2000) holds. The virtualizer follows the `MessageList`
  invariants below (`scrollMargin`, string sentinel keys, zero-height fallback).
  A prepended page keeps the reader's position by the growth of `scrollHeight`.
- **Navigation type.** `'feed'` is in `ConversationType` (`types.ts`), with the optional
  `feedPreset?: 'control'` on `Conversation` (the Control page is that preset). `components/feed/feedConversation.ts` holds
  `isFeedConversation()` and `makeFeedConversation()`.
- **Filters: one evaluator.** `utils/feedFilters.ts` holds `FeedFilters`, the defaults
  (muted and pending hidden, DMs shown) and `rowPassesFilters(row, filters, channels,
nowSec)`, the only place that decides whether a row shows: REST rows and WebSocket rows
  alike, so add a filter there and nowhere else. `isChannelMuted` mirrors the backend's
  `effective_channel_muted` (`muted && (muted_until == null || muted_until > now)`); the
  view re-reads the clock once a minute (an expired timed mute reappears) and the
  `channels` prop changes on every `channel` frame, so both re-evaluate in place. A
  `CHAN` row of a deleted channel and every `PRIV` row ignore mute and pending.
  `src/test/fixtures/feedFilters.json` drives the evaluator in `feedFilters.test.ts` and,
  for the `server` cases, `tests/test_messages_feed_filters.py` against the route.
- **Which filters are server-side, and why.** Only `type` (Channels/Direct messages) and
  `outgoing` (Received/Sent) are sent to `GET /api/messages` (`filtersToQuery`), so a
  hidden kind is not fetched at all. Changing one empties the store (`clearFeedRows`:
  its cursor belongs to the old query) and reloads the first page after a 250 ms
  debounce; both chips of a pair off is an empty state and sends nothing. Muted and
  pending stay client-side (a timed mute changes with the clock, and a server-side
  version needs the NULL-safe join of T22). The price is hollow pages:
  `fillHiddenPages` fetches the next page while fewer than `FEED_MIN_VISIBLE` (20) rows
  show, at most `FEED_MAX_PAGES_PER_GESTURE` (5) pages per gesture (mount, filter
  change, scroll to the top, ticking Muted/Pending).
- **Conversation facets.** `FeedFilters.conversations` (ids `chan:<key>` / `priv:<key>`, lowercase,
  `conversationFacetId`) is evaluated in `rowPassesFilters` like the other client-side filters, so
  hollow-page auto-fetch covers it (it is part of `clientFilterKey`). `components/feed/FeedFacets.tsx`
  shows the Channels and Contacts sections (right column from `md`, "Filters (n)" sheet on a phone;
  native checkboxes) from `countConversations`, which counts the loaded rows the other filters keep
  and ignores the pick itself. A picked conversation absent from the window stays listed with 0. Names
  come from `createFeedNameResolver`. Not in the URL grammar (`encodeFeedQuery` ignores it), cleared by
  `displayFilters`, not written for a preset. Control and event rows ignore it. Numbers go through
  `formatNumber`, never `toLocaleString()`.
- **Quick text filter and mentions.** `FeedFacets` holds a search field (debounced 200 ms,
  `FEED_TEXT_DEBOUNCE_MS`); `FeedView` keeps the text in state only (not stored, not in the URL,
  ignored in display mode) and narrows `visibleRows` through `utils/feedTextFilter.ts`: NFKC +
  `toLocaleLowerCase` substring over conversation name, sender and body; a structured body
  (`isStructuredBody`: Open/One reactions, GIF, location) never matches by its body, so `r:` hits
  no reaction. While a query is on, control and event rows are hidden. No server round-trip; the
  "Search the whole history" link calls `onSearchHistory(query)` (`handleOpenSearchWithQuery`
  through `ConversationPane`) with the field untouched. `FeedMessageRow` marks a non-outgoing row
  where `messageContainsMention(content, radioName)` (the app's `@[name]` rule) holds:
  `data-mention`, an accent left border and an `aria-label`.
- **Storage.** `localStorage` `meshloom-feed-filters` (the prefix matters under HA ingress,
  where `localStorage` is on HA's origin). Read and written in `try/catch`; absent,
  corrupted or blocked storage means the defaults, silently. Two tabs have two stores and
  one `localStorage`: a filter changed in one tab reaches the other on reload only.
- **Rows.** `FeedMessageRow` (memoized) shows the name, bidi-isolated sender with the
  abbreviated key as `title`, time on `received_at`, `RegionBadge`, a _sent_ marker, and
  the body through `MessageBody` (`remoteMedia={false}` for a pending channel; Open
  reactions whose target is not loaded render as `ReactionPayload`). Names
  (`feedNames.ts`): channel/contact by key from the client's lists, then the WebSocket
  frame's `channel_name` / `sender_name`, then the abbreviated key. Day separators
  (`feedDays.ts`) are virtualized items of their own; labels use `Intl` only.
- **Row actions** (`FeedMessageRow` is the list item). Click or Enter on the row calls
  `onNavigateToMessage` (the search payload; `targetMessageId` highlights it); the
  channel/contact name calls `onSelectConversation` (bottom of the conversation);
  the sender is a button for `feedContactKey(row)` (full 64-hex `sender_key`, or the
  PRIV `conversation_key`) and plain text otherwise. `FeedRowMenu` (`...`, long press,
  right click, ContextMenu / Shift+F10) mirrors `MessageActionMenu`; _Copy text_ is
  absent when `navigator.clipboard` is undefined (HTTP on the LAN). A view rendered
  without the three callbacks is read-only. No reply, react or resend. Tests:
  `src/test/feedRowActions.test.tsx`.
- **Capabilities, not roles.** `FeedView` reads `useCapabilities().canReadPrivate` (`read_private`),
  never a role name. Without it: no Direct messages chip (`FeedFilterChips` `showDirect`), filters
  loaded through `withoutPrivate` (URL `types=priv` and a stored `priv` are overridden in memory;
  the stored `priv` is written back as it was, and a mount alone writes nothing), so `filtersToQuery`
  sends `type=CHAN`; `feedStore` drops a `PRIV` row on insert (`setFeedAcceptsPrivate`); a 403 on the
  first page is `feed.forbidden` in the empty state, not the error panel. Control, muted and pending
  stay as the server returns them. The feed implements no access rule. Tests:
  `feedView.test.tsx` ("without read_private").
- **Live merge.** A live row goes through `recordFeedMessage`, sorted by `(received_at, id)`
  wherever it lands (a `historical` row sits in the middle and is never counted as new). A row
  older than the first held row while `hasOlder` is true is not stored: as the new oldest row
  it would hide the gap from the `before` cursor; the page that covers it returns it, and
  `maxKnownId()` (a high-water mark, so also those rows) keeps reconciliation from asking
  again. Acks, deletions and `contact_resolved` renames edit rows in place (`applyFeedAck`,
  `removeFeedMessage`, `renameFeedConversation`); channel renames reach the view as the
  `channels` prop. No scroll jump: the view remembers the item at the top of the viewport
  (`feedScroll.ts`, `pickAnchor`) and after each change moves `scrollTop` by
  `scrollShiftForAnchor` (not while following). A new outgoing row re-pins a reader who scrolled
  up, a historical one never does.
- **Reconnect policy.** `reconcileFeed()` (`feedHistory.ts`, store level: runs with the view
  unmounted, no-op until the feed was opened) fetches
  `GET /api/messages?since_id=<maxKnownId>&limit=1000` with the `type`/`outgoing` of the last
  first-page load, on `onReconnect` and on a `success` toast with code
  `historical_decrypt_complete` (or `_rooms`), merging with `mergeReconciledRows` and repeating
  while a page is full, at most 5 pages; then `resetFeedForRefetch()` empties the store and
  bumps `epoch`, which makes the view fetch the newest page. Overlapping calls run once more
  after the current one; failures are logged and retried at the next trigger. An answer to a
  query that changed meanwhile (compared by value) is dropped. Not caught up: deletions and
  acks made while the socket was down (reload the page). Control rows are outside this
  policy: they come from the live packet stream, are kept
  across the reconnect, and are neither reconciled nor emptied by `resetFeedForRefetch()`.
- **Phone layout.** The two chip groups are single swipeable lines below `md` (wrapped from `md`).
  The text input is 16 px below `md` (iOS zooms smaller ones). `FeedFacets` sections are
  `min-w-0` (a `fieldset` otherwise refuses to shrink and pushes the counts out). The Follow
  button carries `data-above-bottom-nav` so `styles.css` lifts it over the bottom bar. Checklist:
  `docs/dev/feed.md`.
- **Display mode** (`#feed?display=1`, the Display button in the header; `feed/feedDisplay.ts`,
  `FeedDisplayBar.tsx`, slice `ca`). A layer over the reader's filters, never an edit:
  `displayFilters(filters)` forces channels only and no node, and `FeedView` evaluates and queries `shown`
  (so the first request carries `type=CHAN`, whatever `types=` the URL carries), while `filters` stays what the
  chips, `localStorage` and Exit use. Nothing in the mode re-enables DMs or remote media (`remoteMedia={false}` on
  `FeedMessageRow`); the bar holds a clock and Exit only. The rich-payload preference and the saved font scale are
  never written. `useDisplayChrome` sets `data-feed-display` on `<html>` (`styles.css` hides the rail, bottom bar and
  banners, strips blur and shadows; platform variants only there under `[data-platform]`) and raises the font size by
  `DISPLAY_FONT_FACTOR` (1.25) over the saved scale. Follow is forced; rows get no actions and `readOnly` (no
  `tabIndex`). The URL in display mode is the explicit display state (`types=chan`), so a reload opens the same
  screen. Fullscreen is requested on the button click only and leaves silently when unavailable. It hides DMs from
  the screen, it does not protect them (the WebSocket still delivers them; the `kiosk` role locks). Tests:
  `feedView.test.tsx` ("display mode").
- **Last-visit mark.** `localStorage` `meshloom-feed-last-seen` = `{at, id}` of the newest row
  the reader has reached: the last row when following, otherwise the last row at or above the
  bottom edge of the viewport (rows that arrived below it, never scrolled to, stay "new").
  Written on `visibilitychange` to hidden and on unmount (`feedLastSeen.ts`; storage errors are
  ignored). On open the mark is read once; when the first page is in, a "New since your last
  visit" item (`feedDays.ts`, kind `new`) precedes the first non-historical row strictly after
  it. Absent without a mark or when nothing is newer, and it does not move for later live rows.
  If the mark is older than the first 200-row page, the separator lands on the first loaded row.
- **Control source.** `Sources > Control` (on by default) interleaves
  `FeedControlRow`s with the messages: `buildFeedItems(messages, controls)` merges the two
  sorted lists by `received_at` / `packet.timestamp` (a message first on a tie).
  `buildJournalEntries` (incremental, `utils/controlJournal.ts`) turns the held packets
  into entries (classification, pairing, never decrypting third-party Request / Anon
  payloads); `controlParts.tsx` holds the labels (`ControlJournalView` was removed with the Control preset). A row
  click opens `RawPacketInspectorDialog`; the peers open the contact sheet; the GroupData
  channel opens the conversation; the "Paired" badge scrolls to the twin (a plain badge
  when the twin is filtered out). The follow counter counts control rows by the browser
  receipt time (`received_at_ms`), messages by id.
- **Event source.** `Sources > Events` (on by default, owner Q2) interleaves `FeedEventRow`s
  (`feedDays.ts`, kind `event`, merged after messages and control entries on a tie). **The ids come
  from Notifications**: `utils/feedEvents.ts` is the only module of the feed that knows them
  (`NOTIFICATION_EVENT_IDS` = `radio.offline`, `radio.online`, `contact.first_seen`, `channel.found`,
  all of `app/notifications/events.py` `EVENT_IDS`; `FEED_ONLY_EVENT_IDS` = `contact.renamed`, which
  Notifications does not have), with their delays. `tests/test_feed_event_ids.py` fails when
  an id leaves the catalogue or `GET /api/notifications/events` stops serving it. **Two sources, one
  memory** (MSHL-166): `noteNotificationEvent` (`onNotificationEvent`) reads the `notification_event`
  frame for those four ids only and ignores every other id and any `PRIV` frame, so it can never
  make a row from a conversation whatever the role (the server already filters the frame per
  socket); the client reducers below stay for what the dispatcher does not frame (an event no
  enabled channel routes, `rules.emit_ws_events` off, `contact.renamed`). Both share the outage
  state, `seenContacts` and the row keys, so whichever reports first records the row and the
  other adds nothing: a `radio.offline` frame only ends an outage this page saw start (the
  server's grace instead of the session's), a `radio.online` frame only follows a recorded
  `offline`, a `contact.first_seen` frame is skipped for a key already held. Producers are store
  calls from the `useRealtimeAppState` handlers, no React state: `noteRadioHealth` (`onHealth`: `disconnected`/`connecting` arm a
  grace timer (`setRadioOfflineGraceS`: `rules.radio_offline_grace_s` read by `FeedView` for an administrator, the only role that may read the rules; 60 s default for everyone else), stamped at the
  outage start; `connected` before it fires clears it; `rebooting`, `paused` and the identity states end
  the outage without a row, so `EXPECTED_REBOOT_GRACE_SECONDS` reboots stay silent; `radio.online`
  follows only an outage whose row was recorded; the first frame is a baseline, so an outage already running at page load is not reported),
  `noteContactFrame` (`onContact`: a `contact` frame is a snapshot, so only a key neither the module
  nor `contactsRef` (null until the first load, set in `App`) knew is `contact.first_seen` (not type 0/room, like `app/push/first_seen.py`; `noteLocalContact` marks a contact this tab creates by hand), keyed on
  key + `at` (`first_seen`, the frame's `occurred_at`), and only a changed non-empty name is `contact.renamed`; **ignored while
  `activeSnapshotsRef` is non-empty**, i.e. a post-reconnect resync, but remembered), `forgetContact`
  (`onContactDeleted`) and `noteChannelFrame` (`onChannel`: a pending channel the client did not hold).
  `at` is on the server clock, as messages are: browser time minus `getServerClockOffsetSec()` ("Clocks"),
  `contact.first_seen` uses the frame's own stamp; `atMs` (browser) feeds the follow counter. `describeFeedEvent` resolves names through one `indexContacts` map per change of `contacts`. Rows
  follow the capabilities, not a role: the contact sheet always, the Discovered link with `canSend`
  and a channel still pending, the radio link with `isAdmin`; display mode renders them read-only. A
  picked node narrows events to its thread (`eventThreadId`: `us`, `contact:<key>`, `channel:<key>`).
  The chips line says events are kept for this session only. Copy: slice `cr` (`feed.events.*`).
  Tests: `feedEvents.test.ts` (fake timers), `feedEventsFrames.test.ts` (frame vs reducer parity on
  the backend's fixtures, rows and sentences in en and fr; no row from a conversation frame),
  `feedView.test.tsx` ("event rows"),
  `useRealtimeAppState.test.ts`, `feedDaysEvents.test.ts`, `appFeedIsolation.test.tsx`.
- **Nodes facet.** `FeedNodesFacet` (a `md:w-64` column, a sheet on a phone) lists the
  control threads (`classifyThread`: repeaters and rooms, channels, us, unresolved) with
  counts. The pick is `FeedFilters.node` (a thread id, per browser, in the same
  `localStorage` key). A node that no longer exists, or any node while the Control chip
  is off (the facet is hidden), filters nothing: a persisted pick is never a hidden
  filter. An active node shows as the pressed node of the column and as a clear button
  beside the phone facet button. A node narrows the
  control rows to its thread; a channel node narrows the messages to that channel, any
  other node hides the messages (`rowPassesFilters`, client-side only). With Messages off
  and Control on no request is sent (`hasNoMessages`); `hasEmptySelection` is true only
  when nothing can show (no message, control or event source).
- jsdom measures no layout: `src/test/feedView.test.tsx` fakes the scroller geometry.
  Virtualization and the single scroller need a check in a real browser.

`ControlJournalView` keeps its pure helpers in `utils/controlJournal.ts`. Each
observation is decoded once: a decode cache keyed by `getRawPacketObservationKey`
(re-decoded only if the bytes behind a key change) feeds both the filter and
`buildJournalEntries`, which is incremental (new observations only; a change of
contacts, channels or public key rebuilds entries from the cache, not the
decoder; request/response pairing is recomputed each time). `filterControlJournalPackets` drops the decode of keys that left the live buffer; the
feed forgets the decode of a control row it drops (`forgetControlDecode`).
It is a sibling of `utils/rawPacketDerivedCache.ts` (not reused: the journal needs
the decoded object, not that cache's summary fields) with the same eviction rule.

### Clocks

The server `packet.timestamp` is the server wall clock and can be minutes off (a
host without NTP). `recordRawPacket` adds a client-only `received_at_ms` (browser
`Date.now()`) to every live packet. Anything compared with `Date.now()`
(visualizer pruning, `#raw` stats windows, Live laser aging) must use
`received_at_ms`, falling back to `timestamp` only for history-replayed packets,
which are historical on purpose. Displaying a packet's absolute time keeps using
`timestamp`. A server behind by more than the prune delay once wiped every
visualizer node within a second; `src/test/clockSkewPrune.test.ts` covers it.
The reverse direction applies to bounds sent to the server: the `#raw` history
replay presets ("last 1h/24h/7d") are filtered on the server `timestamp`, so
`utils/rawPacketHistoryBounds.ts` shifts `since` by the skew measured on buffered
live packets (`received_at_ms` minus `timestamp`, read with
`getServerClockOffsetSec()` from `stores/rawPacketStore.ts`, 0 when nothing is
buffered) and sends no `until`. The modal recomputes the bounds on every open.
Other server timestamps compared with `Date.now()`:

- A message's `received_at` (server clock) drives the observer reach windows
  (`useVisibleObserverReach`: live ticks only while young, 2 s/60 s polling). Ages
  go through `messageAgeMs(msg, now, getServerClockOffsetSec())`.
- The mesh test listen window is timed from `startedAtMs`, the browser time taken
  just before the send request, not from the server `sent_at`
  (`meshTestAgeMs` in `utils/meshTest.ts`).
- The 30 s byte-perfect channel resend window is decided by the browser clock
  (owner decision 2026-10-17; `utils/resendWindow.ts`). A message is anchored on
  the earliest browser time this tab saw it live (`noteLiveOutgoingChannelMessage`:
  the WS `message` event, or after a send the `sendAnchorMs` anchor, i.e. the
  server stamp converted with the measured skew and bounded by request start and
  response, else the response time; the server stamps once it holds the radio
  lock, not on arrival), else on `sender_timestamp + offset` with the offset from
  `getMeasuredServerClockOffsetSec()` (null when no live packet is buffered).
  `MessageList` times the Resend button with `resendAge`. Only an authoritative
  age (live sighting or measured skew) is sent as `client_age_seconds`; with an
  unknown skew none is sent and the backend checks its own clock, and a stamp more
  than 2 s in the future hides the button. Expiry errors are coded
  (`errors.resend_window_expired`, `errors.resend_window_expired_server`).

### Lazy loading

Heavy surfaces (settings, cracker, search, info sheets, maps, Live, visualizer,
repeater dashboard, raw feed) are loaded with `lazy()`. If an always-loaded module
imports `recharts`, `leaflet`/`react-leaflet`, `qrcode.react` or
`@simplewebauthn/browser` statically, they end up back in the entry chunk. The WebAuthn
library is imported dynamically in `auth/passkeys.ts` and nowhere else (the login page
decides whether to show its button with `passkeysSupported()`, the same test as the library's
`browserSupportsWebAuthn()`, without loading it). `vite.config.ts` defines no manual vendor chunks on
purpose.

### Session gate (`src/auth/`)

- `main.tsx` wraps `App` in `SessionProvider`, which reads `GET /api/auth/session`
  before anything else mounts, so nothing loads before the server knows who calls.
  `setup_required` renders `SetupPage` (the installation-code field only when
  `setup_code_required`, with where to find it per install kind; on the kiosk screen,
  `kiosk_setup_code` set, it re-reads the session every 5 s and opens the app once an
  administrator exists, the screen's own `kiosk` session coming from the launcher's device
  token, `docs/dev/auth.md` "Kiosk device token"), an anonymous
  visit renders `LoginPage` (warns when `!window.isSecureContext`), and a signed-in
  answer (including Basic credentials that already authenticate) renders the app
  with no wall. `useSession()` gives `user`, `role`, `via`, `signOut` (a null user
  outside a provider, so tests of other components need no provider). Auth types
  live in `auth/types.ts`, not `types.ts`.
- **401 handling.** `fetchJson` publishes a 401 on any call except `/auth/login` and
  `/auth/setup` through `auth/authEvents.ts`. `auth_setup_required` re-reads the
  session (back to the setup page); any other code mounts `ReloginDialog` over the
  still-mounted app, so navigation state and drafts survive, and the WebSocket gets
  `AUTH_RESTORED_EVENT` to reconnect at once. Signing in as another user reloads
  the page instead. A tab returning to the foreground re-asks the session.
- **Second factor.** `LoginForm` (also inside `ReloginDialog`) swaps itself for
  `SecondFactorStep` when `POST /auth/login` answers `second_factor`; the step keeps the
  pending token in memory only, sends a TOTP or recovery code to `POST /auth/login/2fa`
  (listed in `AUTH_FORM_PATHS`, so its 401 is a form error) and goes back to the password
  on `auth_2fa_pending_invalid` or after `expires_in` seconds. `user.second_factor_enrolment_required`
  (or a 403 `auth_2fa_enrolment_required`, `onEnrolmentRequired`) makes `SessionProvider`
  render `EnrolmentRequiredPage` instead of the app. Enrolment (`TotpEnrolment`, QR drawn
  with `qrcode.react` from the `otpauth://` URI, never a remote service) and
  `RecoveryCodesView` (codes shown once, acknowledgement before "Done") are shared by that
  page and Settings → Users & Access → `AccessSecurityPanel` (hidden reason under ingress
  or without a password). i18n slice `bo`.
- **Passkeys.** `auth/passkeys.ts` is the only importer of `@simplewebauthn/browser` (dynamic
  `import()`, its own chunk; never import it statically). `LoginForm` shows "Sign in with a
  passkey" only when `passkey` is true (`passkeyLoginAvailable(session)`: the server's
  `methods.passkey` and `passkeysSupported()`), which `LoginPage` passes and `ReloginDialog` does
  not; `SecondFactorStep` shows "Use a passkey" when the challenge lists `passkey`. The two
  `/auth/webauthn/login/*` paths are in `AUTH_FORM_PATHS` (their 401 is a form error).
  `passkeyCeremonyErrorKey` maps the library's errors (and the DOMException under `cause`) to
  `auth.passkey.errors.*`; a cancelled prompt and a timeout are both `NotAllowedError`.
  Settings → Users & Access → `AccessPasskeysPanel` lists, adds (password, plus a code when
  TOTP is on; it warns that a recovery code is spent even if the prompt is abandoned) and removes
  (password) passkeys. i18n slice `bq`.
- **429.** `ApiError.retryAfter` comes from `Retry-After` or `params.retry_after`;
  `formatApiError` passes it as `{{retry_after}}` to `errors.auth_rate_limited`.
- **Ingress.** Home Assistant signs ingress users in on every request (`via: ingress`,
  no cookie): the app mounts straight away. An anonymous answer whose `methods` say
  ingress only (`ingressOnly()`, `auth/types.ts`) renders `IngressSignInPage` instead
  of the login or setup form (password login and setup answer 403 `auth_ingress_only`
  there). Under ingress a 401 or a lost session on return to the foreground re-reads
  the session instead of opening `ReloginDialog` (`docs/dev/auth.md`, "Ingress").
- **Layout and prefetch.** `AuthLayout` scrolls itself (`h-dvh overflow-y-auto`, card
  `m-auto`): `body` is `overflow:hidden`. `index.html` starts its API prefetch only after
  `/api/auth/session` says authenticated, so the gate pages make no 401 requests. It
  prefetches the radio config for an `admin` only, and the app settings from `/api/settings`
  (admin) or `/api/settings/view` (every other role).
- **Capability gating (the rule).** `auth/capabilities.ts` mirrors `ROLE_PERMISSIONS` of
  `app/auth/permissions.py` (the GET `/auth/session` answer carries the role, and a cookie
  session has no token scope, so the role is enough); `useCapabilities()` returns `canSend`,
  `canReadPrivate`, `canAdministerRepeaters`, `isAdmin`, `canOpenSettings`,
  `canOpenSettingsSection(section)`. Outside a provider (tests, previews) it is
  **unrestricted**, so a component test needs no session. A surface the role cannot use is
  hidden (a whole screen, a tool, a button) or disabled (a control inside a shared pane);
  it is never left to fail. **This protects nothing**: the server refuses each request and
  stays the authority, so never skip a server check because the UI hides the control, and a
  new admin-only route needs its UI gated here in the same change. The `access` section
  (Users & Access: `components/settings/SettingsAccessSection.tsx`, lazy-loaded from
  `SettingsModal`) is in `ACCOUNT_SETTINGS_SECTIONS` because its profile and sessions
  panels use `own_account` routes; its users, session lifetime (`AccessSessionLifetimePanel`, `session_lifetime_days`, 1 to 365,
  strings `settings.access.sessionLifetime.*` in slice `az`) and Home Assistant default role
  panels render for `isAdmin` only, as does the audit log (`AccessAuditPanel`: cursor pages of
  `GET /auth/audit`, filters applied client-side to the loaded rows, labels from
  `auditShared.ts`; rows virtualised with `@tanstack/react-virtual` in a fixed-height
  scroller; `details` is never shown). Errors go through `auth/accessErrors.ts` (`formatAccessError`: coded
  `errors.auth_*`, the policy detail, and a generic line for an uncoded 429/503). Strings:
  slice `az` (`settings.access.*`, `settingsNav.access`), token strings in slice `bm`
  (`settings.access.tokens.*`), audit strings in slice `ba`
  (`settings.access.audit.*`), trusted networks and the Access security notice in slice
  `bc` (`settings.access.trustedNetworks.*`, `settings.access.securityNotice.*`,
  `errors.auth_trusted_*`). `AccessTrustedNetworksPanel` edits the rules (a strong
  warning when one maps to an administrator, a live warning under a rule typed as a public or
  /8-or-wider network via `lib/cidr.ts`, mirror of the server's `public_or_wide`, and a notice
  on a rule whose account is disabled or must change its password); the notice banner at the top of the
  section shows the server's `security_notices` and nothing else. `AccessPairingPanel` (admin only; `api.createAuthPairing`/`listAuthPairings`/`revokeAuthPairing`, slice `bv`, `settings.access.pairing.*`) opens a dialog that creates a pairing code and draws its `qr_payload` with `PairingQr.tsx` (the only `qrcode.react` importer on that path, `lazy()`), with a countdown built from `expires_at - created_at` on the local clock (never the server's) that hides the QR at zero; the code exists only in that dialog, the list holds none; closing revokes a pending code (and one whose creation answers after the close), expiry or use (the dialog polls the pending list every 5 s, or a 404 on cancel) drops the code and QR text from state. Test: `src/test/accessPairingPanel.test.tsx`. `AccessTokensPanel` (`api.createAuthToken`/`listAuthTokens`/`revokeAuthToken`) is for `member` and `admin` only (`TOKEN_ROLES`), offers the role's own scopes (`tokenScopesForRole`), shows the secret once in a dialog that Escape and outside clicks cannot close, and under ingress adds a notice that tokens are refused there. Tests: `src/test/accessTokensPanel.test.tsx`, `src/test/settingsAccessSection.test.tsx`,
  `src/test/accessAuditPanel.test.tsx`, `src/test/accessTrustedNetworksPanel.test.tsx`. Add a settings section
  to `ACCOUNT_SETTINGS_SECTIONS` only if every call it makes is open to that role; a tool
  that calls a refused route goes in `TOOL_CAPABILITY` (`navDestinations.ts`). A hook that
  loads at startup must not call an admin-only route for another role (members, viewers and
  kiosks read `/settings/view` through `useAppSettings`, which cannot save, and skip
  `/settings`, `/radio/config`, `/updates`, `/community`, `/push/preferences`,
  the Wi-Fi state; the kiosk also skips the mark-read calls, `read_private`); the kiosk gets
  no contact row, no Direct chip and no settings door.
- **Forced password change.** `GET /auth/session` `user.must_change_password` (or a 403
  `auth_password_change_required` from any call, via `authEvents.ts`) makes `SessionProvider`
  render `ChangePasswordPage` (`PATCH /auth/me`) instead of the app, then re-read the session.
- **Coded 403.** `fetchJson` reports a 403 `auth_forbidden` through `auth/authEvents.ts`
  (`reportForbidden`); `SessionProvider` shows `errors.auth_forbidden` as one toast
  (id `auth_forbidden`), never the server's text. A refused read (a GET, or any call
  needing `read_private`) is a state of the surface and is not toasted
  (`forbiddenIsToasted`). Tests: `src/test/capabilities.test.tsx` (four roles).
- **Sign out** with Basic credentials ends nothing; `signOut()` reports whether the
  session ended and the menu explains when it did not.
- **Remember me.** `LoginForm` has a "Keep me signed in on this device" checkbox (unchecked,
  not persisted); `AuthLoginRequest.remember` goes to `POST /auth/login` (the second step of a
  2FA login inherits it server side) and to `passkeyLoginVerify(credential, remember)`. The
  re-login dialog shares the form. `LoginPage` also shows `auth.passkey.unavailableHint` when the
  server answers `methods.passkey === false` to a passkey-capable browser on a secure page
  (`passkeyUnavailableHere`). Entry chunk: passkey wrappers and strings stay in it (the login
  page needs them at once); only the library and the Settings panel are lazy.
- **Not done on purpose.** No capabilities (the permission registry is a later ticket).
- Strings: slice `aa` (`auth.*`, plus the error codes slice `x` lacks), slice `ai`
  (`auth.ingress.*`, `errors.auth_ingress_only`).
  Tests: `src/test/auth.test.tsx`.

### Extensions section (`components/settings/extensions/`)

Settings → Extensions (section id `extensions`, `SettingsExtensionsSection.tsx`, icon `Puzzle`).
`canOpenSettingsSection` opens it to `admin` and, through `MEMBER_SETTINGS_SECTIONS`, to any
role with `send` (member); viewer and kiosk get no section. A member sees it read-only
(`isAdmin` false): the `plugins-member-readonly` note, no add/create buttons, no switch or
reorder (`InstalledTab canManage`), and a drawer (`InstanceDrawer canManage`) without scope,
quotas, settings, code, logs, diagnostic, restart or uninstall, so no admin-only route is called.
Over `/api/plugins` (`api.getPlugins`,
`getBundledPlugins`, `installPlugin`, `createLocalPlugin`, `patchPlugin`, `putPluginSource`,
`checkPlugin`, `testPlugin`, `restartPlugin`, `getPluginLogs`, `getPluginDiagnostic`,
`deletePlugin`, `getPluginsPause`, `setPluginsPause`; types in `extensions/pluginsTypes.ts`,
`PluginInstance` stays in `types.ts`). Global pause: `GET /plugins/pause` read on mount and on
each health change; a banner (`plugins-paused-banner`: since when, by whom, **Resume** for admins)
while paused, else an admin-only **Pause all extensions** button behind a confirmation dialog;
instances read the `paused` status (tone `warn`). `plugins_disabled_source: "paused"` does not
lock the writes (changes are saved and apply on resume); `env`, `platform` and `isolation` do.
It reads `health` (`plugins_isolation`, `plugins_disabled_source`, `plugins_paused_since`) and
reloads the list whenever `health.plugin_statuses` changes (the server announces `plugin_status`
and a health refresh follows, so no extra WebSocket handler). Rules: `enableBlock()`
(`pluginsShared.ts`) keeps `revoked`/`incompatible` from being enabled (the toggle is disabled
and `toggle()` refuses too); `needs_attention` for a reason other than `config_required` is
enabled only through the drawer's **Accept and enable** (resends `granted_permissions`, then
`enabled: true`); with `plugins_disabled_source` set to anything but `paused` every write is off but
Uninstall. The consent (`AddBundledDialog`) grants exactly the declared permissions and shows
`plugins.permissions.http.private_messages_warning` when `messages.read` (direct or all channels)
meets `http`. `ScopeEditor` is a small own editor (not the fanout `ScopeSelector`, which is private
to `SettingsFanoutSection.tsx`). Quotas: only `messages.send` per hour/day, lowered only.
`MigrationBanner` shows the converted-bots notice from `health.converted_bots` (how many bots became extensions, how many await review) with a dismiss button (`POST /plugins/converted-bots/acknowledge`). `ConvertedBotsBanner` (in `AppShell`'s banner band, administrators only, hidden inside Settings > Extensions) shows the same notice on every view until it is acknowledged, with a button to open the section; the acknowledgement stays in `MigrationBanner`.
Strings: slice `cc` (`settings.extensions.*`, `errors.*` plugin codes), slice `al`
(permissions in plain words) and slice `ce` (`schemaForm.*`, `settings.extensions.config.*`
form strings, `settings.extensions.local.*`). Tests: `src/test/pluginsSection.test.tsx`,
`src/test/pluginsEditor.test.tsx`.

- **Config form** (`PluginConfigForm.tsx`): a `SchemaForm` over the instance's `config_schema`
  with `languageMapResolver(i18n.language)`. `secretsSet` is the `secret_fields` whose
  `config` value is the `********` mask; the mask is never rendered. Save is `PATCH {config}` with
  only what the form returns (the API replaces plain fields and keeps an omitted secret); a
  `config_invalid` answer (`params.issues`: code and path) becomes field errors. The form
  remounts on `updated_at`. `attention.fields` flag the required fields still empty and the drawer's
  **Go to the settings** button (`focusConfigForm`) focuses the first one.
- **Settings an update reset** (MSHL-393): `droppedFields()` (`pluginsShared.ts`) reads the
  `config_dropped` notice (`attention.fields`, the instance keeps running) or a held instance's
  `attention.config_dropped`; `InstanceBadges` shows a *Settings reset* badge and the drawer a
  notice with the field names and **Go to the settings**. The server clears the notice on the next
  config save, and the reloaded instance drops it.
- **Local plugins** (`kind: 'local'`): `CreateLocalDialog` (`POST /plugins`, `echo` or `blank`
  template, hooks) and `LocalPluginEditor` in the drawer: the lazy `BotCodeEditor` (`ariaLabel` prop; its
  file name is a leftover of the retired bot editor) saved with `PUT source`, the static scan listed with line numbers,
  `http_hosts` offered as an **Allow** button (a `granted_permissions` patch), hooks and permissions
  (`PATCH hooks` then `granted_permissions`: a local plugin declares and grants together, so the
  generic permission list is not shown), **Check** (`POST /check`, disabled while the code is
  unsaved) and **Test with a message** (`POST /test`; the page states nothing reached the radio;
  needs the instance enabled and the `message` hook).

### Store and Updates tabs (`components/settings/extensions/`, MSHL-198)

Settings → Extensions → **Store** (`StoreTab`) and **Updates** (`UpdatesTab`), admin-only (the
routes are; a member gets a notice and no request).

- **Plugin content is plain text, always.** A plugin's README, changelog, name, summary, host and
  field names are untrusted. Render them as React text (a `<pre>` for the README and the changelog):
  never `dangerouslySetInnerHTML`, never a Markdown renderer, never an `<a>`/`<img>` built from
  them. There is no Markdown engine or sanitiser in `package.json` on purpose (owner question Q17,
  decided with the signed Store in M2b). The only link on the plugin page is the issue tracker
  built from the **validated** `owner/name` (`issuesUrl`); `src/test/pluginsStore.test.tsx` holds a
  hostile README (`<script>`, inline HTML, `javascript:` links, images) and a hostile changelog.
- **Store tab.** `GET /store/status` drives `StoreActivationCard` (hosts, what is sent, what is
  missed, taken from the server: never hard-code a host) while `enabled` is false; the card's button is
  `PATCH /settings {store_online: true}`. Online, the `store-online` bar holds the **Store online**
  switch (`role="switch"`, admins only) that sends `PATCH /settings {store_online: false}` and brings
  the card back (the help text sits beside it). **Refresh** is `POST /store/refresh` (10 minute
  gap on the server, a 10 s hold in the button). `CustomRepositoriesPanel` lists the repositories
  (`custom` and `not reviewed, not signed` badges, `compatible`, last check, last error); **Details**
  opens `PluginPage` (README from `GET /store/repositories/{o}/{n}/readme?lang=`, permissions,
  hosts, privacy, versions, report and **Copy diagnostic**). `CustomInstallBody` is the consent
  (warning, permissions, privacy, tick box) then, when `entry.config_schema` has properties, a
  `SchemaForm` for the settings; `granted_permissions` is `entry.permissions`, the list on screen.
  Bundled plugins keep `AddBundledDialog` (settings after the install).
- **Updates tab.** `GET /store/updates?lang=` (no egress) plus `GET /store/repositories` for the
  release data. `UpdateDialogBody` shows the changelog (`null`: "no changelog", custom sources
  have none), the permission and schema diffs, and, for a `consent_required` hold, **every**
  permission of the release; the grant sent is exactly `entry.permissions` of the repository
  entry whose `version` equals `to_version` (otherwise no consent is offered and the instance is
  held, reviewed under Installed). A `config_required` hold opens a `SchemaForm` and sends
  `config`. The consent and config apply to one instance (the first held), as the API does.
  Per-instance auto-update is `PATCH {auto_update}`. Each row lists the instances whose
  `dropped_fields` the update would reset (the dialog warns too) and, when every instance is
  marked for auto-update, why it waits (`auto_hold`: `consent_required`, `config_required`,
  `config_dropped`). `PluginPage`'s Versions section shows the same `hold`, `dropped_fields` and
  `auto_hold` per installed instance (from `GET /store/repositories`).
- **Badge and links.** `stores/pluginUpdatesStore.ts` holds the update count (`usePluginUpdateBadge`
  in `AppShell` polls it for admins; `UpdatesTab` writes it); `SettingsIndexView` and the Updates tab
  trigger show it. `SettingsExtensionsSection` takes `onOpenSection` (Settings → Updates link);
  `SettingsUpdatesSection` links back through `requestExtensionsTab('updates')`.
- Strings: slice `pluginstore` (`pluginStore.*`). Tests: `src/test/pluginsStore.test.tsx`,
  `src/test/customRepositoriesPanel.test.tsx`.

### Shared `SchemaForm` (`components/forms/`)

`SchemaForm.tsx` renders a form from a JSON Schema; `schemaFormModel.ts` is the pure half
(`buildFormModel`, `collectValues`, resolvers, `groupFieldErrors`). It is **generic**: nothing in
`components/forms/` imports from `components/settings/extensions/` or the plugin code. Consumers:
the plugin config form (Store T11b) and the Notifications channel forms (T17,
`components/settings/notifications/`), which render the JSON Schema of `model_json_schema()`; never fork it, extend it. Tests: `src/test/schemaForm.test.tsx`
(hand-written plugin schemas and `src/test/fixtures/schemaForm.pydantic.json`, kept equal to
pydantic's output by `tests/test_schema_form_fixture.py`).

- **Props.** `schema`, `values` (stored plain values), `secretsSet`, `resolver`, `onSubmit(values)`
  (a promise: resolve = saved, reject = keep the form as typed), `submitLabel`, `errors`
  (field name to messages, from the server), `disabled`, `flagged`, `idPrefix`. No client-side schema
  validator (no `ajv`): the inputs carry only the native `required`, `min`, `max`, `minlength`,
  `maxlength`, `pattern` and `type` hints; the server validates and its error paths are mapped to
  fields. A hand-written renderer was chosen over `@rjsf/core` and `@jsonforms/react` (both need a
  custom theme for shadcn/Tailwind and ship far more than the subset).
- **Supported subset** (JSON Schema 2020-12): a root `type: object` (or no `type`) with
  `properties`, `required`, `title`, `description`, `$defs`; per property `type` `string` (`enum`,
  `minLength`, `maxLength`, `pattern`, `format: uri|email|password`), `integer` and `number`
  (`enum`, `minimum`, `maximum`), `boolean`, `array` of strings (`minItems`, `maxItems`,
  `uniqueItems`; `items.enum` renders checkboxes, else one value per line); `title`, `description`,
  `default`, `writeOnly`, `examples`. The shapes pydantic emits are read: `anyOf: [<type>,
{type: null}]` is an optional field, a local `$ref` into `$defs` (also with sibling `default` or
  `title`) is inlined. Titles and descriptions may be a string or a `{lang: text}` map.
  Anything else (`oneOf`, `allOf`, `const`, `type: object` nested, a remote `$ref`, an unknown
  `format`...) renders a typed error (`data-testid="schema-form-unsupported"`, naming the field and the
  keyword), never a silently dropped field; an unsupported root replaces the whole form.
- **Secrets.** A `writeOnly: true` property is a write-only field: its value is never read from
  `values`, never displayed, never rendered in the DOM. With `secretsSet` it shows "a value is set"
  with **Replace** (a password input; left empty it keeps the value) and **Clear** (not for a
  required one; sends `""`); without, a password input. `format: password` counts as `writeOnly`. `collectValues` returns a secret only when
  replaced (its new value) or cleared (`""`), so an untouched one is omitted, which is the server's
  "keep" rule. After `onSubmit` settles, resolved or rejected, every secret draft is emptied (the server may have stored it before a later step failed); plain fields keep what was typed.
- **Label resolvers** (`LabelResolver`: `label`, `description`, `option`): `languageMapResolver(lang)`
  reads the schema's own `title`/`description` (string or per-language map, exact tag, base language,
  English, first); `i18nKeyResolver(t, prefix, lang)` looks up `<prefix>.<name>.label`,
  `.description` and `.options.<value>` in i18next and falls back to the schema text. Without a
  resolver text the label is the schema `title`, then the property name. Empty optional values are
  left out of the submitted object.
- Strings: slice `ce` (`schemaForm.*`).

### Share API (`api.ts`)

The terms acceptance fields (`terms_version`, `terms_accepted_version`, `terms_accepted_at`,
`terms_stale` of the status; `share_terms_accepted` of the settings update) are typed in
`utils/shareTerms.ts`, not yet in `types.ts`. The consent dialog sends
`{share_enabled: true, share_terms_accepted: true}`; the server records its own terms version
and the time. When `terms_stale` is true the section shows a notice and the checkbox (off)
reopens the dialog.

`api.getShareStatus()` (no egress), `api.shortenLink(url)` and
`api.getShareMeta(id, {messageId?, signal?})` (`?message_id=` asks the backend to decrypt an
encrypted file for that message's conversation), `api.reportShare(id, {reason, details?})`, `api.listShareMine(cursor?)`,
`api.deleteShare(id)` and `api.clearShareCache()` (admin) call `/api/share/*` (and
`api.uploadShareFile(blob)`, below); types `ShareStatus`, `ShareLink`, `ShareFile`,
`ShareMeta`, `ShareMine`, `ShareMineObject`, `ShareCacheClear` in `types.ts`. Errors are coded (`errors.share_*`, slice `ah`; a
`share_disabled` answer means sharing is off). Call `getShareMeta` only on hover,
focus, a tap or when the preview policy lets a shared image load, never on a plain render.

**Settings section.** `components/settings/SettingsShareSection.tsx` (section id
`share`, after Community in `settingsConstants.ts`) shows the status line, the
enable checkbox, and the shorten-mode and preview-policy radios. Enabling always
opens the consent dialog (`ShareConsentDialog`): what leaves when sending and
reading, who, retention, links to the share service's own `/terms` and `/privacy`
pages (host from `status.host`, never hardcoded; `?lang=`) and the published
`security` doc, and a required terms checkbox; `PATCH /api/settings`
`{share_enabled: true}` is sent only on confirm, and cancelling, closing or an
unticked box leaves it off. Turning it off needs no consent. The status comes from
`stores/shareStatusStore.ts` (`useShareStatus()`, `loadShareStatus()`; module
store, refreshed after every settings change; a request aborts after 10 s and sets `error`, which the composer's File tab shows as a message with a Try again button, disabled and labelled Checking while that retry is in flight). A failing request sets `error` only if no status was stored while it was in flight (`version` counter): a newer answer wins. `unreachable: true` (the share client's breaker is open, known locally) keeps the
enable checkbox disabled with an explanation, and the confirm button too, since the
terms live on the service and could not be read; turning off stays possible.
`dm_encryption_available: false`
(radio key not read yet) shows an explanation that only encrypted DM files are
unavailable. Below the radios (admin): `ShareCacheControl` clears the local object
cache and shows the freed size, then "My uploads". The section is in
`MEMBER_SETTINGS_SECTIONS` (`auth/capabilities.ts`): a member sees only "My uploads",
never the settings or the cache; a viewer or kiosk cannot open it. "My uploads"
(`ShareUploadsList.tsx`, MSHL-247) renders only while sharing is on (off, a notice
says why there is no list, and nothing is requested): kind, type or host, size,
expiry, status; a blocked row is greyed, names its category
(`sharePayload.report.reasons.*`) and links the service's `/legal` page on
`status.host`, with no delete; an active row deletes after a confirm dialog; empty,
error, refresh and "Show more" (`next_cursor`). Strings: slice `ar`
(`settings.share.*`, `settingsNav.share`) and slice `su` (`settings.share.cache.*`,
`settings.share.uploads.*`).
Never rename a slice. `preview_policy` (setting `share_preview_policy`) decides whether a
shared image in a message loads without a tap (Message rendering, `SharePayload`). Tests: `src/test/settingsShareSection.test.tsx`, `src/test/shareUploadsList.test.tsx`. User docs:
`docs/user/{en,fr}/deep/share.md` (also `messages.md`, `trust.md`, `deep/security.md`).

**Composer shortening.** `MessageInput` reads `useShareStatus()` (local, no egress;
off or `shorten_mode: never` means no button, no request). Pure helpers in
`utils/share.ts` (`findLongUrls`, `rewriteUrls`, `shortFormBytes`, `isShareLink`;
it owns `URL_PATTERN`, shared with the linkifier). Mode `auto`: the Shorten button
beside the counter appears only at `limitState === 'error'` with a long non-service
URL; a click calls `api.shortenLink` per URL, sequentially, and rewrites the
**textarea** (all or nothing, toast with Undo, nothing sent). Mode `always`: Send
shortens first, then the byte guard judges the shortened text; if it still fails,
the text stays in the textarea and nothing is sent. Shortening never happens in
`onSend`/`message_send.py`, and `submitMessage` keeps its `error` check. Never
rewritten: share-host URLs, `g:`/`m:`/`r:` payloads. Tests:
`src/test/messageInputShorten.test.tsx`, `shareShortenUtils.test.ts`.

**Composer File tab (MSHL-245).** `ComposerAttachPicker` tab `file` (Paperclip) mounts
`components/ComposerFileTab.tsx`. Off (`useShareStatus().enabled` false): only the consent entry
point, which opens the exported `ShareConsentDialog` and sends
`{share_enabled: true, share_terms_accepted: true}`. On: a `react-dropzone` zone (`accept` =
`IMAGE_ACCEPT`, `multiple: false`, `maxSize` = `MAX_SOURCE_BYTES`, click and keyboard go through its
`open()` button) plus the textarea's `onPaste` in `MessageInput`, which hands a pasted image to the tab
(`IncomingFile`, never into the text). `utils/imageDownscale.ts` decodes
(`createImageBitmap`, `<img>` fallback), scales the longest side to 1600 px, re-encodes (JPEG 0.82; PNG kept
with transparency; WebP when `toBlob` really yields it; GIF untouched, 5 MiB cap), which also strips EXIF;
HEIC and other types are refused with a coded message (`chat.fileTab.error.*`, no upload). Size and type
rules are pure (`fitWithin`, `classifySource`, `chooseOutputType`). The upload is `api.uploadShareFile`
(XHR, raw body, `X-Share-Mode: public`, progress, `AbortSignal`; `encrypted: {type, id}` switches to
`X-Share-Mode: encrypted` with `X-Share-Conversation-Type` / `X-Share-Conversation-Id`, where a channel's
id is its `share_conversation_id`, never its key (MSHL-378), and `name` adds `X-Share-Name` (percent-encoded,
sealed in the envelope; `utils/shareMode.ts` `shareFileName` drops control characters, `/`, `\\`, `.`/`..` and cuts
at 255); failures are the usual `ApiError`, shown as `errors.share_*` toasts). Mode (MSHL-256): `MessageInput`
`shareTarget` (built in `ConversationPane` from `utils/channelClass.ts`: `channelShareTarget`, `contactShareTarget`)
classes the conversation `public` | `hashtag_derived` | `private` | `dm` | `room`; only `private` and `dm` are
`encryptable` (an unknown `key_derived` counts as derived; a room server is refused by the backend). An
encryptable target shows a Public / Encrypted radiogroup (`file-tab-mode`), encrypted by default, the last choice
kept in `localStorage` `meshloom-share-mode` (`utils/shareMode.ts`); encrypted is disabled in a DM unless
`dm_encryption_available` is `true` (missing or false keeps it off) and for a private channel without `share_conversation_id`, with a hint.
A non-encryptable target shows no selector, one line (`chat.fileTab.mode.clear.<class>`) that never uses the
word "private"; a null target (no conversation) shows neither. An upload is reused on retry only for the same
mode and conversation. Send = upload once, then `sendPayload(suggested text + caption)`, so the reply
prefix and the normal send path apply; the uploaded result is kept if the send fails, so a retry does not
upload again. The caption is judged with `utils/fileCaption.ts` against the composer's strict limit minus the
reply prefix, estimated before the upload (link on `status.host`) and checked again with the real link; over
budget is refused, never truncated. Strings: slice `sf` (`chat.fileTab.*`). Tests:
`composerFileTab.test.tsx`, `imageDownscale.test.ts`, `fileCaption.test.ts`. User docs: MSHL-248 (T25).

### i18n

- `src/i18n/index.ts` loads only the active language. i18next `fallbackLng` is
  `false` (the `FALLBACK_LOCALE` constant is not a runtime fallback), so `en` and
  `fr` must have exactly the same keys; `src/test/i18nParity.test.ts` enforces it.
- Strings live in `locales/{en,fr}.json` plus `locales/slices/*.{en,fr}.json`,
  deep-merged over the base. Never rename or delete a slice file (root `AGENTS.md`).
  After adding a slice, delete `node_modules/.vitest-cache` if `i18nParity`
  misses its keys: the cached transform keeps the old `import.meta.glob` list.
- Tests default to `fr` (`src/test/setup.ts`): query copy with `i18n.t('key')`,
  never a literal string.
- Format numbers with `utils/formatNumber.ts` or `{{count, number}}`. Never call
  bare `toLocaleString()` on a number.

### WebSocket (`useWebSocket.ts`, `wsEvents.ts`)

- When a new socket replaces an old one, events from the old socket are ignored
  (`wsRef.current === ws`).
- `wsEvents.ts` checks the envelope and the required fields of each event type,
  not the full payload schema. The event types come from `wsEventTypes.ts`,
  generated from the server's `WsEvent` (`scripts/build/export_ws_events.py`,
  never edited by hand, checked in CI); `KnownWsEventsMatchServer` fails `tsc`
  until `KnownWsEvent` covers exactly those types. The server only sends a
  client the events its role may receive.
- `notification_event` (MSHL-138, `NotificationEventFrame`): one frame per
  notification catalogue event that matched a rule, typed data, no rendered text
  (fixtures `notification_event_*`). `useWebSocket` hands it to `onNotificationEvent`,
  which feeds the Feed's event rows (`utils/feedEvents.ts`, "Event source").
  `rules.emit_ws_events: false` turns it off (Settings > Notifications > Rules,
  "Feed events"); the Feed then falls back on its reducers.
- On connect the server sends only `health`; contacts and channels come from REST.
  After a reconnect `useRealtimeAppState` refetches the REST snapshots; a
  generation counter applies only the newest, and keys already updated by a WS
  event keep their live value.
- `message.historical === true` marks a message stored by historical decryption
  (an old message with a new id). It is reserved for the client: `feedStore`
  keeps the flag on the row and `recordFeedMessage` returns it as `historical`
  (never a "new" message for the feed). The unread counters and toasts still treat
  these messages like any other; the feed's follow counter ignores them. Absent on live frames and REST rows. The toast
  `historical_decrypt_complete` (`_rooms` with a count for the bulk run) is the
  end-of-run signal.
- `raw_packet`: key and dedupe on `observation_id`. `id` is the storage row and repeats.
- `contact_resolved` migrates an identity: the active conversation, cached
  messages, unread state keys and reconnect reconciliation all at once.

### Radio Wi-Fi state

- `stores/radioWifiStore.ts` holds the radio's Wi-Fi state for the Settings panel
  (`components/settings/RadioWifiSettings.tsx`) and the one-time prompt
  (`components/RadioWifiPrompt.tsx`, mounted in `AppShell`). The server sends
  only `health` on socket connect, so the store fetches `GET /api/radio/wifi`
  itself when health first says the radio is connected and set up (wired in
  `useRadioControl`), once more while the answer is `pending`, and after a WS
  reconnect; the `radio_wifi` event keeps it current. An answer older than the
  last event, or one that lands after the radio went away, is dropped (a
  refresh too); a radio that goes away resets it. A failed automatic fetch sets
  `useRadioWifiLoadFailed()` (the panel shows Retry).
- The post-reboot "switch to TCP" toast is armed only by a reboot that should end
  on Wi-Fi (`noteWifiRebootStarted()`), and fires for the first state with an
  address received after the radio went away (health), within 5 minutes.
- The panel re-reads the transport whenever `health.connection_info` or
  `radio_connected` changes: it stays mounted while the radio reconnects. A
  radio that goes away or changes closes its dialogs and clears the typed
  password (the prompt does the same when it closes).
- The prompt has exactly two exits (store / "don't ask again"): no close button,
  Escape and outside clicks ignored. A reboot for Wi-Fi always goes through
  `api.rebootRadioForWifi()` after a confirm, never `api.rebootRadio()`; that
  includes the full backup restore's **Reboot the radio now** when its Wi-Fi
  step wrote to the radio (`restoreWroteWifi()`), which also stores the
  returned state and arms the switch-to-TCP toast. The backend holds the
  prompt (`prompt_needed` false) while a restore job runs.
- Password inputs are `type="password"` with `autoComplete="new-password"`; the
  SSID is never trimmed; the password leaves React state once a request settles.
- The "Go back" after a switch to TCP calls `api.revertRadioTransport()` (the
  server keeps the BLE PIN); never re-send transport settings from the browser.
  The panel and the toast share `utils/radioTcpSwitch.ts`: the way back is
  offered only when health does not show the radio over TCP at the target 30 s
  after the PUT resolved (health from before the switch never counts:
  `connectedOverTcp()`), or when the PUT failed after the server saved it
  (`tcpSwitchApplied()`). A PUT refused before saving keeps an older
  `previous_transport`: never offer it then.

### Community state

- `stores/communityStore.ts` is the only source of "is Community on"
  (`useCommunityEnabled()`, `useCommunityStatus()`). Its `enabled` means on **and
  not waiting for a position**: every surface treats `waiting_for_position` as off,
  except `SettingsCommunitySection`, which reads `status.enabled` (the opt-in).
- Joining and changing the IATA are one `PUT /api/community/me/iata` (`enable: true`
  to join), never a `PATCH` first. Leaving goes through
  `hooks/useCommunityLeave.tsx` (its dialog sends `PATCH {enabled: false}`).
- Never call `/api/community/airports` or ask IATA suggestions while Community is
  off, except the explicit "Suggest codes" click in the IATA form.

### `#live`

- There are **no observer icons**. One observation draws origin (from
  `origin.pubkey` / local advert, **never `hops[0]`**), then the hops, then the ear.
- An empty region selection means every region. Local observations (`iata: null`)
  always pass. `community_packet` reaches every tab, so the browser filter
  (`observationPassesFilters`) is what decides, whatever the relay filtered upstream.
- `livePacketStore` never shows close codes to the user.

### Messages and unreads

- An outgoing message appears after the send API returns: no optimistic insert.
  The backend also emits a WS `message` so other tabs stay in sync.
- The message cache, jump-to-message, bidirectional pagination and reconnect
  reconciliation live in `hooks/useConversationMessages.ts`. While
  `hasNewerMessages` is true (viewing history), WS messages for the active
  conversation are not appended.
- The unread divider is anchored to `first_unread_ids[stateKey]`, never to a
  timestamp. When that id is not loaded, the list offers "Jump to unread".
  `useUnreadCounts.incrementUnread` sets the boundary itself on the read→unread
  transition, because `first_unread_ids` comes only from a full fetch.
- Opening a conversation clears its unread count during render
  (`useUnreadCounts`), so the divider anchor (`hooks/useChannelUnreadMarker.ts`)
  is also computed during render, in the same pass, from the pre-clear count. Moving
  either one into an effect loses the divider (`src/test/useChannelUnreadMarker.test.ts`).
- State keys come from `getStateKey()` (`channel-{key}`, `contact-{full public
key}`); they are not `Message.conversation_key`.

### Message rendering

- `components/message/MessageBody.tsx` is the only place that renders a message's
  text and payloads (links, `@[Name]` mentions, `#channel` references, GIF,
  location, reactions). `MessageList` uses it; the feed and Links & Files must too,
  so a message looks the same everywhere. Search results do not render message text
  through it today.
- The pieces live in `components/message/richPayloads.tsx` (payload renderers) and
  `utils/messageText.tsx` (linkify, mentions, `RegionBadge`, `hasUnexpectedControlChars`).
  Links keep `noopener noreferrer` and the `https?://` + TLD rule.
- Service links (the share hosts of `GET /api/share/status`, id grammar in
  `utils/shareLinks.ts`) render through `components/message/ShareLinkAnchor.tsx`
  (re-exported by `richPayloads.tsx`), which `linkifyText` uses for every URL. The
  destination hint (`-> host`) comes from `GET /api/share/{id}/meta` on hover or
  focus only (never while the message's shared image may not load yet:
  `contexts/ShareLinkGateContext.ts`, provided by `MessageBody`), through `stores/shareMetaStore.ts` (one request per id, 10-minute
  memory capped at 200 ids that never evicts an entry a mounted card subscribes to; failures
  (error, timeout, an encrypted file with `decryptable: null`) are kept only
  `META_FAILURE_TTL_MS` (30 s), a 410 for the full ten minutes; once fetched, the hint stays
  visible for every instance of that id until the memory expires). The remote host
  is never shown raw: `displayShareHost` strips control and bidi characters, shows
  an IDN as punycode and cuts it, and the anchor renders it in a `<bdi dir=ltr>`. The store asks `/api/share/status` (local, no
  egress) only for a URL shaped like a service link, and never requests a meta
  when sharing is off. `utils/messagePreview.ts` previews from the text alone:
  `📷 <link>` is `📷 Image`, `🔒 <link>[#fragment]` is `🔒 Encrypted file`
  (`messagePreview.shareImage`/`shareEncrypted`), a bare link `🔗 caption`; never the URL.
- Shared images (MSHL-246): `components/message/SharePayload.tsx` (re-exported by
  `richPayloads.tsx`), rendered by `MessageBody` after the text (the anchor stays).
  Only the composer's form is an image: `📷 <service link>[ caption]` or, encrypted,
  `🔒 <service link>[ caption]`, optionally behind `@[Name] ` (`utils/sharePayload.ts`
  `sharePayloadLink`, `shareImageId`); a bare service link stays an
  anchor. Whether it loads without a tap is `useShareImage` (`MessageBody`
  holds the tap) over `shareMediaAutoLoads`: `remoteMedia` false, a blocked sender
  (`utils/messageBlocking.ts` `isMessageBlocked`, lists from `BlockedSendersProvider`
  in `App.tsx`) or settings not loaded yet (`loaded` false, also outside a provider)
  means a tap; then `preview_policy` of the
  share status: `always` loads, `private` loads in a DM or a private channel
  (`isPrivateChannel`: `Channel.key_derived === false` and not the Public key; an
  unknown channel is not private), `never` always asks. `MessageBody` takes
  `message` (id for `?message_id=`, sender for the block lists) and
  `privateConversation`; without `message` it always asks. `MessageList` passes
  `isPrivateConversation(msg, channels)`, the feed `FeedRowLabels.privateConversation`.
  Before the tap nothing is requested, meta included, and the visible anchor does
  not warm the meta on hover or focus either (`ShareLinkGateContext`;
  `src/test/sharePayload.test.tsx` spies `fetch`). `interactive={false}` (Feed
  display mode, `FeedMessageRow` `readOnly`) shows the plain `sharePayload.notLoaded`
  label instead of the tap button. On load: meta through `requestShareMeta`; a public image is
  `<img src="./api/share/{id}?message_id=" loading="lazy">` capped at 240x240 opening
  the same URL in a new tab, over 8 MiB (`SHARE_INLINE_MAX_BYTES`, meta `size`) a
  Download card; a 410 meta (`ShareMetaEntry` `missing` with `gone`) shows
  `sharePayload.gone`; any other failure, a link object, a non-public file or an
  image `onError` draws nothing (the raw link stays). Encrypted files (MSHL-256): a `🔒`
  body asks the meta with `?message_id=` and caches it per `(id, message)`
  (`shareMetaKey`; a `📷` body keeps the id alone, shared with the anchor's hover, and when
  that meta says `mode: encrypted` and the card has a message, it is asked again with the
  message id and shown like a `🔒` one, MSHL-384). An
  encrypted file with `decryptable: true` is shown like a public one under a lock badge
  (`share-payload-lock`: decrypted name in a `<bdi>`, type label); `decryptable: false`
  shows `sharePayload.encryptedElsewhere`; unknown (`null`), a failed meta or a failed
  image shows `sharePayload.encryptedUnavailable` with a `sharePayload.retry` button
  (`requestShareMeta(id, messageId, {force: true})`, bypassing the failure memory); the raw link
  always stays. Every loading path ends in a final state: a meta unanswered after
  `META_TIMEOUT_MS` (20 s) is aborted and kept as a failure, the local status request is
  aborted after `STATUS_TIMEOUT_MS` (10 s) and leaves a `missing` entry for a waiting card,
  and a card whose entry was dropped asks again. The card's Report button (only with `canSend`: viewer and kiosk get 403) opens `ShareReportDialog` (8 contract reasons,
  details up to 2000) and calls `api.reportShare` (`POST /api/share/{id}/report`),
  toast on success, `errors.share_*` on failure except `share_quota_key`, which
  is the report limit (5 per instance key and day): `sharePayload.report.quota`. Strings: slice `sp`
  (`sharePayload.*`, `messagePreview.share*`).
- `hasUnexpectedControlChars` flags C0 controls, DEL, bidi embeddings, overrides and
  isolates (U+202A-202E, U+2066-2069), ALM (U+061C), U+200B, U+2060 and U+FEFF. It does
  not flag LRM/RLM, ZWNJ or ZWJ (legitimate in RTL, Indic and emoji text). Its only caller
  is the "corrupt unnamed channel message" label in `MessageList.tsx`, so it never
  flags a message that has a sender name or key. Names made of mesh text are isolated
  with `<bdi>` / the `bidi-isolate` utility (`unicode-bidi: isolate`, `styles.css`): the
  block sender, reactor rows, the chat title; in plain strings (`title`, `aria-label`)
  wrap them with `isolateBidi()`. Conversation-list and feed names are not isolated yet.
- `remoteMedia={false}` keeps a GIF as its raw text instead of loading
  `media.giphy.com`, and a shared image behind its "Tap to load" button. Only reactions follow `useRichPayloads()`; GIFs and locations
  always render.
- `src/test/messageList.test.tsx` ("rendering snapshots") pins the markup: a change
  there is a visual change, not a refactor.

### `MessageList` virtualization

- `scrollMargin` is measured from the spacer's offset; each row subtracts it from
  its `translateY`.
- The pin to the bottom is re-applied for a bounded number of frames while row
  heights settle; a pending `targetMessageId` or a user scroll cancels it.
- `getItemKey` returns a string sentinel past the end: numeric keys would collide
  with message ids.
- jsdom cannot observe any of this. Check it in a real browser.

### Layout

- The conversation column has exactly one vertical scroller, the message viewport.
  Every flex link from `#root` down must be able to shrink (`min-h-0` / overflow);
  `tests/e2e/specs/chat-layout.spec.ts` covers it.
- Keyboard, iOS focus zoom and pull-to-refresh are handled once, in `styles.css`
  and `utils/appViewport.ts`.
- Platform variants go in CSS under `[data-platform=...]` (set by
  `markPlatform()`), never as a branch in TSX.

### Repeater dashboard and rooms

- Login sends exactly one request. The backend may retry once by flood on its own:
  do not add a client-side retry loop.
- "Load All" runs the panes one after another, because the backend serializes
  radio calls under a lock.

### Updates UI

The browser never calls GitHub. Install / auto-update show only when
`apply_supported` is true.

### Web Push

`public/sw.js` displays notifications and handles clicks. There is no in-tab desktop
notification.

`utils/notificationPolicy.ts` (`conversationChannelEnabled`; `utils/pushPolicy.ts` only
re-exports it for one release) mirrors `rules.conversation_channel_enabled`
(`app/notifications/rules.py`, which `app/push/policy.py` now adapts). Both suites read
`tests/fixtures/notification_policy_cases.json` (`test/notificationPolicy.test.ts` here,
`tests/test_notification_policy.py` in the backend), so a case added there must pass on both
sides: change one implementation, change the other. Cases cover DMs, public/`#`/private-key
channels, exceptions, mute, block, the pause and `always_deliver`, and the `mention` and
`cloud` inputs (a mention only reaches the channels of `message.mention` and stored `true`
exceptions; on a private-key channel only a non-cloud one).

The conversation bell (`ConversationNotifyMenu`, in `ChatHeader`, administrators only: the
API is admin-only, so the kiosk, viewer and member roles get none) and the "Notifications"
panel of a list row's three-dots menu (`ConversationOverflowMenu`) share
`ConversationNotifyPanel` (state in `hooks/useConversationNotifications.ts`: it loads
`/api/notifications/channels` + `/rules` once into a module cache shared by every bell (a header remount does not refetch), refetched when a bell opens and on the `notification_channel_changed` WS event, replaced by the answer of each write; writes
`PUT /rules/conversations/{key}` and `PATCH /rules` `paused_until`). One checkbox per
**enabled** channel (type icon, name, status dot from `health.notification_statuses` through
`NotificationStatusContext`, fed by `App.tsx`); the box is the policy result, the caption says
`inherit`/`on`/`off`; toggling to what the default gives clears the exception (`null`), orphan
ids are dropped from the write, and turning `builtin:push` on subscribes this browser first
(`usePush().subscribe()`, a refusal toasts and the exception is still written). "Do not
disturb" is `GlobalPauseControl`, reused. `utils/notifyTarget.ts` builds the policy input from a
conversation. Strings: slice `cp` (`chatHeader.notify*`). Test: `test/conversationNotifyMenu.test.tsx`.

### Notifications channels (`components/settings/notifications/`)

`SettingsNotificationsSection` mounts `NotificationChannelsPanel` (state from
`useNotificationChannels`: channels, `/types`, rules, events) above the legacy defaults table
and the conversation exceptions. `ChannelList` renders the cards (`builtin:push` expands
`PushDevicesBlock`: subscription, devices, VAPID subject). `ChannelForm` is a thin wrapper: the
channel-level fields (name, language, cloud text policy, LAN, TLS) plus the shared `SchemaForm`
fed with the type's JSON Schema from `GET /notifications/types` (hidden fields dropped, labels
from `label_key`/`help_key`, then `settings.notifications.types.<type>.<field>.label`); it
renders no field itself. `AddChannelDialog` is pick, settings (the **Test** calls
`POST /channels/test` before any save) and "What to receive here?" (`receivePatch` in
`notificationsShared.ts` adds the new id to the events of the preset through `PATCH /rules`).
A preset entry (`preset: true`, MSHL-116 and MSHL-132: Telegram, Gotify, Home Assistant, Pushover, Matrix, Signal, Slack, Mattermost, Rocket.Chat, SMS Free Mobile, SMS SFR; data in `app/notifications/presets.py`) is created as `{type: base_type ?? 'apprise', preset: <id>}`; its texts are in slice `cl` (`settings.notifications.types.<id>.name|description|<field>.options`, `notifications.presets.<id>.about|fields|help`, referenced by the schema's `label_key`/`help_key`), `ChannelForm` shows the entry's `help_key` paragraph and `wiki_url` link above the form, `TYPE_ICONS` gives the icon (one per preset), and a type with `include_message_text_locked` (SMS) shows the text-policy select disabled with a note (MSHL-365).
The server answers `409 legacy_managed` for the `legacy:*` channels' config/deletion and legacy event rows
(kept by MSHL-112): a `legacy:*` card's Edit opens `LegacyDestinationForm` (the 4.18.x forms, saving
`notification_destinations` through `PATCH /api/settings`, remove = empty it), never the generic edit
dialog or Delete; `AddChannelDialog` disables a type a `legacy:<type>` channel owns. Secrets are
write-only (`secret_fields`, `********`): never prefilled. Tests: `src/test/notificationsChannels.test.tsx`.

Limits, journal and cap (MSHL-126, slice `qn`): `ChannelForm` has an **Advanced** `<details>` with
`rate_per_min/hour/day` (empty = null = type default, shown from `default_limits`) and
`quiet_hours_exempt`; `advancedBody(draft)` adds them to the create and edit bodies. Each card has
a **Delivery log** button opening `DeliveryLogSheet` (`GET /notifications/deliveries?channel=&limit=50`:
rows, status badges, event names from `log.events.<id with . as _>`, an error code without an `errors.*`
text shown as `log.errorGeneric`, "sent in 24 h" = `totals.sent`). The cards' "held during quiet hours" and
"grouped" lines come from `useDeliveryStats` (one `limit=1` read per channel, `deliveryStats` takes the
exact `totals` of the response, with a row count only as a fallback for a server without `totals`; a failed
read hides the line). A test checks every backend event name has an EN and FR label.
`health.notifications_capped` reaches the panel through `NotificationStatusProvider`'s `capped`
prop (`useNotificationsCapped`) and shows a banner. `health.notifications_failing` arrives the same
way (`failing`, `useNotificationsFailing`, MSHL-130): when it flips the panel reloads the channels,
and `FailingChannelsBanner` lists the ones `channelAttention()` (`notificationsShared.ts`) flags
(`secret_unreadable`, then an enabled channel with status `paused`, then `failure_count >= 3`),
with **Test** and **Resume** (`api.resumeNotificationChannel`; not offered for an unreadable
secret); `ChannelList` shows the same state as a badge. Tests: `notificationsQuietHours.test.tsx`,
`notificationsDeliveries.test.tsx`, `notificationsFailingBanner.test.tsx`.

Heartbeat (MSHL-131, slice `notifheartbeat`): on a type with `heartbeat: true` in `/types`, the **Advanced**
`<details>` also renders `HeartbeatFields` (interval select: off, 5 min, 15 min, 1 h, 6 h, 24 h, plus a
stored custom value; when on, the write-only ping URL with a "saved" placeholder when the channel's
`heartbeat_url` is `********`, a "remove" box, and the GET/POST method). `heartbeatBody(entry, draft)`
(`notificationsShared.ts`) adds `heartbeat_interval_s`, `heartbeat_method` and, only when typed or cleared,
`heartbeat_url` (`""` removes it) to the create and edit bodies; a type without heartbeat sends none.
Test: `notificationsHeartbeat.test.tsx`.

Digest mode (MSHL-136, slice `notifdigest`): the **Advanced** `<details>` of every channel type renders
`DigestField` (interval select: off, 5 min, 15 min, 30 min, 1 h, 6 h, 24 h, plus a stored custom value) bound to
`ChannelDraft.digest_interval_s`, sent by `advancedBody` in the create and edit bodies. Test:
`notificationsDigest.test.tsx`.

The rules half (`NotificationRulesPanel`, same state hook, `setRules` takes each `PATCH /rules` /
`PUT /rules/conversations/{key}` answer) renders, in order: `GlobalPauseControl` (1 h / 8 h /
tomorrow 08:00 local / resume, writes `paused_until`; reused by the bell), `RulesMatrix` (events
from `/events`, `configurable` only, grouped by `rulesShared.groupOf`; a checkbox per channel
with a sticky first column inside an `overflow-x-auto` box; an `urgent` event shows "Always deliver" checked and disabled
(the engine exempts it from the pause whatever is stored); other events read `always_deliver`; `locked_channels` are checked and disabled; `contact.first_seen` has no box of its own (the engine reads `contact_types`, never its
`channels`) and its sub-rows materialise all three `contact_types` kinds on a toggle; the offline grace and the whole-word
mention tick ride their rows), the Feed frame tick (`emit_ws_events`, checked unless the rules say
`false`, slice `notiffeed`, `settings.notifications.feedFrame.*`) and `ConversationExceptions` (chip per channel cycling
inherit/on/off, `PUT null` to remove; orphan channel ids get a removable chip and are dropped from every write, the server refuses unknown ids). A failed load shows an alert with a retry. Strings: slice `ci` (`settings.notifications.matrix.*`,
`.pause.*`). Test: `src/test/notificationsRules.test.tsx`. The old `push_defaults` table and
exceptions list are gone from `SettingsNotificationsSection`.

The Alerts page (`SettingsAlertsSection`) picks where telemetry alerts go with
`TelemetryAlertChannels`: a chip per channel (`aria-pressed`) editing the `telemetry.alert` row
of the rules (`PATCH /rules`, the same row as the matrix). `TelemetryAlertRules` in `types.ts`
no longer has `channels`: a stored `channels` key is ignored. Strings: slice `cp`
(`settings.alerts.channels*`). Test: `src/test/settingsAlertsSection.test.tsx`.

### Styling

- Use rem-based text sizes (`text-[0.8125rem]`, not `text-[13px]`): the font-scale
  setting depends on them. The "Canonical style reference" in `ThemePreview`
  (`components/settings/SettingsLocalSection.tsx`) is the catalogue to copy.
- Any new `font-family` that can show a contact or node name must end with
  `'Meshloom Marks'` (a Noto Sans subset for stacked combining marks no Windows
  font covers), as `body`, Tailwind's `font-sans`/`font-mono`, `.leaflet-container`
  and the visualizer labels do. The app owns `--font-sans`/`--font-mono` (`:root`,
  overridden per theme); Tailwind's font tokens extend them through
  `@theme inline reference` in `index.css`. A plain `@theme` makes them
  self-references and silently drops the fallback.
- Tailwind 4, configured in CSS (`src/index.css`; there is no `tailwind.config.js`),
  built through `@tailwindcss/postcss`. Minimum browsers: Chrome/Edge 111,
  Safari/iOS 16.4, Firefox 128 (`docs/user/*/install.md`). Class sources are
  explicit (`source(none)` plus `@source`): `index.html` and `src/`, minus test
  files, as Tailwind 3's `content` scanned. Automatic detection would also read
  docs and the harness, and the Docker build (no `*.md`, no `src/test/`) would
  ship different CSS from the release build.
- Cascade layers. Tailwind 4 emits native `@layer theme, base, components,
utilities`, and an unlayered rule beats every utility whatever its specificity.
  `index.css`, `themes.css` and `styles.css` place each rule explicitly: palettes and
  element selectors in `@layer base`; component classes no utility competes with in
  `@layer components`; rules that must beat utilities (theme restyling of
  `.bg-card`, `button`…, safe-area and platform overrides) in `@layer utilities`,
  after Tailwind's own, where specificity and order decide as in Tailwind 3. Only
  overrides of unlayered third-party CSS (Leaflet, MapLibre, CodeMirror) stay
  unlayered. Never add a global `*` margin/padding reset: Preflight has one.
  Sonner injects unlayered CSS at runtime, so the toast theme classes in
  `components/ui/sonner.tsx` carry `!` to beat it.
- tailwindcss-animate's keyframes animate `transform`, which Tailwind 4's
  `translate-*` (the separate `translate` property) adds to. On an element centred
  with `translate-x/y-[-50%]`, slide only by the delta (`slide-in-from-bottom-[2%]`
  in `components/ui/dialog.tsx`), never by the centring offset again.
- Tailwind 3 parity shims in `index.css`: plain `:hover` (`@custom-variant hover`),
  the Tailwind 3 `space-x/y-*` sibling rule (Tailwind 4's end margin is ignored by
  inline children such as `<label>`), length line heights for `text-*`, the
  Tailwind 3 sRGB values of the palette shades in use (add a new shade there),
  and Preflight's old placeholder colour, button cursor and border colour.
  `src/test/tailwindBuild.test.ts` compiles the stylesheet and guards the fonts,
  layers, palette, `space-y` shim, class sources, toast `!` and dialog motion.
- Tailwind 4's scanner does not split a class glued to a template interpolation:
  write `bg-black/10 ${x}`, never `bg-black/10${x}`.

## Editing checklist

1. When an API or WS payload changes, update `types.ts`, `wsEvents.ts` and its
   handlers, and the tests (`src/test/fixtures/websocket_events.json` holds WS samples).
2. When hash routing changes, update `utils/urlHash.ts` and `urlHash.test.ts`. A preset of an
   existing view (`#control`) is a `feedPreset`, not a new `ConversationType`.
   The feed's filters ride in the hash (`#feed?src=...&types=...&dir=...&muted=&pending=&node=&display=`,
   also `#control?...`): `parseFeedQuery` / `encodeFeedQuery` in `utils/feedFilters.ts` (URLSearchParams,
   allowlist; unknown keys and malformed values ignored), carried as `Conversation.feedQuery` (canonical,
   no `?`) and stored by `lastViewedConversation`. URL > `localStorage` > defaults, applied in the
   `FeedView` state initializer so the first request already uses it; `FeedView` mirrors later changes
   with `replaceState`. **Never a channel key** (a private channel's key is its secret and bookmarks
   sync to cloud accounts): `node` is `contact:<64-hex>` only. The server twin is
   `is_valid_start_view` (`app/models.py`) for `ui_preferences.start_view`. Tests: `urlHash.test.ts` (parse/encode),
   `feedHash.test.tsx` (grammar, view, sync). `FeedView` reports each change through `onQueryChange`
   (`syncFeedQuery` in the router, no history entry) so `Conversation.feedQuery` is always current;
   a different `initialQuery` (popstate) replaces the filters, and a URL-pinned open never overwrites
   the filters remembered in `localStorage` (URL > storage, not a rewrite).
   A new tool id goes through every surface keyed by it: `navDestinations.ts`
   (`RailItemId`, `RAIL_ITEMS`), `ToolsView.tsx`, `CommandPalette.tsx`, `AppShell.tsx`
   (`TOOL_TYPES`), `lastViewedConversation.ts`, `useConversationRouter.ts`,
   `ConversationPane.tsx` and `isMessageConversation` in `useConversationMessages.ts`.
   `#settings/<section>/<anchor>` opens a section at a known anchor
   (`SETTINGS_ANCHORS`): `#settings/database/backup` scrolls to `#settings-backup`,
   the target of the public backup docs. The anchor is remembered once when the
   hash is read (in `useAppShell`'s state initializer and on popstate, never on
   each render: the hash is rewritten as settings open and the lazy section mounts
   later), a hash without an anchor never clears it, and the section takes it once.
3. When you add a UI string, add the key to both `en` and `fr`.
