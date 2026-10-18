import { beforeEach, describe, expect, it, vi } from 'vitest';

import {
  FEED_MAX_ROWS,
  activateFeedStore,
  applyFeedAck,
  getFeedSnapshot,
  maxKnownId,
  mergeHistoryPage,
  mergeReconciledRows,
  recordFeedMessage,
  removeFeedMessage,
  renameFeedConversation,
  resetFeedForRefetch,
  resetFeedStore,
  subscribeFeedStore,
} from '../stores/feedStore';
import type { Message } from '../types';

function msg(id: number, received_at: number, overrides: Partial<Message> = {}): Message {
  return {
    id,
    type: 'CHAN',
    conversation_key: 'AA'.repeat(16),
    text: `m${id}`,
    sender_timestamp: received_at,
    received_at,
    paths: null,
    txt_type: 0,
    signature: null,
    sender_key: null,
    outgoing: false,
    acked: 0,
    sender_name: null,
    ...overrides,
  };
}

const flush = () => Promise.resolve();
const ids = () => getFeedSnapshot().rows.map((m) => m.id);

describe('feedStore', () => {
  beforeEach(() => {
    resetFeedStore();
    activateFeedStore();
  });

  it('orders by received_at and breaks ties on id', async () => {
    recordFeedMessage(msg(5, 100));
    recordFeedMessage(msg(3, 200));
    recordFeedMessage(msg(9, 100));
    recordFeedMessage(msg(1, 150));
    await flush();
    expect(ids()).toEqual([5, 9, 1, 3]);
  });

  it('dedupes by id', async () => {
    expect(recordFeedMessage(msg(1, 100)).added).toBe(true);
    expect(recordFeedMessage(msg(1, 100, { text: 'dup' })).added).toBe(false);
    await flush();
    expect(getFeedSnapshot().rows).toHaveLength(1);
    expect(getFeedSnapshot().rows[0].text).toBe('m1');
  });

  it('inserts a historical message in the middle and flags it', async () => {
    recordFeedMessage(msg(1, 100));
    recordFeedMessage(msg(3, 300));
    const res = recordFeedMessage(msg(2, 200, { historical: true }));
    await flush();
    expect(res).toEqual({ added: true, historical: true });
    expect(ids()).toEqual([1, 2, 3]);
    expect(getFeedSnapshot().rows[1].historical).toBe(true);
    expect(recordFeedMessage(msg(4, 400)).historical).toBe(false);
  });

  it('keeps position on ack and merges like the conversation cache', async () => {
    recordFeedMessage(msg(1, 100, { paths: [{ path: 'aa', received_at: 1 }] }));
    recordFeedMessage(msg(2, 200));
    await flush();
    applyFeedAck(
      1,
      3,
      [
        { path: 'aa', received_at: 1 },
        { path: 'bb', received_at: 2 },
      ],
      42,
      {
        packet_hash: 'h',
      }
    );
    applyFeedAck(1, 1);
    await flush();
    const first = getFeedSnapshot().rows[0];
    expect(ids()).toEqual([1, 2]);
    expect(first.acked).toBe(3);
    expect(first.paths).toHaveLength(2);
    expect(first.packet_id).toBe(42);
    expect(first.packet_hash).toBe('h');
  });

  it('ignores an ack for an unknown message', async () => {
    recordFeedMessage(msg(1, 100));
    await flush();
    const before = getFeedSnapshot();
    applyFeedAck(99, 1);
    await flush();
    expect(getFeedSnapshot()).toBe(before);
  });

  it('removes a message', async () => {
    mergeHistoryPage([], { hasOlder: false });
    recordFeedMessage(msg(1, 100));
    recordFeedMessage(msg(2, 200));
    removeFeedMessage(1);
    await flush();
    expect(ids()).toEqual([2]);
    expect(recordFeedMessage(msg(1, 100)).added).toBe(true);
  });

  it('renames only the private conversation key', async () => {
    recordFeedMessage(msg(1, 100, { type: 'PRIV', conversation_key: 'abc' }));
    recordFeedMessage(msg(2, 200, { type: 'CHAN', conversation_key: 'abc' }));
    renameFeedConversation('abc', 'abcdef');
    await flush();
    expect(getFeedSnapshot().rows.map((m) => m.conversation_key)).toEqual(['abcdef', 'abc']);
  });

  it('caps at FEED_MAX_ROWS, dropping the oldest and re-arming hasOlder', async () => {
    mergeHistoryPage([], { hasOlder: false });
    await flush();
    expect(getFeedSnapshot().hasOlder).toBe(false);
    for (let i = 1; i <= FEED_MAX_ROWS + 5; i += 1) recordFeedMessage(msg(i, i));
    await flush();
    const snap = getFeedSnapshot();
    expect(snap.rows).toHaveLength(FEED_MAX_ROWS);
    expect(snap.rows[0].id).toBe(6);
    expect(snap.rows[snap.rows.length - 1].id).toBe(FEED_MAX_ROWS + 5);
    expect(snap.hasOlder).toBe(true);
    // Older than the window with older rows on the server: left to pagination.
    expect(recordFeedMessage(msg(1, 1)).added).toBe(false);
  });

  it('merges a history page and tracks maxKnownId', async () => {
    expect(maxKnownId()).toBe(0);
    recordFeedMessage(msg(10, 1000));
    mergeHistoryPage([msg(3, 300), msg(2, 200), msg(10, 1000)], { hasOlder: false });
    await flush();
    expect(ids()).toEqual([2, 3, 10]);
    expect(getFeedSnapshot().hasOlder).toBe(false);
    expect(maxKnownId()).toBe(10);
  });

  it('inserts a live row in the middle, in (received_at, id) order', async () => {
    mergeHistoryPage([msg(1, 100), msg(3, 300)], { hasOlder: false });
    recordFeedMessage(msg(9, 200, { historical: true }));
    await flush();
    expect(ids()).toEqual([1, 9, 3]);
    expect(getFeedSnapshot().rows[1].historical).toBe(true);
  });

  it('keeps a row older than the window out of the array while older rows exist', async () => {
    mergeHistoryPage([msg(5, 500), msg(6, 600)], { hasOlder: true });
    expect(recordFeedMessage(msg(9, 100, { historical: true }))).toEqual({
      added: false,
      historical: true,
    });
    await flush();
    // The `before` cursor still starts at the oldest held row, so the gap is not skipped.
    expect(ids()).toEqual([5, 6]);
    // Reconciliation must not ask for it again.
    expect(maxKnownId()).toBe(9);
    // The page that covers its position returns it.
    mergeHistoryPage([msg(9, 100), msg(4, 400)], { hasOlder: false });
    await flush();
    expect(ids()).toEqual([9, 4, 5, 6]);
  });

  it('merges reconciled rows without touching hasOlder, and refetches after a reset', async () => {
    mergeHistoryPage([msg(5, 500)], { hasOlder: false });
    mergeReconciledRows([msg(8, 100, { historical: true }), msg(9, 900)]);
    await flush();
    expect(ids()).toEqual([8, 5, 9]);
    expect(getFeedSnapshot().hasOlder).toBe(false);
    const epoch = getFeedSnapshot().epoch;
    resetFeedForRefetch();
    await flush();
    expect(ids()).toEqual([]);
    expect(maxKnownId()).toBe(0);
    expect(getFeedSnapshot().epoch).toBe(epoch + 1);
  });

  it('records nothing while inactive', async () => {
    resetFeedStore();
    expect(recordFeedMessage(msg(1, 100))).toEqual({ added: false, historical: false });
    mergeHistoryPage([msg(2, 200)], { hasOlder: false });
    await flush();
    expect(getFeedSnapshot().rows).toHaveLength(0);
    expect(maxKnownId()).toBe(0);
  });

  it('coalesces 2000 records in one tick into one emit', async () => {
    const listener = vi.fn();
    const off = subscribeFeedStore(listener);
    for (let i = 1; i <= 2000; i += 1) recordFeedMessage(msg(i, i));
    expect(listener).not.toHaveBeenCalled();
    await flush();
    expect(listener).toHaveBeenCalledTimes(1);
    expect(getFeedSnapshot().rows).toHaveLength(2000);
    off();
  });
});
