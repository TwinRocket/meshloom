import type { Channel, Message } from '../types';
import { isPendingChannel } from './channelMembership';

/**
 * The feed's filter model and its single evaluator.
 *
 * `rowPassesFilters` decides what the timeline shows, for rows fetched over REST and rows
 * pushed over the WebSocket alike. Two of the filters (`type`, `outgoing`) are also sent
 * to `GET /api/messages` through `filtersToQuery` so hidden rows are not fetched; the
 * server answers the same question as the evaluator, which `feedFilters.json` pins on
 * both sides. Everything else (muted, pending) is client-side only.
 */

export interface FeedFilters {
  sources: { messages: boolean; control: boolean; events: boolean };
  chan: boolean;
  priv: boolean;
  incoming: boolean;
  outgoing: boolean;
  includeMuted: boolean;
  includePending: boolean;
}

export const FEED_FILTERS_STORAGE_KEY = 'meshloom-feed-filters';

export const defaultFeedFilters: FeedFilters = {
  sources: { messages: true, control: false, events: false },
  chan: true,
  priv: true,
  incoming: true,
  outgoing: true,
  includeMuted: false,
  includePending: false,
};

export interface FeedQuery {
  type?: 'CHAN' | 'PRIV';
  outgoing?: boolean;
}

/** Fill a stored or partial object with defaults; a field of the wrong type is ignored. */
export function normalizeFeedFilters(raw: unknown): FeedFilters {
  const input = (typeof raw === 'object' && raw !== null ? raw : {}) as Record<string, unknown>;
  const sources = (
    typeof input.sources === 'object' && input.sources !== null ? input.sources : {}
  ) as Record<string, unknown>;
  const flag = (value: unknown, fallback: boolean) =>
    typeof value === 'boolean' ? value : fallback;
  const d = defaultFeedFilters;
  return {
    sources: {
      messages: flag(sources.messages, d.sources.messages),
      control: flag(sources.control, d.sources.control),
      events: flag(sources.events, d.sources.events),
    },
    chan: flag(input.chan, d.chan),
    priv: flag(input.priv, d.priv),
    incoming: flag(input.incoming, d.incoming),
    outgoing: flag(input.outgoing, d.outgoing),
    includeMuted: flag(input.includeMuted, d.includeMuted),
    includePending: flag(input.includePending, d.includePending),
  };
}

/** Stored filters, or the defaults when storage is absent, blocked or corrupted. */
export function loadFeedFilters(): FeedFilters {
  try {
    const stored = localStorage.getItem(FEED_FILTERS_STORAGE_KEY);
    if (stored) return normalizeFeedFilters(JSON.parse(stored));
  } catch {
    // Private window, blocked storage or invalid JSON: defaults, silently.
  }
  return normalizeFeedFilters(null);
}

export function saveFeedFilters(filters: FeedFilters): void {
  try {
    localStorage.setItem(FEED_FILTERS_STORAGE_KEY, JSON.stringify(filters));
  } catch {
    // The filters just do not survive a reload.
  }
}

/** Mirror of the backend's `effective_channel_muted` (`app/repository/channels.py`). */
export function isChannelMuted(channel: Channel, nowSec: number): boolean {
  return channel.muted && (channel.muted_until == null || channel.muted_until > nowSec);
}

/**
 * True when a pair of chips leaves nothing to show (both unchecked, or no source on):
 * the view shows its empty state and sends no request.
 */
export function hasEmptySelection(filters: FeedFilters): boolean {
  return (
    !filters.sources.messages ||
    (!filters.chan && !filters.priv) ||
    (!filters.incoming && !filters.outgoing)
  );
}

/** The part of the filters the server evaluates (`type`, `outgoing` on `GET /api/messages`). */
export function filtersToQuery(filters: FeedFilters): FeedQuery {
  const query: FeedQuery = {};
  if (filters.chan !== filters.priv) query.type = filters.chan ? 'CHAN' : 'PRIV';
  if (filters.incoming !== filters.outgoing) query.outgoing = filters.outgoing;
  return query;
}

export type FeedFilterRow = Pick<Message, 'type' | 'conversation_key' | 'outgoing'>;

export type HiddenReason = 'muted' | 'pending';

/**
 * Why the muted/pending chips hide this row, or an empty list. Only a `CHAN` row whose
 * channel is still known can be hidden by them: a `PRIV` row, or a row of a deleted
 * channel, has no mute or membership to apply.
 */
function hiddenReasons(
  row: FeedFilterRow,
  channels: readonly Channel[],
  nowSec: number
): HiddenReason[] {
  if (row.type !== 'CHAN') return [];
  const key = row.conversation_key.toUpperCase();
  const channel = channels.find((c) => c.key.toUpperCase() === key);
  if (!channel) return [];
  const reasons: HiddenReason[] = [];
  if (isChannelMuted(channel, nowSec)) reasons.push('muted');
  if (isPendingChannel(channel)) reasons.push('pending');
  return reasons;
}

/** Everything but the muted/pending chips. */
function passesBasicFilters(row: FeedFilterRow, filters: FeedFilters): boolean {
  if (hasEmptySelection(filters)) return false;
  if (row.type === 'CHAN' ? !filters.chan : !filters.priv) return false;
  return row.outgoing ? filters.outgoing : filters.incoming;
}

export function rowPassesFilters(
  row: FeedFilterRow,
  filters: FeedFilters,
  channels: readonly Channel[],
  nowSec: number
): boolean {
  if (!passesBasicFilters(row, filters)) return false;
  const reasons = hiddenReasons(row, channels, nowSec);
  if (reasons.includes('muted') && !filters.includeMuted) return false;
  if (reasons.includes('pending') && !filters.includePending) return false;
  return true;
}

/**
 * Rows only the muted / pending chips keep out, for the counts on those chips. A row
 * hidden for both reasons counts under each: ticking either chip alone would not show it.
 */
export function countHiddenRows(
  rows: readonly FeedFilterRow[],
  filters: FeedFilters,
  channels: readonly Channel[],
  nowSec: number
): { muted: number; pending: number } {
  const counts = { muted: 0, pending: 0 };
  for (const row of rows) {
    if (!passesBasicFilters(row, filters)) continue;
    const reasons = hiddenReasons(row, channels, nowSec);
    if (reasons.includes('muted') && !filters.includeMuted) counts.muted += 1;
    if (reasons.includes('pending') && !filters.includePending) counts.pending += 1;
  }
  return counts;
}
