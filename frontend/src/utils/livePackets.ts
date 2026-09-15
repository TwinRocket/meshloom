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
import { calculateDistance, isValidLocation, MIN_NAMED_HOP_HEX_CHARS } from './pathUtils';
import { getPacketLabel, parsePacket } from './visualizerUtils';

export const LIVE_DIM_AFTER_MS = 5 * 60 * 1000;
/** Unique 1-byte srcHash may name A only inside this radius of the path anchor. */
export const LIVE_ORIGIN_ANCHOR_KM = 20;
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

/** Packet-type palette. Must stay disjoint from NODE_ROLE_STYLE (no shared #f59e0b).
 *  Trace is #a78bfa so it does not collide with LOCAL_RADIO_VISUAL. */
export const LIVE_TYPE_COLORS: Record<CommunityPacketType, string> = {
  advert: '#fde047',
  text: '#22f0ff',
  ack: '#39ff88',
  trace: '#a78bfa',
  other: '#78716c',
};

export type LiveSource = 'local' | 'community';
export type LiveHopConfidence = 'exact' | 'probable' | 'unresolved';
export type LiveRouteKind = 'flood' | 'direct' | 'unknown';

export interface LiveWaypoint {
  lat: number;
  lon: number;
  token: string;
  kind: 'hop' | 'ear' | 'origin';
  confidence: LiveHopConfidence;
  reason?: string;
  label?: string;
  pubkey?: string;
}

export interface LiveEar {
  lat: number;
  lon: number;
  source: 'advert' | 'iata' | 'local';
}

export interface LiveObservation {
  id: string;
  hash8: string;
  /** 16-hex firmware hash when known. Bucket / twin key; hash8 stays the prefix. */
  packetHash: string | null;
  source: LiveSource;
  type: CommunityPacketType;
  snr: number | null;
  iata: string | null;
  t: number;
  earId: string;
  ear: LiveEar | null;
  waypoints: LiveWaypoint[];
  routeKind: LiveRouteKind;
  advertPubkey: string | null;
  srcHash: string | null;
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
 * The local feed is more opaque than the community feed. When the same packet
 * identity exists on both feeds, community is dimmed further so the local drop wins.
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

const FIRMWARE_HASH_RE = /^[0-9a-fA-F]{8,}$/;
const PACKET_HASH16_RE = /^[0-9a-f]{16,}$/;

/** 16-hex lowercase firmware hash, or null when only hash8 (old Stats) is available. */
export function normalizePacketHash16(value: unknown): string | null {
  if (typeof value !== 'string') return null;
  const hex = value.trim().toLowerCase();
  if (!PACKET_HASH16_RE.test(hex)) return null;
  const sliced = hex.slice(0, 16);
  if (sliced === '0'.repeat(16)) return null;
  return sliced;
}

export function packetHashFromRaw(packet: RawPacket): string | null {
  return normalizePacketHash16(packet.packet_hash);
}

/** Bucket / twin identity: 16-hex when present, otherwise hash8. */
export function observationBucketKey(obs: Pick<LiveObservation, 'packetHash' | 'hash8'>): string {
  return obs.packetHash ?? obs.hash8;
}

/** Coalesce key: same firmware hash + first hop + ear is one heard path. */
export function observationCoalesceKey(
  obs: Pick<LiveObservation, 'packetHash' | 'hash8' | 'earId' | 'waypoints'>
): string {
  const first = firstHopWaypoint(obs);
  const hopToken = first?.token.trim().toLowerCase() ?? '';
  return `${observationBucketKey(obs)}:${hopToken}:${obs.earId}`;
}

export function observationHasLocalTwin(
  obs: Pick<LiveObservation, 'packetHash' | 'hash8'>,
  localKeys: ReadonlySet<string>
): boolean {
  if (obs.packetHash) return localKeys.has(obs.packetHash);
  return localKeys.has(obs.hash8);
}

/** Prefer the firmware SHA-256 the backend already computed. Decoder djb2 is fallback only. */
export function hash8FromRaw(packet: RawPacket): string {
  const firmware = packet.packet_hash?.trim();
  if (firmware && FIRMWARE_HASH_RE.test(firmware) && firmware !== '0'.repeat(firmware.length)) {
    return firmware.slice(0, 8).toLowerCase();
  }
  const parsed = parsePacket(packet.data);
  const raw = parsed?.messageHash || '00000000';
  return raw.slice(0, 8).toLowerCase();
}

/** TRANSPORT_FLOOD/FLOOD (0/1) vs DIRECT/TRANSPORT_DIRECT (2/3). */
export function routeKindFromRawHex(data: string): LiveRouteKind {
  const hex = data.trim();
  if (hex.length < 2) return 'unknown';
  const header = Number.parseInt(hex.slice(0, 2), 16);
  if (!Number.isFinite(header)) return 'unknown';
  const routeType = header & 0x03;
  if (routeType === 0x00 || routeType === 0x01) return 'flood';
  if (routeType === 0x02 || routeType === 0x03) return 'direct';
  return 'unknown';
}

export function mergeRouteKind(a: LiveRouteKind, b: LiveRouteKind): LiveRouteKind {
  if (a === b) return a;
  if (a === 'unknown') return b;
  if (b === 'unknown') return a;
  return 'unknown';
}

/** Adverts flood even when Community left routeKind unknown. */
export function inferFloodForAdvert(
  obs: Pick<LiveObservation, 'type' | 'routeKind'>
): LiveRouteKind {
  return obs.type === 'advert' ? 'flood' : obs.routeKind;
}

export interface LiveOriginPin {
  public_key: string;
  lat: number;
  lon: number;
}

function pinKey(node: { public_key: string }): string {
  return node.public_key.trim().toLowerCase();
}

function sameLocation(a: { lat: number; lon: number }, b: { lat: number; lon: number }): boolean {
  return a.lat === b.lat && a.lon === b.lon;
}

/** Local radio GPS is a geometry pin like any directory node. */
export function pinsIncludingLocalRadio(
  nodes: ReadonlyArray<LiveOriginPin>,
  config: RadioConfig | null
): LiveOriginPin[] {
  const pins: LiveOriginPin[] = [];
  const seen = new Set<string>();
  for (const node of nodes) {
    if (!isValidLocation(node.lat, node.lon)) continue;
    const key = pinKey(node);
    if (!key || seen.has(key)) continue;
    seen.add(key);
    pins.push({ public_key: key, lat: node.lat, lon: node.lon });
  }
  if (config && isValidLocation(config.lat, config.lon) && config.public_key) {
    const key = pinKey(config);
    if (key && !seen.has(key)) {
      pins.push({ public_key: key, lat: config.lat, lon: config.lon });
    }
  }
  return pins;
}

export function findPinByKey(
  key: string | null | undefined,
  pins: ReadonlyArray<LiveOriginPin>
): LiveOriginPin | null {
  if (!key) return null;
  const needle = key.trim().toLowerCase();
  if (!needle) return null;
  const matches = pins.filter((pin) => pinKey(pin) === needle || pinKey(pin).startsWith(needle));
  if (needle.length >= 16) {
    const exact = pins.find((pin) => pinKey(pin) === needle);
    return exact ?? null;
  }
  return matches.length === 1 ? matches[0] : null;
}

function firstHopAnchor(obs: LiveObservation): { lat: number; lon: number } | null {
  for (const point of obs.waypoints) {
    if (point.kind === 'hop' && isValidLocation(point.lat, point.lon)) {
      return { lat: point.lat, lon: point.lon };
    }
  }
  if (obs.ear && isValidLocation(obs.ear.lat, obs.ear.lon)) {
    return { lat: obs.ear.lat, lon: obs.ear.lon };
  }
  return null;
}

/**
 * A is before the first hop. Advert = signed key. Message = 1-byte srcHash plus
 * exactly one pin within 20 km of the first hop GPS (or the ear if there is no hop).
 * 0 or 2+ candidates → no origin. Never `contact_key`.
 */
export function resolveOriginPin(
  obs: Pick<LiveObservation, 'advertPubkey' | 'srcHash' | 'waypoints' | 'ear'>,
  pins: ReadonlyArray<LiveOriginPin>
): LiveOriginPin | null {
  if (obs.advertPubkey) {
    const advert = findPinByKey(obs.advertPubkey, pins);
    if (advert) return advert;
  }
  const src = obs.srcHash?.trim().toLowerCase() ?? '';
  if (src.length !== 2) return null;
  const anchor = firstHopAnchor(obs as LiveObservation);
  if (!anchor) return null;
  const nearby = pins.filter((pin) => {
    if (!pinKey(pin).startsWith(src)) return false;
    const km = calculateDistance(anchor.lat, anchor.lon, pin.lat, pin.lon);
    return km != null && km <= LIVE_ORIGIN_ANCHOR_KM;
  });
  return nearby.length === 1 ? nearby[0] : null;
}

/** If A resolves, it is the first drawable vertex (exact). Ears stay in the list. */
export function prependOrigin(
  obs: Pick<LiveObservation, 'advertPubkey' | 'srcHash' | 'waypoints' | 'ear'>,
  pins: ReadonlyArray<LiveOriginPin>
): LiveWaypoint[] {
  const origin = resolveOriginPin(obs, pins);
  if (!origin) return obs.waypoints.slice();
  const originPoint: LiveWaypoint = {
    lat: origin.lat,
    lon: origin.lon,
    token: origin.public_key.slice(0, 8),
    kind: 'origin',
    confidence: 'exact',
    pubkey: origin.public_key,
  };
  const rest = obs.waypoints.filter((point) => !sameLocation(point, origin));
  return [originPoint, ...rest];
}

export function firstHopWaypoint(obs: Pick<LiveObservation, 'waypoints'>): LiveWaypoint | null {
  for (const point of obs.waypoints) {
    if (point.kind !== 'hop') continue;
    if (point.confidence === 'unresolved') continue;
    if (!isValidLocation(point.lat, point.lon)) continue;
    return point;
  }
  return null;
}

function hopIdentity(hop: LiveWaypoint): string {
  const pubkey = hop.pubkey?.trim().toLowerCase();
  if (pubkey) return pubkey;
  return `${hop.token.trim().toLowerCase()}@${hop.lat},${hop.lon}`;
}

export interface FanoutHop {
  key: string;
  lat: number;
  lon: number;
  token: string;
  pubkey?: string;
  label?: string;
  kind: 'hop' | 'ear';
  confidence: Exclude<LiveHopConfidence, 'unresolved'>;
}

export interface FanoutLaser {
  key: string;
  origin: LiveOriginPin;
  hop: FanoutHop;
  obs: LiveObservation;
}

export interface FanoutPointFlash {
  key: string;
  lat: number;
  lon: number;
  kind: 'hop' | 'ear';
  obs: LiveObservation;
}

export interface FanoutPlan {
  origin: LiveOriginPin | null;
  lasers: FanoutLaser[];
  hopFlashes: FanoutPointFlash[];
  earFlashes: FanoutPointFlash[];
}

function asFanoutHop(point: LiveWaypoint, kind: 'hop' | 'ear' = 'hop'): FanoutHop {
  return {
    key: kind === 'ear' ? `ear:${point.token}@${point.lat},${point.lon}` : hopIdentity(point),
    lat: point.lat,
    lon: point.lon,
    token: point.token,
    pubkey: point.pubkey,
    label: point.label,
    kind,
    confidence: point.confidence === 'probable' ? 'probable' : 'exact',
  };
}

function primaryPolylineCoversOriginHop(
  obs: LiveObservation,
  origin: LiveOriginPin,
  hop: FanoutHop,
  pins: ReadonlyArray<LiveOriginPin>
): boolean {
  const waypoints = prependOrigin(obs, pins);
  const hasOrigin = waypoints.some(
    (point) => point.kind === 'origin' && sameLocation(point, origin)
  );
  const hasHop = waypoints.some((point) => point.kind === 'hop' && sameLocation(point, hop));
  return hasOrigin && hasHop;
}

/**
 * Leftover flood rays only: A→first hop when that edge is not already on
 * the primary polyline (prependOrigin + hops + ear). Never A→ear, never a
 * 1-point flash when A is missing — hop→ear is the primary draw.
 */
export function fanoutFromOrigin(
  bucket: { observations: readonly LiveObservation[] },
  pins: ReadonlyArray<LiveOriginPin>,
  alreadySpawnedKeys: ReadonlySet<string> = new Set()
): FanoutPlan {
  let origin: LiveOriginPin | null = null;
  for (const obs of bucket.observations) {
    origin = resolveOriginPin(obs, pins);
    if (origin) break;
  }

  const hopByKey = new Map<string, { hop: FanoutHop; obs: LiveObservation }>();
  for (const obs of bucket.observations) {
    const first = firstHopWaypoint(obs);
    if (!first) continue;
    const hop = asFanoutHop(first);
    if (alreadySpawnedKeys.has(hop.key) || hopByKey.has(hop.key)) continue;
    if (origin && sameLocation(origin, hop)) continue;
    hopByKey.set(hop.key, { hop, obs });
  }

  const lasers: FanoutLaser[] = [];
  if (origin) {
    for (const { hop, obs } of hopByKey.values()) {
      if (primaryPolylineCoversOriginHop(obs, origin, hop, pins)) continue;
      lasers.push({ key: hop.key, origin, hop, obs });
    }
  }

  return { origin, lasers, hopFlashes: [], earFlashes: [] };
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

const PUBKEY64_RE = /^[0-9a-fA-F]{64}$/;

function readOrigin(value: unknown): CommunityPacketHop | null {
  if (value == null) return null;
  const hop = readHop(value);
  if (!hop || !isRecord(value)) return null;
  const pubkey = value.pubkey;
  if (typeof pubkey === 'string' && PUBKEY64_RE.test(pubkey)) {
    hop.pubkey = pubkey.toLowerCase();
    return hop;
  }
  if (hop.confidence === 'unresolved') return null;
  return hop;
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
  const packetHash = normalizePacketHash16(value.packet_hash);
  if (packetHash && packetHash.slice(0, 8) === hash8) {
    packet.packet_hash = packetHash;
  }
  if (value.origin !== undefined) {
    const origin = readOrigin(value.origin);
    if (origin) packet.origin = origin;
  }
  if (
    value.route_kind === 'flood' ||
    value.route_kind === 'direct' ||
    value.route_kind === 'unknown'
  ) {
    packet.route_kind = value.route_kind;
  }
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
      ...(hop.pubkey ? { pubkey: hop.pubkey } : {}),
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
        pubkey: contact.public_key,
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
    packetHash: frame.packet_hash ?? null,
    source: 'community',
    type: frame.type,
    snr: frame.snr ?? null,
    iata: frame.iata.trim() ? frame.iata.trim().toUpperCase() : null,
    t: frame.t,
    earId: frame.ear_id,
    ear,
    waypoints: waypointsFromCommunity(frame),
    routeKind: frame.route_kind ?? 'unknown',
    advertPubkey: frame.origin?.pubkey ?? null,
    srcHash: null,
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
    packetHash: packetHashFromRaw(packet),
    source: 'local',
    type: packetTypeFromRaw(parsed.payloadType),
    snr: packet.snr,
    iata: null,
    t,
    earId,
    ear,
    waypoints: waypointsFromRaw(packet, prefixIndex, ear, earId),
    routeKind: routeKindFromRawHex(packet.data),
    advertPubkey: parsed.advertPubkey,
    srcHash: parsed.srcHash,
  };
}

export function isStaleLiveTime(t: number, now: number = Date.now()): boolean {
  return now - t >= LIVE_DIM_AFTER_MS;
}
