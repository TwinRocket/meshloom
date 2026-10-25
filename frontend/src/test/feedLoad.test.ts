import { beforeEach, describe, expect, it, vi } from 'vitest';

import {
  FEED_CONTROL_MAX,
  FEED_MAX_ROWS,
  activateFeedStore,
  getFeedSnapshot,
  mergeHistoryPage,
  recordFeedControlPacket,
  recordFeedMessage,
  resetFeedStore,
  subscribeFeedStore,
} from '../stores/feedStore';
import { resetRawPacketStore } from '../stores/rawPacketStore';
import type { Message, RawPacket } from '../types';

/**
 * Load test of the feed store at the Pi 4 budget (10 000 messages, bursts of 17 frames/s):
 * 2 000 message frames and 500 raw packets. It asserts counts, never wall time (a CI runner is
 * too unstable for that): comparisons per insertion, one emit per burst, bounded memory. Timing
 * numbers are printed by the opt-in test at the bottom (`FEED_PERF=1`).
 */

const FRAMES = 2000;
const PACKETS = 500;
const log2 = (n: number) => Math.ceil(Math.log2(n));

let compared = 0;
/** A message whose `received_at` counts how often the store reads it: one read per comparison side. */
function countedMessage(id: number, receivedAt: number): Message {
  let value = receivedAt;
  const m = {
    id,
    type: 'CHAN',
    conversation_key: '8B3387E9C5CDEA6AC9E5EDBAA115CD72',
    text: `m${id}`,
    sender_timestamp: receivedAt,
    paths: null,
    txt_type: 0,
    signature: null,
    sender_key: null,
    outgoing: false,
    acked: 0,
    sender_name: null,
  } as unknown as Message;
  Object.defineProperty(m, 'received_at', {
    enumerable: true,
    get: () => {
      compared += 1;
      return value;
    },
    set: (v: number) => {
      value = v;
    },
  });
  return m;
}

function packet(id: number, timestamp: number): RawPacket {
  return {
    id,
    observation_id: id,
    timestamp,
    data: `0100bbaa${id.toString(16).padStart(4, '0')}${'11'.repeat(16)}`,
    payload_type: 'Request',
    snr: null,
    rssi: null,
    decrypted: false,
    decrypted_info: null,
  };
}

/** A deterministic shuffle: live frames are not strictly ordered. */
function shuffled(n: number): number[] {
  const out = Array.from({ length: n }, (_, i) => i + 1);
  let seed = 12345;
  for (let i = n - 1; i > 0; i -= 1) {
    seed = (seed * 1103515245 + 12345) & 0x7fffffff;
    const j = seed % (i + 1);
    [out[i], out[j]] = [out[j], out[i]];
  }
  return out;
}

const flush = () => Promise.resolve();

beforeEach(() => {
  resetFeedStore();
  resetRawPacketStore();
  activateFeedStore();
  compared = 0;
  // The whole history is loaded: a live row older than the window is stored, not left to a
  // later page (see `recordFeedMessage`).
  mergeHistoryPage([], { hasOlder: false });
});

describe('feed store under load', () => {
  it.each([
    ['in order', (n: number) => Array.from({ length: n }, (_, i) => i + 1)],
    ['out of order', shuffled],
  ])('inserts %s with O(log n) comparisons', async (_label, order) => {
    for (const id of order(FRAMES)) recordFeedMessage(countedMessage(id, 1_700_000_000 + id));
    await flush();
    expect(getFeedSnapshot().rows).toHaveLength(FRAMES);
    // Each insertion is a binary search: at most ceil(log2(n + 1)) + 1 probes, two reads each
    // (the probe and the inserted row), whatever the arrival order.
    const perInsert = compared / FRAMES;
    expect(perInsert).toBeLessThanOrEqual(2 * (log2(FRAMES + 1) + 1));
    // The rows come out sorted by (received_at, id).
    const rows = getFeedSnapshot().rows;
    for (let i = 1; i < rows.length; i += 1) {
      expect(rows[i - 1].id).toBeLessThan(rows[i].id);
    }
  });

  it('publishes one snapshot per burst of messages and packets', async () => {
    const listener = vi.fn();
    const off = subscribeFeedStore(listener);
    for (let id = 1; id <= FRAMES; id += 1)
      recordFeedMessage(countedMessage(id, 1_700_000_000 + id));
    for (let id = 1; id <= PACKETS; id += 1)
      recordFeedControlPacket(packet(id, 1_700_000_000 + id));
    expect(listener).not.toHaveBeenCalled();
    await flush();
    expect(listener).toHaveBeenCalledTimes(1);
    off();
  });

  it('keeps memory bounded: at most FEED_MAX_ROWS messages and FEED_CONTROL_MAX control rows', async () => {
    const extraRows = 500;
    const extraPackets = 200;
    for (let id = 1; id <= FEED_MAX_ROWS + extraRows; id += 1)
      recordFeedMessage(countedMessage(id, 1_700_000_000 + id));
    for (let id = 1; id <= FEED_CONTROL_MAX + extraPackets; id += 1)
      recordFeedControlPacket(packet(id, 1_700_000_000 + id));
    await flush();
    const snapshot = getFeedSnapshot();
    expect(snapshot.rows).toHaveLength(FEED_MAX_ROWS);
    expect(snapshot.controlPackets).toHaveLength(FEED_CONTROL_MAX);
    // The oldest go, the newest stay.
    expect(snapshot.rows[0].id).toBe(extraRows + 1);
    expect(snapshot.rows[snapshot.rows.length - 1].id).toBe(FEED_MAX_ROWS + extraRows);
    expect(snapshot.controlPackets[0].id).toBe(extraPackets + 1);
    expect(snapshot.hasOlder).toBe(true); // dropping the oldest rows reopens the history below
  });

  it('orders 500 packets by comparing timestamps a logarithmic number of times', async () => {
    let reads = 0;
    const counted = (id: number): RawPacket => {
      const p = packet(id, 1_700_000_000 + id);
      let ts = p.timestamp;
      Object.defineProperty(p, 'timestamp', {
        enumerable: true,
        get: () => {
          reads += 1;
          return ts;
        },
        set: (v: number) => {
          ts = v;
        },
      });
      return p;
    };
    for (const id of shuffled(PACKETS)) recordFeedControlPacket(counted(id));
    await flush();
    expect(getFeedSnapshot().controlPackets).toHaveLength(PACKETS);
    // Two reads per probe; a few more come from the journal's own decoding of the packet.
    expect(reads / PACKETS).toBeLessThanOrEqual(2 * (log2(PACKETS + 1) + 1) + 6);
  });
});

// Numbers for docs/dev/feed.md; out of the gate. `FEED_PERF=1 npx vitest run src/test/feedLoad.test.ts`
describe.skipIf(!process.env.FEED_PERF)('slow: feed store timings', () => {
  it('records and publishes the load within a loose bound', async () => {
    const t0 = performance.now();
    for (const id of shuffled(FRAMES)) recordFeedMessage(countedMessage(id, 1_700_000_000 + id));
    const t1 = performance.now();
    for (let id = 1; id <= PACKETS; id += 1)
      recordFeedControlPacket(packet(id, 1_700_000_000 + id));
    const t2 = performance.now();
    await flush();
    const t3 = performance.now();
    const line = `${FRAMES} messages (shuffled) ${(t1 - t0).toFixed(1)} ms, ${PACKETS} packets ${(t2 - t1).toFixed(1)} ms, publish ${(t3 - t2).toFixed(1)} ms`;
    process.stdout.write(`[feed perf] ${line}\n`);
    expect(t3 - t0).toBeLessThan(5000);
  });
});
