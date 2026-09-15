import type {
  CommunityPacketType,
  Contact,
  DirectoryMapNode,
  DirectoryNodeRole,
} from '../../types';
import { isValidLocation } from '../../utils/pathUtils';
import {
  LIVE_PACKET_TYPES,
  LIVE_TYPE_COLORS,
  isStaleLiveTime,
  liveOpacity,
  liveTypeColor,
  observationBucketKey,
  snrWeight,
  type FanoutHop,
  type LiveObservation,
  type LiveOriginPin,
  type LiveRouteKind,
  type LiveSource,
  type LiveWaypoint,
} from '../../utils/livePackets';

export { LIVE_PACKET_TYPES };

/** 0-edge (1-point flash) travel. Multi-hop uses laserTravelMs(placeableEdges). */
export const LIVE_PACKET_MS = 1400;
/** @deprecated Use laserTravelMs(placeableEdges). */
export const LIVE_SEGMENT_MS = LIVE_PACKET_MS;
export const LIVE_TRAIL_FRACTION = 0.25;
export const LIVE_TRAIL_MS = Math.round(LIVE_PACKET_MS * LIVE_TRAIL_FRACTION);
export const LIVE_REMANENCE_MS = 400;
export const LIVE_STAGGER_MS = 90;
/** Short coalesce so a hop+ear frame draws as one polyline, not after a 5s hold. */
export const LIVE_HOLD_MS = 300;
/** Simultaneous in-flight lasers. Surplus waits in MAX_PENDING_ANIMS.
 *
 * Raised from 20 after profiling a real window: 116.9fps, worst frame 16.8ms,
 * no frame over 50ms. The limit here is legibility, not render cost — past
 * roughly 36 the map reads as noise. Steady demand at the measured arrival rate
 * is already ~20 slots, so this absorbs a burst instead of queueing it. */
export const MAX_CONCURRENT_ANIMS = 32;
/** Deferred shots waiting for a free slot. Overflow sheds the oldest. */
export const MAX_PENDING_ANIMS = 48;
/** Newest retained shots, including in-flight and not-yet-started. */
export const MAX_LIVE_SHOTS = 140;
/** Newest observations kept when one coalesce bucket releases. */
export const MAX_LIVE_CATCHUP = 36;
export const LIVE_CAMERA_STORAGE_KEY = 'meshloom-live-camera';

export const LASER_CORE_WIDTH_MIN = 2.0;
export const LASER_CORE_WIDTH_MAX = 2.8;
export const LASER_GLOW_WIDTH_SCALE = 3.25;
export const LASER_GLOW_ALPHA = 0.48;

export const LIVE_RIPPLE_SCALE = 4;
export const LIVE_RIPPLE_MS = 760;
export const LIVE_PIN_VISUAL_RADIUS_SCALE = 1.2;

export type LiveHopConfidence = 'exact' | 'probable' | 'unresolved';
export type LonLat = [number, number];
export type Rgba = [number, number, number, number];

export interface LiveViewFilters {
  iata: string;
  hiddenTypes: ReadonlySet<CommunityPacketType>;
  exactOnly: boolean;
}

export interface LiveCamera {
  lat: number;
  lon: number;
  zoom: number;
}

export interface LaserPolyline {
  points: LonLat[];
  vertexLabel: Array<string | undefined>;
  vertexKind: Array<LiveWaypoint['kind']>;
  vertexPubkey: Array<string | undefined>;
  vertexToken: Array<string | undefined>;
  edgeConfidence: Array<'exact' | 'probable'>;
  edgeReason: Array<string | undefined>;
  edgeLabel: Array<string | undefined>;
}

export interface LiveDrawSegment {
  id: string;
  observationId: string;
  path: LonLat[];
  confidence: 'exact' | 'probable';
  reason?: string;
  fromLabel?: string;
  toLabel?: string;
  label?: string;
  colorHex: string;
  width: number;
  opacityScale: number;
  dashed: boolean;
}

export interface LaserTravel {
  finished: boolean;
  headT: number;
  head: LonLat | null;
  trailStartT: number;
}

export interface ConfidenceStroke {
  width: number;
  opacityScale: number;
  dashed: boolean;
}

export type LiveRoleShape = 'circle' | 'square' | 'hexagon' | 'triangle';

export type NormalizedDirectoryRole = 'repeater' | 'companion' | 'room' | 'sensor' | 'unknown';

export interface NodeRoleStyle {
  color: string;
  radius: number;
  shape: LiveRoleShape;
}

export interface LocalRadioVisual {
  color: string;
  ring: string;
  radius: number;
  opacity: number;
}

const COMPANION_STYLE: NodeRoleStyle = { color: '#60a5fa', radius: 3.2, shape: 'square' };

/** Role palette. Disjoint from LIVE_TYPE_COLORS; MeshLoom identity, not Wong.
 *  No observer entry: #live draws packets and hops, never who heard them. */
export const NODE_ROLE_STYLE: Record<NormalizedDirectoryRole, NodeRoleStyle> = {
  repeater: { color: '#f43f5e', radius: 4.4, shape: 'circle' },
  companion: COMPANION_STYLE,
  room: { color: '#818cf8', radius: 4.1, shape: 'hexagon' },
  sensor: { color: '#2dd4bf', radius: 3.5, shape: 'triangle' },
  unknown: { color: '#64748b', radius: 2.8, shape: 'circle' },
};

export const LIVE_ROLE_LEGEND: ReadonlyArray<{
  role: NormalizedDirectoryRole;
  shape: LiveRoleShape;
}> = [
  { role: 'repeater', shape: 'circle' },
  { role: 'companion', shape: 'square' },
  { role: 'room', shape: 'hexagon' },
  { role: 'sensor', shape: 'triangle' },
];

export const LIVE_ROLE_SHAPES: readonly LiveRoleShape[] = [
  'circle',
  'square',
  'hexagon',
  'triangle',
];

export const DEFAULT_LIVE_CAMERA: LiveCamera = { lat: 24, lon: 8, zoom: 2.15 };

export function emptyLiveFilters(): LiveViewFilters {
  return { iata: '', hiddenTypes: new Set(), exactOnly: false };
}

export function waypointConfidence(point: LiveWaypoint): LiveHopConfidence {
  const extra = point as LiveWaypoint & { confidence?: LiveHopConfidence };
  if (
    extra.confidence === 'exact' ||
    extra.confidence === 'probable' ||
    extra.confidence === 'unresolved'
  ) {
    return extra.confidence;
  }
  return (point.kind as string) === 'fade' ? 'probable' : 'exact';
}

export function waypointReason(point: LiveWaypoint): string | undefined {
  const extra = point as LiveWaypoint & { reason?: string };
  return extra.reason;
}

export function waypointLabel(point: LiveWaypoint): string | undefined {
  const extra = point as LiveWaypoint & { label?: string };
  return extra.label;
}

export function hexToRgba(hex: string, alpha: number): Rgba {
  const raw = hex.replace('#', '');
  const full =
    raw.length === 3
      ? raw
          .split('')
          .map((ch) => ch + ch)
          .join('')
      : raw;
  const n = Number.parseInt(full, 16);
  if (!Number.isFinite(n)) return [148, 163, 184, Math.round(clamp01(alpha) * 255)];
  return [(n >> 16) & 255, (n >> 8) & 255, n & 255, Math.round(clamp01(alpha) * 255)];
}

export function laserWidth(weight: number): number {
  return LASER_CORE_WIDTH_MIN + clamp01(weight) * (LASER_CORE_WIDTH_MAX - LASER_CORE_WIDTH_MIN);
}

export function laserGlowWidth(coreWidth: number): number {
  return coreWidth * LASER_GLOW_WIDTH_SCALE;
}

export function laserHeadRadii(coreWidth: number): { halo: number; core: number } {
  return {
    halo: 3.4 + coreWidth * 0.35,
    core: 1.7 + coreWidth * 0.15,
  };
}

export function placeableEdges(pointCount: number): number {
  return Math.max(0, pointCount - 1);
}

export function laserTravelMs(edges: number): number {
  return Math.min(4000, Math.max(1400, 1400 + 450 * Math.max(0, edges)));
}

export function laserRemanenceMs(edges: number): number {
  return Math.min(1400, Math.max(400, 400 + 250 * Math.max(0, edges)));
}

export function pinVisualRadius(roleRadius: number): number {
  return roleRadius * LIVE_PIN_VISUAL_RADIUS_SCALE;
}

export function rippleRadii(pinRadius: number, t: number): { radius: number; lineAlpha: number } {
  const progress = clamp01(t);
  const visual = pinVisualRadius(pinRadius);
  return {
    radius: visual * (1 + (LIVE_RIPPLE_SCALE - 1) * progress),
    lineAlpha: 1 - progress,
  };
}

export function strokeStyleForConfidence(
  confidence: 'exact' | 'probable',
  baseWidth: number
): ConfidenceStroke {
  if (confidence === 'probable') {
    return { width: baseWidth * 0.52, opacityScale: 0.4, dashed: true };
  }
  return { width: baseWidth, opacityScale: 1, dashed: false };
}

/** Your own radio, drawn once. Community ears are not nodes on this map. */
export const LOCAL_RADIO_VISUAL: LocalRadioVisual = {
  color: '#e879f9',
  ring: '#f5d0fe',
  radius: 7.5,
  opacity: 0.88,
};

export function normalizeDirectoryRole(
  role: DirectoryNodeRole | string | undefined
): NormalizedDirectoryRole {
  if (role === 'repeater' || role === 'room' || role === 'sensor') {
    return role;
  }
  if (role === 'companion' || role === 'client' || role === 'chat') {
    return 'companion';
  }
  return 'unknown';
}

export function nodeRoleStyle(role: DirectoryNodeRole | string | undefined): NodeRoleStyle {
  return NODE_ROLE_STYLE[normalizeDirectoryRole(role)];
}

export function observationPassesFilters(obs: LiveObservation, filters: LiveViewFilters): boolean {
  const iata = filters.iata.trim().toUpperCase();
  if (iata && obs.iata && obs.iata.toUpperCase() !== iata) return false;
  return !filters.hiddenTypes.has(obs.type);
}

export function collectIataCodes(observations: Iterable<LiveObservation>): string[] {
  const codes = new Set<string>();
  for (const obs of observations) {
    if (obs.iata) codes.add(obs.iata.toUpperCase());
  }
  return [...codes].sort();
}

export function filterLiveObservations(
  observations: LiveObservation[],
  filters: LiveViewFilters
): LiveObservation[] {
  return observations.filter((obs) => observationPassesFilters(obs, filters));
}

export function localHash8Set(observations: LiveObservation[]): Set<string> {
  const set = new Set<string>();
  for (const obs of observations) {
    if (obs.source !== 'local') continue;
    set.add(observationBucketKey(obs));
    set.add(obs.hash8);
  }
  return set;
}

export function observationDrawOpacity(obs: LiveObservation, hash8HasLocalTwin: boolean): number {
  return liveOpacity(obs.source, obs.snr, hash8HasLocalTwin);
}

export function observationColor(obs: LiveObservation): string {
  return liveTypeColor(obs.type);
}

export function shouldSpawnLaser(obs: LiveObservation, now: number = Date.now()): boolean {
  return !isStaleLiveTime(obs.t, now);
}

export function emptyLaserPolyline(): LaserPolyline {
  return {
    points: [],
    vertexLabel: [],
    vertexKind: [],
    vertexPubkey: [],
    vertexToken: [],
    edgeConfidence: [],
    edgeReason: [],
    edgeLabel: [],
  };
}

export function buildLaserPolyline(obs: LiveObservation): LaserPolyline {
  const points: LonLat[] = [];
  const vertexLabel: Array<string | undefined> = [];
  const vertexKind: Array<LiveWaypoint['kind']> = [];
  const vertexPubkey: Array<string | undefined> = [];
  const vertexToken: Array<string | undefined> = [];
  const edgeConfidence: Array<'exact' | 'probable'> = [];
  const edgeReason: Array<string | undefined> = [];
  const edgeLabel: Array<string | undefined> = [];

  for (const point of obs.waypoints) {
    const confidence = waypointConfidence(point);
    if (confidence === 'unresolved') continue;
    if (!Number.isFinite(point.lat) || !Number.isFinite(point.lon)) continue;
    const label = waypointLabel(point);
    if (points.length > 0) {
      edgeConfidence.push(confidence === 'probable' ? 'probable' : 'exact');
      edgeReason.push(waypointReason(point));
      edgeLabel.push(label);
    }
    points.push([point.lon, point.lat]);
    vertexLabel.push(label);
    vertexKind.push(point.kind);
    vertexPubkey.push(point.pubkey);
    vertexToken.push(point.token);
  }

  return {
    points,
    vertexLabel,
    vertexKind,
    vertexPubkey,
    vertexToken,
    edgeConfidence,
    edgeReason,
    edgeLabel,
  };
}

/** Heard path: exact/probable hops + ear. `routeKind` does not strip hops —
 *  unknown still draws hop→ear. `>=2` points is a line; `1` is a pulse. */
export function drawableLaserPolyline(
  obs: LiveObservation,
  _routeKind: LiveRouteKind = obs.routeKind
): LaserPolyline {
  return buildLaserPolyline(obs);
}

export function laserPolylineOriginToHop(origin: LiveOriginPin, hop: FanoutHop): LaserPolyline {
  const kind = hop.kind ?? 'hop';
  return {
    points: [
      [origin.lon, origin.lat],
      [hop.lon, hop.lat],
    ],
    vertexLabel: [undefined, hop.label],
    vertexKind: ['origin', kind],
    vertexPubkey: [origin.public_key, hop.pubkey],
    vertexToken: [origin.public_key.slice(0, 8), hop.token],
    edgeConfidence: [hop.confidence],
    edgeReason: [undefined],
    edgeLabel: [hop.label],
  };
}

export function segmentsFromObservation(
  obs: LiveObservation,
  exactOnly: boolean
): LiveDrawSegment[] {
  const poly = buildLaserPolyline(obs);
  const colorHex = observationColor(obs);
  const width = laserWidth(snrWeight(obs.snr));
  const segments: LiveDrawSegment[] = [];

  for (let i = 0; i < poly.edgeConfidence.length; i++) {
    const confidence = poly.edgeConfidence[i];
    if (exactOnly && confidence === 'probable') continue;
    const style = strokeStyleForConfidence(confidence, width);
    const from = poly.points[i];
    const to = poly.points[i + 1];
    segments.push({
      id: `${obs.id}:${i}`,
      observationId: obs.id,
      path: [from, to],
      confidence,
      reason: poly.edgeReason[i],
      fromLabel: poly.vertexLabel[i],
      toLabel: poly.vertexLabel[i + 1],
      label: poly.edgeLabel[i],
      colorHex,
      width: style.width,
      opacityScale: style.opacityScale,
      dashed: style.dashed,
    });
  }

  return segments;
}

export function laserTravel(
  pointCount: number,
  elapsedMs: number,
  totalMs: number = LIVE_PACKET_MS
): LaserTravel {
  const duration = Math.max(totalMs, 1);
  if (pointCount <= 0) {
    return { finished: true, headT: 1, head: null, trailStartT: 1 };
  }
  if (pointCount === 1) {
    const finished = elapsedMs >= duration;
    return {
      finished,
      headT: finished ? 1 : clamp01(elapsedMs / duration),
      head: null,
      trailStartT: 0,
    };
  }
  const headT = clamp01(elapsedMs / duration);
  const trailWindow = Math.min(0.85, LIVE_TRAIL_FRACTION);
  return {
    finished: elapsedMs >= duration,
    headT,
    head: null,
    trailStartT: Math.max(0, headT - trailWindow),
  };
}

const ROLE_ICON_CELL = 64;

export type RoleIconMapping = Record<
  LiveRoleShape,
  {
    x: number;
    y: number;
    width: number;
    height: number;
    anchorX: number;
    anchorY: number;
    mask: true;
  }
>;

export function drawRoleShape(
  ctx: Pick<
    CanvasRenderingContext2D,
    'beginPath' | 'moveTo' | 'lineTo' | 'closePath' | 'arc' | 'fill'
  >,
  shape: LiveRoleShape,
  cx: number,
  cy: number,
  r: number
): void {
  ctx.beginPath();
  if (shape === 'circle') {
    ctx.arc(cx, cy, r, 0, Math.PI * 2);
  } else if (shape === 'square') {
    ctx.moveTo(cx - r, cy - r);
    ctx.lineTo(cx + r, cy - r);
    ctx.lineTo(cx + r, cy + r);
    ctx.lineTo(cx - r, cy + r);
    ctx.closePath();
  } else {
    const sides = shape === 'hexagon' ? 6 : 3;
    const start = shape === 'triangle' ? -Math.PI / 2 : 0;
    for (let i = 0; i < sides; i++) {
      const angle = start + (i * 2 * Math.PI) / sides;
      const x = cx + r * Math.cos(angle);
      const y = cy + r * Math.sin(angle);
      if (i === 0) ctx.moveTo(x, y);
      else ctx.lineTo(x, y);
    }
    ctx.closePath();
  }
  ctx.fill();
}

export function buildRoleIconAtlas(): { atlas: HTMLCanvasElement; mapping: RoleIconMapping } {
  const canvas = document.createElement('canvas');
  canvas.width = ROLE_ICON_CELL * LIVE_ROLE_SHAPES.length;
  canvas.height = ROLE_ICON_CELL;
  const ctx = canvas.getContext('2d');
  const mapping = {} as RoleIconMapping;
  LIVE_ROLE_SHAPES.forEach((shape, index) => {
    if (ctx) {
      ctx.save();
      ctx.translate(index * ROLE_ICON_CELL + ROLE_ICON_CELL / 2, ROLE_ICON_CELL / 2);
      ctx.fillStyle = '#ffffff';
      drawRoleShape(ctx, shape, 0, 0, ROLE_ICON_CELL * 0.36);
      ctx.restore();
    }
    mapping[shape] = {
      x: index * ROLE_ICON_CELL,
      y: 0,
      width: ROLE_ICON_CELL,
      height: ROLE_ICON_CELL,
      anchorX: ROLE_ICON_CELL / 2,
      anchorY: ROLE_ICON_CELL / 2,
      mask: true,
    };
  });
  return { atlas: canvas, mapping };
}

export function paletteColorsOverlap(): boolean {
  const types = new Set(Object.values(LIVE_TYPE_COLORS));
  return Object.values(NODE_ROLE_STYLE).some((style) => types.has(style.color));
}

export function interpolatePolyline(points: LonLat[], t: number): LonLat | null {
  if (points.length === 0) return null;
  if (points.length === 1 || t <= 0) return points[0];
  if (t >= 1) return points[points.length - 1];
  const scaled = t * (points.length - 1);
  const index = Math.min(points.length - 2, Math.floor(scaled));
  const local = scaled - index;
  const a = points[index];
  const b = points[index + 1];
  return [a[0] + (b[0] - a[0]) * local, a[1] + (b[1] - a[1]) * local];
}

export function remanenceOpacity(ageMs: number, lifetimeMs: number = LIVE_REMANENCE_MS): number {
  if (ageMs < 0) return 1;
  if (ageMs >= lifetimeMs) return 0;
  const linear = 1 - ageMs / lifetimeMs;
  return linear * linear;
}

export function dashLonLat(from: LonLat, to: LonLat, dashDeg = 0.16, gapDeg = 0.12): LonLat[][] {
  const dx = to[0] - from[0];
  const dy = to[1] - from[1];
  const len = Math.hypot(dx, dy);
  if (len < 1e-9) return [[from, to]];
  const pieces: LonLat[][] = [];
  const dashT = dashDeg / len;
  const gapT = gapDeg / len;
  let t = 0;
  while (t < 1 - 1e-6) {
    const t1 = Math.min(1, t + dashT);
    pieces.push([
      [from[0] + dx * t, from[1] + dy * t],
      [from[0] + dx * t1, from[1] + dy * t1],
    ]);
    t = t1 + gapT;
  }
  return pieces.length > 0 ? pieces : [[from, to]];
}

export function flattenDashes(pieces: LonLat[][]): LonLat[] {
  const path: LonLat[] = [];
  for (const piece of pieces) {
    if (path.length > 0) path.push(piece[0]);
    path.push(piece[0], piece[1]);
  }
  return path;
}

export function slicePolyline(points: LonLat[], t0: number, t1: number): LonLat[] {
  if (points.length === 0) return [];
  if (points.length === 1) return [points[0]];
  const start = Math.min(t0, t1);
  const end = Math.max(t0, t1);
  if (end - start < 1e-5) {
    const head = interpolatePolyline(points, end);
    return head ? [head] : [];
  }
  const lastIndex = points.length - 1;
  const startScaled = start * lastIndex;
  const endScaled = end * lastIndex;
  const startEdge = Math.min(lastIndex - 1, Math.floor(startScaled));
  const endEdge = Math.min(lastIndex - 1, Math.floor(endScaled - 1e-9));
  const out: LonLat[] = [];
  const first = interpolatePolyline(points, start);
  if (first) out.push(first);
  for (let i = startEdge + 1; i <= endEdge; i++) {
    out.push(points[i]);
  }
  const last = interpolatePolyline(points, end);
  if (last) out.push(last);
  return out;
}

export function visibleEdgeSlices(
  poly: LaserPolyline,
  t0: number,
  t1: number,
  exactOnly: boolean
): Array<{
  path: LonLat[];
  confidence: 'exact' | 'probable';
  reason?: string;
  fromLabel?: string;
  toLabel?: string;
  label?: string;
}> {
  if (poly.points.length < 2) return [];
  const start = clamp01(Math.min(t0, t1));
  const end = clamp01(Math.max(t0, t1));
  const edgeCount = poly.edgeConfidence.length;
  const slices: Array<{
    path: LonLat[];
    confidence: 'exact' | 'probable';
    reason?: string;
    fromLabel?: string;
    toLabel?: string;
    label?: string;
  }> = [];

  for (let i = 0; i < edgeCount; i++) {
    const confidence = poly.edgeConfidence[i];
    if (exactOnly && confidence === 'probable') continue;
    const edgeStart = i / edgeCount;
    const edgeEnd = (i + 1) / edgeCount;
    const lo = Math.max(start, edgeStart);
    const hi = Math.min(end, edgeEnd);
    if (hi - lo <= 1e-5) continue;
    const localStart = (lo - edgeStart) / (edgeEnd - edgeStart);
    const localEnd = (hi - edgeStart) / (edgeEnd - edgeStart);
    const path = slicePolyline([poly.points[i], poly.points[i + 1]], localStart, localEnd);
    if (path.length < 2) continue;
    slices.push({
      path,
      confidence,
      reason: poly.edgeReason[i],
      fromLabel: poly.vertexLabel[i],
      toLabel: poly.vertexLabel[i + 1],
      label: poly.edgeLabel[i],
    });
  }
  return slices;
}

/** Name the vertex the cursor is actually on, not the whole edge's destination. */
export function nearerEndpointLabel(
  path: LonLat[],
  fromLabel: string | undefined,
  toLabel: string | undefined,
  at: LonLat
): string | undefined {
  if (path.length === 0) return toLabel ?? fromLabel;
  const start = path[0];
  const end = path[path.length - 1];
  const d0 = (at[0] - start[0]) ** 2 + (at[1] - start[1]) ** 2;
  const d1 = (at[0] - end[0]) ** 2 + (at[1] - end[1]) ** 2;
  return d0 <= d1 ? (fromLabel ?? toLabel) : (toLabel ?? fromLabel);
}

/** New array so deck.gl cannot keep a mutated path reference as "unchanged". */
export function cloneLonLatPath(path: LonLat[]): LonLat[] {
  return path.map((point) => [point[0], point[1]]);
}

export function cloneLonLat(point: LonLat): LonLat {
  return [point[0], point[1]];
}

export function liveHoverKey(
  hover:
    | { kind: 'node'; name: string }
    | { kind: 'probable'; reason: string; label?: string }
    | { kind: 'exact'; label?: string }
    | { kind: 'local' }
    | null
): string {
  if (!hover) return '';
  if (hover.kind === 'node') return `node:${hover.name}`;
  if (hover.kind === 'probable') return `probable:${hover.reason}:${hover.label ?? ''}`;
  if (hover.kind === 'exact') return `exact:${hover.label ?? ''}`;
  return 'local';
}

export function trailAlpha(headT: number, trailStartT: number, sampleT: number): number {
  if (headT <= trailStartT) return 1;
  return clamp01((sampleT - trailStartT) / (headT - trailStartT));
}

function hasRealGps(node: DirectoryMapNode): boolean {
  return (
    Number.isFinite(node.lat) && Number.isFinite(node.lon) && !(node.lat === 0 && node.lon === 0)
  );
}

/** Geometry pins: any role with real GPS, observers included. Used for origin/hops. */
export function geometryDirectoryNodes(nodes: DirectoryMapNode[]): DirectoryMapNode[] {
  return nodes.filter(hasRealGps);
}

/** City-plan icons: placeable mesh nodes. Observer GPS is usable but not painted. */
export function mappableDirectoryNodes(nodes: DirectoryMapNode[]): DirectoryMapNode[] {
  return geometryDirectoryNodes(nodes).filter((node) => node.role !== 'observer');
}

export function readLiveCamera(
  storage: Pick<Storage, 'getItem'> | null = defaultStorage()
): LiveCamera | null {
  if (!storage) return null;
  try {
    const raw = storage.getItem(LIVE_CAMERA_STORAGE_KEY);
    if (!raw) return null;
    const parsed = JSON.parse(raw) as Partial<LiveCamera>;
    if (
      typeof parsed.lat === 'number' &&
      Number.isFinite(parsed.lat) &&
      typeof parsed.lon === 'number' &&
      Number.isFinite(parsed.lon) &&
      typeof parsed.zoom === 'number' &&
      Number.isFinite(parsed.zoom)
    ) {
      return { lat: parsed.lat, lon: parsed.lon, zoom: parsed.zoom };
    }
  } catch {
    /* quota / parse */
  }
  return null;
}

export function writeLiveCamera(
  camera: LiveCamera,
  storage: Pick<Storage, 'setItem'> | null = defaultStorage()
): void {
  if (!storage) return;
  try {
    storage.setItem(LIVE_CAMERA_STORAGE_KEY, JSON.stringify(camera));
  } catch {
    /* quota */
  }
}

/**
 * Whether to frame the nodes rather than keep the camera as it is.
 *
 * Arriving means wanting to see what is being heard, so a camera left over from a
 * previous visit does not stand: it could be anywhere, with nothing on screen and
 * no hint that anything is missing. Only the reader moving the map in this session
 * holds the view.
 */
export function shouldAutoFitCamera(userMoved: boolean, nodeCount: number): boolean {
  return !userMoved && nodeCount > 0;
}

export function selectCatchup(newIds: string[], max: number = MAX_LIVE_CATCHUP): Set<string> {
  if (newIds.length <= max) return new Set(newIds);
  return new Set(newIds.slice(newIds.length - max));
}

export function twinOpacity(
  source: LiveSource,
  snr: number | null,
  hash8HasLocalTwin: boolean
): number {
  return liveOpacity(source, snr, hash8HasLocalTwin);
}

function contactRole(type: number): DirectoryNodeRole {
  if (type === 2) return 'repeater';
  if (type === 3) return 'room';
  if (type === 4) return 'sensor';
  if (type === 1) return 'companion';
  return 'unknown';
}

function isBlockedContact(
  contact: Contact,
  blockedKeys: readonly string[] | undefined,
  blockedNames: readonly string[] | undefined
): boolean {
  const key = contact.public_key.toLowerCase();
  if (blockedKeys?.length && blockedKeys.some((item) => item.toLowerCase() === key)) {
    return true;
  }
  return !!(blockedNames?.length && contact.name != null && blockedNames.includes(contact.name));
}

export function localContactsToMapNodes(
  contacts: readonly Contact[],
  blockedKeys?: readonly string[],
  blockedNames?: readonly string[]
): DirectoryMapNode[] {
  const nodes: DirectoryMapNode[] = [];
  for (const contact of contacts) {
    if (!isValidLocation(contact.lat, contact.lon)) continue;
    if (isBlockedContact(contact, blockedKeys, blockedNames)) continue;
    if (contact.lat == null || contact.lon == null) continue;
    nodes.push({
      public_key: contact.public_key.toLowerCase(),
      name: contact.name ?? contact.public_key.slice(0, 12),
      role: contactRole(contact.type),
      lat: contact.lat,
      lon: contact.lon,
      source: 'local',
      last_seen: contact.last_seen,
    });
  }
  return nodes;
}

export function mergeLocalOverDirectory(
  directory: readonly DirectoryMapNode[],
  local: readonly DirectoryMapNode[],
  tombstones: ReadonlySet<string>
): DirectoryMapNode[] {
  const hidden = new Set([...tombstones].map((key) => key.toLowerCase()));
  const byKey = new Map<string, DirectoryMapNode>();
  for (const node of directory) {
    const key = node.public_key.toLowerCase();
    if (hidden.has(key)) continue;
    byKey.set(key, { ...node, public_key: key });
  }
  for (const node of local) {
    const key = node.public_key.toLowerCase();
    hidden.delete(key);
    byKey.set(key, { ...node, public_key: key });
  }
  return geometryDirectoryNodes([...byKey.values()]);
}

export function hopVertexT(pointCount: number, index: number): number {
  if (pointCount <= 1) return 0;
  return index / (pointCount - 1);
}

export function findPinnedHop(
  token: string | undefined,
  pubkey: string | undefined,
  nodes: readonly DirectoryMapNode[]
): DirectoryMapNode | null {
  if (pubkey) {
    const needle = pubkey.toLowerCase();
    const exact = nodes.find((node) => node.public_key.toLowerCase() === needle);
    if (exact) return exact;
  }
  if (!token || token.trim().length < 4) return null;
  const prefix = token.trim().toLowerCase();
  const matches = nodes.filter((node) => node.public_key.toLowerCase().startsWith(prefix));
  return matches.length === 1 ? matches[0] : null;
}

function clamp01(value: number): number {
  if (value <= 0) return 0;
  if (value >= 1) return 1;
  return value;
}

function defaultStorage(): Storage | null {
  try {
    return typeof localStorage === 'undefined' ? null : localStorage;
  } catch {
    return null;
  }
}
