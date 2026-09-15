import { afterEach, describe, expect, it, vi } from 'vitest';

import { LiveMapController } from '../components/live/liveMap';
import { LIVE_HOLD_MS } from '../components/live/liveRender';
import type { LiveObservation, LiveWaypoint } from '../utils/livePackets';

const { FakeMap } = vi.hoisted(() => {
  class FakeMap {
    handlers = new Map<string, Array<(...args: unknown[]) => void>>();
    addControl = vi.fn();
    removeControl = vi.fn();
    remove = vi.fn();
    resize = vi.fn();
    fitBounds = vi.fn();
    getCenter = () => ({ lat: 46.2, lng: 5.2 });
    getZoom = () => 6;
    on(event: string, cb: (...args: unknown[]) => void) {
      const list = this.handlers.get(event) ?? [];
      list.push(cb);
      this.handlers.set(event, list);
      if (event === 'load') queueMicrotask(() => cb());
    }
    off() {}
  }
  return { FakeMap };
});

vi.mock('maplibre-gl', () => {
  class LngLatBounds {
    extend() {
      return this;
    }
  }
  class NavigationControl {}
  const maplibregl = { Map: FakeMap, NavigationControl, LngLatBounds };
  return { default: maplibregl, Map: FakeMap, NavigationControl, LngLatBounds };
});

vi.mock('maplibre-gl/dist/maplibre-gl.css', () => ({}));

vi.mock('@deck.gl/mapbox', () => ({
  MapboxOverlay: class {
    setProps = vi.fn();
    constructor(_props: unknown) {}
  },
}));

vi.mock('@deck.gl/layers', () => ({
  PathLayer: class {
    constructor(public props: unknown) {}
  },
  ScatterplotLayer: class {
    constructor(public props: unknown) {}
  },
  IconLayer: class {
    constructor(public props: unknown) {}
  },
}));

function waypoint(
  lat: number,
  lon: number,
  extras: Partial<LiveWaypoint> = {}
): LiveWaypoint {
  return {
    lat,
    lon,
    token: extras.token ?? 'ear',
    kind: extras.kind ?? 'ear',
    confidence: extras.confidence ?? 'exact',
    ...extras,
  };
}

function observation(overrides: Partial<LiveObservation> = {}): LiveObservation {
  return {
    id: overrides.id ?? 'obs-1',
    hash8: overrides.hash8 ?? '19d68fe9',
    source: overrides.source ?? 'local',
    type: overrides.type ?? 'advert',
    snr: overrides.snr ?? 0,
    iata: overrides.iata ?? null,
    t: overrides.t ?? Date.now(),
    earId: overrides.earId ?? 'ear-1',
    ear: overrides.ear ?? { lat: 45.72, lon: 5.08, source: 'local' },
    waypoints: overrides.waypoints ?? [waypoint(45.72, 5.08, { kind: 'ear' })],
    routeKind: overrides.routeKind ?? 'flood',
    advertPubkey: overrides.advertPubkey ?? null,
    srcHash: overrides.srcHash ?? null,
  };
}

async function readyController(now: () => number): Promise<LiveMapController> {
  const host = document.createElement('div');
  const engine = new LiveMapController(host, { now });
  await Promise.resolve();
  await Promise.resolve();
  return engine;
}

describe('LiveMapController', () => {
  const engines: LiveMapController[] = [];

  afterEach(() => {
    for (const engine of engines) engine.destroy();
    engines.length = 0;
  });

  it('holds a 1-point flash until travel ends and actually draws the head', async () => {
    let t = 1_000;
    const engine = await readyController(() => t);
    engines.push(engine);
    engine.syncObservations([observation()], new Set());
    expect(engine.pendingBucketCount()).toBe(1);
    expect(engine.getShotSnapshots()).toEqual([]);

    t += LIVE_HOLD_MS;
    engine.syncObservations([observation()], new Set());
    const shots = engine.getShotSnapshots();
    expect(shots).toHaveLength(1);
    expect(shots[0].pointCount).toBe(1);
    expect(shots[0].finishedAt).toBeNull();
    expect(shots[0].travelMs).toBe(1400);
    expect(shots[0].remanenceMs).toBe(400);

    t += 700;
    engine.setFilters({ iata: '', hiddenTypes: new Set(), exactOnly: false });
    expect(engine.getShotSnapshots()[0].finishedAt).toBeNull();

    t += 700;
    engine.setFilters({ iata: '', hiddenTypes: new Set(), exactOnly: false });
    expect(engine.getShotSnapshots()[0].finishedAt).not.toBeNull();
  });

  it('plays one startedAt per hash8 bucket and skips A replay on a late ear', async () => {
    let t = 2_000;
    const engine = await readyController(() => t);
    engines.push(engine);
    engine.setDirectoryNodes([
      {
        public_key: 'aa'.repeat(32),
        name: 'A',
        role: 'companion',
        lat: 45.76,
        lon: 4.84,
        source: 'local',
      },
    ]);
    engine.syncObservations(
      [
        observation({
          advertPubkey: 'aa'.repeat(32),
          waypoints: [
            waypoint(45.74, 4.92, { kind: 'hop', token: 'bb22', pubkey: 'bb'.repeat(32) }),
            waypoint(45.72, 5.08, { kind: 'ear' }),
          ],
        }),
      ],
      new Set()
    );
    t += LIVE_HOLD_MS;
    engine.syncObservations(
      [
        observation({
          advertPubkey: 'aa'.repeat(32),
          waypoints: [
            waypoint(45.74, 4.92, { kind: 'hop', token: 'bb22', pubkey: 'bb'.repeat(32) }),
            waypoint(45.72, 5.08, { kind: 'ear' }),
          ],
        }),
      ],
      new Set()
    );
    const first = engine.getShotSnapshots();
    const originCount = engine.getRippleSnapshots().filter((row) => row.id.startsWith('origin:'))
      .length;
    expect(first).toHaveLength(1);
    expect(originCount).toBe(1);

    t += 50;
    engine.syncObservations(
      [
        observation({
          id: 'obs-late',
          advertPubkey: 'aa'.repeat(32),
          waypoints: [waypoint(46.2, 6.1, { kind: 'ear' })],
        }),
      ],
      new Set()
    );
    const afterLate = engine.getShotSnapshots();
    expect(afterLate.map((shot) => shot.id).sort()).toEqual(['obs-1', 'obs-late']);
    expect(afterLate[0].startedAt).not.toBe(afterLate[1].startedAt);
    expect(engine.getRippleSnapshots().filter((row) => row.id.startsWith('origin:'))).toHaveLength(
      1
    );
  });

  it('does not spawn a remaining-path laser for DIRECT', async () => {
    let t = 3_000;
    const engine = await readyController(() => t);
    engines.push(engine);
    engine.syncObservations(
      [
        observation({
          routeKind: 'direct',
          type: 'text',
          waypoints: [
            waypoint(45.7, 4.8, { kind: 'hop', token: 'aa11' }),
            waypoint(45.72, 5.08, { kind: 'ear' }),
          ],
        }),
      ],
      new Set()
    );
    t += LIVE_HOLD_MS;
    engine.syncObservations(
      [
        observation({
          routeKind: 'direct',
          type: 'text',
          waypoints: [
            waypoint(45.7, 4.8, { kind: 'hop', token: 'aa11' }),
            waypoint(45.72, 5.08, { kind: 'ear' }),
          ],
        }),
      ],
      new Set()
    );
    expect(engine.getShotSnapshots()[0].pointCount).toBe(1);
  });
});
