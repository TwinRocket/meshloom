import { afterEach, describe, expect, it, vi } from 'vitest';

import { LiveMapController } from '../components/live/liveMap';
import { LIVE_HOLD_MS } from '../components/live/liveRender';
import type { RadioConfig } from '../types';
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

function waypoint(lat: number, lon: number, extras: Partial<LiveWaypoint> = {}): LiveWaypoint {
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
    packetHash: overrides.packetHash ?? '19d68fe91e75c7de',
  };
}

function radioConfig(overrides: Partial<RadioConfig> = {}): RadioConfig {
  return {
    public_key: overrides.public_key ?? 'aa'.repeat(32),
    name: overrides.name ?? 'me',
    lat: overrides.lat ?? 45.76,
    lon: overrides.lon ?? 4.84,
    tx_power: 22,
    max_tx_power: 22,
    radio: { freq: 869.525, bw: 250, sf: 11, cr: 5 },
    path_hash_mode: 0,
    path_hash_mode_supported: true,
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

  it('fans out A→first hop after the hold and skips A replay on a late ear', async () => {
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
    const lasers = engine.getShotSnapshots().filter((shot) => shot.pointCount === 2);
    expect(lasers).toHaveLength(2);
    expect(lasers.map((shot) => shot.vertexKinds)).toEqual([
      ['origin', 'hop'],
      ['origin', 'ear'],
    ]);
    expect(lasers[0].firstPoint).toEqual([4.84, 45.76]);
    expect(lasers[0].lastPoint).toEqual([4.92, 45.74]);
    expect(lasers[1].lastPoint).toEqual([5.08, 45.72]);
    expect(engine.getRippleSnapshots().filter((row) => row.id.startsWith('origin:'))).toHaveLength(
      1
    );

    t += 50;
    engine.syncObservations(
      [
        observation({
          id: 'obs-late',
          advertPubkey: 'aa'.repeat(32),
          ear: { lat: 46.2, lon: 6.1, source: 'advert' },
          waypoints: [waypoint(46.2, 6.1, { kind: 'ear' })],
        }),
      ],
      new Set()
    );
    const afterLate = engine.getShotSnapshots();
    expect(afterLate.filter((shot) => shot.pointCount === 2)).toHaveLength(3);
    expect(afterLate.some((shot) => shot.lastPoint?.[0] === 6.1 && shot.pointCount === 2)).toBe(
      true
    );
    expect(engine.getRippleSnapshots().filter((row) => row.id.startsWith('origin:'))).toHaveLength(
      1
    );
  });

  it('uses the local radio pin as origin A and ripples it', async () => {
    let t = 4_000;
    const engine = await readyController(() => t);
    engines.push(engine);
    engine.setLocalRadio(radioConfig());
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
    expect(engine.getRippleSnapshots().filter((row) => row.id.startsWith('origin:'))).toHaveLength(
      1
    );
    const laser = engine.getShotSnapshots().find((shot) => shot.pointCount === 2);
    expect(laser?.firstPoint).toEqual([4.84, 45.76]);
    expect(laser?.lastPoint).toEqual([4.92, 45.74]);
  });

  it('buckets on 16-hex packet_hash and fans out a community advert with unknown routeKind', async () => {
    let t = 5_000;
    const engine = await readyController(() => t);
    engines.push(engine);
    engine.setLocalRadio(radioConfig());
    engine.syncObservations(
      [
        observation({
          id: 'local-1',
          source: 'local',
          hash8: '19d68fe9',
          packetHash: '19d68fe91e75c7de',
          advertPubkey: 'aa'.repeat(32),
          waypoints: [waypoint(45.76, 4.84, { kind: 'ear' })],
        }),
        observation({
          id: 'community-1',
          source: 'community',
          type: 'advert',
          routeKind: 'unknown',
          hash8: '19d68fe9',
          packetHash: '19d68fe91e75c7de',
          advertPubkey: null,
          waypoints: [
            waypoint(45.74, 4.92, { kind: 'hop', token: 'bb22', pubkey: 'bb'.repeat(32) }),
            waypoint(46.2, 6.1, { kind: 'ear' }),
          ],
        }),
      ],
      new Set()
    );
    expect(engine.pendingBucketCount()).toBe(1);
    t += LIVE_HOLD_MS;
    engine.setFilters({ iata: '', hiddenTypes: new Set(), exactOnly: false });
    const laser = engine.getShotSnapshots().find((shot) => shot.pointCount === 2);
    expect(laser?.firstPoint).toEqual([4.84, 45.76]);
    expect(laser?.lastPoint).toEqual([4.92, 45.74]);
  });

  it('uses observer GPS as a hop pin without drawing an observer icon', async () => {
    let t = 6_000;
    const engine = await readyController(() => t);
    engines.push(engine);
    engine.setLocalRadio(radioConfig());
    engine.setDirectoryNodes([
      {
        public_key: 'bb'.repeat(32),
        name: 'Heard-by',
        role: 'observer',
        lat: 45.74,
        lon: 4.92,
        source: 'community-db',
      },
    ]);
    expect(engine.geometryPublicKeys()).toEqual(['bb'.repeat(32)]);
    expect(engine.drawnPublicKeys()).toEqual([]);
    engine.syncObservations(
      [
        observation({
          advertPubkey: 'aa'.repeat(32),
          waypoints: [
            waypoint(45.74, 4.92, { kind: 'hop', token: 'bb22', pubkey: 'bb'.repeat(32) }),
          ],
        }),
      ],
      new Set()
    );
    t += LIVE_HOLD_MS;
    engine.setFilters({ iata: '', hiddenTypes: new Set(), exactOnly: false });
    const laser = engine.getShotSnapshots().find((shot) => shot.pointCount === 2);
    expect(laser?.lastPoint).toEqual([4.92, 45.74]);
    expect(engine.drawnPublicKeys()).toEqual([]);
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
