import { describe, expect, it } from 'vitest';

import type { RawPacket } from '../types';
import {
  estimateServerClockOffsetSec,
  HISTORY_PERIOD_SECONDS,
  measureServerClockOffsetSec,
  resolveHistoryBounds,
} from '../utils/rawPacketHistoryBounds';

const SERVER_NOW_S = 1_791_000_000;

function packet(serverTimestamp: number, receivedAtMs?: number, id = 1): RawPacket {
  return {
    id,
    observation_id: id,
    timestamp: serverTimestamp,
    data: 'aa',
    payload_type: 'TEXT',
    snr: null,
    rssi: null,
    decrypted: false,
    decrypted_info: null,
    ...(receivedAtMs === undefined ? {} : { received_at_ms: receivedAtMs }),
  };
}

/** Packets the server stamped at SERVER_NOW_S - n, received by a browser skewed by skewSec. */
function livePackets(skewSec: number, count = 5): RawPacket[] {
  return Array.from({ length: count }, (_, i) => {
    const serverTs = SERVER_NOW_S - (count - 1 - i);
    return packet(serverTs, (serverTs + skewSec) * 1000 + 40, i + 1);
  });
}

/** Server-side filter applied by GET /api/packets/history. */
function serverMatches(ts: number, bounds: { since?: number; until?: number }): boolean {
  return (
    (bounds.since === undefined || ts >= bounds.since) &&
    (bounds.until === undefined || ts <= bounds.until)
  );
}

describe('estimateServerClockOffsetSec', () => {
  it('returns 0 without live packets', () => {
    expect(estimateServerClockOffsetSec([])).toBe(0);
    expect(estimateServerClockOffsetSec([packet(SERVER_NOW_S)])).toBe(0);
  });

  it('tells "not measured" (null) apart from a measured zero skew', () => {
    expect(measureServerClockOffsetSec([packet(SERVER_NOW_S)])).toBeNull();
    expect(measureServerClockOffsetSec(livePackets(0))).toBe(0);
  });

  for (const skewSec of [0, 8 * 60, -8 * 60, 2 * 3600, -2 * 3600]) {
    it(`measures a browser clock ${skewSec}s ahead of the server`, () => {
      expect(estimateServerClockOffsetSec(livePackets(skewSec))).toBe(skewSec);
    });
  }

  it('ignores one outlier (late WS frame) via the median', () => {
    const packets = livePackets(300);
    packets[2] = { ...packets[2], received_at_ms: (packets[2].timestamp + 900) * 1000 };
    expect(estimateServerClockOffsetSec(packets)).toBe(300);
  });

  it('skips history-replayed packets without receipt time', () => {
    expect(estimateServerClockOffsetSec([...livePackets(-120), packet(1_000)])).toBe(-120);
  });
});

describe('resolveHistoryBounds vs server clock', () => {
  // serverBehindSec > 0: the server clock is that many seconds behind the browser.
  for (const serverBehindSec of [0, 8 * 60, -8 * 60, 2 * 3600, -2 * 3600]) {
    it(`keeps the last hour of packets with the server ${serverBehindSec}s behind`, () => {
      const browserNowMs = (SERVER_NOW_S + serverBehindSec) * 1000;
      const offset = estimateServerClockOffsetSec(livePackets(serverBehindSec));
      const bounds = resolveHistoryBounds('1h', '', '', offset, browserNowMs);

      expect(bounds.invalid).toBe(false);
      expect(bounds.until).toBeUndefined();
      expect(bounds.since).toBe(SERVER_NOW_S - HISTORY_PERIOD_SECONDS['1h']);
      // Stored server timestamps: just now, 59 min ago, 61 min ago.
      expect(serverMatches(SERVER_NOW_S, bounds)).toBe(true);
      expect(serverMatches(SERVER_NOW_S - 59 * 60, bounds)).toBe(true);
      expect(serverMatches(SERVER_NOW_S - 61 * 60, bounds)).toBe(false);
    });
  }

  it('without live traffic, a server ahead still returns its latest packets (no until)', () => {
    const browserNowMs = (SERVER_NOW_S - 600) * 1000; // server 10 min ahead
    const bounds = resolveHistoryBounds('24h', '', '', 0, browserNowMs);
    expect(serverMatches(SERVER_NOW_S, bounds)).toBe(true);
  });

  it('sends a custom range as typed, ignoring the offset', () => {
    const since = '2026-10-01T10:00';
    const until = '2026-10-01T12:00';
    const bounds = resolveHistoryBounds('custom', since, until, 3600);
    expect(bounds).toEqual({
      since: Math.floor(new Date(since).getTime() / 1000),
      until: Math.floor(new Date(until).getTime() / 1000),
      invalid: false,
    });
    expect(resolveHistoryBounds('custom', until, since).invalid).toBe(true);
  });
});
