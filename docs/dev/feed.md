# Feed: developer notes

The Feed (`#feed`, `#control`) is the global timeline of messages, control packets and events.
The state is in `frontend/src/stores/feedStore.ts`, the view in
`frontend/src/components/feed/`; the invariants are in `frontend/AGENTS.md` ("Feed"). This file
holds the performance budget and what guards it. The code is the source of truth.

## Performance

Budget: a Raspberry Pi 4, 10 000 stored messages, 4 frames/s on average and 17/s at the peak. The
Feed must never make the conversation slower. Wall-time assertions are unstable on a CI runner,
so the guards count work instead.

| Guard | Where | What it pins |
|---|---|---|
| Query plans | `tests/test_messages_feed_filters.py::test_listing_plan_uses_index_without_temp_sort` | `EXPLAIN QUERY PLAN` of `GET /api/messages` for every combination the Feed sends: type (any, CHAN, PRIV) x outgoing (any, true, false) x cursor (first page, `before`/`before_id`, `since_id`), plus the blocked lists, on a seeded table. Each plan must use an index and no temp B-tree. No index was added: no plan degrades. |
| Control history plan | `tests/test_raw_packet_payload_type.py::test_plan_uses_the_payload_type_index_without_temp_sort` | `EXPLAIN QUERY PLAN` of the per-type query behind `GET /api/packets/history` (`raw_packets.payload_type`, migration 133): `idx_raw_packets_payload_type_timestamp`, no temp B-tree. The slow-marked `test_four_type_history_on_100000_rows_is_fast` (`-m slow`) times the four-type page and the backfill. |
| Store load | `frontend/src/test/feedLoad.test.ts` | 2 000 message frames and 500 raw packets: comparisons per insertion stay under 2 x (log2(n+1) + 1) in and out of order, one publish per burst, memory bounded (`FEED_MAX_ROWS` = 2 000 messages, `FEED_CONTROL_MAX` = 500 control rows). |
| Chat isolation | `frontend/src/test/appFeedIsolation.test.tsx` | The `MessageList` mock render count does not change under 2 000 messages and 500 packets. |
| Row memoisation | `frontend/src/test/feedPerf.test.tsx` | A new message re-renders no row already shown; `createFeedNameResolver` hands a row the same labels object. |
| Timings | `FEED_PERF=1 npx vitest run src/test/feedLoad.test.ts` | Loose bounds and numbers; out of the gate. |

Mechanics, as they are today:

- Messages are inserted by binary search on `(received_at, id)`; an append is a `push`, never a
  re-sort. One `queueMicrotask` publishes one snapshot per burst (a copy of the working arrays).
- At most `FEED_DOM_MAX` (300) rows are mounted, whatever the memory window holds.
- `FeedMessageRow` is `memo`: its props must keep their identity between renders. The labels
  object comes from a per-resolver `WeakMap` keyed on the row object, and `time` from a per-language
  cache; an acked or renamed row is a new object and re-renders alone.

### Numbers

Measured on a development laptop (not the Pi), jsdom:

| Measure | Before | After |
|---|---|---|
| `FeedMessageRow` renders when one message arrives, 18 rows mounted (jsdom viewport; 300 at most in a browser) | 18 | 0 |
| 2 000 messages inserted in random order | about 2 ms | about 2 ms |
| 500 control packets inserted | about 0.3 ms | about 0.3 ms |
| Publish of the snapshot after the burst | under 1 ms | under 1 ms |

The store itself was already cheap; what cost was the rows re-rendering on every emit, because
`labels` and `time` were new objects at each render and defeated the memo.

## Device checklist (phone, Home Assistant webview, Pi kiosk)

Run it after any change to the Feed's layout, to `AppShell` / `BottomNav`, and for the React
Compiler visual review (same surfaces). Without hardware, probe with Playwright device
emulation against a local build: start the backend with no radio (`MESHCORE_DATABASE_PATH` on a
temp file, `uv run uvicorn app.main:app --no-proxy-headers`), create a test administrator on the setup page, and
seed `messages` / `channels` with long unbroken words, long URLs, long sender and channel names,
RTL and emoji text, a few hundred rows. Emulate `iPhone 13` / `Pixel 7` at 360 x 740 and
360 x 640, inject `--safe-area-top: 47px; --safe-area-bottom: 34px; --safe-area-bottom-capped: 12px`
(Chromium reports no insets), and throttle the CPU through CDP (`Emulation.setCPUThrottlingRate`,
6 for a Pi-like run). Items that need real hardware are marked **hardware**.

| Area | Check | Expected |
|---|---|---|
| Phone | No horizontal scroll at 360 px (`scrollWidth <= innerWidth`) | none; long words and URLs wrap |
| Phone | Exactly one vertical scroller (the list); the page itself never scrolls | `tests/e2e/specs/feed-layout.spec.ts` |
| Phone | Filter chips: two single-line swipeable rows, 36 px touch height | the list keeps the room |
| Phone | Filters sheet: opens, safe-area padding, names truncate but the counts stay visible | no clipped count |
| Phone | Text filter focused with the keyboard up (viewport shrunk to 420 px) | field and list reachable; 16 px font so iOS does not zoom |
| Phone | Long press on a row (500 ms), then release | row menu opens, no iOS callout, the release does not navigate |
| Phone | Follow button when scrolled up | fully above the bottom bar (`data-above-bottom-nav`) |
| Phone | Back to Tools (header arrow, browser back) | the Tools list, hash updated |
| Phone | Copy text without `navigator.clipboard` (HTTP on the LAN) | the item is absent, in the Feed and in conversations |
| HA ingress | Serve under `/api/hassio_ingress/<token>/` (proxy that strips the prefix, WebSocket included) | assets, `/api`, WebSocket and `#feed` hash routing work; `localStorage` keys keep their `meshloom-` prefix (HA's origin is shared) |
| HA ingress | Display mode inside an `<iframe>` without `allowfullscreen` | mode works, chrome hidden, "Quit" visible; no console error |
| HA companion apps | iOS and Android webview: safe areas, keyboard, long press, display mode, fullscreen fallback | **hardware** |
| Pi 4 kiosk | `#feed?display=1`, Control on, 30 min of live traffic; note CPU and memory | **hardware**; DOM cap (`FEED_DOM_MAX` 300) holds, no layout jank on inserts |
| Pi-like CPU | CPU throttled 6x, 900 rows, 40 fast scrolls | at most 25 rows mounted, three long tasks at startup, about 13 MB heap |

Known limit: the shell banners (no radio, Community off) stack above the Feed and can leave a
360 x 640 screen with a short list; they are the shell's, not the Feed's.
