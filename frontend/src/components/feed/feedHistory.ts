import { api } from '../../api';
import { getFeedSnapshot, mergeHistoryPage } from '../../stores/feedStore';
import { FEED_PAGE_SIZE } from './feedConversation';

/**
 * Fetch one page of history into the feed store.
 *
 * The first call (no cursor) asks for the newest page; later calls pass the oldest
 * held row's `(received_at, id)`, which is exactly how the server orders and cuts
 * (`received_at DESC, id DESC`). A page shorter than the limit is the last one.
 */
export async function loadFeedPage(
  cursor: { received_at: number; id: number } | null,
  signal?: AbortSignal
): Promise<void> {
  const rows = await api.getMessages(
    {
      limit: FEED_PAGE_SIZE,
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
