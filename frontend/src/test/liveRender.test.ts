import { describe, expect, it } from 'vitest';

import type { CommunityPacket, CommunityPacketType, Contact } from '../types';
import {
  observationFromCommunity,
  type LiveObservation,
  type LiveWaypoint,
} from '../utils/livePackets';
import { LIVE_TYPE_COLORS } from '../utils/livePackets';
import { osmDarkRasterStyle } from '../utils/mapTiles';
import {
  LASER_CORE_WIDTH_MAX,
  LASER_CORE_WIDTH_MIN,
  LASER_GLOW_ALPHA,
  LASER_GLOW_WIDTH_SCALE,
  LIVE_HOLD_MS,
  LIVE_PACKET_MS,
  LIVE_REMANENCE_MS,
  MAX_CONCURRENT_ANIMS,
  MAX_PENDING_ANIMS,
  LIVE_RIPPLE_MS,
  LIVE_RIPPLE_SCALE,
  LIVE_ROLE_LEGEND,
  NODE_ROLE_STYLE,
  buildLaserPolyline,
  drawableLaserPolyline,
  findPinnedHop,
  geometryDirectoryNodes,
  laserRemanenceMs,
  laserTravelMs,
  localContactsToMapNodes,
  mergeLocalOverDirectory,
  pinVisualRadius,
  placeableEdges,
  rippleRadii,
  cloneLonLatPath,
  collectIataCodes,
  dashLonLat,
  LOCAL_RADIO_VISUAL,
  filterLiveObservations,
  hexToRgba,
  interpolatePolyline,
  laserGlowWidth,
  laserHeadRadii,
  laserTravel,
  laserWidth,
  liveHoverKey,
  mappableDirectoryNodes,
  nearerEndpointLabel,
  nodeRoleStyle,
  normalizeDirectoryRole,
  observationPassesFilters,
  paletteColorsOverlap,
  remanenceOpacity,
  resolveLiveKnownNodeAction,
  segmentsFromObservation,
  selectCatchup,
  shouldAutoFitCamera,
  shouldSpawnLaser,
  strokeStyleForConfidence,
  visibleEdgeSlices,
  waypointConfidence,
} from '../components/live/liveRender';

/** Captured community_packet that rendered FR83-Mont-Caume on the Nice coast. */
const NCE_MONT_CAUME_FRAME: CommunityPacket = {
  v: 2,
  event_id: 'e3ad43e0c2ae49969048e1ec0773d72a',
  hash8: '6e6939eb',
  type: 'other',
  iata: 'NCE',
  snr: -9,
  t: 1789360773578,
  hop_count: 6,
  ear: { lat: 43.760531, lon: 7.177876, source: 'advert' },
  ear_id: '71c397fdd1390ba98b555aeb2aea812cdc164e1a5da51aa731d2c89e335e6f03',
  path: ['7f91', 'a3cb', 'f5e6', '3b42', 'f604', '56d9'],
  hops: [
    {
      token: '7f91',
      confidence: 'unresolved',
      reason: 'geo_filtered',
      lat: undefined,
      lon: undefined,
      name: undefined,
      pubkey: undefined,
    },
    {
      token: 'a3cb',
      confidence: 'exact',
      lat: 43.600193,
      lon: 3.825857,
      name: 'FR34MPL-MAR',
      pubkey: 'a3cb5563fe76597bf657d29109126b5120e477b3b0c3c914079fc575aeeaf588',
    },
    {
      token: 'f5e6',
      confidence: 'exact',
      lat: 43.535191,
      lon: 3.811703,
      name: 'FR34MPL-VLM',
      pubkey: 'f5e6b76f5a3ef9d1db5f4dbdff2edd654c0110a527aca955129854d404a371b5',
    },
    {
      token: '3b42',
      confidence: 'exact',
      lat: 43.20536,
      lon: 5.953823,
      name: 'FR83-Grand-Cap',
      pubkey: '3b42e2faf534e33fb390b9c7d51c0f9ea01487b2dfa2f26a6a479c582f4d0973',
    },
    {
      token: 'f604',
      confidence: 'exact',
      lat: 43.182626,
      lon: 5.898634,
      name: 'FR83-Mont-Caume',
      pubkey: 'f60431bc302cb60259562ca4e39973a347986bb44925c7a677e18814a77ff407',
    },
    {
      token: '56d9',
      confidence: 'exact',
      lat: 43.800231,
      lon: 7.412203,
      name: 'FR06-PEIL-RPL1\u2600\ufe0f',
      pubkey: '56d9854b379d33d1f7d94681e3c684661d3bab50c37b40be9fcc3fa7127cd906',
    },
  ],
};

function waypoint(
  lat: number,
  lon: number,
  extras: Partial<LiveWaypoint> & {
    confidence?: 'exact' | 'probable' | 'unresolved';
    reason?: string;
  } = {}
): LiveWaypoint {
  return {
    lat,
    lon,
    token: extras.token ?? 'ab12',
    kind: extras.kind ?? 'hop',
    ...extras,
  } as LiveWaypoint;
}

function observation(overrides: Partial<LiveObservation> = {}): LiveObservation {
  return {
    id: overrides.id ?? 'obs-1',
    hash8: overrides.hash8 ?? 'deadbeef',
    source: overrides.source ?? 'community',
    type: overrides.type ?? 'text',
    snr: overrides.snr ?? 0,
    iata: overrides.iata ?? 'LYS',
    t: overrides.t ?? Date.now(),
    earId: overrides.earId ?? 'ear-1',
    ear: overrides.ear ?? { lat: 45.7, lon: 4.8, source: 'advert' },
    waypoints: overrides.waypoints ?? [
      waypoint(45.7, 4.8, { confidence: 'exact', token: 'aa11' }),
      waypoint(46.2, 6.1, { confidence: 'probable', token: 'bb22', reason: 'nearest-advert' }),
    ],
    routeKind: overrides.routeKind ?? 'flood',
    advertPubkey: overrides.advertPubkey ?? null,
    srcHash: overrides.srcHash ?? null,
    packetHash: overrides.packetHash ?? null,
  } as LiveObservation;
}

describe('live observation filters', () => {
  it('keeps every observation when filters are empty', () => {
    const rows = [observation(), observation({ id: 'obs-2', type: 'ack', iata: 'NCE' })];
    expect(
      filterLiveObservations(rows, { iata: '', hiddenTypes: new Set(), exactOnly: false })
    ).toHaveLength(2);
  });

  it('filters by IATA and hidden packet types', () => {
    const rows = [
      observation({ id: 'a', type: 'text', iata: 'LYS' }),
      observation({ id: 'b', type: 'ack', iata: 'LYS' }),
      observation({ id: 'c', type: 'text', iata: 'NCE' }),
    ];
    const hiddenTypes = new Set<CommunityPacketType>(['ack']);
    const filtered = filterLiveObservations(rows, { iata: 'lys', hiddenTypes, exactOnly: false });
    expect(filtered.map((row) => row.id)).toEqual(['a']);
  });

  it('collects sorted unique IATA codes', () => {
    expect(
      collectIataCodes([
        observation({ iata: 'nce' }),
        observation({ iata: 'LYS' }),
        observation({ iata: 'LYS' }),
        observation({ iata: null }),
      ])
    ).toEqual(['LYS', 'NCE']);
  });

  it('does not treat exactOnly as an observation-level hide', () => {
    const obs = observation();
    expect(
      observationPassesFilters(obs, { iata: '', hiddenTypes: new Set(), exactOnly: true })
    ).toBe(true);
  });

  it('keeps local observations without IATA when a region filter is set', () => {
    const local = observation({ id: 'local', source: 'local', iata: null, type: 'text' });
    expect(
      observationPassesFilters(local, { iata: 'LYS', hiddenTypes: new Set(), exactOnly: false })
    ).toBe(true);
  });
});

describe('exact vs probable segments', () => {
  it('reads arrival-waypoint confidence and keeps probable thinner and dashed', () => {
    const obs = observation();
    const segments = segmentsFromObservation(obs, false);
    expect(segments).toHaveLength(1);
    expect(segments[0].confidence).toBe('probable');
    expect(segments[0].dashed).toBe(true);
    expect(segments[0].reason).toBe('nearest-advert');
    expect(segments[0].opacityScale).toBeLessThan(1);
    expect(segments[0].width).toBeLessThan(laserWidth(1));
  });

  it('drops probable segments when exact-only is on', () => {
    const obs = observation({
      waypoints: [
        waypoint(45.7, 4.8, { confidence: 'exact' }),
        waypoint(46.0, 5.5, { confidence: 'probable', reason: 'guess' }),
        waypoint(46.5, 6.2, { confidence: 'exact' }),
      ],
    });
    expect(segmentsFromObservation(obs, true).map((seg) => seg.confidence)).toEqual(['exact']);
    expect(segmentsFromObservation(obs, false).map((seg) => seg.confidence)).toEqual([
      'probable',
      'exact',
    ]);
  });

  it('never draws unresolved hops', () => {
    const obs = observation({
      waypoints: [
        waypoint(45.7, 4.8, { confidence: 'exact' }),
        waypoint(0, 0, { confidence: 'unresolved', token: 'cd' }),
        waypoint(46.2, 6.1, { confidence: 'exact' }),
      ],
    });
    const poly = buildLaserPolyline(obs);
    expect(poly.points).toEqual([
      [4.8, 45.7],
      [6.1, 46.2],
    ]);
    expect(poly.edgeConfidence).toEqual(['exact']);
  });

  it('treats a legacy fade waypoint as probable', () => {
    expect(waypointConfidence(waypoint(45.7, 4.8, { kind: 'fade' as LiveWaypoint['kind'] }))).toBe(
      'probable'
    );
  });
});

describe('laser travel and remanence', () => {
  it('scales travel and remanence from placeableEdges, 1400 ms at one point', () => {
    expect(placeableEdges(1)).toBe(0);
    expect(laserTravelMs(0)).toBe(1400);
    expect(laserRemanenceMs(0)).toBe(400);
    expect(laserTravelMs(1)).toBe(1850);
    expect(laserTravelMs(20)).toBe(4000);
    expect(laserRemanenceMs(20)).toBe(1400);
    expect(LIVE_PACKET_MS).toBe(1400);
    expect(LIVE_REMANENCE_MS).toBe(400);
    expect(LIVE_HOLD_MS).toBeGreaterThanOrEqual(200);
    expect(LIVE_HOLD_MS).toBeLessThanOrEqual(400);
    expect(MAX_CONCURRENT_ANIMS).toBe(32);
    expect(MAX_PENDING_ANIMS).toBe(48);
    expect(laserTravel(3, 0, 1850).headT).toBe(0);
    expect(laserTravel(3, 925, 1850).headT).toBeCloseTo(0.5);
    expect(laserTravel(1, 1399, 1400).finished).toBe(false);
    expect(laserTravel(1, 1400, 1400).finished).toBe(true);
  });

  it('interpolates along the polyline in lon/lat order', () => {
    const mid = interpolatePolyline(
      [
        [0, 0],
        [10, 10],
      ],
      0.5
    );
    expect(mid).toEqual([5, 5]);
  });

  it('fades remanence to zero by the end of its lifetime', () => {
    expect(remanenceOpacity(0)).toBe(1);
    expect(remanenceOpacity(LIVE_REMANENCE_MS / 2, LIVE_REMANENCE_MS)).toBeCloseTo(0.25);
    expect(remanenceOpacity(LIVE_REMANENCE_MS)).toBe(0);
    expect(remanenceOpacity(20_000)).toBe(0);
  });

  it('clips visible edges to the travelling head and keeps probable distinct', () => {
    const poly = buildLaserPolyline(
      observation({
        waypoints: [
          waypoint(0, 0, { confidence: 'exact' }),
          waypoint(0, 10, { confidence: 'exact' }),
          waypoint(0, 20, { confidence: 'probable', reason: 'region' }),
        ],
      })
    );
    const early = visibleEdgeSlices(poly, 0, 0.4, false);
    expect(early).toHaveLength(1);
    expect(early[0].confidence).toBe('exact');
    const late = visibleEdgeSlices(poly, 0.6, 1, false);
    expect(late.some((slice) => slice.confidence === 'probable' && slice.reason === 'region')).toBe(
      true
    );
    expect(visibleEdgeSlices(poly, 0, 1, true).every((slice) => slice.confidence === 'exact')).toBe(
      true
    );
  });
});

describe('stroke, color, and node/ear encoding', () => {
  it('maps packet-type hex into an RGBA with the requested alpha', () => {
    expect(hexToRgba('#22c55e', 1)).toEqual([34, 197, 94, 255]);
    expect(hexToRgba('#06b6d4', 0.5)[3]).toBe(128);
  });

  it('keeps probable strokes thinner and dashed versus exact', () => {
    const exact = strokeStyleForConfidence('exact', 4);
    const probable = strokeStyleForConfidence('probable', 4);
    expect(exact.dashed).toBe(false);
    expect(probable.dashed).toBe(true);
    expect(probable.width).toBeLessThan(exact.width);
    expect(probable.opacityScale).toBeLessThan(exact.opacityScale);
  });

  it('splits a probable hop into dashed pieces instead of a solid line', () => {
    const pieces = dashLonLat([0, 0], [1, 0], 0.2, 0.2);
    expect(pieces.length).toBeGreaterThan(1);
    expect(pieces[0][0]).toEqual([0, 0]);
  });

  it('gives each community role a distinct color, size, and shape', () => {
    expect(nodeRoleStyle('repeater').color).not.toBe(nodeRoleStyle('companion').color);
    expect(nodeRoleStyle('repeater').radius).toBeGreaterThan(nodeRoleStyle('unknown').radius);
    expect(nodeRoleStyle('not-a-role').color).toBe(nodeRoleStyle('unknown').color);
    expect(nodeRoleStyle('repeater').shape).toBe('circle');
    expect(nodeRoleStyle('companion').shape).toBe('square');
    expect(nodeRoleStyle('client').shape).toBe('square');
    expect(nodeRoleStyle('room').shape).toBe('hexagon');
    expect(nodeRoleStyle('sensor').shape).toBe('triangle');
    expect(LIVE_ROLE_LEGEND.map((entry) => entry.shape)).toEqual([
      'circle',
      'square',
      'hexagon',
      'triangle',
    ]);
  });

  it('has no observer role: #live draws packets and hops, not who heard them', () => {
    expect(LIVE_ROLE_LEGEND.map((entry) => entry.role)).not.toContain('observer');
    expect(Object.keys(NODE_ROLE_STYLE)).not.toContain('observer');
    expect(nodeRoleStyle('observer')).toEqual(nodeRoleStyle('unknown'));
  });

  it('maps companion aliases without collapsing them to unknown', () => {
    expect(normalizeDirectoryRole('companion')).toBe('companion');
    expect(normalizeDirectoryRole('client')).toBe('companion');
    expect(normalizeDirectoryRole('chat')).toBe('companion');
    expect(nodeRoleStyle('client').color).toBe(nodeRoleStyle('companion').color);
  });

  it('keeps packet-type and role palettes disjoint and off the old shared amber', () => {
    expect(paletteColorsOverlap()).toBe(false);
    expect(Object.values(LIVE_TYPE_COLORS)).not.toContain('#f59e0b');
    expect(Object.values(NODE_ROLE_STYLE).map((style) => style.color)).not.toContain('#f59e0b');
    const wong = ['#D55E00', '#56B4E9', '#009E73', '#F0E442', '#CC79A7'];
    for (const color of wong) {
      expect(Object.values(LIVE_TYPE_COLORS)).not.toContain(color);
      expect(Object.values(NODE_ROLE_STYLE).map((style) => style.color)).not.toContain(color);
    }
  });

  it('draws a 2.0–2.8 neon core with a 3.25× / 0.48 glow', () => {
    expect(LASER_CORE_WIDTH_MIN).toBe(2.0);
    expect(LASER_CORE_WIDTH_MAX).toBe(2.8);
    expect(laserWidth(0)).toBe(2.0);
    expect(laserWidth(1)).toBe(2.8);
    expect(LASER_GLOW_WIDTH_SCALE).toBe(3.25);
    expect(LASER_GLOW_ALPHA).toBe(0.48);
    expect(laserGlowWidth(laserWidth(1))).toBeCloseTo(2.8 * 3.25);
    const head = laserHeadRadii(laserWidth(1));
    expect(head.halo).toBeCloseTo(3.4 + 2.8 * 0.35);
    expect(head.core).toBeCloseTo(1.7 + 2.8 * 0.15);
  });

  it('uses OSM raster tiles instead of CARTO Dark Matter', () => {
    const encoded = JSON.stringify(osmDarkRasterStyle());
    expect(encoded).toContain('openstreetmap');
    expect(encoded).not.toMatch(/carto/i);
    expect(encoded).toContain('#05070a');
  });

  it('keeps one visual for your own radio and none for community ears', () => {
    expect(LOCAL_RADIO_VISUAL.radius).toBeGreaterThan(0);
    expect(Object.values(NODE_ROLE_STYLE).map((style) => style.color)).not.toContain(
      LOCAL_RADIO_VISUAL.color
    );
  });

  it('keeps observer GPS for geometry and skips the observer icon', () => {
    const nodes = [
      {
        public_key: 'aa',
        name: 'A',
        role: 'repeater' as const,
        lat: 45.7,
        lon: 4.8,
        source: 'community-db' as const,
      },
      {
        public_key: 'bb',
        name: 'B',
        role: 'client' as const,
        lat: 0,
        lon: 0,
        source: 'community-db' as const,
      },
      {
        public_key: 'cc',
        name: 'Ear',
        role: 'observer' as const,
        lat: 45.8,
        lon: 4.9,
        source: 'community-db' as const,
      },
    ];
    expect(mappableDirectoryNodes(nodes).map((node) => node.public_key)).toEqual(['aa']);
    expect(geometryDirectoryNodes(nodes).map((node) => node.public_key)).toEqual(['aa', 'cc']);
  });
});

describe('camera and spawn policy', () => {
  it('opens the info pane for a known companion and the conversation for a repeater', () => {
    const companion = {
      public_key: 'aa'.repeat(32),
      name: 'Alice',
      type: 1,
    } as Contact;
    const repeater = {
      public_key: 'bb'.repeat(32),
      name: 'FR83-RPT',
      type: 2,
    } as Contact;
    const room = {
      public_key: 'cc'.repeat(32),
      name: 'Ops',
      type: 3,
    } as Contact;
    expect(resolveLiveKnownNodeAction('AA'.repeat(32), [companion, repeater])).toEqual({
      kind: 'info',
      publicKey: companion.public_key,
    });
    expect(resolveLiveKnownNodeAction(repeater.public_key, [companion, repeater])).toEqual({
      kind: 'conversation',
      publicKey: repeater.public_key,
      name: 'FR83-RPT',
    });
    expect(resolveLiveKnownNodeAction(room.public_key, [room])).toEqual({
      kind: 'conversation',
      publicKey: room.public_key,
      name: 'Ops',
    });
    expect(resolveLiveKnownNodeAction('dd'.repeat(32), [companion])).toBeNull();
  });

  it('never auto-fits after a saved camera or a user move', () => {
    expect(shouldAutoFitCamera(false, 10)).toBe(true);
    // A camera saved on a previous visit no longer suppresses the fit: arriving
    // means seeing what is being heard, wherever it is.
    expect(shouldAutoFitCamera(true, 10)).toBe(false);
    expect(shouldAutoFitCamera(false, 0)).toBe(false);
  });

  it('keeps only the newest catch-up lasers', () => {
    expect([...selectCatchup(['a', 'b', 'c', 'd'], 2)]).toEqual(['c', 'd']);
  });

  it('refuses to spawn lasers from stale timestamps', () => {
    expect(shouldSpawnLaser(observation({ t: Date.now() }))).toBe(true);
    expect(shouldSpawnLaser(observation({ t: Date.now() - 6 * 60 * 1000 }))).toBe(false);
  });

  it('keeps a 0-hop arrival as one point, so it flashes instead of drawing a line', () => {
    const poly = buildLaserPolyline(
      observation({ waypoints: [waypoint(43.76, 7.17, { kind: 'ear' })] })
    );
    expect(poly.points).toEqual([[7.17, 43.76]]);
    expect(poly.edgeConfidence).toEqual([]);
  });

  it('keeps hops on the polyline for DIRECT and unknown route kinds', () => {
    const hops = observation({
      routeKind: 'direct',
      waypoints: [
        waypoint(45.7, 4.8, { kind: 'hop', token: 'aa11' }),
        waypoint(46.2, 6.1, { kind: 'ear', token: 'ear' }),
      ],
    });
    expect(drawableLaserPolyline(hops, 'direct').points).toEqual([
      [4.8, 45.7],
      [6.1, 46.2],
    ]);
    expect(drawableLaserPolyline(hops, 'unknown').points).toEqual([
      [4.8, 45.7],
      [6.1, 46.2],
    ]);
    expect(drawableLaserPolyline(hops, 'unknown').vertexKind).toEqual(['hop', 'ear']);
    expect(drawableLaserPolyline(hops, 'flood').points).toEqual([
      [4.8, 45.7],
      [6.1, 46.2],
    ]);
  });

  it('treats an advert with community unknown routeKind as flood through the arrival', () => {
    const advert = observation({
      type: 'advert',
      routeKind: 'unknown',
      waypoints: [
        waypoint(45.7, 4.8, { kind: 'hop', token: 'aa11' }),
        waypoint(46.2, 6.1, { kind: 'ear', token: 'ear' }),
      ],
    });
    expect(drawableLaserPolyline(advert, 'unknown').points).toEqual([
      [4.8, 45.7],
      [6.1, 46.2],
    ]);
    expect(drawableLaserPolyline(advert, 'unknown').vertexKind).toEqual(['hop', 'ear']);
  });
});

describe('contacts overlay and ripples', () => {
  it('merges local contacts over directory and hides session tombstones', () => {
    const directory = [
      {
        public_key: 'AA'.repeat(32),
        name: 'Catalog',
        role: 'repeater' as const,
        lat: 45.7,
        lon: 4.8,
        source: 'community-db' as const,
      },
      {
        public_key: 'bb'.repeat(32),
        name: 'Backdrop',
        role: 'companion' as const,
        lat: 46.2,
        lon: 6.1,
        source: 'community-db' as const,
      },
    ];
    const local = localContactsToMapNodes([
      {
        public_key: 'aa'.repeat(32),
        name: 'LocalWin',
        type: 2,
        flags: 0,
        direct_path: null,
        direct_path_len: -1,
        direct_path_hash_mode: 0,
        last_advert: null,
        lat: 45.71,
        lon: 4.81,
        last_seen: null,
        on_radio: false,
        favorite: false,
        last_contacted: null,
        last_read_at: null,
        first_seen: null,
      },
    ]);
    const merged = mergeLocalOverDirectory(directory, local, new Set());
    expect(merged.map((node) => node.public_key)).toEqual(['aa'.repeat(32), 'bb'.repeat(32)]);
    expect(merged[0].name).toBe('LocalWin');
    expect(merged[0].lat).toBe(45.71);
    const tombstoned = mergeLocalOverDirectory(directory, [], new Set(['aa'.repeat(32)]));
    expect(tombstoned.map((node) => node.public_key)).toEqual(['bb'.repeat(32)]);
  });

  it('keeps observer GPS in the merged pin list and uses it as a hop pin', () => {
    const observer = {
      public_key: 'ee'.repeat(32),
      name: 'Ear',
      role: 'observer' as const,
      lat: 45.8,
      lon: 4.9,
      source: 'community-db' as const,
    };
    const merged = mergeLocalOverDirectory([observer], [], new Set());
    expect(merged.map((node) => node.public_key)).toEqual(['ee'.repeat(32)]);
    expect(mappableDirectoryNodes(merged)).toEqual([]);
    expect(findPinnedHop(undefined, observer.public_key, merged)?.lat).toBe(45.8);
  });

  it('drops blocked contacts from the overlay and never upserts hops', () => {
    const blocked = localContactsToMapNodes(
      [
        {
          public_key: 'cc'.repeat(32),
          name: 'Blocked',
          type: 1,
          flags: 0,
          direct_path: null,
          direct_path_len: -1,
          direct_path_hash_mode: 0,
          last_advert: null,
          lat: 45.1,
          lon: 4.1,
          last_seen: null,
          on_radio: false,
          favorite: false,
          last_contacted: null,
          last_read_at: null,
          first_seen: null,
        },
      ],
      ['cc'.repeat(32)],
      []
    );
    expect(blocked).toEqual([]);
  });

  it('grows a type-colored ring to 4× the visual pin in 700–800 ms', () => {
    expect(LIVE_RIPPLE_SCALE).toBe(4);
    expect(LIVE_RIPPLE_MS).toBeGreaterThanOrEqual(700);
    expect(LIVE_RIPPLE_MS).toBeLessThanOrEqual(800);
    const start = rippleRadii(4.4, 0);
    const end = rippleRadii(4.4, 1);
    expect(start.radius).toBeCloseTo(pinVisualRadius(4.4));
    expect(end.radius).toBeCloseTo(pinVisualRadius(4.4) * 4);
    expect(end.lineAlpha).toBe(0);
  });
});

describe('captured NCE Mont-Caume frame', () => {
  const montCaume: [number, number] = [5.898634, 43.182626];
  const peil: [number, number] = [7.412203, 43.800231];
  const ear: [number, number] = [7.177876, 43.760531];

  function built() {
    const obs = observationFromCommunity(NCE_MONT_CAUME_FRAME);
    expect(obs).not.toBeNull();
    const poly = buildLaserPolyline(obs!);
    const segments = segmentsFromObservation(obs!, false);
    const slices = visibleEdgeSlices(poly, 0, 1, false);
    return { obs: obs!, poly, segments, slices };
  }

  it('drops the leading geo_filtered hop without shifting later names', () => {
    const { obs, poly } = built();
    expect(obs.waypoints.map((point) => point.label)).toEqual([
      'FR34MPL-MAR',
      'FR34MPL-VLM',
      'FR83-Grand-Cap',
      'FR83-Mont-Caume',
      'FR06-PEIL-RPL1\u2600\ufe0f',
      undefined,
    ]);
    expect(poly.points).toHaveLength(6);
    expect(poly.vertexLabel).toEqual([
      'FR34MPL-MAR',
      'FR34MPL-VLM',
      'FR83-Grand-Cap',
      'FR83-Mont-Caume',
      'FR06-PEIL-RPL1\u2600\ufe0f',
      undefined,
    ]);
    expect(poly.edgeLabel).toHaveLength(poly.points.length - 1);
    expect(poly.edgeConfidence).toHaveLength(poly.points.length - 1);
  });

  it('keeps FR83-Mont-Caume on the edge that ends at Mont Caume, not Peille or the ear', () => {
    const { segments, slices } = built();
    const named = [...segments, ...slices].filter((row) => row.label === 'FR83-Mont-Caume');
    expect(named.length).toBeGreaterThan(0);
    for (const row of named) {
      expect(row.path[row.path.length - 1]).toEqual(montCaume);
      expect(row.path[row.path.length - 1]).not.toEqual(peil);
      expect(row.path[row.path.length - 1]).not.toEqual(ear);
      expect(row.toLabel).toBe('FR83-Mont-Caume');
      expect(row.fromLabel).toBe('FR83-Grand-Cap');
    }
  });

  it('names the nearer vertex when hovering the long Mont-Caume–Peille edge over the coast', () => {
    const { slices } = built();
    const across = slices.find(
      (slice) =>
        slice.fromLabel === 'FR83-Mont-Caume' && slice.toLabel === 'FR06-PEIL-RPL1\u2600\ufe0f'
    );
    expect(across).toBeDefined();
    expect(across!.path[0]).toEqual(montCaume);
    expect(across!.path[1]).toEqual(peil);
    expect(nearerEndpointLabel(across!.path, across!.fromLabel, across!.toLabel, peil)).toBe(
      'FR06-PEIL-RPL1\u2600\ufe0f'
    );
    expect(nearerEndpointLabel(across!.path, across!.fromLabel, across!.toLabel, montCaume)).toBe(
      'FR83-Mont-Caume'
    );
    expect(nearerEndpointLabel(across!.path, across!.fromLabel, across!.toLabel, ear)).toBe(
      'FR06-PEIL-RPL1\u2600\ufe0f'
    );
  });

  it('does not treat every exact slice as the same hover identity', () => {
    expect(liveHoverKey({ kind: 'exact', label: 'FR83-Mont-Caume' })).not.toBe(
      liveHoverKey({ kind: 'exact', label: 'FR06-PEIL-RPL1\u2600\ufe0f' })
    );
    expect(liveHoverKey({ kind: 'exact', label: 'FR83-Mont-Caume' })).toBe('exact:FR83-Mont-Caume');
  });

  it('replaces pooled path arrays instead of mutating them in place', () => {
    const first = cloneLonLatPath([montCaume, peil]);
    const second = cloneLonLatPath([ear, montCaume]);
    expect(first).not.toBe(second);
    expect(first[0]).not.toBe(montCaume);
    expect(second[1]).toEqual(montCaume);
  });
});
