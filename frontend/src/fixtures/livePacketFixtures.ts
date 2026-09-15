import type { CommunityPacket } from '../types';
import { recordCommunityPacket, relancerLive, setLiveCloseCode } from '../stores/livePacketStore';
import { LIVE_STAGGER_MS } from '../utils/livePackets';

/**
 * Offline community live feed for local UI work. The browser never calls Stats.
 */
export const LIVE_PACKET_FIXTURES: CommunityPacket[] = [
  {
    v: 2,
    event_id: 'fix-advert-lys',
    hash8: 'deadbeef',
    type: 'advert',
    path: ['ab12', 'cd34'],
    hop_count: 2,
    hops: [
      { token: 'ab12', lat: 45.76, lon: 4.84, confidence: 'exact', name: 'Lyon-Nord' },
      { token: 'cd34', lat: 45.74, lon: 4.92, confidence: 'exact' },
    ],
    ear: { lat: 45.7256, lon: 5.0811, source: 'advert' },
    snr: -4.2,
    iata: 'LYS',
    t: 0,
    ear_id: 'ear-lys-1',
  },
  {
    v: 2,
    event_id: 'fix-text-lys',
    hash8: 'cafef00d',
    type: 'text',
    path: ['11aa', '22bb', '33cc'],
    hop_count: 3,
    hops: [
      { token: '11aa', lat: 45.69, lon: 4.79, confidence: 'exact' },
      { token: '22bb', lat: 45.71, lon: 4.88, confidence: 'probable', reason: 'geo_filtered' },
      { token: '33cc', lat: 45.73, lon: 4.99, confidence: 'exact' },
    ],
    ear: { lat: 45.7256, lon: 5.0811, source: 'iata' },
    snr: 6.5,
    iata: 'LYS',
    t: 0,
    ear_id: 'ear-lys-1',
  },
  {
    v: 2,
    event_id: 'fix-ack-unresolved',
    hash8: 'a1b2c3d4',
    type: 'ack',
    path: ['fe10', 'cd'],
    hop_count: 2,
    hops: [
      { token: 'fe10', lat: 45.78, lon: 4.86, confidence: 'exact' },
      { token: 'cd', confidence: 'unresolved', reason: 'ambiguous_prefix' },
    ],
    ear: { lat: 45.7256, lon: 5.0811, source: 'advert' },
    snr: -11,
    iata: 'LYS',
    t: 0,
    ear_id: 'ear-lys-2',
  },
  {
    v: 2,
    event_id: 'fix-trace-cdg',
    hash8: '01020304',
    type: 'trace',
    path: ['aa01', 'bb02'],
    hop_count: 2,
    hops: [
      { token: 'aa01', lat: 48.86, lon: 2.35, confidence: 'exact' },
      { token: 'bb02', lat: 48.95, lon: 2.45, confidence: 'exact' },
    ],
    ear: { lat: 49.0097, lon: 2.5479, source: 'advert' },
    snr: 1.2,
    iata: 'CDG',
    t: 0,
    ear_id: 'ear-cdg-1',
  },
  {
    v: 2,
    event_id: 'fix-other-gva',
    hash8: '99887766',
    type: 'other',
    path: ['99aa'],
    hop_count: 1,
    hops: [{ token: '99aa', lat: 46.2, lon: 6.14, confidence: 'exact' }],
    ear: { lat: 46.2381, lon: 6.1089, source: 'iata' },
    iata: 'GVA',
    t: 0,
    ear_id: 'ear-gva-1',
  },
];

const playTimers: number[] = [];

export function stopLivePacketFixtures(): void {
  for (const timer of playTimers) {
    window.clearTimeout(timer);
  }
  playTimers.length = 0;
}

/** Play a few community_packet frames into the live store (no Stats call). */
export function playLivePacketFixtures(staggerMs: number = LIVE_STAGGER_MS): void {
  stopLivePacketFixtures();
  const now = Date.now();
  LIVE_PACKET_FIXTURES.forEach((fixture, index) => {
    playTimers.push(
      window.setTimeout(() => {
        recordCommunityPacket({ ...fixture, t: now + index * staggerMs });
      }, index * staggerMs)
    );
  });
}

/** Fixture stand-in for close 4001 — Relancer refreshes the live feed. */
export function simulateLiveJwtExpired(): void {
  setLiveCloseCode(4001);
}

export function relancerLiveFromFixture(): void {
  relancerLive();
  playLivePacketFixtures();
}
