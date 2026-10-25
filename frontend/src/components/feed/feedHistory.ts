import { api, ApiError } from '../../api';
import {
  getControlHistoryState,
  getFeedSnapshot,
  isFeedStoreActive,
  maxKnownId,
  mergeControlHistory,
  mergeHistoryPage,
  mergeReconciledRows,
  resetFeedForRefetch,
  updateControlHistory,
} from '../../stores/feedStore';
import { CONTROL_JOURNAL_PAYLOAD_TYPES } from '../../utils/controlJournal';
import type { FeedQuery } from '../../utils/feedFilters';
import { FEED_PAGE_SIZE } from './feedConversation';

/** Rows a filtered page must leave visible before the next page is fetched on its own. */
export const FEED_MIN_VISIBLE = 20;
/** Pages fetched per gesture (mount, filter change, scroll to the top), the first included. */
export const FEED_MAX_PAGES_PER_GESTURE = 5;

/** Rows asked per `since_id` page (the route's maximum). */
export const FEED_RECONCILE_PAGE_SIZE = 1000;
/** Full `since_id` pages merged before giving up and refetching the newest page. */
export const FEED_RECONCILE_MAX_PAGES = 5;

// The server-side part of the filters the store was last loaded with: reconciliation must
// ask the same question, or it would merge rows the chips hide from the store.
let serverQuery: FeedQuery = {};
let serverQueryKey = '{}';
let reconciling: Promise<void> | null = null;
let reconcileAgain = false;

/**
 * Fetch one page of history into the feed store.
 *
 * The first call (no cursor) asks for the newest page; later calls pass the oldest
 * held row's `(received_at, id)`, which is exactly how the server orders and cuts
 * (`received_at DESC, id DESC`). A page shorter than the limit is the last one.
 * `query` is the part of the filters the server evaluates (`type`, `outgoing`).
 */
export async function loadFeedPage(
  cursor: { received_at: number; id: number } | null,
  signal?: AbortSignal,
  query: FeedQuery = {}
): Promise<void> {
  if (!cursor) {
    serverQuery = query;
    serverQueryKey = JSON.stringify(query);
  }
  const rows = await api.getMessages(
    {
      limit: FEED_PAGE_SIZE,
      ...query,
      ...(cursor && { before: cursor.received_at, before_id: cursor.id }),
    },
    signal
  );
  const full = rows.length >= FEED_PAGE_SIZE;
  // A re-mount fetches the newest page again: it must not revive "older exists" on a
  // store that has already reached the beginning.
  const known = getFeedSnapshot();
  const hasOlder = cursor || known.rows.length === 0 ? full : full && known.hasOlder;
  mergeHistoryPage(rows, { hasOlder });
}

/**
 * Client-side filters (muted, pending) can leave a fetched page nearly empty. While fewer
 * than `FEED_MIN_VISIBLE` rows show and older rows exist, fetch the next page, at most
 * `maxPages` times (a gesture's own page already counts against `FEED_MAX_PAGES_PER_GESTURE`). `visibleCount` counts the rows the filters keep in the
 * store's current snapshot (published by a microtask queued before `loadFeedPage`
 * resolves, so it is fresh after each `await`).
 */
export async function fillHiddenPages(
  visibleCount: () => number,
  query: FeedQuery,
  signal?: AbortSignal,
  maxPages: number = FEED_MAX_PAGES_PER_GESTURE
): Promise<void> {
  for (let pages = 0; pages < maxPages; pages++) {
    if (signal?.aborted) return;
    const { rows, hasOlder } = getFeedSnapshot();
    const oldest = rows[0];
    if (!hasOlder || !oldest || visibleCount() >= FEED_MIN_VISIBLE) return;
    await loadFeedPage({ received_at: oldest.received_at, id: oldest.id }, signal, query);
  }
}

/**
 * Catch up after a gap the live stream cannot be trusted across: a reconnect (the server
 * evicts a client 2 048 frames behind, and a historical-decryption burst is exactly that)
 * or the end of a historical decryption.
 *
 * `GET /api/messages?since_id=<highest id seen>` returns rows with a larger id, oldest id
 * first, historical rows included (their `received_at` is old, so they merge in the middle).
 * A full page means more may follow, at most `FEED_RECONCILE_MAX_PAGES` times; past that the
 * store is emptied and the view fetches the newest page again. Deletions made while the
 * socket was down are not caught up. Works whether or not the view is mounted, as long as
 * the store is attached; calls that overlap run once more after the current pass.
 */
export function reconcileFeed(): Promise<void> {
  if (!isFeedStoreActive()) return Promise.resolve();
  if (reconciling) {
    reconcileAgain = true;
    return reconciling;
  }
  reconciling = (async () => {
    try {
      do {
        reconcileAgain = false;
        await reconcileOnce();
      } while (reconcileAgain);
    } catch (err) {
      // The next reconnect or decryption toast tries again; nothing to show the reader.
      console.error('Feed reconciliation failed', err);
    } finally {
      reconciling = null;
    }
  })();
  return reconciling;
}

async function reconcileOnce(): Promise<void> {
  let sinceId = maxKnownId();
  // Nothing loaded yet: the view's own first fetch covers it.
  if (sinceId < 1) return;
  const query = serverQuery;
  const queryKey = serverQueryKey;
  for (let page = 0; page < FEED_RECONCILE_MAX_PAGES; page++) {
    const rows = await api.getMessages({
      since_id: sinceId,
      limit: FEED_RECONCILE_PAGE_SIZE,
      ...query,
    });
    // The filters changed (store emptied) while the request was in flight: stale answer.
    if (queryKey !== serverQueryKey) return;
    mergeReconciledRows(rows);
    if (rows.length < FEED_RECONCILE_PAGE_SIZE) return;
    sinceId = Math.max(sinceId, ...rows.map((row) => row.id));
  }
  resetFeedForRefetch();
}

/** Control packets asked per history request (the route's `limit`). */
export const FEED_CONTROL_HISTORY_LIMIT = 200;
/** Newest stored packets the server may walk per request (its own maximum). */
export const FEED_CONTROL_HISTORY_MAX_SCAN = 20_000;

/**
 * Replay stored control packets into the feed. `'open'` is the first mount of the session
 * (once; the caller waits for the first message page so the request stays off the first
 * paint), `'more'` is the "Load earlier control packets" action and continues below the
 * oldest replayed packet. The four types go in one request. A refused read (403) is a
 * session without the surface: nothing more is offered, silently.
 */
export async function loadControlHistory(
  mode: 'open' | 'more',
  signal?: AbortSignal
): Promise<void> {
  if (!isFeedStoreActive()) return;
  const state = getControlHistoryState();
  if (state.loading) return;
  if (mode === 'open' ? state.started : state.exhausted) return;
  updateControlHistory({ started: true, loading: true, error: false });
  try {
    const page = await api.getPacketsHistory(
      {
        payload_type: CONTROL_JOURNAL_PAYLOAD_TYPES.join(','),
        limit: FEED_CONTROL_HISTORY_LIMIT,
        max_scan: FEED_CONTROL_HISTORY_MAX_SCAN,
        ...(mode === 'more' && state.cursorId !== null && { after_id: state.cursorId }),
      },
      signal
    );
    if (signal?.aborted) {
      updateControlHistory({ loading: false, started: state.started });
      return;
    }
    mergeControlHistory(page.items, {
      truncated: page.truncated,
      scanned: page.scanned,
      limit: FEED_CONTROL_HISTORY_LIMIT,
    });
  } catch (err) {
    if (signal?.aborted) {
      updateControlHistory({ loading: false, started: state.started });
      return;
    }
    if (err instanceof ApiError && err.status === 403) {
      updateControlHistory({ loading: false, exhausted: true });
      return;
    }
    updateControlHistory({ loading: false, error: true });
  }
}
