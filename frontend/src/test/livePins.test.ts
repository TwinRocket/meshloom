import { describe, expect, it } from 'vitest';

import {
  LIVE_CLUSTER_MAX_ZOOM,
  applyObservationPins,
  clusterLiveNodes,
  mergeLiveNodes,
  nodeDrawOpacity,
  pinsFromObservation,
} from '../components/live/livePins';
import {
  LASER_CORE_WIDTH_MAX,
  LASER_GLOW_WIDTH_SCALE,
  NODE_ROLE_STYLE,
  buildLaserPolyline,
  laserGlowWidth,
  laserWidth,
  nodeRoleStyle,
} from '../components/live/liveRender';
import {
  NCE_ADVERT_FRAME,
  NCE_IATA_CENTROID,
  NCE_IATA_EAR_FRAME,
  NCE_LIVE_ROLE_NODES,
  NCE_MONT_CAUME,
  NCE_MONT_CAUME_FRAME,
} from '../fixtures/nceLiveFixtures';
import { LIVE_TYPE_COLORS, observationFromCommunity } from '../utils/livePackets';

describe('pins from the live packet stream', () => {
  it('lights FR83-Mont-Caume from an exact hop without a directory snapshot', () => {
    const obs = observationFromCommunity(NCE_MONT_CAUME_FRAME);
    expect(obs).not.toBeNull();
    const pins = pinsFromObservation(obs!);
    const montCaume = pins.find((pin) => pin.name === NCE_MONT_CAUME.name);
    expect(montCaume).toMatchObject({
      public_key: NCE_MONT_CAUME.pubkey,
      lat: NCE_MONT_CAUME.lat,
      lon: NCE_MONT_CAUME.lon,
    });
    expect(pins.some((pin) => pin.public_key.startsWith('71c397'))).toBe(true);
  });

  it('refreshes a directory node last_seen when the same exact hop arrives', () => {
    const obs = observationFromCommunity({ ...NCE_ADVERT_FRAME, t: 1_800_000_000_000 });
    expect(obs).not.toBeNull();
    const stream = new Map<string, (typeof NCE_LIVE_ROLE_NODES)[number]>();
    const lit = applyObservationPins(NCE_LIVE_ROLE_NODES, stream, obs!);
    expect(lit).toHaveLength(2);
    const merged = mergeLiveNodes(NCE_LIVE_ROLE_NODES, stream.values());
    const montCaume = merged.find((node) => node.public_key === NCE_MONT_CAUME.pubkey);
    expect(montCaume?.role).toBe('repeater');
    expect(montCaume?.source).toBe('community-db');
    expect(montCaume?.last_seen).toBe(1_800_000_000);
  });

  it('does not wait for directory to show a brand-new exact hop', () => {
    const obs = observationFromCommunity(NCE_MONT_CAUME_FRAME);
    const stream = new Map();
    applyObservationPins([], stream, obs!);
    expect(stream.size).toBeGreaterThan(0);
    expect([...stream.values()].some((node) => node.name === NCE_MONT_CAUME.name)).toBe(true);
  });
});

describe('NCE / Mont-Caume fixture verification', () => {
  it('keeps repeater, companion, room, and advert immediately distinct', () => {
    expect(nodeRoleStyle('repeater').shape).toBe('circle');
    expect(nodeRoleStyle('companion').shape).toBe('square');
    expect(nodeRoleStyle('room').shape).toBe('hexagon');
    expect(nodeRoleStyle('repeater').color).not.toBe(nodeRoleStyle('companion').color);
    expect(nodeRoleStyle('companion').color).not.toBe(nodeRoleStyle('room').color);
    expect(nodeRoleStyle('room').color).not.toBe(nodeRoleStyle('repeater').color);
    expect(LIVE_TYPE_COLORS.advert).not.toBe(NODE_ROLE_STYLE.repeater.color);
    expect(LIVE_TYPE_COLORS.advert).not.toBe(NODE_ROLE_STYLE.companion.color);
    expect(LIVE_TYPE_COLORS.advert).not.toBe(NODE_ROLE_STYLE.room.color);
    expect(NCE_LIVE_ROLE_NODES.map((node) => nodeRoleStyle(node.role).shape)).toEqual([
      'circle',
      'square',
      'hexagon',
    ]);
    expect(NCE_ADVERT_FRAME.type).toBe('advert');
  });

  it('keeps lasers thin on the captured NCE path', () => {
    const obs = observationFromCommunity(NCE_MONT_CAUME_FRAME);
    expect(obs).not.toBeNull();
    const poly = buildLaserPolyline(obs!);
    expect(poly.points.length).toBeGreaterThan(2);
    expect(laserWidth(1)).toBeLessThanOrEqual(LASER_CORE_WIDTH_MAX);
    expect(LASER_GLOW_WIDTH_SCALE).toBeLessThanOrEqual(2);
    expect(laserGlowWidth(laserWidth(1))).toBeLessThanOrEqual(laserWidth(1) * 2);
  });

  it('never draws a laser to the Nice airport centroid', () => {
    const captured = observationFromCommunity(NCE_MONT_CAUME_FRAME);
    const iataOnly = observationFromCommunity(NCE_IATA_EAR_FRAME);
    expect(captured).not.toBeNull();
    expect(iataOnly).not.toBeNull();
    const airport: [number, number] = [NCE_IATA_CENTROID.lon, NCE_IATA_CENTROID.lat];
    for (const obs of [captured!, iataOnly!]) {
      const poly = buildLaserPolyline(obs);
      expect(poly.points).not.toContainEqual(airport);
      expect(
        obs.waypoints.some((point) => point.lat === airport[1] && point.lon === airport[0])
      ).toBe(false);
    }
    expect(iataOnly!.ear).toEqual({
      lat: NCE_IATA_CENTROID.lat,
      lon: NCE_IATA_CENTROID.lon,
      source: 'iata',
    });
  });
});

describe('live node clustering', () => {
  it('collapses nearby Riviera pins at world zoom and expands role shapes later', () => {
    const clustered = clusterLiveNodes(NCE_LIVE_ROLE_NODES, 3);
    expect(clustered.some((item) => item.kind === 'cluster')).toBe(true);
    const expanded = clusterLiveNodes(NCE_LIVE_ROLE_NODES, LIVE_CLUSTER_MAX_ZOOM);
    expect(expanded.every((item) => item.kind === 'node')).toBe(true);
    expect(expanded.map((item) => (item.kind === 'node' ? item.node.role : ''))).toEqual([
      'repeater',
      'companion',
      'room',
    ]);
    expect(
      expanded.map((item) => (item.kind === 'node' ? nodeRoleStyle(item.node.role).shape : ''))
    ).toEqual(['circle', 'square', 'hexagon']);
  });

  it('leaves a lone pin unclustered even at low zoom', () => {
    const items = clusterLiveNodes(NCE_LIVE_ROLE_NODES.slice(0, 1), 2);
    expect(items).toEqual([{ kind: 'node', node: NCE_LIVE_ROLE_NODES[0] }]);
  });
});

describe('pin freshness', () => {
  it('lights a just-heard pin and fades a stale directory last_seen', () => {
    const now = 1_800_000_000_000;
    expect(nodeDrawOpacity(1_800_000_000, now, true)).toBe(1);
    expect(nodeDrawOpacity(1_800_000_000, now, false)).toBeGreaterThan(
      nodeDrawOpacity(1_700_000_000, now, false)
    );
  });
});
