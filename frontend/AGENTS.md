# Frontend AGENTS.md

Frontend-only notes. Repo map, commands, the quality gate, and cross-layer rules are in the root `AGENTS.md`. This file, like the root one, is a hypothesis. Check it against `src/` before you rely on it.

## Stack (from `package.json`)

- React 19, TypeScript 5, Vite 8 (rolldown), Vitest 5 + Testing Library (jsdom), Tailwind 3.
- Radix primitives in `components/ui/`, `cmdk`, `sonner` (toasts), `lucide-react`, `@tanstack/react-virtual` (message list).
- i18next / react-i18next: `en` and `fr`.
- Maps: `maplibre-gl` 6 + `@deck.gl/*` for `#live` (`components/live/`). Leaflet / react-leaflet for the node map, mini-maps, and pickers.
- `recharts` (charts), `three` + `d3-force-3d` (visualizer), CodeMirror (bot editor), `qrcode.react`.
- `@michaelhart/meshcore-decoder` is an npm alias of `meshcore-decoder-multibyte-patch@0.3.0`. Keep packet parsing consistent with backend `app/path_utils.py`.
- `meshcore-hashtag-cracker` + `nosleep.js` (channel cracker; `utils/hashtagKey.ts` uses the cracker's key derivation).

## Commands (run in `frontend/`)

```bash
npm run dev          # Vite on :5173, proxies /api to :8000
npm run test:run     # vitest run
npm run lint         # eslint src/ (lint:fix to autofix)
npm run format       # prettier --write src/
npm run typecheck    # tsc on all of src/, tests included (CI and all_quality.sh)
npm run build        # tsc -p tsconfig.build.json && vite build -> dist/
```

`npm run packaged-build` writes `frontend/prebuilt` and is for releases only.

`npm run build` type-checks only what `src/main.tsx` imports, plus the ambient `src/**/*.d.ts` (`tsconfig.build.json`), like the bundle itself. A Portainer GitOps stack keeps files deleted upstream, and such a dead file must not fail its image build. A file nothing imports is therefore checked by `npm run typecheck` only.

## Where things live

A map of the main files of the web interface.

- `main.tsx`: waits for `i18nReady`, then renders. It also registers `public/sw.js`, on secure contexts only.
- `App.tsx` wires the hooks and builds the `AppShell` props. `components/AppShell.tsx` owns the layout, the settings/search/cracker surfaces, the modals, and the info sheets. `components/ConversationPane.tsx` picks the active surface: map, live, visualizer, raw feed, trace, locate, test, repeater dashboard, room gate, or chat.
- `api.ts`: typed REST client. `types.ts`: shared contracts. `wsEvents.ts` + `useWebSocket.ts`: the WebSocket.
- `hooks/`: state ownership (`useConversationMessages`, `useUnreadCounts`, `useRealtimeAppState`, `useRepeaterDashboard`, `useOssUpdates`, `usePushSubscription`, ...).
- `stores/`: state held outside React. `rawPacketStore.ts` holds overheard packets and `livePacketStore.ts` holds `#live` packets and the Community live connection state.
- `utils/urlHash.ts` handles hash routing. `components/settings/settingsConstants.ts` (`SETTINGS_SECTION_ORDER`) lists the settings sections.
- `i18n/`: `locales/{en,fr}.json` plus `locales/slices/*.{en,fr}.json`.
- `src/test/`: vitest suites. `tests/e2e/` at the repo root: Playwright, for layout checks that jsdom cannot do.

## Invariants that are easy to break

Rules that, if broken, make the interface slow or wrong without any visible error.

### Packet stream stays out of React ancestors
`stores/rawPacketStore.ts` is read through `useSyncExternalStore` (`useRawPackets()`, `useRawPacketStatsSession()`). Only leaf views subscribe to it: `RawPacketFeedView`, `VisualizerView`, `LiveView`, `ControlJournalView`, and `CrackerPanel`. **No ancestor of `MessageList` (`App`, `AppShell`, `ConversationPane`) may subscribe**, or every packet re-renders the whole tree. `src/test/appPacketIsolation.test.tsx` enforces this rule.

**Clocks.** The server `packet.timestamp` is the server wall clock and can be minutes off. `recordRawPacket` adds a client-only `received_at_ms` (browser `Date.now()`) to every live packet. Anything compared with `Date.now()` (visualizer pruning, `#raw` stats windows, Live view laser aging) must use `received_at_ms`, falling back to `timestamp` only for history-replayed packets. Displaying a packet's absolute time keeps using `timestamp`.

### Lazy loading
Heavy surfaces are loaded with `lazy()`: `SettingsModal`, `CrackerPanel`, `SearchView`, the info sheets, `MapView`, `LiveView`, `VisualizerView`, `RepeaterDashboard`, `RawPacketFeedView`, and the map sub-components. If an always-loaded module imports `recharts`, `leaflet`/`react-leaflet`, or `qrcode.react` statically, they end up back in the entry chunk. `vite.config.ts` defines no manual vendor chunks on purpose: Vite splits the chunks automatically.

### i18n
- `src/i18n/index.ts` loads only the active language, on demand. There is no fallback language, so `en` and `fr` must have exactly the same keys. `src/test/i18nParity.test.ts` enforces this. Both languages are preloaded in `src/test/setup.ts`.
- Format numbers with `utils/formatNumber.ts` or `{{count, number}}`. Never call bare `toLocaleString()`.

### WebSocket (`useWebSocket.ts`, `wsEvents.ts`)
- When a new socket replaces an old one, events from the old socket are ignored (`wsRef.current === ws`). Reconnect uses a capped exponential backoff: 1 s to 30 s with ±25 % jitter. The backoff resets only after the connection has stayed up for 10 s or delivered a message. A text `ping` is sent every 30 s.
- `wsEvents.ts` checks the envelope and the required fields of each event type. It does not validate the full payload schema.
- Events: `health`, `message`, `contact`, `contact_resolved`, `channel`, `contact_deleted`, `channel_deleted`, `raw_packet`, `community_packet`, `community_live`, `message_acked`, `message_deleted`, `error`, `success`, `pong`.
- On connect, the server sends only `health`. Contacts and channels come from REST. After a reconnect, `useRealtimeAppState` fetches the REST snapshots again. A generation counter makes sure only the newest snapshot is applied, and keys already updated by a WS event keep their live value.
- `raw_packet`: use `observation_id` as the key to render and dedupe events. `id` is the storage row and can repeat.
- `contact_resolved` migrates an identity. It affects the active conversation, the cached messages, the unread state keys, and the reconnect reconciliation all at once.

### Community state
`stores/communityStore.ts` is the only source of "is Community on" (`useCommunityEnabled()`, `useCommunityStatus()`). App settings seed it on startup from `directory_available`; `GET`/`PATCH /api/community` answers replace the seed (`setCommunityStatus`, `refreshCommunityStatus`); every `community_live` WS status triggers a re-read (concurrent re-reads share one request), and so does a WS reconnect, so other tabs follow an opt-out, an opt-in and an IATA change. Nothing keeps a copy: `SettingsCommunitySection` reads its status from the store, loads it with `loadCommunityStatus()` (the same shared request as `refreshCommunityStatus()`, but it rejects, so a failure shows its `formatApiError` detail) and writes each answer back with `setCommunityStatus()`; `lookupCommunityIataLocation()` (`utils/locationPicker.ts`) reads `getCommunitySnapshot()` and asks the backend only before the store knows. Changing the IATA is one `PUT /api/community/me/iata` (no `PATCH` first): the backend clears the old region's status, binds with Community, then saves and reconnects. Joining is that same call with `enable: true` (no `PATCH` either; the settings have no "enable" checkbox, and while Community is on only a "Leave" button sends `PATCH {enabled:false}`; the PATCH stays for API use): Community binds the IATA first, and only then does the backend turn Community on; a refused bind (the IATA change cap gives `settings.community.joinIataChangeCap`) leaves it off. The Leave button reads `settings.community.leaving` while its PATCH runs and is described by its help text (`aria-describedby`). `App` derives `directoryEnabled` from the store, so the router leaves `#test` and the test tool disappears as soon as Community is off; `MessageList` passes it to `PathModal`, which then asks `POST /api/directory/resolve-hops` nothing. With Community off, `ObserverReachModal` and `MeshTestView` say so instead of showing an empty list, the "heard by" badges are hidden, and nothing calls `/api/community/airports`: the location picker checks the store first, `LiveView` frames the chosen regions only while Community is on, the IATA search in the settings shows only while it is on, and the join form has none.

### Community live status
`livePacketStore.applyLiveStatus` keeps only what `LiveView` shows: the opt-out flag, `auth_error`, `clock_skew_s`, the banner they produce, and `paused`. Close codes never reach the user, so the store ignores `close_code` and `connected`. It reads `state` to treat `auth_rejected` as `token_rejected` for older relays, and `paused` (Community paused its public feed) as a flag that opt-out and a token refusal override. `LiveView` shows a paused feed as a small chip in the toolbar, inside a `role="status"` region that stays mounted empty (`sr-only`) so a new pause is announced (`live.paused`, tooltip `live.pausedHelp` on an `aria-hidden` node, so screen readers get the label and the help once, from `sr-only` text), never a banner, and only while no banner is shown.

### Messages and unreads
- An outgoing message appears after the send API returns. There is no optimistic insert. The backend also emits a WS `message` so other tabs stay in sync.
- The message cache, jump-to-message, bidirectional pagination, and reconnect reconciliation all live in `hooks/useConversationMessages.ts`. While `hasNewerMessages` is true (the user is viewing history), WS messages for the active conversation are not appended.
- The unread divider is anchored to `first_unread_ids[stateKey]`, the id of the oldest unread message, never to a timestamp. When that id is not in the loaded window, the list offers "Jump to unread" instead of a divider. `useUnreadCounts.incrementUnread` sets the boundary itself on the read→unread transition, because `first_unread_ids` comes only from a full `/read-state/unreads` fetch.
- State keys come from `getStateKey()`: `channel-{key}` and `contact-{full public key}`. They are not `Message.conversation_key`.

### `MessageList` virtualization
- `scrollMargin` is measured from the spacer's offset, because of the padding and the "older messages" banner. Each row subtracts it from its `translateY`.
- The pin to the bottom is applied again for a bounded number of animation frames while row heights settle. A pending `targetMessageId` or a user scroll cancels it.
- `getItemKey` returns a string sentinel for indices past the end. Plain numeric keys would collide with message ids.
- jsdom cannot observe any of this. Check it in a real browser.

### Layout
- The conversation column has exactly one vertical scroller, the message viewport. Every flex link from `#root` down must be able to shrink (`min-h-0` / overflow). `tests/e2e/specs/chat-layout.spec.ts` covers this.
- Keyboard handling, iOS focus zoom, and pull-to-refresh are handled once, in `styles.css` and `utils/appViewport.ts` (`--app-height`, `interactive-widget`, a 16 px floor for coarse pointers).
- Platform chrome: `data-platform` on `<html>` (`ios` | `android` | `other`, set by `markPlatform()`), `.liquid-surface`, `.glass-back-button`, and `--bottom-nav-height`. Add a platform variant as CSS under `[data-platform=...]`, never as a branch in TSX.

### `#live`
`LiveView` + `components/live/` animate packets along their hops over the directory map (`GET /api/directory/nodes/live`). There are **no observer icons**: `NODE_ROLE_STYLE` has no observer role. Observer GPS stays in `geometryDirectoryNodes` only so that hops can be resolved, and `mappableDirectoryNodes` skips it. One observation draws one polyline: origin (from `origin.pubkey` / local advert, never `hops[0]`), then the hops, then the ear. Two or more points draw a line, one point a pulse. A 300 ms hold (`LIVE_HOLD_MS`) merges frames for the same path. `MAX_CONCURRENT_ANIMS` caps how many animations run at once.

Region filter: `LiveViewFilters.iata` is a `Set` (empty = every region), picked in `live/LiveIataMenu.tsx` (a disclosure button named by its visible text, label + selection, opening a `role="group"` of checkboxes with "All regions"; focus goes to the first box on open and back to the button on Escape). Unticking the last region means every region, and so does a list over 64 codes (`LIVE_IATA_SELECTION_MAX`, the relay's limit). Options = `GET /api/community/iata/active` (only while Community is ready; failures are ignored) ∪ the regions seen in the feed ∪ the radio's IATA, the placeholders `XXX`, `LOC`, `AAA` excluded (`LIVE_PLACEHOLDER_IATAS`). Default = the radio's IATA; the choice is kept in localStorage `meshloom-live-iata` (`utils/liveIataPreference.ts`: `"all"` or a JSON list, every access in try/catch; it stays after an opt-out, on purpose, for the return) and sent with the subscribe, every 30 s heartbeat, and at once on change. Opening Live or changing the choice frames the chosen regions: `utils/liveRegionCenters.ts` places each code at its airport (`GET /api/community/airports`, exact IATA match, one lookup per code per page). Only while Community is on: off, the map frames everything and nothing is asked. An empty answer or a failure is not remembered, and the cache is cleared whenever the store turns Community on, so turning it back on frames the regions again without a reload. Each answer's `session_id` replaces the tab's: a relay that forgot the session (TTL, restart) mints a new one, and the next beats reuse it. The filter follows the packet: `LiveMapController` keeps a bounded `LivePacketRegionIndex` (bucket key → regions that heard it, local observations counted under the radio's IATA), and `observationPassesFilters` lets an observation through when its packet was heard in a chosen region. Local observations (`iata: null`) always pass. The backend relay filters upstream too, but `community_packet` reaches every tab, so the browser filter is what decides.

### Repeater dashboard and rooms
- On mount, the dashboard first loads `GET /api/contacts/{key}/repeater/cache` (database only, each pane with its own `fetched_at`).
- Login sends exactly one request. The backend may retry once by flood on its own, so do not add a client-side retry loop.
- Each pane is retried up to 3 times (`MAX_RETRIES`). "Load All" runs the panes one after another, because the backend serializes radio calls under a lock. "Load All" can be cancelled.
- Room contacts (`type=3`) keep the chat view, with `RoomServerPanel` as the gate above it.

### Updates UI (`#settings/updates`)
`hooks/useOssUpdates.ts` polls `GET /api/updates` every 5 min, and every 1.5 s while an update job is running. Clicking Install stores the target version in `sessionStorage` (`meshloom.updateTarget`). The hook then treats API downtime as a restart, and reloads once `health.app_info.version` or `updates.current` matches the target. It stops on `job.state === 'failed'` or after 5 min. `SettingsUpdatesSection` shows Install / auto-update only when `apply_supported` is true. Otherwise it shows the Home Assistant hint (`addon`) or the manual recipes. The browser never calls GitHub.

### Web Push
`public/sw.js` displays notifications and handles clicks. `usePushSubscription` is the client for `/api/push/*`, and `utils/pushPolicy.ts` mirrors `app/push/policy.py`. The settings live under Settings → Notifications. Clicking the `ChatHeader` bell with no subscription only subscribes. Later clicks change the per-conversation override. There is no in-tab desktop notification.

### Styling
Use rem-based text sizes (`text-[0.8125rem]`, not `text-[13px]`), because the font-scale setting depends on them. The "Canonical style reference" inside `ThemePreview` (`components/settings/SettingsLocalSection.tsx`) is the catalogue of patterns to copy.

Fonts come from the system, except `'Meshloom Marks'` (`src/assets/fonts/meshloom-marks.woff2`, declared in `index.css`). It is a subset of Noto Sans for the stacked combining marks that no Windows font covers (U+1AB0–1AFF, U+1DC0–1DFF), and it is fetched only when an earlier font misses a character. Any new `font-family` that can show a contact or node name must end with it, as `body`, Tailwind's `font-sans`/`font-mono`, `.leaflet-container` and the 3D visualizer labels already do. Its OFL licence goes into `LICENSES.md` through `scripts/build/collect_licenses.sh`.

## Editing checklist

1. When an API or WS payload changes, update `types.ts`, `wsEvents.ts` and its handlers, and the tests (`src/test/fixtures/websocket_events.json` holds WS samples).
2. When hash routing changes, update `utils/urlHash.ts` and `urlHash.test.ts`.
3. When you add a UI string, add the key to both `en` and `fr`.
