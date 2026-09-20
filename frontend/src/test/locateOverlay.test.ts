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
