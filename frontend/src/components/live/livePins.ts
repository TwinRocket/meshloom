import type { DirectoryMapNode, DirectoryNodeRole, DirectoryNodeSource } from '../../types';
import type { LiveObservation } from '../../utils/livePackets';
import { normalizeDirectoryRole } from './liveRender';

/** Below this zoom, nearby pins collapse. At or above it, role shapes stay readable. */
export const LIVE_CLUSTER_MAX_ZOOM = 7;
export const LIVE_CLUSTER_RADIUS_PX = 44;
export const LIVE_PIN_LIT_MS = 1400;
export const LIVE_DIRECTORY_REFRESH_MS = 60_000;

export interface LiveStreamPin {
  public_key: string;
  name: string;
  role: DirectoryNodeRole;
  lat: number;
  lon: number;
  source: DirectoryNodeSource;
  last_seen: number;
}

export type LiveClusterItem =
  | { kind: 'node'; node: DirectoryMapNode }
  | {
      kind: 'cluster';
      id: string;
      lon: number;
      lat: number;
      count: number;
      roles: DirectoryNodeRole[];
    };

export function observationUnixSeconds(t: number): number {
  if (!Number.isFinite(t) || t <= 0) return 0;
  return t > 1e12 ? Math.floor(t / 1000) : Math.floor(t);
}

export function isMappableCoord(lat: number, lon: number): boolean {
  return Number.isFinite(lat) && Number.isFinite(lon) && !(lat === 0 && lon === 0);
}

export function looksLikePubkey(value: string | undefined | null): value is string {
  return typeof value === 'string' && /^[0-9a-f]{16,64}$/i.test(value.trim());
}

function hasExactIdentity(
  label: string | undefined,
  pubkey: string | undefined,
  token: string
): boolean {
  if (typeof label === 'string' && label.trim()) return true;
  if (looksLikePubkey(pubkey)) return true;
  return /^[0-9a-f]{4,}$/i.test(token.trim());
}

export function pinsFromObservation(obs: LiveObservation): LiveStreamPin[] {
  const lastSeen = observationUnixSeconds(obs.t);
  const source: DirectoryNodeSource = obs.source === 'local' ? 'local' : 'community-db';
  const pins: LiveStreamPin[] = [];
  const used = new Set<string>();

  for (const hop of obs.waypoints) {
    if (hop.kind !== 'hop' || hop.confidence !== 'exact') continue;
    if (!isMappableCoord(hop.lat, hop.lon)) continue;
    if (!hasExactIdentity(hop.label, hop.pubkey, hop.token)) continue;
    const publicKey = (looksLikePubkey(hop.pubkey) ? hop.pubkey : hop.token).trim().toLowerCase();
    if (!publicKey || used.has(publicKey)) continue;
    used.add(publicKey);
    const name = hop.label?.trim() || publicKey.slice(0, 12);
    pins.push({
      public_key: publicKey,
      name,
      role: 'unknown',
      lat: hop.lat,
      lon: hop.lon,
      source,
      last_seen: lastSeen,
    });
  }

  if (obs.ear?.source === 'advert' && isMappableCoord(obs.ear.lat, obs.ear.lon)) {
    const earKey = looksLikePubkey(obs.earId) ? obs.earId.trim().toLowerCase() : `ear:${obs.earId}`;
    if (!used.has(earKey)) {
      pins.push({
        public_key: earKey,
        name: looksLikePubkey(obs.earId) ? earKey.slice(0, 12) : 'Advert',
        role: 'unknown',
        lat: obs.ear.lat,
        lon: obs.ear.lon,
        source,
        last_seen: lastSeen,
      });
    }
  }

  return pins;
}

export function matchDirectoryNode(
  nodes: readonly DirectoryMapNode[],
  pin: Pick<LiveStreamPin, 'public_key' | 'name'>
): DirectoryMapNode | null {
  const key = pin.public_key.trim().toLowerCase();
  if (key) {
    const exact = nodes.find((node) => node.public_key.toLowerCase() === key);
    if (exact) return exact;
    if (key.length >= 4 && key.length < 64 && !key.startsWith('ear:')) {
      const prefixHits = nodes.filter((node) => node.public_key.toLowerCase().startsWith(key));
      if (prefixHits.length === 1) return prefixHits[0];
    }
  }
  const name = pin.name.trim().toLowerCase();
  if (!name) return null;
  const nameHits = nodes.filter((node) => node.name.trim().toLowerCase() === name);
  return nameHits.length === 1 ? nameHits[0] : null;
}

export function upsertLivePin(
  directory: readonly DirectoryMapNode[],
  stream: Map<string, DirectoryMapNode>,
  pin: LiveStreamPin
): DirectoryMapNode {
  const fromDirectory = matchDirectoryNode(directory, pin);
  const fromStream = matchDirectoryNode([...stream.values()], pin);
  const existing = fromDirectory ?? fromStream;
  const id = (existing?.public_key ?? pin.public_key).toLowerCase();
  const lastSeen = Math.max(existing?.last_seen ?? 0, pin.last_seen);
  const next: DirectoryMapNode = fromDirectory
    ? {
        ...fromDirectory,
        last_seen: lastSeen || fromDirectory.last_seen,
      }
    : {
        public_key: id,
        name: existing?.name || pin.name,
        role: existing && existing.role !== 'unknown' ? existing.role : pin.role,
        lat: pin.lat,
        lon: pin.lon,
        source: existing?.source ?? pin.source,
        last_seen: lastSeen || pin.last_seen,
      };
  stream.set(id, next);
  return next;
}

export function mergeLiveNodes(
  directory: readonly DirectoryMapNode[],
  stream: Iterable<DirectoryMapNode>
): DirectoryMapNode[] {
  const byKey = new Map<string, DirectoryMapNode>();
  for (const node of directory) {
    byKey.set(node.public_key.toLowerCase(), { ...node });
  }
  for (const node of stream) {
    const key = node.public_key.toLowerCase();
    const existing = byKey.get(key);
    if (!existing) {
      byKey.set(key, node);
      continue;
    }
    const lastSeen = Math.max(existing.last_seen ?? 0, node.last_seen ?? 0);
    byKey.set(key, {
      ...existing,
      last_seen: lastSeen || existing.last_seen,
    });
  }
  return [...byKey.values()];
}

export function applyObservationPins(
  directory: readonly DirectoryMapNode[],
  stream: Map<string, DirectoryMapNode>,
  obs: LiveObservation
): DirectoryMapNode[] {
  return pinsFromObservation(obs).map((pin) => upsertLivePin(directory, stream, pin));
}

export function nodeDrawOpacity(
  lastSeen: number | null | undefined,
  nowMs: number,
  lit: boolean
): number {
  if (lit) return 1;
  if (lastSeen == null || lastSeen <= 0) return 0.38;
  const ageMs = nowMs - (lastSeen > 1e12 ? lastSeen : lastSeen * 1000);
  if (ageMs <= 5 * 60 * 1000) return 0.9;
  if (ageMs <= 60 * 60 * 1000) return 0.58;
  return 0.34;
}

export function clusterRadiusPx(count: number): number {
  return 8 + Math.log2(Math.max(2, count)) * 2.4;
}

export function projectWebMercator(lon: number, lat: number, zoom: number): [number, number] {
  const scale = 256 * 2 ** zoom;
  const x = ((lon + 180) / 360) * scale;
  const clamped = Math.min(85.05112878, Math.max(-85.05112878, lat));
  const latRad = (clamped * Math.PI) / 180;
  const y = ((1 - Math.log(Math.tan(latRad) + 1 / Math.cos(latRad)) / Math.PI) / 2) * scale;
  return [x, y];
}

export function clusterLiveNodes(
  nodes: readonly DirectoryMapNode[],
  zoom: number,
  radiusPx: number = LIVE_CLUSTER_RADIUS_PX,
  maxZoom: number = LIVE_CLUSTER_MAX_ZOOM
): LiveClusterItem[] {
  if (zoom >= maxZoom || nodes.length <= 1) {
    return nodes.map((node) => ({ kind: 'node' as const, node }));
  }
  const cells = new Map<string, DirectoryMapNode[]>();
  for (const node of nodes) {
    const [x, y] = projectWebMercator(node.lon, node.lat, zoom);
    const key = `${Math.floor(x / radiusPx)}:${Math.floor(y / radiusPx)}`;
    const bucket = cells.get(key);
    if (bucket) bucket.push(node);
    else cells.set(key, [node]);
  }
  const items: LiveClusterItem[] = [];
  for (const [key, bucket] of cells) {
    if (bucket.length === 1) {
      items.push({ kind: 'node', node: bucket[0] });
      continue;
    }
    let lon = 0;
    let lat = 0;
    const roles = new Set<DirectoryNodeRole>();
    for (const node of bucket) {
      lon += node.lon;
      lat += node.lat;
      roles.add(normalizeDirectoryRole(node.role));
    }
    items.push({
      kind: 'cluster',
      id: `cluster:${key}`,
      lon: lon / bucket.length,
      lat: lat / bucket.length,
      count: bucket.length,
      roles: [...roles],
    });
  }
  return items;
}
