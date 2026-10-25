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
