import { describe, expect, it } from 'vitest';

import { observationFromCommunity, asCommunityPacket } from '../utils/livePackets';

/**
 * Captured from `sanitize_community_packet` in app/services/community_live.py, itself
 * fed the frame `sanitize_event` emits in meshloom-stats. Regenerate both sides together
 * if the wire shape changes; a drift here is invisible until the map goes blank.
 */
const RELAY_FRAME = {
  ear: { lat: 43.66, lon: 7.21, source: 'advert' },
  ear_id: 'abc',
  event_id: 'evt-v2',
  hash8: 'deadbeef',
  hop_count: 2,
  hops: [
    {
      confidence: 'exact',
      lat: 45.7,
      lon: 4.8,
      name: 'repeater',
      pubkey: 'ab12cdcd',
      token: 'ab12',
    },
    { confidence: 'unresolved', reason: 'ambiguous_prefix', token: 'cd' },
  ],
  iata: 'LYS',
  path: ['ab12', 'cd'],
  snr: -8.5,
  t: 1710000000000,
  type: 'advert',
  v: 2,
};

describe('stats -> relay -> browser frame', () => {
  it('accepts the frame the relay actually emits', () => {
    expect(asCommunityPacket(RELAY_FRAME)).not.toBeNull();
  });

  it('draws the resolved hop and the ear, and nothing for the unresolved hop', () => {
    const obs = observationFromCommunity(asCommunityPacket(RELAY_FRAME)!)!;
    expect(obs.type).toBe('advert');
    expect(obs.ear).toEqual({ lat: 43.66, lon: 7.21, source: 'advert' });
    expect(obs.waypoints.map((point) => point.token)).toEqual(['ab12', 'abc']);
    expect(obs.waypoints.some((point) => point.token === 'cd')).toBe(false);
  });

  it('degrades the segment after a skipped hop instead of claiming an RF jump', () => {
    const obs = observationFromCommunity(asCommunityPacket(RELAY_FRAME)!)!;
    const ear = obs.waypoints.find((point) => point.kind === 'ear');
    expect(ear?.confidence).toBe('probable');
    expect(ear?.reason).toBe('skipped_unresolved');
  });

  it('survives a relay frame with no ear position', () => {
    const obs = observationFromCommunity(asCommunityPacket({ ...RELAY_FRAME, ear: null })!)!;
    expect(obs.ear).toBeNull();
    expect(obs.waypoints.map((point) => point.token)).toEqual(['ab12']);
  });
});
