import { describe, expect, it, beforeEach } from 'vitest';

import {
  MAX_LIVE_COMMUNITY_PACKETS,
  applyLiveStatus,
  getCommunityPackets,
  getLiveConnectionState,
  isSilentLiveClose,
  liveBannerI18nKey,
  recordCommunityPacket,
  resetLivePacketStore,
  setLiveCloseCode,
  setLiveOptOut,
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

  it('stores fixture close and opt-out flags', () => {
    setLiveCloseCode(4001);
    setLiveOptOut(true);
    expect(getLiveConnectionState()).toMatchObject({
      closeCode: null,
      optOut: true,
      banner: 'opt_out',
      reconnecting: false,
    });
  });

  it('applies backend community_live close codes', () => {
    applyLiveStatus({ close_code: 4002, opted_out: false });
    expect(getLiveConnectionState()).toMatchObject({
      closeCode: 4002,
      optOut: false,
      inactiveObserver: true,
      connected: false,
      reconnecting: false,
      banner: 'inactive',
    });
    expect(liveBannerI18nKey(getLiveConnectionState())).toBe('live.bannerInactive');
  });

  it('treats 4003 as 4005 and never raises a user-visible error', () => {
    applyLiveStatus({ close_code: LIVE_CLOSE_SLOT_BUSY, opted_out: false, connected: false });
    const afterBusy = getLiveConnectionState();
    expect(afterBusy.closeCode).toBe(LIVE_CLOSE_SUPERSEDED);
    expect(afterBusy.banner).toBeNull();
    expect(afterBusy.inactiveObserver).toBe(false);
    expect(afterBusy.reconnecting).toBe(true);
    expect(liveBannerI18nKey(afterBusy)).toBeNull();
    expect(isSilentLiveClose(LIVE_CLOSE_SLOT_BUSY)).toBe(true);

    applyLiveStatus({ close_code: LIVE_CLOSE_SUPERSEDED, opted_out: false, connected: false });
    const afterSuperseded = getLiveConnectionState();
    expect(afterSuperseded.closeCode).toBe(LIVE_CLOSE_SUPERSEDED);
    expect(afterSuperseded.banner).toBeNull();
    expect(afterSuperseded.reconnecting).toBe(true);
    expect(liveBannerI18nKey(afterSuperseded)).toBeNull();
    expect(isSilentLiveClose(LIVE_CLOSE_SUPERSEDED)).toBe(true);
  });

  it('setLiveCloseCode(4005) does not produce an error banner', () => {
    setLiveCloseCode(4005);
    const state = getLiveConnectionState();
    expect(state.closeCode).toBe(4005);
    expect(state.banner).toBeNull();
    expect(state.reconnecting).toBe(true);
    expect(liveBannerI18nKey(state)).toBeNull();
  });

  it('tolerates a missing reconnecting field on live status', () => {
    applyLiveStatus({ close_code: null, opted_out: false, connected: true });
    expect(getLiveConnectionState()).toMatchObject({
      connected: true,
      reconnecting: false,
      banner: null,
    });
  });

  it('distinguishes connected, reconnecting, opt-out, and the 24h gate', () => {
    applyLiveStatus({ close_code: null, opted_out: false, connected: true });
    expect(getLiveConnectionState()).toMatchObject({
      connected: true,
      reconnecting: false,
      optOut: false,
      banner: null,
    });

    applyLiveStatus({ close_code: 4001, opted_out: false, connected: false });
    expect(getLiveConnectionState()).toMatchObject({
      connected: false,
      reconnecting: true,
      banner: null,
    });

    applyLiveStatus({ opted_out: true });
    expect(getLiveConnectionState()).toMatchObject({
      optOut: true,
      connected: false,
      reconnecting: false,
      banner: 'opt_out',
    });
    expect(liveBannerI18nKey(getLiveConnectionState())).toBe('live.bannerOptOut');
  });
});
