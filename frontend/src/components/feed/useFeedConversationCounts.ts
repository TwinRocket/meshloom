import { useEffect, useRef, useState } from 'react';

import { api } from '../../api';
import type { FeedConversationCount, FeedQuery } from '../../utils/feedFilters';

/** Quiet period after the newest row changes before the counts are fetched again. */
export const FEED_COUNTS_DEBOUNCE_MS = 1500;

/**
 * Messages per conversation from the server, for the Muted / Pending counts and the Channels /
 * Contacts facets (the loaded rows no longer hold them: the listing is filtered server-side).
 *
 * Asked with the `type` and `outgoing` of the listing and nothing else: mute, membership, the
 * node and the pick are applied by `countHiddenMessages` / `countConversations`, so one answer
 * serves both and the clock and the channel list stay the evaluator's. Refetched when that
 * query changes and, debounced, when the newest row the store holds changes (a live message).
 * The previous answer stays until the next one arrives; a failure keeps it too (the counts are
 * a hint, never a reason to show an error).
 *
 * @param query the `type` / `outgoing` of the listing
 * @param enabled false while no message request is sent
 * @param newestId id of the newest row the store holds, or 0
 */
export function useFeedConversationCounts(
  query: Pick<FeedQuery, 'type' | 'outgoing'>,
  enabled: boolean,
  newestId: number
): readonly FeedConversationCount[] {
  const [entries, setEntries] = useState<readonly FeedConversationCount[]>([]);
  const { type, outgoing } = query;
  const loadedRef = useRef(false);
  const lastNewestRef = useRef(newestId);
  useEffect(() => {
    if (!enabled) return;
    const controller = new AbortController();
    // The first answer, and a change of query, do not wait; only a new live row does.
    const wait = loadedRef.current && lastNewestRef.current !== newestId;
    lastNewestRef.current = newestId;
    const timer = setTimeout(
      () => {
        Promise.resolve()
          .then(() => api.getConversationCounts({ type, outgoing }, controller.signal))
          .then((res) => {
            if (controller.signal.aborted) return;
            loadedRef.current = true;
            setEntries(res.conversations);
          })
          .catch(() => {});
      },
      wait ? FEED_COUNTS_DEBOUNCE_MS : 0
    );
    return () => {
      clearTimeout(timer);
      controller.abort();
    };
  }, [type, outgoing, enabled, newestId]);
  return entries;
}
