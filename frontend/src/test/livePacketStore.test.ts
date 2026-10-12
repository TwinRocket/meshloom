import { describe, expect, it, beforeEach } from 'vitest';

import {
  MAX_LIVE_COMMUNITY_PACKETS,
  applyLiveStatus,
  getCommunityPackets,
  getLiveConnectionState,
  liveBannerI18nKey,
  recordCommunityPacket,
  resetLivePacketStore,
} from '../stores/livePacketStore';
import type { CommunityPacket } from '../types';
import { LIVE_CLOSE_SLOT_BUSY, LIVE_CLOSE_SUPERSEDED } from '../types';

function packet(id: string): CommunityPacket {
  return {
    v: 2,
    event_id: id,
    hash8: 'deadbeef',
    type: 'advert',
    path: ['ab12'],
    hop_count: 1,
    hops: [{ token: 'ab12', lat: 45.7, lon: 4.8, confidence: 'exact' }],
    ear: { lat: 45.72, lon: 5.08, source: 'advert' },
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

  it('drops malformed or v1 frames instead of storing a partial packet', () => {
    recordCommunityPacket({ ...packet('v1'), v: 1 });
    recordCommunityPacket({ ...packet('bad'), hash8: 'nope' });
    expect(getCommunityPackets()).toHaveLength(0);
  });

  it('never raises a banner for a close code, 4002 included', () => {
    for (const code of [4001, 4002, LIVE_CLOSE_SLOT_BUSY, 4004, LIVE_CLOSE_SUPERSEDED] as const) {
      applyLiveStatus({ close_code: code, opted_out: false, connected: false });
      expect(getLiveConnectionState()).toMatchObject({
        optOut: false,
        authError: null,
        banner: null,
      });
      expect(liveBannerI18nKey(getLiveConnectionState())).toBeNull();
    }
  });

  it('switches between connected and opt-out', () => {
    applyLiveStatus({ close_code: null, opted_out: false, connected: true });
    expect(getLiveConnectionState()).toMatchObject({ optOut: false, banner: null });

    applyLiveStatus({ opted_out: true });
    expect(getLiveConnectionState()).toMatchObject({ optOut: true, banner: 'opt_out' });
    expect(liveBannerI18nKey(getLiveConnectionState())).toBe('live.bannerOptOut');

    resetLivePacketStore();
    expect(getLiveConnectionState()).toMatchObject({ optOut: false, banner: null });
  });

  it('maps a given-up token refusal to a banner, not to reconnecting', () => {
    applyLiveStatus({
      close_code: null,
      opted_out: false,
      connected: false,
      state: 'auth_rejected',
      auth_error: 'clock_skew',
      clock_skew_s: 125.4,
    });
    const state = getLiveConnectionState();
    expect(state.authError).toBe('clock_skew');
    expect(state.clockSkewS).toBe(125);
    expect(liveBannerI18nKey(state)).toBe('live.bannerClockSkew');

    applyLiveStatus({
      close_code: null,
      opted_out: false,
      connected: false,
      state: 'auth_rejected',
    });
    expect(liveBannerI18nKey(getLiveConnectionState())).toBe('live.bannerTokenRejected');

    applyLiveStatus({ close_code: null, opted_out: false, connected: true, state: 'connected' });
    expect(getLiveConnectionState().authError).toBeNull();
    expect(liveBannerI18nKey(getLiveConnectionState())).toBeNull();
  });

  it('opt-out wins over a token refusal', () => {
    applyLiveStatus({
      close_code: null,
      opted_out: true,
      connected: false,
      auth_error: 'token_rejected',
    });
    expect(getLiveConnectionState().authError).toBeNull();
    expect(liveBannerI18nKey(getLiveConnectionState())).toBe('live.bannerOptOut');
  });
});
