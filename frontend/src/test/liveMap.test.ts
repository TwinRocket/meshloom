import { afterEach, describe, expect, it, vi } from 'vitest';

import { LiveMapController } from '../components/live/liveMap';
import {
  LIVE_HOLD_MS,
  LIVE_PACKET_MS,
  LIVE_REMANENCE_MS,
  LIVE_STAGGER_MS,
  MAX_CONCURRENT_ANIMS,
  MAX_LIVE_CATCHUP,
  MAX_PENDING_ANIMS,
} from '../components/live/liveRender';
import type { CommunityPacketType, DirectoryMapNode, RadioConfig } from '../types';
import type { LiveObservation, LiveWaypoint } from '../utils/livePackets';

const { FakeMap, maps, overlays } = vi.hoisted(() => {
  const maps: Array<{ fireLoad: () => void }> = [];
  const overlays: Array<{ setProps: ReturnType<typeof vi.fn> }> = [];
  class FakeMap {
    static autoLoad = true;
    handlers = new Map<string, Array<(...args: unknown[]) => void>>();
    addControl = vi.fn();
    removeControl = vi.fn();
    remove = vi.fn();
    resize = vi.fn();
    fitBounds = vi.fn();
    getCenter = () => ({ lat: 46.2, lng: 5.2 });
    getZoom = () => 6;
    constructor() {
      maps.push(this);
    }
    fireLoad() {
      for (const cb of this.handlers.get('load') ?? []) cb();
    }
    on(event: string, cb: (...args: unknown[]) => void) {
      const list = this.handlers.get(event) ?? [];
      list.push(cb);
      this.handlers.set(event, list);
      if (event === 'load' && FakeMap.autoLoad) queueMicrotask(() => cb());
    }
    off() {}
  }
  return { FakeMap, maps, overlays };
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
    constructor(_props: unknown) {
      overlays.push(this);
    }
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

function uniqueFlash(id: string, index: number): LiveObservation {
  const tag = index.toString().padStart(4, '0');
  return observation({
    id,
    hash8: index.toString(16).padStart(8, '0'),
    packetHash: `hash-${tag}`,
    earId: `ear-${tag}`,
    waypoints: [waypoint(45.72, 5.08, { kind: 'ear', token: `ear-${tag}` })],
  });
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

type LayerProps = {
  id: string;
  data: unknown[];
  updateTriggers?: Record<string, unknown>;
};

function lastLayer(id: string): LayerProps | undefined {
  const overlay = overlays[overlays.length - 1];
  const calls = overlay?.setProps.mock.calls ?? [];
  const last = calls[calls.length - 1]?.[0] as
    | { layers?: Array<{ props: LayerProps }> }
    | undefined;
  return last?.layers?.find((layer) => layer.props.id === id)?.props;
}

function directoryNode(overrides: Partial<DirectoryMapNode> = {}): DirectoryMapNode {
  return {
    public_key: overrides.public_key ?? 'cc'.repeat(32),
    name: overrides.name ?? 'Pin',
    role: overrides.role ?? 'companion',
    lat: overrides.lat ?? 45.76,
    lon: overrides.lon ?? 4.84,
    source: overrides.source ?? 'community-db',
    last_seen: overrides.last_seen,
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
    FakeMap.autoLoad = true;
    maps.length = 0;
    overlays.length = 0;
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

  it('draws one A→hop→ear polyline after the coalesce and does not replay A on a late ear', async () => {
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
    const lasers = engine.getShotSnapshots();
    expect(lasers).toHaveLength(1);
    expect(lasers[0].pointCount).toBe(3);
    expect(lasers[0].vertexKinds).toEqual(['origin', 'hop', 'ear']);
    expect(lasers[0].firstPoint).toEqual([4.84, 45.76]);
    expect(lasers[0].lastPoint).toEqual([5.08, 45.72]);
    expect(engine.getRippleSnapshots().filter((row) => row.id.startsWith('origin:'))).toHaveLength(
      1
    );

    t += 50;
    engine.syncObservations(
      [
        observation({
          id: 'obs-late',
          advertPubkey: 'aa'.repeat(32),
          earId: 'ear-late',
          ear: { lat: 46.2, lon: 6.1, source: 'advert' },
          waypoints: [waypoint(46.2, 6.1, { kind: 'ear' })],
        }),
      ],
      new Set()
    );
    expect(engine.getShotSnapshots()).toHaveLength(1);
    t += LIVE_HOLD_MS;
    engine.setFilters({ iata: '', hiddenTypes: new Set(), exactOnly: false });
    const afterLate = engine.getShotSnapshots();
    expect(afterLate).toHaveLength(2);
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
    const laser = engine.getShotSnapshots()[0];
    expect(laser?.pointCount).toBe(3);
    expect(laser?.firstPoint).toEqual([4.84, 45.76]);
    expect(laser?.lastPoint).toEqual([5.08, 45.72]);
    const laserKinds = laser?.vertexKinds;
    expect(laserKinds?.[laserKinds.length - 1]).toBe('ear');
  });

  it('draws hop→ear for a community advert like ea6e0c86 without origin A', async () => {
    let t = 5_000;
    const engine = await readyController(() => t);
    engines.push(engine);
    engine.syncObservations(
      [
        observation({
          id: 'ea6e0c86-obs',
          source: 'community',
          type: 'advert',
          routeKind: 'unknown',
          hash8: 'ea6e0c86',
          packetHash: 'ea6e0c86deadbeef',
          advertPubkey: null,
          earId: 'ear-nice',
          ear: { lat: 43.660905, lon: 7.186681, source: 'advert' },
          waypoints: [
            waypoint(43.685501, 7.210411, {
              kind: 'hop',
              token: 'ab12',
              label: 'Fr06 catAng R2',
            }),
            waypoint(43.660905, 7.186681, { kind: 'ear' }),
          ],
        }),
      ],
      new Set()
    );
    expect(engine.pendingBucketCount()).toBe(1);
    expect(engine.getShotSnapshots()).toEqual([]);
    t += LIVE_HOLD_MS;
    engine.setFilters({ iata: '', hiddenTypes: new Set(), exactOnly: false });
    const shots = engine.getShotSnapshots();
    expect(shots).toHaveLength(1);
    expect(shots[0].pointCount).toBe(2);
    expect(shots[0].firstPoint).toEqual([7.210411, 43.685501]);
    expect(shots[0].lastPoint).toEqual([7.186681, 43.660905]);
    expect(shots[0].vertexKinds).toEqual(['hop', 'ear']);
    const shotKinds = shots[0].vertexKinds;
    expect(shotKinds?.[shotKinds.length - 1]).toBe('ear');
  });

  it('coalesces the same packet_hash + first hop + ear_id into one shot', async () => {
    let t = 5_500;
    const engine = await readyController(() => t);
    engines.push(engine);
    const shared = {
      source: 'community' as const,
      type: 'advert' as const,
      routeKind: 'unknown' as const,
      hash8: 'ea6e0c86',
      packetHash: 'ea6e0c86deadbeef',
      advertPubkey: null,
      earId: 'ear-nice',
      ear: { lat: 43.660905, lon: 7.186681, source: 'advert' as const },
      waypoints: [
        waypoint(43.685501, 7.210411, { kind: 'hop', token: 'ab12' }),
        waypoint(43.660905, 7.186681, { kind: 'ear' }),
      ],
    };
    engine.syncObservations(
      [observation({ ...shared, id: 'evt-a' }), observation({ ...shared, id: 'evt-b' })],
      new Set()
    );
    expect(engine.pendingBucketCount()).toBe(1);
    t += LIVE_HOLD_MS;
    engine.setFilters({ iata: '', hiddenTypes: new Set(), exactOnly: false });
    expect(engine.getShotSnapshots()).toHaveLength(1);
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

  it('draws hop→ear for DIRECT text instead of stripping to an ear pulse', async () => {
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
    const shot = engine.getShotSnapshots()[0];
    expect(shot.pointCount).toBe(2);
    expect(shot.vertexKinds).toEqual(['hop', 'ear']);
  });

  it('caps in-flight shots at MAX_CONCURRENT_ANIMS and defers the rest', async () => {
    let t = 8_000;
    const engine = await readyController(() => t);
    engines.push(engine);
    const surplus = 5;
    const rows = Array.from({ length: MAX_CONCURRENT_ANIMS + surplus }, (_, i) =>
      uniqueFlash(`cap-${i}`, i)
    );
    engine.syncObservations(rows, new Set());
    t += LIVE_HOLD_MS;
    engine.setFilters({ iata: '', hiddenTypes: new Set(), exactOnly: false });
    expect(engine.getShotSnapshots()).toHaveLength(MAX_CONCURRENT_ANIMS);
    expect(engine.pendingAnimCount()).toBe(surplus);
    expect(engine.droppedPendingCount()).toBe(0);
  });

  it('starts a deferred observation once an in-flight slot frees', async () => {
    let t = 9_000;
    const engine = await readyController(() => t);
    engines.push(engine);
    const rows = Array.from({ length: MAX_CONCURRENT_ANIMS + 5 }, (_, i) =>
      uniqueFlash(`late-${i}`, i)
    );
    engine.syncObservations(rows, new Set());
    t += LIVE_HOLD_MS;
    engine.setFilters({ iata: '', hiddenTypes: new Set(), exactOnly: false });
    // Derive from the cap: hardcoding the index pins the test to one ceiling.
    const firstDeferred = `late-${MAX_CONCURRENT_ANIMS}`;
    const lastDeferred = `late-${MAX_CONCURRENT_ANIMS + 4}`;
    expect(engine.getShotSnapshots().map((shot) => shot.id)).not.toContain(firstDeferred);

    t += LIVE_PACKET_MS + (MAX_CONCURRENT_ANIMS - 1) * LIVE_STAGGER_MS;
    engine.setFilters({ iata: '', hiddenTypes: new Set(), exactOnly: false });
    const ids = engine.getShotSnapshots().map((shot) => shot.id);
    expect(ids).toContain(firstDeferred);
    expect(ids).toContain(lastDeferred);
    expect(engine.pendingAnimCount()).toBe(0);
    expect(engine.droppedPendingCount()).toBe(0);
  });

  it('bounds the pending queue and sheds the oldest overflow', async () => {
    let t = 10_000;
    const engine = await readyController(() => t);
    engines.push(engine);
    const overflow = 10;
    const rows = Array.from(
      { length: MAX_CONCURRENT_ANIMS + MAX_PENDING_ANIMS + overflow },
      (_, i) => uniqueFlash(`shed-${i}`, i)
    );
    engine.syncObservations(rows, new Set());
    t += LIVE_HOLD_MS;
    engine.setFilters({ iata: '', hiddenTypes: new Set(), exactOnly: false });
    expect(engine.getShotSnapshots()).toHaveLength(MAX_CONCURRENT_ANIMS);
    expect(engine.pendingAnimCount()).toBe(MAX_PENDING_ANIMS);
    expect(engine.droppedPendingCount()).toBe(overflow);

    t += LIVE_PACKET_MS + (MAX_CONCURRENT_ANIMS - 1) * LIVE_STAGGER_MS;
    engine.setFilters({ iata: '', hiddenTypes: new Set(), exactOnly: false });
    const ids = engine.getShotSnapshots().map((shot) => shot.id);
    expect(ids).not.toContain(`shed-${MAX_CONCURRENT_ANIMS}`);
    expect(ids).not.toContain(`shed-${MAX_CONCURRENT_ANIMS + overflow - 1}`);
    expect(ids).toContain(`shed-${MAX_CONCURRENT_ANIMS + overflow}`);
    expect(engine.pendingAnimCount()).toBe(MAX_PENDING_ANIMS - MAX_CONCURRENT_ANIMS);
  });

  it('keeps bucket stagger bounded to now after sustained arrivals', async () => {
    let t = 40_000;
    const engine = await readyController(() => t);
    engines.push(engine);
    const maxAhead = (MAX_CONCURRENT_ANIMS - 1) * LIVE_STAGGER_MS;
    const arrivals = 220;
    const cadenceMs = 93;

    for (let i = 0; i < arrivals; i++) {
      engine.syncObservations([uniqueFlash(`sust-${i}`, i)], new Set());
      t += cadenceMs;
      engine.setFilters({ iata: '', hiddenTypes: new Set(), exactOnly: false });
      for (const shot of engine.getShotSnapshots()) {
        expect(shot.startedAt).toBeLessThanOrEqual(t + maxAhead);
      }
    }

    const ids = engine.getShotSnapshots().map((shot) => shot.id);
    const latest = Math.max(
      ...ids.filter((id) => id.startsWith('sust-')).map((id) => Number(id.slice(5)))
    );
    expect(latest).toBeGreaterThan(arrivals - 10);
    expect(engine.droppedPendingCount()).toBe(0);
    expect(engine.pendingAnimCount()).toBe(0);
  });

  it('counts coalesce catchup trims instead of dropping them silently', async () => {
    let t = 12_000;
    const engine = await readyController(() => t);
    engines.push(engine);
    const extra = 8;
    const rows = Array.from({ length: MAX_LIVE_CATCHUP + extra }, (_, i) =>
      observation({
        id: `catch-${i}`,
        hash8: 'aabbccdd',
        packetHash: 'aabbccdd-same-path',
        earId: 'ear-shared',
        waypoints: [waypoint(45.72, 5.08, { kind: 'ear', token: 'ear' })],
      })
    );
    engine.syncObservations(rows, new Set());
    expect(engine.pendingBucketCount()).toBe(1);
    t += LIVE_HOLD_MS;
    engine.setFilters({ iata: '', hiddenTypes: new Set(), exactOnly: false });
    expect(engine.droppedCatchupCount()).toBe(extra);
    expect(engine.getShotSnapshots()).toHaveLength(1);
  });

  it('releases a type-filtered shot slot after travel so a visible one can draw', async () => {
    let t = 13_000;
    const engine = await readyController(() => t);
    engines.push(engine);
    const hiddenAdvert: Set<CommunityPacketType> = new Set(['advert']);
    const hideAdvert = { iata: '', hiddenTypes: hiddenAdvert, exactOnly: false };
    engine.setFilters(hideAdvert);
    engine.syncObservations([uniqueFlash('hid-0', 0)], new Set());
    t += LIVE_HOLD_MS;
    engine.setFilters(hideAdvert);
    expect(engine.getShotSnapshots().map((shot) => shot.id)).toEqual(['hid-0']);
    expect(engine.getShotSnapshots()[0].finishedAt).toBeNull();

    t += LIVE_PACKET_MS;
    engine.setFilters(hideAdvert);
    expect(engine.getShotSnapshots()[0].finishedAt).not.toBeNull();
    expect(engine.getShotSnapshots().map((shot) => shot.id)).toEqual(['hid-0']);

    engine.syncObservations(
      [
        observation({
          id: 'vis-1',
          type: 'text',
          hash8: 'ffffffff',
          packetHash: 'hash-vis-1',
          earId: 'ear-vis',
          waypoints: [waypoint(45.72, 5.08, { kind: 'ear', token: 'ear-vis' })],
        }),
      ],
      new Set()
    );
    t += LIVE_HOLD_MS;
    engine.setFilters(hideAdvert);
    const afterVisible = engine.getShotSnapshots();
    expect(afterVisible.map((shot) => shot.id)).toContain('vis-1');
    expect(engine.pendingAnimCount()).toBe(0);

    t += LIVE_REMANENCE_MS;
    engine.setFilters(hideAdvert);
    const afterExpiry = engine.getShotSnapshots().map((shot) => shot.id);
    expect(afterExpiry).not.toContain('hid-0');
    expect(afterExpiry).toContain('vis-1');
  });

  it('does not grow retained shots without bound under sustained hidden traffic', async () => {
    let t = 50_000;
    const engine = await readyController(() => t);
    engines.push(engine);
    const hideAdvert = {
      iata: '',
      hiddenTypes: new Set<CommunityPacketType>(['advert']),
      exactOnly: false,
    };
    engine.setFilters(hideAdvert);
    const arrivals = 220;
    const cadenceMs = 93;
    let peak = 0;

    for (let i = 0; i < arrivals; i++) {
      engine.syncObservations([uniqueFlash(`hide-${i}`, i)], new Set());
      t += cadenceMs;
      engine.setFilters(hideAdvert);
      peak = Math.max(peak, engine.getShotSnapshots().length);
    }

    expect(peak).toBeLessThan(arrivals / 2);
    expect(engine.getShotSnapshots().length).toBeLessThan(arrivals / 2);
    expect(engine.droppedPendingCount()).toBe(0);
    expect(engine.pendingAnimCount()).toBe(0);
  });

  it('keeps live-nodes data reference and triggers across draws without a pin change', async () => {
    let t = 60_000;
    const engine = await readyController(() => t);
    engines.push(engine);
    engine.setDirectoryNodes([directoryNode()]);
    const first = lastLayer('live-nodes');
    expect(first?.data.length).toBeGreaterThan(0);

    t += 16;
    engine.setFilters({ iata: '', hiddenTypes: new Set(), exactOnly: false });
    const second = lastLayer('live-nodes');
    expect(second?.data).toBe(first?.data);
    expect(second?.updateTriggers?.getPosition).toBe(first?.updateTriggers?.getPosition);
    expect(second?.data.length).toBe(first?.data.length);
    expect(second?.data.length).toBeGreaterThan(0);
  });

  it('ignores last_seen churn when the pin fingerprint is unchanged', async () => {
    const engine = await readyController(() => 61_000);
    engines.push(engine);
    const pin = directoryNode({ last_seen: 1_700_000_000 });
    engine.setDirectoryNodes([pin]);
    const first = lastLayer('live-nodes');

    engine.setDirectoryNodes([{ ...pin, last_seen: 1_800_000_000 }]);
    const second = lastLayer('live-nodes');
    expect(second?.data).toBe(first?.data);
    expect(second?.updateTriggers?.getPosition).toBe(first?.updateTriggers?.getPosition);
    expect(second?.data.length).toBe(first?.data.length);
  });

  it('rebuilds live-nodes when a pin is removed or moved', async () => {
    const engine = await readyController(() => 62_000);
    engines.push(engine);
    const stay = directoryNode({ public_key: 'aa'.repeat(32), name: 'Stay' });
    const leave = directoryNode({
      public_key: 'bb'.repeat(32),
      name: 'Leave',
      lat: 46.1,
      lon: 6.1,
    });
    engine.setDirectoryNodes([stay, leave]);
    const first = lastLayer('live-nodes');
    expect(first?.data.length).toBe(2);

    engine.setDirectoryNodes([{ ...stay, lat: 46.2, lon: 6.2 }]);
    const second = lastLayer('live-nodes');
    expect(second?.data.length).toBe(1);
    expect((second?.data[0] as { position: [number, number] }).position).toEqual([6.2, 46.2]);
    expect(second?.updateTriggers?.getPosition).not.toBe(first?.updateTriggers?.getPosition);
  });

  it('clears live-local-radio after setLocalRadio(null)', async () => {
    const engine = await readyController(() => 63_000);
    engines.push(engine);
    engine.setLocalRadio(radioConfig());
    expect(lastLayer('live-local-radio')?.data.length).toBe(1);

    engine.setLocalRadio(null);
    engine.setFilters({ iata: '', hiddenTypes: new Set(), exactOnly: false });
    const local = lastLayer('live-local-radio');
    expect(local?.data.length).toBe(0);
  });

  it('shows pins set before map load on the first ready draw', async () => {
    FakeMap.autoLoad = false;
    const host = document.createElement('div');
    const engine = new LiveMapController(host, { now: () => 64_000 });
    engines.push(engine);
    engine.setDirectoryNodes([directoryNode()]);
    expect(lastLayer('live-nodes')).toBeUndefined();

    maps[maps.length - 1]?.fireLoad();
    const pins = lastLayer('live-nodes');
    expect(pins?.data.length).toBeGreaterThan(0);
    expect((pins?.data[0] as { id: string }).id).toBe('cc'.repeat(32));
  });
});
