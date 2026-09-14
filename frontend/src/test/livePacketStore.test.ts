import { describe, expect, it, beforeEach } from 'vitest';

import {
  MAX_LIVE_COMMUNITY_PACKETS,
  applyLiveStatus,
  getCommunityPackets,
  getLiveConnectionState,
  recordCommunityPacket,
  resetLivePacketStore,
  setLiveCloseCode,
  setLiveOptOut,
} from '../stores/livePacketStore';
import type { CommunityPacket } from '../types';

function packet(id: string): CommunityPacket {
  return {
    v: 1,
    event_id: id,
    hash8: id.slice(0, 8).padEnd(8, '0'),
    type: 'advert',
    path: ['ab12'],
    hop_count: 1,
    hops: [{ token: 'ab12', lat: 45.7, lon: 4.8 }],
    iata: 'LYS',
    t: 1,
    ear_id: 'ear',
  };
}

describe('livePacketStore', () => {
  beforeEach(() => {
    resetLivePacketStore();
  });

  it('drops the oldest community packets at the cap', () => {
    for (let i = 0; i < MAX_LIVE_COMMUNITY_PACKETS + 3; i++) {
      recordCommunityPacket(packet(`evt-${i}`));
    }
    const stored = getCommunityPackets();
    expect(stored).toHaveLength(MAX_LIVE_COMMUNITY_PACKETS);
    expect(stored[0].event_id).toBe('evt-3');
    expect(stored[stored.length - 1]?.event_id).toBe(`evt-${MAX_LIVE_COMMUNITY_PACKETS + 2}`);
  });

  it('dedupes by event_id', () => {
    recordCommunityPacket(packet('same'));
    recordCommunityPacket(packet('same'));
    expect(getCommunityPackets()).toHaveLength(1);
  });

  it('stores fixture close and opt-out flags', () => {
    setLiveCloseCode(4001);
    setLiveOptOut(true);
    expect(getLiveConnectionState()).toMatchObject({ closeCode: 4001, optOut: true });
  });

  it('applies backend community_live close codes', () => {
    applyLiveStatus({ close_code: 4002, opted_out: false });
    expect(getLiveConnectionState()).toMatchObject({
      closeCode: 4002,
      optOut: false,
      inactiveObserver: true,
    });
  });
});
