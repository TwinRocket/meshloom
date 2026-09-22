import { describe, expect, it } from 'vitest';

import type { LocateResponse } from '../types';
import { applyHopOverlay, diskBounds, mergeReachOverlay } from '../utils/locateOverlay';

function baseLocate(): LocateResponse {
  return {
    query: 'ghost',
    identity: {
      public_key: 'aa'.repeat(32),
      name: 'Ghost',
      contact_type: 1,
      inferred: false,
      last_seen: null,
    },
    source: 'local',
    directory_enabled: true,
    default_radius_km: 20,
    anchors: [
      {
        kind: 'first_hop',
        source: 'local',
        name: 'Hint',
        public_key: 'bb'.repeat(32),
        hop_prefix: 'abcd',
        lat: 45,
        lon: 5,
        radius_km: 20,
        calibratable: true,
      },
    ],
    unresolved_hops: [],
    declared_gps: null,
    heard_locally_0hop: false,
    radio_has_gps: false,
    empty_reason: null,
  };
}

describe('locateOverlay', () => {
  it('appends Community 0-hop observers', () => {
    const merged = mergeReachOverlay(baseLocate(), {
      node: { public_key: 'aa'.repeat(32), name: 'Ghost', lat: 48, lon: 2 },
      observers: [
        {
          public_key: 'cc'.repeat(32),
          name: 'Obs',
          count: 3,
          avg_snr: 4,
          lat: 47,
          lon: 3,
        },
      ],
      directory_enabled: true,
    });
    expect(merged.source).toBe('mixte');
    expect(merged.declared_gps).toEqual({ lat: 48, lon: 2, source: 'corescope' });
    expect(merged.anchors.some((anchor) => anchor.kind === 'corescope_0hop')).toBe(true);
  });

  it('draws a flood first hop and skips a repeater already heard direct', () => {
    const ear = 'cc'.repeat(32);
    const repeater = 'dd'.repeat(32);
    const local = baseLocate();
    local.anchors.push({
      kind: 'local_0hop',
      source: 'local',
      name: 'Home',
      public_key: ear,
      lat: 46,
      lon: 4,
      radius_km: 20,
      calibratable: false,
    });
    const merged = mergeReachOverlay(local, {
      node: { public_key: 'aa'.repeat(32), name: 'Ghost', lat: 48, lon: 2 },
      observers: [
        {
          public_key: ear,
          name: 'Obs',
          count: 2,
          lat: 47,
          lon: 3,
        },
      ],
      first_hops: [
        {
          hop_prefix: 'cc12',
          public_key: ear,
          name: 'Obs',
          lat: 47,
          lon: 3,
          count: 4,
        },
        {
          hop_prefix: 'dd34',
          public_key: repeater,
          name: 'Repeater',
          lat: 44,
          lon: 6,
          count: 5,
        },
      ],
      unresolved_first_hops: [{ prefix: 'EE56', reason: 'no_gps' }],
      directory_enabled: true,
    });
    const disks = merged.anchors.filter((anchor) => anchor.public_key === ear);
    expect(disks.map((anchor) => anchor.kind).sort()).toEqual(['corescope_0hop', 'local_0hop']);
    const first = merged.anchors.find((anchor) => anchor.public_key === repeater);
    expect(first?.kind).toBe('first_hop');
    expect(first?.source).toBe('corescope');
    expect(first?.radius_km).toBe(20);
    expect(first?.calibratable).toBe(true);
    expect(first?.snr).toBeUndefined();
    expect(merged.unresolved_hops).toEqual([{ prefix: 'ee56', reason: 'no_gps', candidates: [] }]);
  });

  it('revokes a local first-hop when the directory has no match', () => {
    const merged = applyHopOverlay(baseLocate(), { resolved: {} }, ['ABCD']);
    expect(merged.anchors).toEqual([]);
    expect(merged.unresolved_hops[0]?.reason).toBe('unmatched');
  });

  it('fits disks by radius, not just centers', () => {
    const points = diskBounds(
      [
        {
          kind: 'local_0hop',
          source: 'local',
          name: 'Home',
          lat: 45,
          lon: 5,
          radius_km: 20,
          calibratable: false,
        },
      ],
      null
    );
    expect(points.length).toBe(2);
    expect(points[0][0]).toBeLessThan(45);
    expect(points[1][0]).toBeGreaterThan(45);
  });
});
