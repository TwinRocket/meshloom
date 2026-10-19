import { beforeEach, describe, expect, it, vi } from 'vitest';

import {
  FEED_CONTROL_MAX,
  FEED_MAX_ROWS,
  activateFeedStore,
  applyFeedAck,
  getFeedSnapshot,
  maxKnownId,
  mergeHistoryPage,
  recordFeedControlPacket,
  mergeReconciledRows,
  recordFeedMessage,
  removeFeedMessage,
  renameFeedConversation,
  setFeedAcceptsPrivate,
  resetFeedForRefetch,
  resetFeedStore,
  subscribeFeedStore,
  clearFeedRows,
} from '../stores/feedStore';
import { recordRawPacket, resetRawPacketStore } from '../stores/rawPacketStore';
import type { Message, RawPacket } from '../types';

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

function control(id: number, timestamp: number, overrides: Partial<RawPacket> = {}): RawPacket {
  return {
    id,
    observation_id: id,
    timestamp,
    // Flood Request: header 0x01.
    data: `0100bbaa${id.toString(16).padStart(4, '0')}${'11'.repeat(16)}`,
    payload_type: 'Request',
    snr: null,
    rssi: null,
    decrypted: false,
    decrypted_info: null,
    ...overrides,
  };
}
const controlIds = () => getFeedSnapshot().controlPackets.map((p) => p.id);

describe('feedStore control rows', () => {
  beforeEach(() => {
    resetFeedStore();
    resetRawPacketStore();
    activateFeedStore();
  });

  it('keeps the four control types and drops everything else', async () => {
    const header = (type: number) => ((type << 2) | 1).toString(16).padStart(2, '0');
    recordFeedControlPacket(control(1, 10));
    recordFeedControlPacket(
      control(2, 11, { data: `${header(1)}00bbaa${'11'.repeat(16)}`, payload_type: 'Response' })
    );
    recordFeedControlPacket(
      control(3, 12, { data: `${header(7)}00bb${'cc'.repeat(34)}`, payload_type: 'AnonRequest' })
    );
    recordFeedControlPacket(
      control(4, 13, { data: `${header(6)}00bbaa${'11'.repeat(16)}`, payload_type: 'GroupData' })
    );
    recordFeedControlPacket(
      control(5, 14, { data: `${header(5)}00bbaa${'11'.repeat(16)}`, payload_type: 'GroupText' })
    );
    recordFeedControlPacket(
      control(6, 15, { data: `${header(4)}00${'77'.repeat(8)}`, payload_type: 'Advert' })
    );
    await flush();
    expect(controlIds()).toEqual([1, 2, 3, 4]);
  });

  it('orders by timestamp and dedupes by observation key', async () => {
    expect(recordFeedControlPacket(control(1, 30))).toBe(true);
    expect(recordFeedControlPacket(control(2, 10))).toBe(true);
    expect(recordFeedControlPacket(control(1, 30))).toBe(false);
    // Same observation, other db id: still the same arrival.
    expect(recordFeedControlPacket({ ...control(9, 30), observation_id: 1 })).toBe(false);
    await flush();
    expect(controlIds()).toEqual([2, 1]);
  });

  it('bounds the held control packets at FEED_CONTROL_MAX, dropping the oldest', async () => {
    for (let i = 1; i <= FEED_CONTROL_MAX + 5; i += 1) recordFeedControlPacket(control(i, i));
    await flush();
    const ids = controlIds();
    expect(ids).toHaveLength(FEED_CONTROL_MAX);
    expect(ids[0]).toBe(6);
    expect(recordFeedControlPacket(control(1, 1))).toBe(true);
  });

  it('is not emptied by clearFeedRows (a filter reload), only by a reset', async () => {
    recordFeedControlPacket(control(1, 1));
    recordFeedMessage(msg(1, 100));
    await flush();
    clearFeedRows();
    await flush();
    expect(getFeedSnapshot().rows).toHaveLength(0);
    expect(controlIds()).toEqual([1]);
    resetFeedStore();
    expect(controlIds()).toEqual([]);
  });

  it('records nothing and decodes nothing while inactive', () => {
    resetFeedStore();
    expect(recordFeedControlPacket(control(1, 1))).toBe(false);
    expect(getFeedSnapshot().controlPackets).toEqual([]);
  });

  it('seeds from the raw buffer on activation, once', async () => {
    resetFeedStore();
    recordRawPacket(control(1, 5));
    recordRawPacket(control(2, 6, { payload_type: 'GroupText', data: `15${'00'.repeat(10)}` }));
    activateFeedStore();
    await flush();
    expect(controlIds()).toEqual([1]);
    // A second activation does not seed again over what has been recorded since.
    resetRawPacketStore();
    activateFeedStore();
    await flush();
    expect(controlIds()).toEqual([1]);
  });
});

describe('direct messages without read_private', () => {
  const dm = (id: number) => msg(id, 100 + id, { type: 'PRIV', conversation_key: 'bb'.repeat(32) });

  beforeEach(() => {
    resetFeedStore();
    activateFeedStore();
  });

  it('drops a PRIV row on insert and keeps channel rows', async () => {
    setFeedAcceptsPrivate(false);
    expect(recordFeedMessage(dm(1)).added).toBe(false);
    expect(recordFeedMessage(msg(2, 102)).added).toBe(true);
    mergeHistoryPage([dm(3), msg(4, 104)], { hasOlder: false });
    await Promise.resolve();
    expect(getFeedSnapshot().rows.map((r) => r.id)).toEqual([2, 4]);
  });

  it('accepts PRIV rows by default and again after a reset', () => {
    expect(recordFeedMessage(dm(1)).added).toBe(true);
    setFeedAcceptsPrivate(false);
    resetFeedStore();
    activateFeedStore();
    expect(recordFeedMessage(dm(2)).added).toBe(true);
  });
});
