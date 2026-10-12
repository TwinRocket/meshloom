import { afterEach, describe, expect, it, vi } from 'vitest';

import type { RawPacket } from '../types';
import {
  getRawPacketStatsSession,
  recordRawPacket,
  resetRawPacketStore,
} from '../stores/rawPacketStore';
import { buildRawPacketStatsSnapshot, summarizeRawPacketForStats } from '../utils/rawPacketStats';

const SERVER_NOW_S = 1_791_000_000;

function livePacket(serverTimestamp: number, id = 1): RawPacket {
  return {
    id,
    observation_id: id,
    timestamp: serverTimestamp,
    data: '09046F17C47ED00A13E16AB5B94B1CC2D1A5059C6E5A6253C60D',
    payload_type: 'TEXT',
    snr: null,
    rssi: null,
    decrypted: false,
    decrypted_info: null,
  };
}

/** serverBehindMin > 0: the server clock is that many minutes behind the browser. */
function liveCountInWindow(
  serverBehindMin: number,
  window: '1m' | '5m' | '30m' | 'session'
): number {
  const browserNowMs = SERVER_NOW_S * 1000 + serverBehindMin * 60_000;
  vi.useFakeTimers();
  vi.setSystemTime(browserNowMs);
  resetRawPacketStore();
  recordRawPacket(livePacket(SERVER_NOW_S));
  vi.setSystemTime(browserNowMs + 30_000);
  return buildRawPacketStatsSnapshot(getRawPacketStatsSession(), window).packetCount;
}

describe('raw packet stats windows vs server clock', () => {
  afterEach(() => {
    vi.useRealTimers();
    resetRawPacketStore();
  });

  for (const skew of [0, 8, -8, 30, -30]) {
    it(`counts a live packet in every window with the server clock ${skew} min behind`, () => {
      expect(liveCountInWindow(skew, '1m')).toBe(1);
      expect(liveCountInWindow(skew, '5m')).toBe(1);
      expect(liveCountInWindow(skew, '30m')).toBe(1);
      expect(liveCountInWindow(skew, 'session')).toBe(1);
    });
  }

  it('keeps the server timestamp for packets without a receipt time (history replay)', () => {
    expect(summarizeRawPacketForStats(livePacket(SERVER_NOW_S)).timestamp).toBe(SERVER_NOW_S);
    expect(
      summarizeRawPacketForStats({ ...livePacket(SERVER_NOW_S), received_at_ms: 5_000_999 })
        .timestamp
    ).toBe(5_000);
  });
});
