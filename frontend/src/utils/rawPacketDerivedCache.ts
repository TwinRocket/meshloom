import { useMemo } from 'react';

import type { Channel, RawPacket } from '../types';
import { deriveHashtagKeyHex } from './hashtagKey';
import { getRawPacketObservationKey } from './rawPacketIdentity';
import {
  collectGroupDataKeys,
  createDecoderOptions,
  decodePacketSummary,
  isPacketOpen,
} from './rawPacketInspector';
import { getPacketTypeName } from './rawPacketStats';

export type RawPacketDerivedScope = 'live' | 'replay';

export interface RawPacketDerivedCacheOptions {
  /** Live uses `obs-*` (or `db-*`). Replay uses `hist-{id}`. */
  scope?: RawPacketDerivedScope;
  channels?: Channel[] | null;
  /** Extra channel secrets already extracted by the caller. */
  channelKeys?: readonly string[];
  /** Community hashtag names — identity is the name list; secrets are derived. */
  communityNames?: readonly string[];
  extraSecrets?: readonly string[];
  /** Extra invalidation token. Composed with channel/community/extraSecrets identity. */
  generation?: string | number;
}

/**
 * Cached per-observation fields. Computed without writing `packet.decrypted`
 * or `packet.decrypted_info`.
 */
export interface RawPacketDerived {
  /** `obs-*` / `db-*` (live) or `hist-{id}` (replay). */
  cacheKey: string;
  payloadType: string;
  routeType: string;
  /** Decoder summary, including Control/Request/Path subtype bits. */
  summary: string;
  details?: string;
  /** `packet_hash`, else storage `id`. */
  repeatKey: string;
  clientDecoded: boolean;
  isOpen: boolean;
}

export interface RawPacketDerivedEntry extends RawPacketDerived {
  packet: RawPacket;
}

export interface RawPacketDerivedCacheStats {
  size: number;
  hits: number;
  misses: number;
  namespace: string | null;
}

const EMPTY_OPTIONS: RawPacketDerivedCacheOptions = Object.freeze({});
const EMPTY_STRINGS: readonly string[] = Object.freeze([]);

let cache = new Map<string, RawPacketDerived>();
let activeNamespace: string | null = null;
let hits = 0;
let misses = 0;

function trimList(values: readonly string[] | undefined): string[] {
  const out: string[] = [];
  for (const value of values ?? EMPTY_STRINGS) {
    const trimmed = value.trim();
    if (trimmed) out.push(trimmed);
  }
  return out;
}

function channelKeysFromOptions(options: RawPacketDerivedCacheOptions): string[] {
  const keys: string[] = [];
  for (const channel of options.channels ?? []) {
    const key = channel.key?.trim();
    if (key) keys.push(key);
  }
  keys.push(...trimList(options.channelKeys));
  return keys;
}

function communitySecretsFromNames(names: readonly string[]): string[] {
  return names.map((name) => deriveHashtagKeyHex(name));
}

function secretChannels(secrets: readonly string[]): Channel[] {
  return secrets.map((key) => ({
    key,
    name: '',
    is_hashtag: false,
    on_radio: false,
    last_read_at: null,
    favorite: false,
    muted: false,
  }));
}

/** Repeat aggregation key: firmware hash, else storage row id. */
export function getPacketRepeatKey(packet: Pick<RawPacket, 'id' | 'packet_hash'>): string {
  return packet.packet_hash || String(packet.id);
}

/** Replay identity. Isolated from live `obs-*` / `db-*` keys. */
export function getRawPacketReplayCacheKey(dbId: number): string {
  return `hist-${dbId}`;
}

export function getRawPacketDerivedCacheKey(
  packet: Pick<RawPacket, 'id' | 'observation_id'>,
  scope: RawPacketDerivedScope = 'live'
): string {
  if (scope === 'replay') {
    return getRawPacketReplayCacheKey(packet.id);
  }
  return getRawPacketObservationKey(packet);
}

function serializeKeyIdentity(options: RawPacketDerivedCacheOptions): string {
  const channelKeys = channelKeysFromOptions(options);
  const communityNames = trimList(options.communityNames);
  const extraSecrets = trimList(options.extraSecrets);
  return `k:${channelKeys.join(',')}#n:${communityNames.join('\0')}#x:${extraSecrets.join(',')}`;
}

/**
 * Namespace for the derived cache. Channel keys, community names, and extra
 * secrets always participate. A generation token is prepended when set:
 * `gen:${generation}|k:…#n:…#x:…`. Changing any part invalidates.
 */
export function serializeRawPacketDerivedNamespace(
  options: RawPacketDerivedCacheOptions = EMPTY_OPTIONS
): string {
  const identity = serializeKeyIdentity(options);
  if (options.generation !== undefined && options.generation !== '') {
    return `gen:${options.generation}|${identity}`;
  }
  return identity;
}

function activateNamespace(namespace: string): void {
  if (activeNamespace === namespace) return;
  cache = new Map();
  activeNamespace = namespace;
  hits = 0;
  misses = 0;
}

export function getRawPacketDerivedCacheStats(): RawPacketDerivedCacheStats {
  return { size: cache.size, hits, misses, namespace: activeNamespace };
}

export function clearRawPacketDerivedCache(): void {
  cache = new Map();
  activeNamespace = null;
  hits = 0;
  misses = 0;
}

/** Drop entries the caller no longer holds. Does not touch the live packet store. */
export function retainRawPacketDerivedKeys(keys: Iterable<string>): void {
  const keep = new Set(keys);
  for (const key of cache.keys()) {
    if (!keep.has(key)) cache.delete(key);
  }
}

export function peekRawPacketDerived(
  cacheKey: string,
  options?: RawPacketDerivedCacheOptions
): RawPacketDerived | undefined {
  if (options) {
    const namespace = serializeRawPacketDerivedNamespace(options);
    if (namespace !== activeNamespace) return undefined;
  }
  return cache.get(cacheKey);
}

/**
 * Decode once per observation + namespace. Never writes the packet.
 * `clientDecoded` comes from `decrypted_info.group_data`, `parseGroupData`,
 * or npm GroupText decrypt — via `decodePacketSummary` / `isPacketClientDecoded`.
 */
export function computeRawPacketDerived(
  packet: RawPacket,
  options: RawPacketDerivedCacheOptions = EMPTY_OPTIONS
): RawPacketDerived {
  const scope = options.scope ?? 'live';
  const channelKeys = channelKeysFromOptions(options);
  const extraSecrets = [
    ...communitySecretsFromNames(trimList(options.communityNames)),
    ...trimList(options.extraSecrets),
  ];
  const decoderOptions = createDecoderOptions([
    ...(options.channels ?? []),
    ...secretChannels([...channelKeys, ...extraSecrets]),
  ]);
  const inspectExtras = {
    channelKeys: collectGroupDataKeys(options.channels, [...channelKeys, ...extraSecrets]),
    extraSecrets,
  };
  const decoded = decodePacketSummary(packet, decoderOptions, inspectExtras);
  const payloadType = getPacketTypeName(packet, decoderOptions) || decoded.payloadType;
  const clientDecoded = decoded.clientDecoded;

  return {
    cacheKey: getRawPacketDerivedCacheKey(packet, scope),
    payloadType,
    routeType: decoded.routeType,
    summary: decoded.summary,
    details: decoded.details,
    repeatKey: getPacketRepeatKey(packet),
    clientDecoded,
    isOpen: isPacketOpen(payloadType, packet, clientDecoded),
  };
}

export function getRawPacketDerived(
  packet: RawPacket,
  options: RawPacketDerivedCacheOptions = EMPTY_OPTIONS
): RawPacketDerived {
  const namespace = serializeRawPacketDerivedNamespace(options);
  activateNamespace(namespace);
  const cacheKey = getRawPacketDerivedCacheKey(packet, options.scope ?? 'live');
  const cached = cache.get(cacheKey);
  if (cached) {
    hits += 1;
    return cached;
  }
  misses += 1;
  const derived = computeRawPacketDerived(packet, options);
  cache.set(cacheKey, derived);
  return derived;
}

export function mapRawPacketsDerived(
  packets: readonly RawPacket[],
  options: RawPacketDerivedCacheOptions = EMPTY_OPTIONS
): Map<string, RawPacketDerived> {
  const mapped = new Map<string, RawPacketDerived>();
  for (const packet of packets) {
    const derived = getRawPacketDerived(packet, options);
    mapped.set(derived.cacheKey, derived);
  }
  retainRawPacketDerivedKeys(mapped.keys());
  return mapped;
}

/**
 * Hook for C/F: map a packet list through the derived cache.
 * Namespace changes (channels / community names / generation) drop cached rows.
 * Does not subscribe to or rewrite the live packet store.
 */
export function useRawPacketDerivedCache(
  packets: readonly RawPacket[],
  options: RawPacketDerivedCacheOptions = EMPTY_OPTIONS
): RawPacketDerivedEntry[] {
  const scope = options.scope ?? 'live';
  const { channels, channelKeys, communityNames, extraSecrets, generation } = options;

  return useMemo(() => {
    const ctx: RawPacketDerivedCacheOptions = {
      scope,
      channels,
      channelKeys,
      communityNames,
      extraSecrets,
      generation,
    };
    const rows = packets.map((packet) => ({
      packet,
      ...getRawPacketDerived(packet, ctx),
    }));
    retainRawPacketDerivedKeys(rows.map((row) => row.cacheKey));
    return rows;
  }, [packets, scope, channels, channelKeys, communityNames, extraSecrets, generation]);
}
