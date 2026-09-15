import type {
  CommunityHopConfidence,
  CommunityPacket,
  CommunityPacketEar,
  CommunityPacketHop,
  CommunityPacketType,
  Contact,
  RadioConfig,
  RawPacket,
} from '../types';
import { isValidLocation, MIN_NAMED_HOP_HEX_CHARS } from './pathUtils';
import { hashString } from './contactAvatar';
import { getPacketLabel, parsePacket } from './visualizerUtils';

export const LIVE_STAGGER_MS = 150;
export const LIVE_DIM_AFTER_MS = 5 * 60 * 1000;
export const LIVE_COMMUNITY_SCHEMA = 2;
export const LIVE_PACKET_TYPES: readonly CommunityPacketType[] = [
  'advert',
  'text',
  'ack',
  'trace',
  'other',
];

export type SavedMapCamera = { lat: number; lon: number; zoom: number };

export function readSavedMapCamera(storageKey: string): SavedMapCamera | null {
  try {
    const raw = localStorage.getItem(storageKey);
    if (!raw) return null;
    const parsed = JSON.parse(raw) as Partial<SavedMapCamera>;
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
    /* ignore quota / parse */
  }
  return null;
}

export function writeSavedMapCamera(storageKey: string, camera: SavedMapCamera): void {
  try {
    localStorage.setItem(storageKey, JSON.stringify(camera));
  } catch {
    /* ignore quota */
  }
}

/** Packet-type palette. Must stay disjoint from NODE_ROLE_STYLE (no shared #f59e0b). */
export const LIVE_TYPE_COLORS: Record<CommunityPacketType, string> = {
  advert: '#fbbf24',
  text: '#22d3ee',
  ack: '#4ade80',
  trace: '#c084fc',
  other: '#78716c',
};

export type LiveSource = 'local' | 'community';
export type LiveHopConfidence = 'exact' | 'probable' | 'unresolved';

export interface LiveWaypoint {
  lat: number;
  lon: number;
  token: string;
  kind: 'hop' | 'ear';
  confidence: LiveHopConfidence;
  reason?: string;
  label?: string;
}

export interface LiveEar {
  lat: number;
  lon: number;
  source: 'advert' | 'iata' | 'local';
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
  ear: LiveEar | null;
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

export function isLiveHopConfidence(value: unknown): value is CommunityHopConfidence {
  return value === 'exact' || value === 'probable' || value === 'unresolved';
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

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value);
}

function finiteCoord(value: unknown): number | null {
  if (typeof value !== 'number' || !Number.isFinite(value)) return null;
  return value;
}

function readEar(value: unknown): CommunityPacketEar | null | undefined {
  if (value == null) return null;
  if (!isRecord(value)) return undefined;
  const lat = finiteCoord(value.lat);
  const lon = finiteCoord(value.lon);
  const source = value.source;
  if (lat == null || lon == null || !isValidLocation(lat, lon)) return undefined;
  if (source !== 'advert' && source !== 'iata') return undefined;
  return { lat, lon, source };
}

function readHop(value: unknown): CommunityPacketHop | null {
  if (!isRecord(value)) return null;
  const token = value.token;
  if (typeof token !== 'string' || !token.trim()) return null;
  if (!isLiveHopConfidence(value.confidence)) return null;
  const hop: CommunityPacketHop = { token: token.trim(), confidence: value.confidence };
  if (typeof value.reason === 'string' && value.reason) hop.reason = value.reason;
  if (typeof value.pubkey === 'string' && value.pubkey) hop.pubkey = value.pubkey;
  if (typeof value.name === 'string' && value.name) hop.name = value.name;
  if (hop.confidence === 'unresolved') {
    return hop;
  }
  const lat = finiteCoord(value.lat);
  const lon = finiteCoord(value.lon);
  if (lat == null || lon == null || !isValidLocation(lat, lon)) return null;
  hop.lat = lat;
  hop.lon = lon;
  return hop;
}

const HASH8_RE = /^[0-9a-f]{8}$/;

export function asCommunityPacket(value: unknown): CommunityPacket | null {
  if (!isRecord(value)) return null;
  if (value.v !== LIVE_COMMUNITY_SCHEMA) return null;
  if (typeof value.event_id !== 'string' || !value.event_id) return null;
  if (typeof value.hash8 !== 'string') return null;
  const hash8 = value.hash8.trim().toLowerCase().slice(0, 8);
  if (!HASH8_RE.test(hash8)) return null;
  if (!isCommunityPacketType(value.type)) return null;
  if (!Array.isArray(value.hops)) return null;
  const hops: CommunityPacketHop[] = [];
  for (const item of value.hops) {
    const hop = readHop(item);
    if (!hop) return null;
    hops.push(hop);
  }
  let path: string[];
  if (value.path === undefined) {
    path = hops.map((hop) => hop.token);
  } else if (
    Array.isArray(value.path) &&
    value.path.length === hops.length &&
    value.path.every((item) => typeof item === 'string')
  ) {
    path = value.path;
  } else {
    return null;
  }
  if (value.hop_count !== undefined && value.hop_count !== hops.length) return null;
  if (typeof value.ear_id !== 'string' || !value.ear_id) return null;
  if (typeof value.t !== 'number' || !Number.isFinite(value.t)) return null;
  const ear = readEar(value.ear);
  if (ear === undefined) return null;
  if (value.iata !== undefined && typeof value.iata !== 'string') return null;
  const snr =
    value.snr === undefined || value.snr === null
      ? undefined
      : typeof value.snr === 'number' && Number.isFinite(value.snr)
        ? value.snr
        : undefined;
  const packet: CommunityPacket = {
    v: LIVE_COMMUNITY_SCHEMA,
    event_id: value.event_id,
    hash8,
    type: value.type,
    path,
    hop_count: hops.length,
    hops,
    ear,
    iata: value.iata ?? '',
    t: value.t,
    ear_id: value.ear_id,
  };
  if (snr !== undefined) packet.snr = snr;
  return packet;
}

function hopHasCoords(
  hop: CommunityPacketHop
): hop is CommunityPacketHop & { lat: number; lon: number } {
  return (
    (hop.confidence === 'exact' || hop.confidence === 'probable') &&
    isValidLocation(hop.lat ?? null, hop.lon ?? null)
  );
}

function arrivalConfidence(
  hopConfidence: LiveHopConfidence,
  skippedUnresolved: boolean
): LiveHopConfidence {
  if (!skippedUnresolved) return hopConfidence;
  return hopConfidence === 'exact' ? 'probable' : hopConfidence;
}

function appendEarWaypoint(
  waypoints: LiveWaypoint[],
  ear: LiveEar,
  earId: string,
  skippedUnresolved: boolean
): void {
  const confidence: LiveHopConfidence = skippedUnresolved ? 'probable' : 'exact';
  const reason = skippedUnresolved ? 'skipped_unresolved' : undefined;
  if (waypoints.length === 0) {
    waypoints.push({
      lat: ear.lat,
      lon: ear.lon,
      token: earId,
      kind: 'ear',
      confidence: skippedUnresolved ? 'probable' : 'exact',
      reason,
    });
    return;
  }
  const last = waypoints[waypoints.length - 1];
  if (last.lat === ear.lat && last.lon === ear.lon) return;
  waypoints.push({
    lat: ear.lat,
    lon: ear.lon,
    token: earId,
    kind: 'ear',
    confidence,
    reason,
  });
}

export function waypointsFromCommunity(packet: CommunityPacket): LiveWaypoint[] {
  const waypoints: LiveWaypoint[] = [];
  let skippedUnresolved = false;

  for (const hop of packet.hops) {
    if (!hopHasCoords(hop)) {
      skippedUnresolved = true;
      continue;
    }
    const confidence = arrivalConfidence(hop.confidence, skippedUnresolved);
    const reason =
      hop.reason ??
      (skippedUnresolved && hop.confidence === 'exact' ? 'skipped_unresolved' : undefined);
    waypoints.push({
      lat: hop.lat,
      lon: hop.lon,
      token: hop.token,
      kind: 'hop',
      confidence,
      reason,
      label: hop.name,
    });
    skippedUnresolved = false;
  }

  if (packet.ear) {
    appendEarWaypoint(
      waypoints,
      { lat: packet.ear.lat, lon: packet.ear.lon, source: packet.ear.source },
      packet.ear_id,
      skippedUnresolved
    );
  }

  return waypoints;
}

export function waypointsFromRaw(
  packet: RawPacket,
  prefixIndex: Map<string, Contact[]>,
  ear: LiveEar | null,
  earId: string
): LiveWaypoint[] {
  const parsed = parsePacket(packet.data);
  if (!parsed) return [];

  const waypoints: LiveWaypoint[] = [];
  let skippedUnresolved = false;

  for (const token of parsed.pathBytes) {
    const contact = uniqueGpsContact(token, prefixIndex);
    if (contact && contact.lat != null && contact.lon != null) {
      const confidence = arrivalConfidence('exact', skippedUnresolved);
      waypoints.push({
        lat: contact.lat,
        lon: contact.lon,
        token,
        kind: 'hop',
        confidence,
        reason: skippedUnresolved ? 'skipped_unresolved' : undefined,
        label: contact.name ?? undefined,
      });
      skippedUnresolved = false;
    } else {
      skippedUnresolved = true;
    }
  }

  if (ear) appendEarWaypoint(waypoints, ear, earId, skippedUnresolved);
  return waypoints;
}

export function observationFromCommunity(packet: CommunityPacket): LiveObservation | null {
  const frame = asCommunityPacket(packet);
  if (!frame) return null;
  const ear: LiveEar | null = frame.ear
    ? { lat: frame.ear.lat, lon: frame.ear.lon, source: frame.ear.source }
    : null;
  return {
    id: frame.event_id,
    hash8: frame.hash8,
    source: 'community',
    type: frame.type,
    snr: frame.snr ?? null,
    iata: frame.iata.trim() ? frame.iata.trim().toUpperCase() : null,
    t: frame.t,
    earId: frame.ear_id,
    ear,
    waypoints: waypointsFromCommunity(frame),
  };
}

export function observationFromRaw(
  packet: RawPacket,
  prefixIndex: Map<string, Contact[]>,
  config: RadioConfig | null
): LiveObservation | null {
  const parsed = parsePacket(packet.data);
  if (!parsed) return null;
  const ear: LiveEar | null =
    config && isValidLocation(config.lat, config.lon)
      ? { lat: config.lat, lon: config.lon, source: 'local' }
      : null;
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

export function isStaleLiveTime(t: number, now: number = Date.now()): boolean {
  return now - t >= LIVE_DIM_AFTER_MS;
}
