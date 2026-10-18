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
  when nothing can show.
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
imports `recharts`, `leaflet`/`react-leaflet` or `qrcode.react` statically, they end
up back in the entry chunk. `vite.config.ts` defines no manual vendor chunks on
purpose.

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
  not the full payload schema.
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
- `hasUnexpectedControlChars` flags C0 controls, DEL, bidi embeddings, overrides and
  isolates (U+202A-202E, U+2066-2069), ALM (U+061C), U+200B, U+2060 and U+FEFF. It does
  not flag LRM/RLM, ZWNJ or ZWJ (legitimate in RTL, Indic and emoji text). Its only caller
  is the "corrupt unnamed channel message" label in `MessageList.tsx`, so it never
  flags a message that has a sender name or key. Names made of mesh text are isolated
  with `<bdi>` / the `bidi-isolate` utility (`unicode-bidi: isolate`, `styles.css`): the
  block sender, reactor rows, the chat title; in plain strings (`title`, `aria-label`)
  wrap them with `isolateBidi()`. Conversation-list and feed names are not isolated yet.
- `remoteMedia={false}` keeps a GIF as its raw text instead of loading
  `media.giphy.com`. Only reactions follow `useRichPayloads()`; GIFs and locations
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

`public/sw.js` displays notifications and handles clicks; `utils/pushPolicy.ts`
mirrors `app/push/policy.py`. The `ChatHeader` bell opens `ConversationNotifyMenu`
(push, email, webhook, Apprise per conversation). Turning push on subscribes this browser
first when needed, then sets the per-conversation override (`App.tsx`). There is
no in-tab desktop notification.

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
