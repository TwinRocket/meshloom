import type { Contact, ObserverReachEntry } from '../types';
import { stripRegionScopePrefix } from './regionScope';
import { calculateDistance, isValidLocation } from './pathUtils';
import { observerReachPollIntervalMs } from './observerReach';

/**
 * The radio test, minus React and minus Leaflet.
 *
 * A test flood is sent once and then listened to for ten minutes. Leaving the page
 * during those ten minutes must not throw the run away, and neither the list nor
 * the map may invent a position for a hop that never reported one — both rules are
 * enforced here rather than in each of the two views.
 */

/** Where the run being listened to is kept, so a tab reload keeps listening. */
export const MESH_TEST_RUN_KEY = 'meshloom-mesh-test-run';

export interface MeshTestRun {
  packetHash: string;
  /** Unix seconds, from the send response (server clock). */
  sentAt: number;
  /**
   * Browser-clock ms when the send was requested. The listen window is timed from
   * here, so a skewed server clock neither closes it early nor stretches it. Absent
   * on runs saved before this field existed: those fall back to `sentAt`.
   */
  startedAtMs?: number;
  floodScope: string;
  originLat: number | null;
  originLon: number | null;
}

export interface MeshTestHop {
  prefix: string;
  /** Position in the path, from 0, counting hops with no GPS as well. */
  hopIndex: number;
  name: string | null;
  lat: number | null;
  lon: number | null;
}

export interface MeshTestObserver {
  key: string;
  name: string;
  isMLC: boolean;
  /** Raw role from the directory. Null or empty means unknown. */
  role: string | null;
  hops: number | null;
  snr: number | null;
  rssi: number | null;
  lat: number | null;
  lon: number | null;
  /** From the send origin, computed in the browser: reach has no distance here. */
  distanceKm: number | null;
  path: MeshTestHop[];
}

export interface MeshTestPathPoint {
  lat: number;
  lon: number;
  /** The hop this point belongs to, or null for the origin and the observer. */
  hopIndex: number | null;
  label: string | null;
}

export type MeshTestListenState = 'listening' | 'stopped';

export function readMeshTestRun(): MeshTestRun | null {
  try {
    const raw = sessionStorage.getItem(MESH_TEST_RUN_KEY);
    if (!raw) return null;
    const parsed = JSON.parse(raw) as Partial<MeshTestRun>;
    if (typeof parsed.packetHash !== 'string' || parsed.packetHash === '') return null;
    if (typeof parsed.sentAt !== 'number' || !Number.isFinite(parsed.sentAt)) return null;
    return {
      packetHash: parsed.packetHash,
      sentAt: parsed.sentAt,
      ...(typeof parsed.startedAtMs === 'number' && Number.isFinite(parsed.startedAtMs)
        ? { startedAtMs: parsed.startedAtMs }
        : {}),
      floodScope: typeof parsed.floodScope === 'string' ? parsed.floodScope : '',
      originLat: typeof parsed.originLat === 'number' ? parsed.originLat : null,
      originLon: typeof parsed.originLon === 'number' ? parsed.originLon : null,
    };
  } catch {
    return null;
  }
}

export function saveMeshTestRun(run: MeshTestRun): void {
  try {
    sessionStorage.setItem(MESH_TEST_RUN_KEY, JSON.stringify(run));
  } catch {
    // Private mode or quota: the run still works, it just will not survive a reload.
  }
}

export function clearMeshTestRun(): void {
  try {
    sessionStorage.removeItem(MESH_TEST_RUN_KEY);
  } catch {
    // Nothing to recover from — the run was in memory anyway.
  }
}

export function meshTestOrigin(run: MeshTestRun | null): { lat: number; lon: number } | null {
  if (!run || !isValidLocation(run.originLat, run.originLon)) return null;
  return { lat: run.originLat!, lon: run.originLon! };
}

export function observerRowKey(observer: ObserverReachEntry, index: number): string {
  return `${observer.public_key ?? observer.name}-${index}`;
}

/** Age of the run on the browser clock, the one `nowMs` comes from. */
export function meshTestAgeMs(run: MeshTestRun, nowMs: number = Date.now()): number {
  return nowMs - (run.startedAtMs ?? run.sentAt * 1000);
}

/**
 * Still listening, or finished?
 *
 * The poll schedule is the definition: while there is an interval left there is
 * something to wait for. `sealed` can end it early, but never before ten minutes,
 * so a sealed flag is not what says the listening is over.
 */
export function meshTestListenState(ageMs: number, sealed: boolean): MeshTestListenState {
  if (sealed) return 'stopped';
  return observerReachPollIntervalMs(ageMs) === null ? 'stopped' : 'listening';
}

/** Hops then distance. Missing values sort last so the mesh's near edge reads first. */
export function sortMeshTestObservers(observers: MeshTestObserver[]): MeshTestObserver[] {
  return [...observers].sort((a, b) => {
    const hopsA = a.hops ?? Number.POSITIVE_INFINITY;
    const hopsB = b.hops ?? Number.POSITIVE_INFINITY;
    if (hopsA !== hopsB) return hopsA - hopsB;
    const distA = a.distanceKm ?? Number.POSITIVE_INFINITY;
    const distB = b.distanceKm ?? Number.POSITIVE_INFINITY;
    if (distA !== distB) return distA - distB;
    return a.name.localeCompare(b.name);
  });
}

export function meshTestDistanceKm(
  origin: { lat: number; lon: number } | null,
  lat: number | null | undefined,
  lon: number | null | undefined
): number | null {
  if (!origin || !isValidLocation(lat ?? null, lon ?? null)) return null;
  return calculateDistance(origin.lat, origin.lon, lat ?? null, lon ?? null);
}

/**
 * Distance from the send origin to an ear. A rounded position (about 1 km, an
 * ear that does not broadcast its own) places the ear but never makes a
 * distance, so it cannot set the farthest-ear record.
 */
export function meshTestObserverDistanceKm(
  origin: { lat: number; lon: number } | null,
  entry: Pick<ObserverReachEntry, 'position_precision'>,
  placed: PlacedObserverCoordinates
): number | null {
  if (entry.position_precision === 'rounded' || placed.byName) return null;
  return meshTestDistanceKm(origin, placed.lat, placed.lon);
}

const FULL_PUBLIC_KEY_RE = /^[0-9a-f]{64}$/i;

/**
 * Where an ear sits on the map. `byName`: placed from a local advert matched
 * by name only, which may be another node, so it never makes a distance.
 */
export interface PlacedObserverCoordinates {
  lat: number | null;
  lon: number | null;
  byName?: boolean;
}

/**
 * Reach GPS first, then the local advert for the same public key, then (map
 * only, `byName`) the one local advert with the same name, when the ear has
 * no full public key of its own.
 */
export function placedObserverCoordinates(
  entry: Pick<ObserverReachEntry, 'lat' | 'lon' | 'public_key'> & { name?: string | null },
  contacts: Array<Pick<Contact, 'public_key' | 'lat' | 'lon'> & { name?: string | null }>
): PlacedObserverCoordinates {
  if (isValidLocation(entry.lat ?? null, entry.lon ?? null)) {
    return { lat: entry.lat ?? null, lon: entry.lon ?? null };
  }
  const local = contactCoordinates(entry.public_key, contacts);
  if (local) return { lat: local.lat, lon: local.lon };
  // An ear with its own full key is that node: a same-name advert with
  // another key is another node.
  if (FULL_PUBLIC_KEY_RE.test(entry.public_key ?? '')) return { lat: null, lon: null };
  const named = contactCoordinatesByName(entry.name, contacts);
  if (!named) return { lat: null, lon: null };
  return { lat: named.lat, lon: named.lon, byName: true };
}

/** Local advert GPS when the reach row itself has none. */
export function contactCoordinates(
  publicKey: string | null | undefined,
  contacts: Pick<Contact, 'public_key' | 'lat' | 'lon'>[]
): { lat: number; lon: number } | null {
  if (!publicKey) return null;
  const key = publicKey.toLowerCase();
  const contact = contacts.find((item) => item.public_key.toLowerCase() === key);
  if (!contact || !isValidLocation(contact.lat, contact.lon)) return null;
  return { lat: contact.lat!, lon: contact.lon! };
}

/** One local advert with this exact name and a real position. */
export function contactCoordinatesByName(
  name: string | null | undefined,
  contacts: Array<Pick<Contact, 'lat' | 'lon'> & { name?: string | null }>
): { lat: number; lon: number } | null {
  const label = name?.trim();
  if (!label) return null;
  const matches = contacts.filter(
    (item) => item.name === label && isValidLocation(item.lat, item.lon)
  );
  if (matches.length !== 1) return null;
  return { lat: matches[0].lat!, lon: matches[0].lon! };
}

/**
 * The points a path's line may actually join.
 *
 * Hops that reported no position are left out entirely rather than guessed at, so
 * the line runs from the last known point straight to the next one. That segment is
 * shorter than the route it stands for, which is the honest way to draw a route
 * with a hole in it — a fabricated midpoint would not be.
 */
export function meshTestPathPoints(
  observer: MeshTestObserver,
  origin: { lat: number; lon: number } | null
): MeshTestPathPoint[] {
  const points: MeshTestPathPoint[] = [];
  if (origin) {
    points.push({ lat: origin.lat, lon: origin.lon, hopIndex: null, label: null });
  }
  for (const hop of observer.path) {
    if (!isValidLocation(hop.lat, hop.lon)) continue;
    points.push({
      lat: hop.lat!,
      lon: hop.lon!,
      hopIndex: hop.hopIndex,
      label: hop.name ?? hop.prefix,
    });
  }
  if (isValidLocation(observer.lat, observer.lon)) {
    points.push({
      lat: observer.lat!,
      lon: observer.lon!,
      hopIndex: null,
      label: observer.name,
    });
  }
  return points;
}

export interface MeshTestSummary {
  observerCount: number;
  positionedCount: number;
  maxHops: number | null;
  maxDistanceKm: number | null;
}

export function meshTestSummary(observers: MeshTestObserver[]): MeshTestSummary {
  let positionedCount = 0;
  let maxHops: number | null = null;
  let maxDistanceKm: number | null = null;
  for (const observer of observers) {
    if (isValidLocation(observer.lat, observer.lon)) positionedCount += 1;
    if (observer.hops != null && (maxHops == null || observer.hops > maxHops)) {
      maxHops = observer.hops;
    }
    if (
      observer.distanceKm != null &&
      (maxDistanceKm == null || observer.distanceKm > maxDistanceKm)
    ) {
      maxDistanceKm = observer.distanceKm;
    }
  }
  return { observerCount: observers.length, positionedCount, maxHops, maxDistanceKm };
}

/** The region to preselect: the node's own scope, but only if it is actually offered. */
export function preselectedFloodScope(
  floodScope: string | undefined,
  knownRegions: string[]
): string {
  const current = stripRegionScopePrefix(floodScope).trim().toLowerCase();
  if (current) {
    const match = knownRegions.find(
      (region) => stripRegionScopePrefix(region).trim().toLowerCase() === current
    );
    if (match) return match;
  }
  return knownRegions[0] ?? '';
}
