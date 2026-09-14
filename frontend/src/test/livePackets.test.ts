import { describe, expect, it } from 'vitest';

import type { CommunityPacket, Contact } from '../types';
import {
  applyHopJitter,
  buildPrefixIndex,
  isOneByteHopToken,
  liveBoundsShouldFit,
  liveOpacity,
  observationFromCommunity,
  polylinePositions,
  uniqueGpsContact,
  waypointsFromCommunity,
  waypointsFromRaw,
} from '../utils/livePackets';
import type { RawPacket } from '../types';

function contact(prefix: string, lat: number, lon: number): Contact {
  return {
    public_key: `${prefix}${'0'.repeat(64 - prefix.length)}`,
    name: prefix,
    type: 2,
    flags: 0,
    direct_path: null,
    direct_path_len: -1,
    direct_path_hash_mode: 0,
    last_advert: null,
    lat,
    lon,
    last_seen: null,
    on_radio: false,
    favorite: false,
    last_contacted: null,
    last_read_at: null,
    first_seen: null,
  };
}

describe('live hop resolution', () => {
  it('never first-matches a 1-byte hop even when a unique contact exists', () => {
    const index = buildPrefixIndex([contact('cd', 45.7, 4.8)]);
    expect(isOneByteHopToken('cd')).toBe(true);
    expect(uniqueGpsContact('cd', index)).toBeNull();
  });

  it('resolves a unique 2-byte hop with GPS', () => {
    const hop = contact('ab12', 45.76, 4.84);
    const index = buildPrefixIndex([hop]);
    expect(uniqueGpsContact('ab12', index)?.public_key).toBe(hop.public_key);
  });

  it('leaves an ambiguous 2-byte hop unresolved', () => {
    const index = buildPrefixIndex([contact('ab12aa', 45.7, 4.8), contact('ab12bb', 46.2, 6.1)]);
    expect(uniqueGpsContact('ab12', index)).toBeNull();
  });
});

describe('community waypoints', () => {
  const packet: CommunityPacket = {
    v: 1,
    event_id: 'e1',
    hash8: 'deadbeef',
    type: 'ack',
    path: ['fe10', 'cd'],
    hop_count: 2,
    hops: [
      { token: 'fe10', lat: 45.78, lon: 4.86 },
      { token: 'cd', unresolved: true },
    ],
    iata: 'LYS',
    t: 1,
    ear_id: 'ear-1',
  };

  it('fades toward the ear and never invents an unresolved segment', () => {
    const ear = { lat: 45.7256, lon: 5.0811 };
    const waypoints = waypointsFromCommunity(packet, ear);
    expect(waypoints.map((point) => point.kind)).toEqual(['hop', 'fade']);
    expect(polylinePositions(waypoints)).toEqual([[45.78, 4.86]]);
    expect(waypoints.some((point) => point.token === 'cd' && point.kind === 'hop')).toBe(false);
  });

  it('ignores unknown schema versions', () => {
    expect(observationFromCommunity({ ...packet, v: 2 })).toBeNull();
  });

  it('places a community ear on any IATA centroid, not a 5-city allow-list', () => {
    const jfk = observationFromCommunity({ ...packet, event_id: 'e-jfk', iata: 'JFK' });
    const nrt = observationFromCommunity({ ...packet, event_id: 'e-nrt', iata: 'NRT' });
    expect(jfk?.ear).not.toBeNull();
    expect(nrt?.ear).not.toBeNull();
    expect(Math.abs((jfk?.ear?.lat ?? 0) - 40.6394)).toBeLessThan(0.2);
    expect(Math.abs((nrt?.ear?.lat ?? 0) - 35.7686)).toBeLessThan(0.2);
    expect(observationFromCommunity({ ...packet, iata: 'ZZZ' })?.ear).toBeNull();
  });

  it('pulses the ear when a community packet has no resolved hops', () => {
    const direct: CommunityPacket = {
      ...packet,
      path: [],
      hop_count: 0,
      hops: [],
    };
    const waypoints = waypointsFromCommunity(direct, { lat: 45.7, lon: 4.8 });
    expect(waypoints).toEqual([{ lat: 45.7, lon: 4.8, token: 'ear-1', kind: 'ear' }]);
  });

  it('spreads two ears that share an IATA', () => {
    const a = observationFromCommunity({ ...packet, ear_id: 'ear-a', iata: 'LYS' });
    const b = observationFromCommunity({ ...packet, ear_id: 'ear-b', iata: 'LYS' });
    expect(a?.ear).not.toEqual(b?.ear);
  });
});

describe('local vs community opacity', () => {
  it('keeps local rain more opaque when the same hash8 exists on both feeds', () => {
    expect(liveOpacity('local', 0, true)).toBeGreaterThan(liveOpacity('community', 0, true));
    expect(liveOpacity('community', 0, true)).toBeLessThan(liveOpacity('community', 0, false));
  });
});

describe('live bounds', () => {
  it('auto-fits only the first time ears appear', () => {
    expect(liveBoundsShouldFit(false, 0)).toBe(false);
    expect(liveBoundsShouldFit(false, 1)).toBe(true);
    expect(liveBoundsShouldFit(true, 3)).toBe(false);
  });
});

describe('hop jitter', () => {
  it('offsets hop waypoints but not the ear', () => {
    const jittered = applyHopJitter(
      [
        { lat: 45.7, lon: 4.8, token: 'ab12', kind: 'hop' },
        { lat: 45.72, lon: 5.08, token: 'ear', kind: 'ear' },
      ],
      'seed'
    );
    expect(jittered[0].lat).not.toBe(45.7);
    expect(jittered[1]).toEqual({ lat: 45.72, lon: 5.08, token: 'ear', kind: 'ear' });
  });
});

describe('raw waypoints', () => {
  it('does not invent a local segment for a 1-byte path hop', () => {
    const raw: RawPacket = {
      id: 1,
      timestamp: Date.now(),
      data: '00',
      payload_type: 'OTHER',
      snr: null,
      rssi: null,
      decrypted: false,
      decrypted_info: null,
    };
    const index = buildPrefixIndex([contact('ab', 45.7, 4.8)]);
    expect(waypointsFromRaw(raw, index, { lat: 45.72, lon: 5.08 }, 'ear')).toEqual([]);
  });
});
