import { api } from '../../api';
import { getFeedSnapshot, mergeHistoryPage } from '../../stores/feedStore';
import type { FeedQuery } from '../../utils/feedFilters';
import { FEED_PAGE_SIZE } from './feedConversation';

/** Rows a filtered page must leave visible before the next page is fetched on its own. */
export const FEED_MIN_VISIBLE = 20;
/** Pages fetched per gesture (mount, filter change, scroll to the top), the first included. */
export const FEED_MAX_PAGES_PER_GESTURE = 5;

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
