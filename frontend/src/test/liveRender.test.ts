import { describe, expect, it } from 'vitest';

import type { CommunityPacketType } from '../types';
import type { LiveObservation, LiveWaypoint } from '../utils/livePackets';
import {
  LIVE_SEGMENT_MS,
  buildLaserPolyline,
  collectIataCodes,
  dashLonLat,
  earVisual,
  filterLiveObservations,
  hexToRgba,
  interpolatePolyline,
  laserTravel,
  laserWidth,
  mappableDirectoryNodes,
  nodeRoleStyle,
  observationPassesFilters,
  remanenceOpacity,
  segmentsFromObservation,
  selectCatchup,
  shouldAutoFitCamera,
  shouldSpawnLaser,
  strokeStyleForConfidence,
  visibleEdgeSlices,
  waypointConfidence,
} from '../components/live/liveRender';

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
  it('starts at the first vertex and finishes after every segment', () => {
    expect(laserTravel(3, 0).headT).toBe(0);
    expect(laserTravel(3, 0).finished).toBe(false);
    expect(laserTravel(3, LIVE_SEGMENT_MS).headT).toBeCloseTo(0.5);
    expect(laserTravel(3, LIVE_SEGMENT_MS * 2).finished).toBe(true);
    expect(laserTravel(3, LIVE_SEGMENT_MS * 2).headT).toBe(1);
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
    expect(remanenceOpacity(4500, 9000)).toBeCloseTo(0.25);
    expect(remanenceOpacity(9000)).toBe(0);
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

  it('gives each community role a distinct color and size', () => {
    expect(nodeRoleStyle('repeater').color).not.toBe(nodeRoleStyle('client').color);
    expect(nodeRoleStyle('repeater').radius).toBeGreaterThan(nodeRoleStyle('unknown').radius);
    expect(nodeRoleStyle('not-a-role').color).toBe(nodeRoleStyle('unknown').color);
  });

  it('makes advert ears tighter and brighter than IATA centroids', () => {
    const advert = earVisual('advert');
    const iata = earVisual('iata');
    expect(advert.radius).toBeLessThan(iata.radius);
    expect(advert.opacity).toBeGreaterThan(iata.opacity);
    expect(advert.color).not.toBe(iata.color);
    expect(earVisual('local').color).not.toBe(advert.color);
  });

  it('drops directory nodes without a real position', () => {
    expect(
      mappableDirectoryNodes([
        { public_key: 'aa', name: 'A', role: 'repeater', lat: 45.7, lon: 4.8, source: 'corescope' },
        { public_key: 'bb', name: 'B', role: 'client', lat: 0, lon: 0, source: 'corescope' },
      ])
    ).toHaveLength(1);
  });
});

describe('camera and spawn policy', () => {
  it('never auto-fits after a saved camera or a user move', () => {
    expect(shouldAutoFitCamera(false, false, 10)).toBe(true);
    expect(shouldAutoFitCamera(true, false, 10)).toBe(false);
    expect(shouldAutoFitCamera(false, true, 10)).toBe(false);
    expect(shouldAutoFitCamera(false, false, 0)).toBe(false);
  });

  it('keeps only the newest catch-up lasers', () => {
    expect([...selectCatchup(['a', 'b', 'c', 'd'], 2)]).toEqual(['c', 'd']);
  });

  it('refuses to spawn lasers from stale timestamps', () => {
    expect(shouldSpawnLaser(observation({ t: Date.now() }))).toBe(true);
    expect(shouldSpawnLaser(observation({ t: Date.now() - 6 * 60 * 1000 }))).toBe(false);
  });
});
