import type { Message } from '../../types';
import type { FeedItem } from './feedDays';

/** `localStorage` key of the per-browser "where was I" mark (prefix matters under HA ingress). */
export const FEED_LAST_SEEN_STORAGE_KEY = 'meshloom-feed-last-seen';

/** `(received_at, id)` of the newest row the reader had in view: the feed's sort key. */
export interface FeedLastSeen {
  at: number;
  id: number;
}

export function loadFeedLastSeen(): FeedLastSeen | null {
  try {
    const raw = localStorage.getItem(FEED_LAST_SEEN_STORAGE_KEY);
    if (!raw) return null;
    const parsed: unknown = JSON.parse(raw);
    if (
      typeof parsed === 'object' &&
      parsed !== null &&
      Number.isFinite((parsed as FeedLastSeen).at) &&
      Number.isFinite((parsed as FeedLastSeen).id)
    ) {
      const { at, id } = parsed as FeedLastSeen;
      return { at, id };
    }
  } catch {
    // Blocked or corrupted storage: no mark, no separator.
  }
  return null;
}

export function saveFeedLastSeen(mark: FeedLastSeen): void {
  try {
    localStorage.setItem(FEED_LAST_SEEN_STORAGE_KEY, JSON.stringify(mark));
  } catch {
    // Storage full or blocked: the next visit simply has no separator.
  }
}

/** The mark for a list of rows sorted by `(received_at, id)`: its last row. */
export function lastSeenOf(rows: readonly Message[]): FeedLastSeen | null {
  const last = rows[rows.length - 1];
  return last ? { at: last.received_at, id: last.id } : null;
}

/**
 * Id of the first row strictly after the mark, or null (no mark, nothing newer). A
 * `historical` row is never the answer: its `received_at` is old, but if a clock quirk put
 * it after the mark it is still not news.
 */
export function firstRowAfterMark(
  rows: readonly Message[],
  mark: FeedLastSeen | null
): number | null {
  if (!mark) return null;
  for (const row of rows) {
    if (row.historical === true) continue;
    if (row.received_at > mark.at || (row.received_at === mark.at && row.id > mark.id)) {
      return row.id;
    }
  }
  return null;
}

/**
 * The mark for a reader who may have scrolled up: the last message row whose top edge is
 * above `bottom` (the viewport's bottom edge, in content coordinates). Rows below it were
 * never reached and stay "new" next time. `starts[i]` is the offset of `items[i]`.
 */
export function lastReachedMark(
  items: readonly FeedItem[],
  starts: readonly number[],
  bottom: number
): FeedLastSeen | null {
  for (let i = items.length - 1; i >= 0; i--) {
    const item = items[i];
    if (item.kind === 'row' && (starts[i] ?? Infinity) < bottom) {
      return { at: item.row.received_at, id: item.row.id };
    }
  }
  return null;
}
