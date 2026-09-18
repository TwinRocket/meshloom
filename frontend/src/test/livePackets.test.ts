import { describe, expect, it } from 'vitest';

import type { CommunityPacket, Contact, RadioConfig, RawPacket } from '../types';
import {
  asCommunityPacket,
  buildPrefixIndex,
  fanoutFromOrigin,
  hash8FromRaw,
  inferFloodForAdvert,
  packetHashFromRaw,
  isOneByteHopToken,
  liveOpacity,
  liveTypeColor,
  packetTypeFromRaw,
  observationBucketKey,
  observationCoalesceKey,
  observationFromCommunity,
  observationFromRaw,
  pinsIncludingLocalRadio,
  prependOrigin,
  resolveOriginPin,
  routeKindFromRawHex,
  uniqueGpsContact,
  waypointsFromCommunity,
  waypointsFromRaw,
  type LiveObservation,
} from '../utils/livePackets';

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

function packet(overrides: Partial<CommunityPacket> = {}): CommunityPacket {
  return {
    v: 2,
    event_id: 'e1',
    hash8: 'deadbeef',
    type: 'ack',
    path: ['fe10', 'cd'],
    hop_count: 2,
    hops: [
      { token: 'fe10', lat: 45.78, lon: 4.86, confidence: 'exact', name: 'Fe10' },
      { token: 'cd', confidence: 'unresolved', reason: 'ambiguous_prefix' },
    ],
    ear: { lat: 45.7256, lon: 5.0811, source: 'advert' },
    iata: 'LYS',
    t: 1_710_000_000_000,
    ear_id: 'ear-1',
    ...overrides,
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
  it('skips unresolved hops and shortcuts to the next real coordinate', () => {
    const waypoints = waypointsFromCommunity(packet());
    expect(waypoints.map((point) => point.kind)).toEqual(['hop', 'ear']);
    expect(waypoints[0]).toMatchObject({
      token: 'fe10',
      lat: 45.78,
      lon: 4.86,
      confidence: 'exact',
      label: 'Fe10',
    });
    expect(waypoints[1]).toMatchObject({
      kind: 'ear',
      lat: 45.7256,
      lon: 5.0811,
      confidence: 'probable',
      reason: 'skipped_unresolved',
    });
    expect(waypoints.some((point) => point.token === 'cd')).toBe(false);
    expect(waypoints.map((point) => [point.lat, point.lon])).toEqual([
      [45.78, 4.86],
      [45.7256, 5.0811],
    ]);
  });

  it('never invents coordinates for an unresolved hop', () => {
    const hops = packet({
      path: ['aa01'],
      hop_count: 1,
      hops: [{ token: 'aa01', confidence: 'unresolved', reason: 'no_candidate' }],
      ear: null,
    });
    const waypoints = waypointsFromCommunity(hops);
    expect(waypoints).toEqual([]);
    expect(observationFromCommunity(hops)?.waypoints).toEqual([]);
  });

  it('keeps exact and probable hops on their server coordinates', () => {
    const frame = packet({
      path: ['aa', 'bb'],
      hop_count: 2,
      hops: [
        { token: 'aa', lat: 45.1, lon: 4.1, confidence: 'exact' },
        { token: 'bb', lat: 45.2, lon: 4.2, confidence: 'probable', reason: 'geo_filtered' },
      ],
      ear: { lat: 45.3, lon: 4.3, source: 'iata' },
    });
    const waypoints = waypointsFromCommunity(frame);
    expect(waypoints).toEqual([
      { lat: 45.1, lon: 4.1, token: 'aa', kind: 'hop', confidence: 'exact' },
      {
        lat: 45.2,
        lon: 4.2,
        token: 'bb',
        kind: 'hop',
        confidence: 'probable',
        reason: 'geo_filtered',
      },
      { lat: 45.3, lon: 4.3, token: 'ear-1', kind: 'ear', confidence: 'exact' },
    ]);
  });

  it('marks the next resolved hop as a shortcut after an unresolved gap', () => {
    const frame = packet({
      path: ['aa', 'bb', 'cc'],
      hop_count: 3,
      hops: [
        { token: 'aa', lat: 45.1, lon: 4.1, confidence: 'exact' },
        { token: 'bb', confidence: 'unresolved', reason: 'no_position' },
        { token: 'cc', lat: 45.3, lon: 4.3, confidence: 'exact', name: 'Cc' },
      ],
      ear: null,
    });
    const waypoints = waypointsFromCommunity(frame);
    expect(waypoints).toHaveLength(2);
    expect(waypoints[1]).toMatchObject({
      token: 'cc',
      lat: 45.3,
      lon: 4.3,
      confidence: 'probable',
      reason: 'skipped_unresolved',
      label: 'Cc',
    });
  });

  it('pulses the ear when a community packet has no resolved hops', () => {
    const direct = packet({
      path: [],
      hop_count: 0,
      hops: [],
    });
    expect(waypointsFromCommunity(direct)).toEqual([
      { lat: 45.7256, lon: 5.0811, token: 'ear-1', kind: 'ear', confidence: 'exact' },
    ]);
  });

  it('uses the packet ear, never an IATA centroid fallback', () => {
    const withEar = observationFromCommunity(packet({ iata: 'JFK' }));
    expect(withEar?.ear).toEqual({ lat: 45.7256, lon: 5.0811, source: 'advert' });
    const noEar = observationFromCommunity(packet({ event_id: 'e-none', iata: 'NRT', ear: null }));
    expect(noEar?.ear).toBeNull();
    expect(noEar?.waypoints.some((point) => point.kind === 'ear')).toBe(false);
  });

  it('keeps legend colors on the observation type', () => {
    expect(liveTypeColor('advert')).toBe('#fde047');
    expect(liveTypeColor('text')).toBe('#22f0ff');
    expect(liveTypeColor('ack')).toBe('#39ff88');
    expect(liveTypeColor('trace')).toBe('#a78bfa');
    expect(liveTypeColor('other')).toBe('#78716c');
    expect(liveTypeColor('req')).toBe('#ff4528');
    expect(liveTypeColor('grp_txt')).toBe('#ff3d9a');
    expect(observationFromCommunity(packet())?.type).toBe('ack');
  });

  it('paints an unrecognised server token instead of dropping the frame', () => {
    const obs = observationFromCommunity(packet({ type: 'future_token' }));
    expect(obs).not.toBeNull();
    expect(obs?.type).toBe('future_token');
    expect(liveTypeColor(obs!.type)).toBe('#78716c');
    expect(asCommunityPacket({ ...packet(), type: 'ADVERT' })).toBeNull();
    expect(asCommunityPacket({ ...packet(), type: '' })).toBeNull();
  });

  it('maps the full MeshCore nibble, reserved values staying other', () => {
    const expected: Record<number, string> = {
      0x00: 'req',
      0x01: 'response',
      0x02: 'text',
      0x03: 'ack',
      0x04: 'advert',
      0x05: 'grp_txt',
      0x06: 'grp_data',
      0x07: 'anon_req',
      0x08: 'path',
      0x09: 'trace',
      0x0a: 'multipart',
      0x0b: 'control',
      0x0c: 'other',
      0x0d: 'other',
      0x0e: 'other',
      0x0f: 'raw_custom',
    };
    for (let nibble = 0; nibble <= 0x0f; nibble += 1) {
      expect(packetTypeFromRaw(nibble)).toBe(expected[nibble]);
    }
  });
});

describe('malformed community frames', () => {
  it('returns null for unknown schema versions', () => {
    expect(observationFromCommunity({ ...packet(), v: 1 })).toBeNull();
    expect(asCommunityPacket({ ...packet(), v: 3 })).toBeNull();
  });

  it('returns null instead of a half-filled observation', () => {
    expect(asCommunityPacket({ v: 2, event_id: 'e1' })).toBeNull();
    expect(observationFromCommunity({ ...packet(), event_id: '' })).toBeNull();
    expect(observationFromCommunity({ ...packet(), hash8: 'zz' })).toBeNull();
    expect(observationFromCommunity({ ...packet(), type: 'ADVERT' })).toBeNull();
    expect(observationFromCommunity({ ...packet(), type: '' })).toBeNull();
    expect(
      observationFromCommunity({
        ...packet(),
        hops: [{ token: 'aa', confidence: 'exact' }],
      })
    ).toBeNull();
    expect(
      observationFromCommunity({
        ...packet(),
        hops: [{ token: 'aa', lat: 45.1, lon: 4.1, confidence: 'exact' }],
        path: ['aa', 'extra'],
      })
    ).toBeNull();
    expect(
      observationFromCommunity({
        ...packet(),
        ear: { lat: 999, lon: 4, source: 'advert' },
      })
    ).toBeNull();
    expect(observationFromCommunity({ ...packet(), t: Number.NaN })).toBeNull();
  });
});

describe('local vs community opacity', () => {
  it('keeps local rain more opaque when the same hash8 exists on both feeds', () => {
    expect(liveOpacity('local', 0, true)).toBeGreaterThan(liveOpacity('community', 0, true));
    expect(liveOpacity('community', 0, true)).toBeLessThan(liveOpacity('community', 0, false));
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
    expect(waypointsFromRaw(raw, index, { lat: 45.72, lon: 5.08, source: 'local' }, 'ear')).toEqual(
      []
    );
  });

  it('resolves a unique geolocated contact as exact and leaves others undrawn', () => {
    const index = buildPrefixIndex([contact('ab12', 45.76, 4.84), contact('cd99', 0, 0)]);
    const parsedPath = {
      pathBytes: ['ab12', 'cd', 'ffff'],
    };
    const waypoints: LiveObservation['waypoints'] = [];
    let skipped = false;
    for (const token of parsedPath.pathBytes) {
      const match = uniqueGpsContact(token, index);
      if (match?.lat != null && match.lon != null) {
        waypoints.push({
          lat: match.lat,
          lon: match.lon,
          token,
          kind: 'hop',
          confidence: skipped ? 'probable' : 'exact',
          reason: skipped ? 'skipped_unresolved' : undefined,
          label: match.name ?? undefined,
        });
        skipped = false;
      } else {
        skipped = true;
      }
    }
    expect(waypoints).toEqual([
      {
        lat: 45.76,
        lon: 4.84,
        token: 'ab12',
        kind: 'hop',
        confidence: 'exact',
        label: 'ab12',
      },
    ]);
  });

  it('uses the radio config as a local ear', () => {
    const config: RadioConfig = {
      public_key: 'aa'.repeat(32),
      name: 'me',
      lat: 45.72,
      lon: 5.08,
      tx_power: 22,
      max_tx_power: 22,
      radio: { freq: 869.525, bw: 250, sf: 11, cr: 5 },
      path_hash_mode: 0,
      path_hash_mode_supported: true,
    };
    const raw: RawPacket = {
      id: 9,
      observation_id: 3,
      timestamp: Date.now(),
      data: '00',
      payload_type: 'OTHER',
      snr: -2,
      rssi: null,
      decrypted: false,
      decrypted_info: null,
    };
    const obs = observationFromRaw(raw, buildPrefixIndex([]), config);
    if (obs) {
      expect(obs.source).toBe('local');
      expect(obs.ear).toEqual({ lat: 45.72, lon: 5.08, source: 'local' });
    }
  });
});

describe('probable confidence survives observation building', () => {
  it('keeps the reason so the renderer can explain a dashed segment', () => {
    const cdg = observationFromCommunity(
      packet({
        event_id: 'e-cdg',
        type: 'trace',
        iata: 'CDG',
        path: ['aa'],
        hop_count: 1,
        hops: [
          { token: 'aa', lat: 48.8, lon: 2.3, confidence: 'probable', reason: 'geo_filtered' },
        ],
      })
    )!;
    const hop = cdg.waypoints.find((point) => point.kind === 'hop');
    expect(hop?.confidence).toBe('probable');
    expect(hop?.reason).toBe('geo_filtered');
  });
});

describe('firmware hash8 and origin resolution', () => {
  it('reads hash8 from firmware packet_hash, not the decoder djb2', () => {
    const packet: RawPacket = {
      id: 1,
      timestamp: Date.now(),
      data: '1100dead',
      payload_type: 'ADVERT',
      snr: null,
      rssi: null,
      decrypted: false,
      decrypted_info: null,
      packet_hash: '19D68FE91E75C7DE',
    };
    expect(hash8FromRaw(packet)).toBe('19d68fe9');
    expect(packetHashFromRaw(packet)).toBe('19d68fe91e75c7de');
    expect(
      observationBucketKey({ hash8: hash8FromRaw(packet), packetHash: packetHashFromRaw(packet) })
    ).toBe('19d68fe91e75c7de');
    expect(routeKindFromRawHex('1100dead')).toBe('flood');
    expect(routeKindFromRawHex('1200dead')).toBe('direct');
  });

  it('uses 16-hex packet_hash as the bucket key when community sends it', () => {
    const frame = packet({
      hash8: '19d68fe9',
      packet_hash: '19D68FE91E75C7DE',
    });
    const parsed = asCommunityPacket(frame);
    expect(parsed?.packet_hash).toBe('19d68fe91e75c7de');
    const obs = observationFromCommunity(frame)!;
    expect(obs.packetHash).toBe('19d68fe91e75c7de');
    expect(obs.hash8).toBe('19d68fe9');
    expect(observationBucketKey(obs)).toBe('19d68fe91e75c7de');
    expect(observationBucketKey({ hash8: '19d68fe9', packetHash: null })).toBe('19d68fe9');
  });

  it('resolves A from advertPubkey or a unique 20 km srcHash pin, never contact_key', () => {
    const pin = {
      public_key: 'ab'.repeat(32),
      lat: 45.76,
      lon: 4.84,
    };
    const far = { public_key: 'cd'.repeat(32), lat: 48.8, lon: 2.3 };
    expect(
      resolveOriginPin(
        {
          advertPubkey: 'ab'.repeat(32),
          srcHash: null,
          waypoints: [],
          ear: null,
        },
        [pin]
      )?.public_key
    ).toBe(pin.public_key);
    expect(
      resolveOriginPin(
        {
          advertPubkey: null,
          srcHash: 'ab',
          waypoints: [{ lat: 45.76, lon: 4.84, token: 'fe10', kind: 'hop', confidence: 'exact' }],
          ear: null,
        },
        [pin]
      )?.public_key
    ).toBe(pin.public_key);
    expect(
      resolveOriginPin(
        {
          advertPubkey: null,
          srcHash: 'ab',
          waypoints: [{ lat: 45.76, lon: 4.84, token: 'fe10', kind: 'hop', confidence: 'exact' }],
          ear: null,
        },
        [pin, { ...pin, public_key: 'ab'.repeat(16) + '11'.repeat(16) }]
      )
    ).toBeNull();
    expect(
      resolveOriginPin(
        {
          advertPubkey: null,
          srcHash: 'ab',
          waypoints: [],
          ear: { lat: 48.8, lon: 2.3, source: 'local' },
        },
        [pin, far]
      )
    ).toBeNull();
    expect(observationFromCommunity(packet())?.advertPubkey).toBeNull();
    expect(observationFromCommunity(packet())?.originGps).toBeNull();
    expect(observationFromCommunity(packet())?.srcHash).toBeNull();
    const named = observationFromCommunity(
      packet({
        origin: {
          token: 'ab12',
          confidence: 'exact',
          lat: 43.7,
          lon: 7.25,
          pubkey: 'ab'.repeat(32),
          name: 'OnAir',
        },
      })
    );
    expect(named?.advertPubkey).toBe('ab'.repeat(32));
    expect(named?.originGps).toEqual({ lat: 43.7, lon: 7.25, name: 'OnAir' });
    expect(observationFromCommunity(packet())?.routeKind).toBe('unknown');
    expect(observationFromCommunity(packet({ route_kind: 'flood' }))?.routeKind).toBe('flood');
    expect(asCommunityPacket(packet({ route_kind: 'direct' }))?.route_kind).toBe('direct');
    expect(asCommunityPacket({ ...packet(), route_kind: 'nope' })?.route_kind).toBeUndefined();
  });

  it('coalesces on packet hash, first hop token, and ear_id', () => {
    const obs = observationFromCommunity(
      packet({
        hash8: 'ea6e0c86',
        packet_hash: 'ea6e0c86deadbeef',
        path: ['ab12'],
        hop_count: 1,
        hops: [{ token: 'ab12', lat: 43.685501, lon: 7.210411, confidence: 'exact' }],
        ear: { lat: 43.660905, lon: 7.186681, source: 'advert' },
        ear_id: 'ear-nice',
      })
    )!;
    expect(observationCoalesceKey(obs)).toBe('ea6e0c86deadbeef:ab12:ear-nice');
  });

  it('uses community origin.pubkey to match the pin already on the map', () => {
    const pin = {
      public_key: 'ab'.repeat(32),
      lat: 43.7,
      lon: 7.25,
    };
    const frame = packet({
      type: 'advert',
      origin: {
        token: pin.public_key.slice(0, 8),
        confidence: 'unresolved',
        reason: 'no_position',
        pubkey: pin.public_key,
      },
    });
    const obs = observationFromCommunity(frame);
    expect(obs?.advertPubkey).toBe(pin.public_key);
    expect(resolveOriginPin(obs!, [pin])?.public_key).toBe(pin.public_key);
  });

  it('uses the local radio pin as origin A for a matching advert pubkey', () => {
    const config: RadioConfig = {
      public_key: 'ab'.repeat(32),
      name: 'me',
      lat: 45.76,
      lon: 4.84,
      tx_power: 22,
      max_tx_power: 22,
      radio: { freq: 869.525, bw: 250, sf: 11, cr: 5 },
      path_hash_mode: 0,
      path_hash_mode_supported: true,
    };
    const pins = pinsIncludingLocalRadio([], config);
    expect(pins).toEqual([{ public_key: 'ab'.repeat(32), lat: 45.76, lon: 4.84 }]);
    expect(
      resolveOriginPin(
        {
          advertPubkey: 'ab'.repeat(32),
          srcHash: null,
          waypoints: [],
          ear: null,
        },
        pins
      )?.public_key
    ).toBe(config.public_key);
    const prepended = prependOrigin(
      {
        advertPubkey: 'ab'.repeat(32),
        srcHash: null,
        waypoints: [{ lat: 45.74, lon: 4.92, token: 'fe10', kind: 'hop', confidence: 'exact' }],
        ear: null,
      },
      pins
    );
    expect(prepended[0]).toMatchObject({
      kind: 'origin',
      confidence: 'exact',
      lat: 45.76,
      lon: 4.84,
      pubkey: config.public_key,
    });
    expect(prepended[1]).toMatchObject({ kind: 'hop', lat: 45.74, lon: 4.92 });
  });

  it('does not add leftover A→hop or A→ear when the primary polyline already covers them', () => {
    const origin = { public_key: 'ab'.repeat(32), lat: 45.76, lon: 4.84 };
    const hop = { public_key: 'cd'.repeat(32), lat: 45.74, lon: 4.92 };
    const obs: LiveObservation = {
      id: 'c1',
      hash8: '19d68fe9',
      packetHash: '19d68fe91e75c7de',
      source: 'community',
      type: 'advert',
      snr: 0,
      iata: 'LYS',
      t: Date.now(),
      earId: 'ear-1',
      ear: { lat: 45.72, lon: 5.08, source: 'advert' },
      waypoints: [
        {
          lat: hop.lat,
          lon: hop.lon,
          token: 'cd34',
          kind: 'hop',
          confidence: 'exact',
          pubkey: hop.public_key,
        },
        { lat: 45.72, lon: 5.08, token: 'ear-1', kind: 'ear', confidence: 'exact' },
      ],
      routeKind: 'unknown',
      advertPubkey: 'ab'.repeat(32),
      srcHash: null,
    };
    expect(inferFloodForAdvert(obs)).toBe('flood');
    const plan = fanoutFromOrigin({ observations: [obs] }, [origin, hop]);
    expect(plan.origin?.public_key).toBe(origin.public_key);
    expect(plan.lasers).toEqual([]);
    expect(plan.hopFlashes).toEqual([]);
    expect(plan.earFlashes).toEqual([]);
  });

  it('draws A→ear when the advert has no hop (0-hop flood)', () => {
    const origin = { public_key: 'ab'.repeat(32), lat: 45.76, lon: 4.84 };
    const obs: LiveObservation = {
      id: 'c0',
      hash8: '19d68fe9',
      packetHash: '19d68fe91e75c7de',
      source: 'community',
      type: 'advert',
      snr: 0,
      iata: 'NCE',
      t: Date.now(),
      earId: 'ear-0',
      ear: { lat: 45.72, lon: 5.08, source: 'advert' },
      waypoints: [{ lat: 45.72, lon: 5.08, token: 'ear-0', kind: 'ear', confidence: 'exact' }],
      routeKind: 'unknown',
      advertPubkey: origin.public_key,
      srcHash: null,
    };
    const plan = fanoutFromOrigin({ observations: [obs] }, [origin]);
    expect(plan.lasers).toEqual([]);
    expect(plan.hopFlashes).toEqual([]);
    expect(plan.earFlashes).toEqual([]);
  });

  it('does not flash hop or ear when A is missing — hop→ear is the primary draw', () => {
    const hop = {
      lat: 45.74,
      lon: 4.92,
      token: 'cd34',
      kind: 'hop' as const,
      confidence: 'exact' as const,
    };
    const obs: LiveObservation = {
      id: 'c2',
      hash8: 'cafef00d',
      packetHash: null,
      source: 'community',
      type: 'advert',
      snr: 0,
      iata: null,
      t: Date.now(),
      earId: 'ear-2',
      ear: { lat: 45.72, lon: 5.08, source: 'iata' },
      waypoints: [hop, { lat: 45.72, lon: 5.08, token: 'ear-2', kind: 'ear', confidence: 'exact' }],
      routeKind: 'unknown',
      advertPubkey: null,
      srcHash: null,
    };
    const plan = fanoutFromOrigin({ observations: [obs] }, []);
    expect(plan.origin).toBeNull();
    expect(plan.lasers).toEqual([]);
    expect(plan.hopFlashes).toEqual([]);
    expect(plan.earFlashes).toEqual([]);
  });
});
