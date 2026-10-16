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
  on WSL and Docker bind mounts.
- `npm run build` type-checks only what `src/main.tsx` reaches; `npm run typecheck`
  checks all of `src/` (root `AGENTS.md`, "Deployments that never delete files").

## Invariants that are easy to break

### Packet stream stays out of React ancestors

`stores/rawPacketStore.ts` is read through `useSyncExternalStore`
(`useRawPackets()`, `useRawPacketStatsSession()`). Only leaf views subscribe:
`RawPacketFeedView`, `VisualizerView`, `LiveView`, `ControlJournalView`,
`CrackerPanel`. **No ancestor of `MessageList` (`App`, `AppShell`,
`ConversationPane`) may subscribe**, or every packet re-renders the whole tree.
`src/test/appPacketIsolation.test.tsx` enforces this.

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
- `raw_packet`: key and dedupe on `observation_id`. `id` is the storage row and repeats.
- `contact_resolved` migrates an identity: the active conversation, cached
  messages, unread state keys and reconnect reconciliation all at once.

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
- State keys come from `getStateKey()` (`channel-{key}`, `contact-{full public
  key}`); they are not `Message.conversation_key`.

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
(push, email, webhook per conversation). Turning push on subscribes this browser
first when needed, then sets the per-conversation override (`App.tsx`). There is
no in-tab desktop notification.

### Styling

- Use rem-based text sizes (`text-[0.8125rem]`, not `text-[13px]`): the font-scale
  setting depends on them. The "Canonical style reference" in `ThemePreview`
  (`components/settings/SettingsLocalSection.tsx`) is the catalogue to copy.
- Any new `font-family` that can show a contact or node name must end with
  `'Meshloom Marks'` (a Noto Sans subset for stacked combining marks no Windows
  font covers), as `body`, Tailwind's `font-sans`/`font-mono`, `.leaflet-container`
  and the visualizer labels do.

## Editing checklist

1. When an API or WS payload changes, update `types.ts`, `wsEvents.ts` and its
   handlers, and the tests (`src/test/fixtures/websocket_events.json` holds WS samples).
2. When hash routing changes, update `utils/urlHash.ts` and `urlHash.test.ts`.
3. When you add a UI string, add the key to both `en` and `fr`.
