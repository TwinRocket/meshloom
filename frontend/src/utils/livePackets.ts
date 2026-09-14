import type {
  CommunityPacket,
  CommunityPacketHop,
  CommunityPacketType,
  Contact,
  RadioConfig,
  RawPacket,
} from '../types';
import iataCentroids from '../data/iataCentroids.json';
import { isValidLocation, MIN_NAMED_HOP_HEX_CHARS } from './pathUtils';
import { hashString } from './contactAvatar';
import { getPacketLabel, parsePacket } from './visualizerUtils';

export const LIVE_SEGMENT_MS = 800;
export const LIVE_STAGGER_MS = 150;
export const LIVE_POLYLINE_MIN_MS = 6000;
export const LIVE_POLYLINE_MAX_MS = 10000;
export const LIVE_DIM_AFTER_MS = 5 * 60 * 1000;
export const MAX_LIVE_PARTICLES = 150;
export const LIVE_HOP_JITTER_DEG = 0.012;

/** OurAirports IATA centroids (public domain). Ear position, never contributor GPS. */
export const LIVE_IATA_CENTROIDS = iataCentroids as Record<string, [number, number]>;

export const LIVE_TYPE_COLORS: Record<CommunityPacketType, string> = {
  advert: '#f59e0b',
  text: '#06b6d4',
  ack: '#22c55e',
  trace: '#f97316',
  other: '#94a3b8',
};

export type LiveSource = 'local' | 'community';

export interface LiveWaypoint {
  lat: number;
  lon: number;
  token: string;
  /** `fade` approaches the ear after an unresolved hop — never a drawn segment. */
  kind: 'hop' | 'ear' | 'fade';
}

export interface LiveObservation {
  id: string;
  hash8: string;
  source: LiveSource;
  type: CommunityPacketType;
  snr: number | null;
  iata: string | null;
  t: number;
  earId: string;
  ear: { lat: number; lon: number } | null;
  waypoints: LiveWaypoint[];
}

export function isCommunityPacketType(value: unknown): value is CommunityPacketType {
  return (
    value === 'advert' ||
    value === 'text' ||
    value === 'ack' ||
    value === 'trace' ||
    value === 'other'
  );
}

export function liveTypeColor(type: CommunityPacketType): string {
  return LIVE_TYPE_COLORS[type];
}

/** Map typical LoRa SNR onto 0.35–1 for size / opacity. */
export function snrWeight(snr: number | null | undefined): number {
  if (snr == null || !Number.isFinite(snr)) return 0.65;
  return Math.min(1, Math.max(0.35, (snr + 20) / 30));
}

/**
 * Local rain is more opaque than community rain. When the same hash8 is
 * present on both feeds, community is dimmed further so the local drop wins.
 */
export function liveOpacity(
  source: LiveSource,
  snr: number | null | undefined,
  hash8HasLocalTwin: boolean
): number {
  const weight = snrWeight(snr);
  if (source === 'local') {
    return Math.min(1, (hash8HasLocalTwin ? 0.98 : 0.9) * weight);
  }
  return (hash8HasLocalTwin ? 0.32 : 0.5) * weight;
}

export function hopTokenHexLength(token: string): number {
  return token.trim().replace(/\s+/g, '').length;
}

/** 1-byte prefixes are never first-matched to a contact. */
export function isOneByteHopToken(token: string): boolean {
  return hopTokenHexLength(token) < MIN_NAMED_HOP_HEX_CHARS;
}

export function uniqueGpsContact(
  token: string,
  prefixIndex: Map<string, Contact[]>
): Contact | null {
  if (isOneByteHopToken(token)) return null;
  const matches = prefixIndex.get(token.trim().toLowerCase());
  if (!matches || matches.length !== 1) return null;
  const contact = matches[0];
  return isValidLocation(contact.lat, contact.lon) ? contact : null;
}

export function buildPrefixIndex(contacts: Contact[]): Map<string, Contact[]> {
  const index = new Map<string, Contact[]>();
  for (const contact of contacts) {
    const key = contact.public_key.toLowerCase();
    for (const len of [4, 6, 8, 12]) {
      if (key.length < len) continue;
      const prefix = key.slice(0, len);
      const bucket = index.get(prefix);
      if (bucket) bucket.push(contact);
      else index.set(prefix, [contact]);
    }
  }
  return index;
}

export function iataCentroid(iata: string | null | undefined): [number, number] | null {
  if (!iata) return null;
  return LIVE_IATA_CENTROIDS[iata.trim().toUpperCase()] ?? null;
}

export function packetTypeFromRaw(payloadType: number): CommunityPacketType {
  switch (getPacketLabel(payloadType)) {
    case 'AD':
      return 'advert';
    case 'GT':
    case 'DM':
      return 'text';
    case 'ACK':
      return 'ack';
    case 'TR':
      return 'trace';
    default:
      return 'other';
  }
}

export function hash8FromRaw(packet: RawPacket): string {
  const parsed = parsePacket(packet.data);
  const raw = parsed?.messageHash || hashString(packet.data).toString(16).padStart(8, '0');
  return raw.slice(0, 8).toLowerCase();
}

function hopHasCoords(hop: CommunityPacketHop): hop is CommunityPacketHop & {
  lat: number;
  lon: number;
} {
  return !hop.unresolved && isValidLocation(hop.lat ?? null, hop.lon ?? null);
}

/**
 * Build rain waypoints. Unresolved hops never invent a segment; the drop
 * fades toward the ear from the last resolved hop (or only pulses the ear).
 */
export function waypointsFromCommunity(
  packet: CommunityPacket,
  ear: { lat: number; lon: number } | null
): LiveWaypoint[] {
  const waypoints: LiveWaypoint[] = [];
  let pendingUnresolved = false;

  for (const hop of packet.hops) {
    if (hopHasCoords(hop)) {
      waypoints.push({ lat: hop.lat, lon: hop.lon, token: hop.token, kind: 'hop' });
      pendingUnresolved = false;
    } else {
      pendingUnresolved = true;
    }
  }

  if (!ear) return waypoints;

  if (pendingUnresolved) {
    waypoints.push({ lat: ear.lat, lon: ear.lon, token: packet.ear_id, kind: 'fade' });
  } else if (waypoints.length > 0) {
    const last = waypoints[waypoints.length - 1];
    if (last.lat !== ear.lat || last.lon !== ear.lon) {
      waypoints.push({ lat: ear.lat, lon: ear.lon, token: packet.ear_id, kind: 'ear' });
    }
  }

  return waypoints;
}

export function waypointsFromRaw(
  packet: RawPacket,
  prefixIndex: Map<string, Contact[]>,
  ear: { lat: number; lon: number } | null,
  earId: string
): LiveWaypoint[] {
  const parsed = parsePacket(packet.data);
  if (!parsed) return [];

  const waypoints: LiveWaypoint[] = [];
  let pendingUnresolved = false;

  for (const token of parsed.pathBytes) {
    const contact = uniqueGpsContact(token, prefixIndex);
    if (contact) {
      waypoints.push({
        lat: contact.lat!,
        lon: contact.lon!,
        token,
        kind: 'hop',
      });
      pendingUnresolved = false;
    } else {
      pendingUnresolved = true;
    }
  }

  if (!ear) return waypoints;

  if (pendingUnresolved) {
    waypoints.push({ lat: ear.lat, lon: ear.lon, token: earId, kind: 'fade' });
  } else if (waypoints.length > 0) {
    const last = waypoints[waypoints.length - 1];
    if (last.lat !== ear.lat || last.lon !== ear.lon) {
      waypoints.push({ lat: ear.lat, lon: ear.lon, token: earId, kind: 'ear' });
    }
  }

  return waypoints;
}

export function observationFromCommunity(packet: CommunityPacket): LiveObservation | null {
  if (packet.v !== 1 || !isCommunityPacketType(packet.type)) return null;
  const centroid = iataCentroid(packet.iata);
  const ear = centroid ? { lat: centroid[0], lon: centroid[1] } : null;
  return {
    id: packet.event_id,
    hash8: packet.hash8.toLowerCase(),
    source: 'community',
    type: packet.type,
    snr: packet.snr ?? null,
    iata: packet.iata || null,
    t: packet.t,
    earId: packet.ear_id,
    ear,
    waypoints: waypointsFromCommunity(packet, ear),
  };
}

export function observationFromRaw(
  packet: RawPacket,
  prefixIndex: Map<string, Contact[]>,
  config: RadioConfig | null
): LiveObservation | null {
  const parsed = parsePacket(packet.data);
  if (!parsed) return null;
  const ear =
    config && isValidLocation(config.lat, config.lon) ? { lat: config.lat, lon: config.lon } : null;
  const earId = (config?.public_key || 'local-ear').slice(0, 16);
  const t = packet.timestamp > 1e12 ? packet.timestamp : packet.timestamp * 1000;
  return {
    id: `local:${packet.observation_id ?? packet.id}`,
    hash8: hash8FromRaw(packet),
    source: 'local',
    type: packetTypeFromRaw(parsed.payloadType),
    snr: packet.snr,
    iata: null,
    t,
    earId,
    ear,
    waypoints: waypointsFromRaw(packet, prefixIndex, ear, earId),
  };
}

/** Polyline uses resolved hops + ear only — never a fade approach. */
export function polylinePositions(waypoints: LiveWaypoint[]): [number, number][] {
  const points: [number, number][] = [];
  for (const point of waypoints) {
    if (point.kind === 'fade') break;
    points.push([point.lat, point.lon]);
  }
  return points;
}

export function applyHopJitter(waypoints: LiveWaypoint[], seed: string): LiveWaypoint[] {
  return waypoints.map((point, index) => {
    if (point.kind !== 'hop') return point;
    const jitter = seededJitter(seed, index);
    return {
      ...point,
      lat: point.lat + jitter[0],
      lon: point.lon + jitter[1],
    };
  });
}

function seededJitter(seed: string, index: number): [number, number] {
  let h = 2166136261;
  const text = `${seed}:${index}`;
  for (let i = 0; i < text.length; i++) {
    h ^= text.charCodeAt(i);
    h = Math.imul(h, 16777619);
  }
  const u = ((h >>> 0) % 10000) / 10000 - 0.5;
  const v = ((h >>> 8) % 10000) / 10000 - 0.5;
  return [u * LIVE_HOP_JITTER_DEG * 2, v * LIVE_HOP_JITTER_DEG * 2];
}

export function polylineLifetimeMs(seed: string): number {
  let h = 0;
  for (let i = 0; i < seed.length; i++) h = (h * 31 + seed.charCodeAt(i)) >>> 0;
  const span = LIVE_POLYLINE_MAX_MS - LIVE_POLYLINE_MIN_MS;
  return LIVE_POLYLINE_MIN_MS + (h % (span + 1));
}

export function isStaleLiveTime(t: number, now: number = Date.now()): boolean {
  return now - t >= LIVE_DIM_AFTER_MS;
}
