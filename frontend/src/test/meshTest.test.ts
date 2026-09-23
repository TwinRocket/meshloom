import { describe, expect, it } from 'vitest';

import { MID_REACH_MS, YOUNG_REACH_MS } from '../utils/observerReach';
import {
  getLastViewedConversation,
  saveLastViewedConversation,
} from '../utils/lastViewedConversation';
import {
  meshTestListenState,
  meshTestPathPoints,
  preselectedFloodScope,
  sortMeshTestObservers,
  type MeshTestObserver,
} from '../utils/meshTest';

function observer(overrides: Partial<MeshTestObserver> = {}): MeshTestObserver {
  return {
    key: 'ear',
    name: 'Lyon',
    isMLC: false,
    role: 'repeater',
    hops: 1,
    snr: null,
    rssi: null,
    lat: 45.75,
    lon: 4.85,
    distanceKm: 12,
    path: [],
    ...overrides,
  };
}

describe('meshTestPathPoints', () => {
  const origin = { lat: 48.85, lon: 2.35 };

  it('skips a hop that has no position and joins the points that do', () => {
    const points = meshTestPathPoints(
      observer({
        path: [
          { prefix: 'AA', hopIndex: 0, name: 'Relay', lat: 47, lon: 3 },
          { prefix: 'BB', hopIndex: 1, name: null, lat: null, lon: null },
          { prefix: 'CC', hopIndex: 2, name: 'Far', lat: 46, lon: 4 },
        ],
      }),
      origin
    );

    expect(points.map((point) => point.hopIndex)).toEqual([null, 0, 2, null]);
    expect(points.map((point) => [point.lat, point.lon])).toEqual([
      [48.85, 2.35],
      [47, 3],
      [46, 4],
      [45.75, 4.85],
    ]);
    expect(points.some((point) => point.label === 'BB')).toBe(false);
  });
});

describe('meshTestListenState', () => {
  it('keeps listening through the young and mid windows', () => {
    expect(meshTestListenState(YOUNG_REACH_MS - 1, false)).toBe('listening');
    expect(meshTestListenState(YOUNG_REACH_MS, false)).toBe('listening');
    expect(meshTestListenState(MID_REACH_MS - 1, false)).toBe('listening');
  });

  it('stops at ten minutes, and earlier only when the reach is sealed', () => {
    expect(meshTestListenState(MID_REACH_MS, false)).toBe('stopped');
    expect(meshTestListenState(1_000, true)).toBe('stopped');
  });
});

describe('sortMeshTestObservers', () => {
  it('orders by hops, then distance, with missing values last', () => {
    const sorted = sortMeshTestObservers([
      observer({ key: 'far', name: 'Far', hops: 2, distanceKm: 1 }),
      observer({ key: 'near', name: 'Near', hops: 1, distanceKm: 40 }),
      observer({ key: 'closer', name: 'Closer', hops: 1, distanceKm: 4 }),
      observer({ key: 'unknown', name: 'Unknown', hops: null, distanceKm: 0 }),
    ]);
    expect(sorted.map((item) => item.key)).toEqual(['closer', 'near', 'far', 'unknown']);
  });
});

describe('preselectedFloodScope', () => {
  it('keeps the radio scope only when that region is registered', () => {
    expect(preselectedFloodScope('nl-gr', ['de-by', 'nl-gr'])).toBe('nl-gr');
    expect(preselectedFloodScope('#nl-gr', ['de-by', 'nl-gr'])).toBe('nl-gr');
    expect(preselectedFloodScope('#NL-GR', ['de-by', 'nl-gr'])).toBe('nl-gr');
    expect(preselectedFloodScope('fr-idf', ['de-by', 'nl-gr'])).toBe('de-by');
    expect(preselectedFloodScope(undefined, [])).toBe('');
  });
});

describe('last viewed radio test', () => {
  it('does not restore a saved radio-test conversation', () => {
    saveLastViewedConversation({ type: 'test', id: 'test', name: 'Radio test' });
    expect(getLastViewedConversation()).toBeNull();
  });
});
