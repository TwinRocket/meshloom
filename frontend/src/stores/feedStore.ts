import { useSyncExternalStore } from 'react';

import type { Message, MessagePath } from '../types';

/**
 * The global message stream (the feed), kept outside React like `rawPacketStore`.
 *
 * A message must never re-render `App → AppShell → ConversationPane → MessageList`, so
 * nothing on that path may subscribe: only the feed view reads `useFeedRows()`.
 *
 * Two rules keep the cost near zero:
 * - Lazy attach: until `activateFeedStore()` (first feed mount) every record call is a
 *   flag check. Users who never open the feed pay nothing.
 * - Batched emits: mutations edit a private working array and one microtask publishes a
 *   fresh immutable snapshot and notifies once, so a historical-decryption burst is one
 *   render, not thousands.
 */

export const FEED_MAX_ROWS = 2000;

export interface FeedAckExtras {
  packet_hash?: string | null;
  observer_reach_eligible?: boolean | null;
}

export interface FeedSnapshot {
  rows: readonly Message[];
  /** True when rows older than the first one may exist on the server. */
  hasOlder: boolean;
}

let active = false;
// Working copy, mutated in place; `snapshot.rows` is a frozen-by-convention copy of it.
let working: Message[] = [];
const ids = new Set<number>();
let hasOlder = true;
let snapshot: FeedSnapshot = { rows: [], hasOlder: true };
let flushScheduled = false;
const listeners = new Set<() => void>();

function subscribe(listener: () => void): () => void {
  listeners.add(listener);
  return () => {
    listeners.delete(listener);
  };
}

/** Subscribe to every published change (for callers that need more than the hook). */
export const subscribeFeedStore = subscribe;

function flush(): void {
  flushScheduled = false;
  snapshot = { rows: [...working], hasOlder };
  for (const listener of listeners) {
    listener();
  }
}

function scheduleEmit(): void {
  if (flushScheduled) return;
  flushScheduled = true;
  queueMicrotask(flush);
}

function compare(a: Message, b: Message): number {
  return a.received_at - b.received_at || a.id - b.id;
}

/** Index where `msg` belongs, after any equal-or-smaller rows (ids are unique anyway). */
function insertionIndex(msg: Message): number {
  let lo = 0;
  let hi = working.length;
  while (lo < hi) {
    const mid = (lo + hi) >>> 1;
    if (compare(working[mid], msg) <= 0) lo = mid + 1;
    else hi = mid;
  }
  return lo;
}

function insert(msg: Message): boolean {
  if (ids.has(msg.id)) return false;
  const index = insertionIndex(msg);
  if (index === working.length) working.push(msg);
  else working.splice(index, 0, msg);
  ids.add(msg.id);
  return true;
}

/** Drop the oldest rows beyond the cap; the newer side is never truncated. */
function enforceCap(): void {
  const overflow = working.length - FEED_MAX_ROWS;
  if (overflow <= 0) return;
  for (const dropped of working.splice(0, overflow)) {
    ids.delete(dropped.id);
  }
  hasOlder = true;
}

/** Attach the store; called at the first `FeedView` mount. Idempotent. */
export function activateFeedStore(): void {
  active = true;
}

export function isFeedStoreActive(): boolean {
  return active;
}

/**
 * Record one live message. `historical` echoes `msg.historical`: such a row is stored and
 * flagged but must never be counted as new by the caller.
 */
export function recordFeedMessage(msg: Message): { added: boolean; historical: boolean } {
  if (!active) return { added: false, historical: false };
  const historical = msg.historical === true;
  if (!insert(msg)) return { added: false, historical };
  enforceCap();
  scheduleEmit();
  return { added: true, historical };
}

export function applyFeedAck(
  messageId: number,
  ackCount: number,
  paths?: MessagePath[],
  packetId?: number | null,
  extras?: FeedAckExtras
): void {
  if (!active || !ids.has(messageId)) return;
  const index = working.findIndex((row) => row.id === messageId);
  if (index < 0) return;
  const current = working[index];
  // Same merge rule as the per-conversation cache (`conversationMessageCache.updateAck`);
  // the position is unchanged because (received_at, id) is.
  working[index] = {
    ...current,
    acked: Math.max(current.acked, ackCount),
    ...(paths !== undefined && paths.length >= (current.paths?.length ?? 0) && { paths }),
    ...(packetId !== undefined && { packet_id: packetId }),
    ...(extras?.packet_hash !== undefined && { packet_hash: extras.packet_hash }),
    ...(extras?.observer_reach_eligible !== undefined && {
      observer_reach_eligible: extras.observer_reach_eligible,
    }),
  };
  scheduleEmit();
}

export function removeFeedMessage(messageId: number): void {
  if (!active || !ids.has(messageId)) return;
  working = working.filter((row) => row.id !== messageId);
  ids.delete(messageId);
  scheduleEmit();
}

/** A contact resolved from a prefix to a full key: move its private messages. */
export function renameFeedConversation(oldKey: string, newKey: string): void {
  if (!active || oldKey === newKey) return;
  let changed = false;
  working = working.map((row) => {
    if (row.type !== 'PRIV' || row.conversation_key !== oldKey) return row;
    changed = true;
    return { ...row, conversation_key: newKey };
  });
  if (changed) scheduleEmit();
}

/** Merge a page of older rows fetched over REST; `hasOlder` is the server's answer. */
export function mergeHistoryPage(rows: readonly Message[], opts: { hasOlder: boolean }): void {
  if (!active) return;
  hasOlder = opts.hasOlder;
  for (const row of rows) insert(row);
  enforceCap();
  scheduleEmit();
}

/** Highest message id held, or 0 when empty. */
export function maxKnownId(): number {
  let max = 0;
  for (const row of working) if (row.id > max) max = row.id;
  return max;
}

/**
 * Empty the store but keep it attached: a change of the server-side filters (`type`,
 * `outgoing`) reloads the first page, and the cursor must not cross the old query.
 */
export function clearFeedRows(): void {
  working = [];
  ids.clear();
  hasOlder = true;
  scheduleEmit();
}

/** Full reset, including the lazy-attach flag. Used by tests to isolate cases. */
export function resetFeedStore(): void {
  active = false;
  working = [];
  ids.clear();
  hasOlder = true;
  snapshot = { rows: [], hasOlder: true };
  flushScheduled = false;
  for (const listener of listeners) listener();
}

export function getFeedSnapshot(): FeedSnapshot {
  return snapshot;
}

export function useFeedRows(): FeedSnapshot {
  return useSyncExternalStore(subscribe, getFeedSnapshot);
}
