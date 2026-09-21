import type { RawPacket } from '../types';
import { getRawPacketObservationKey } from './rawPacketIdentity';

/** Process-local observation id only — never a packet buffer, never a URL hash. */
export const VISUALIZER_FOCUS_OBSERVATION_KEY = 'visualizerFocusObservationKey';

/** Optional firmware packet hash (16 hex), not payload bytes. */
export const VISUALIZER_FOCUS_PACKET_HASH_KEY = 'visualizerFocusPacketHash';

/** Optional filter ids as a JSON string array. */
export const VISUALIZER_FOCUS_FILTER_IDS_KEY = 'visualizerFocusFilterIds';

export interface VisualizerFocusHandoff {
  observationKey: string;
  packetHash?: string;
  filterIds?: string[];
}

export interface SetVisualizerFocusHandoffInput {
  observationKey: string;
  packetHash?: string;
  filterIds?: string[];
}

let rememberedHandoff: VisualizerFocusHandoff | null = null;
let rememberTimer: ReturnType<typeof setTimeout> | null = null;

function rememberHandoff(handoff: VisualizerFocusHandoff | null): void {
  rememberedHandoff = handoff;
  if (rememberTimer !== null) {
    clearTimeout(rememberTimer);
    rememberTimer = null;
  }
  if (handoff) {
    // StrictMode remounts synchronously in the same turn; drop the cache after that.
    rememberTimer = setTimeout(() => {
      rememberedHandoff = null;
      rememberTimer = null;
    }, 0);
  }
}

function normalizeObservationKey(value: string): string {
  const trimmed = value.trim();
  if (/^\d+$/.test(trimmed)) {
    return `obs-${trimmed}`;
  }
  return trimmed;
}

function normalizePacketHash(value: string | undefined): string | undefined {
  if (typeof value !== 'string') {
    return undefined;
  }
  const trimmed = value.trim();
  return trimmed.length > 0 ? trimmed : undefined;
}

function normalizeFilterIds(value: string[] | undefined): string[] | undefined {
  if (!Array.isArray(value)) {
    return undefined;
  }
  const ids = value.filter((id): id is string => typeof id === 'string' && id.trim().length > 0);
  return ids.length > 0 ? ids : undefined;
}

function parseFilterIds(raw: string | null): string[] | undefined {
  if (!raw) {
    return undefined;
  }
  try {
    const parsed: unknown = JSON.parse(raw);
    if (!Array.isArray(parsed)) {
      return undefined;
    }
    return normalizeFilterIds(parsed.filter((id): id is string => typeof id === 'string'));
  } catch {
    return undefined;
  }
}

function readStorageHandoff(): VisualizerFocusHandoff | null {
  try {
    const rawKey = sessionStorage.getItem(VISUALIZER_FOCUS_OBSERVATION_KEY);
    if (!rawKey) {
      return null;
    }
    const observationKey = normalizeObservationKey(rawKey);
    if (!observationKey) {
      return null;
    }
    const packetHash = normalizePacketHash(
      sessionStorage.getItem(VISUALIZER_FOCUS_PACKET_HASH_KEY) ?? undefined
    );
    const filterIds = parseFilterIds(sessionStorage.getItem(VISUALIZER_FOCUS_FILTER_IDS_KEY));
    return {
      observationKey,
      ...(packetHash ? { packetHash } : {}),
      ...(filterIds ? { filterIds } : {}),
    };
  } catch {
    return null;
  }
}

function writeStorageHandoff(handoff: VisualizerFocusHandoff): void {
  try {
    sessionStorage.setItem(VISUALIZER_FOCUS_OBSERVATION_KEY, handoff.observationKey);
    if (handoff.packetHash) {
      sessionStorage.setItem(VISUALIZER_FOCUS_PACKET_HASH_KEY, handoff.packetHash);
    } else {
      sessionStorage.removeItem(VISUALIZER_FOCUS_PACKET_HASH_KEY);
    }
    if (handoff.filterIds && handoff.filterIds.length > 0) {
      sessionStorage.setItem(VISUALIZER_FOCUS_FILTER_IDS_KEY, JSON.stringify(handoff.filterIds));
    } else {
      sessionStorage.removeItem(VISUALIZER_FOCUS_FILTER_IDS_KEY);
    }
  } catch {
    // Private mode — in-memory remember still covers the same-tab handoff.
  }
}

function clearStorageHandoff(): void {
  try {
    sessionStorage.removeItem(VISUALIZER_FOCUS_OBSERVATION_KEY);
    sessionStorage.removeItem(VISUALIZER_FOCUS_PACKET_HASH_KEY);
    sessionStorage.removeItem(VISUALIZER_FOCUS_FILTER_IDS_KEY);
  } catch {
    // ignore
  }
}

/**
 * Write a #raw → #visualizer focus handoff. IDs only — never the packet buffer.
 * Agent F should call this, then navigate to `#visualizer`.
 */
export function setVisualizerFocusHandoff(input: SetVisualizerFocusHandoffInput): void {
  const observationKey = normalizeObservationKey(input.observationKey ?? '');
  if (!observationKey) {
    return;
  }
  const packetHash = normalizePacketHash(input.packetHash);
  const filterIds = normalizeFilterIds(input.filterIds);
  const handoff: VisualizerFocusHandoff = {
    observationKey,
    ...(packetHash ? { packetHash } : {}),
    ...(filterIds ? { filterIds } : {}),
  };
  rememberHandoff(handoff);
  writeStorageHandoff(handoff);
}

/** Peek without clearing. Used by the graph hook before VisualizerView consumes. */
export function peekVisualizerFocusHandoff(): VisualizerFocusHandoff | null {
  return readStorageHandoff() ?? rememberedHandoff;
}

/** Read and clear sessionStorage. Safe to call twice in the same turn (StrictMode). */
export function consumeVisualizerFocusHandoff(): VisualizerFocusHandoff | null {
  const stored = readStorageHandoff();
  if (stored) {
    clearStorageHandoff();
    rememberHandoff(stored);
    return stored;
  }
  return rememberedHandoff;
}

export function findVisualizerFocusPacket(
  packets: readonly RawPacket[],
  handoff: VisualizerFocusHandoff | null
): RawPacket | null {
  if (!handoff) {
    return null;
  }
  const byObservation = packets.find(
    (packet) => getRawPacketObservationKey(packet) === handoff.observationKey
  );
  if (byObservation) {
    return byObservation;
  }
  if (!handoff.packetHash) {
    return null;
  }
  const wanted = handoff.packetHash.toLowerCase();
  return packets.find((packet) => (packet.packet_hash ?? '').toLowerCase() === wanted) ?? null;
}

export function packetMatchesVisualizerFocus(
  packet: RawPacket,
  handoff: VisualizerFocusHandoff | null
): boolean {
  if (!handoff) {
    return false;
  }
  if (getRawPacketObservationKey(packet) === handoff.observationKey) {
    return true;
  }
  if (!handoff.packetHash) {
    return false;
  }
  return (packet.packet_hash ?? '').toLowerCase() === handoff.packetHash.toLowerCase();
}

/** Test helper — also useful if a later handoff must replace a stuck remember cache. */
export function resetVisualizerFocusHandoff(): void {
  rememberedHandoff = null;
  if (rememberTimer !== null) {
    clearTimeout(rememberTimer);
    rememberTimer = null;
  }
  clearStorageHandoff();
}
