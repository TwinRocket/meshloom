import { useSyncExternalStore } from 'react';

import type { RawPacket } from '../types';
import { getRawPacketReplayCacheKey } from '../utils/rawPacketDerivedCache';

/**
 * Historical replay overlay for #raw. Isolated from the live overheard stream:
 * hist-{dbId} keys, no observation_id, RSSI/SNR always null. clearRawPackets()
 * (WS reconnect) must not touch this store.
 */

let packets: RawPacket[] = [];
let replayActive = false;
const listeners = new Set<() => void>();

function subscribe(listener: () => void): () => void {
  listeners.add(listener);
  return () => {
    listeners.delete(listener);
  };
}

function emit(): void {
  for (const listener of listeners) {
    listener();
  }
}

/** Replay identity. Never mix these into the live `obs-*` / `db-*` keyspace. */
export function getReplayPacketKey(packet: Pick<RawPacket, 'id'>): string {
  return getRawPacketReplayCacheKey(packet.id);
}

/**
 * Normalize a stored history row. RSSI/SNR are not retained on disk for this
 * endpoint — do not invent them. observation_id is live-only.
 */
export function toReplayPacket(item: RawPacket): RawPacket {
  return {
    id: item.id,
    timestamp: item.timestamp,
    data: item.data,
    payload_type: item.payload_type,
    decrypted: item.decrypted,
    decrypted_info: item.decrypted_info ?? null,
    rssi: null,
    snr: null,
    transport_code: item.transport_code ?? null,
    region: item.region ?? null,
    packet_hash: item.packet_hash ?? null,
  };
}

export function getReplayPackets(): RawPacket[] {
  return packets;
}

export function getReplayPacketKeys(): string[] {
  return packets.map((packet) => getReplayPacketKey(packet));
}

export function isReplayActive(): boolean {
  return replayActive;
}

/** Install a replay snapshot. Copies and strips live-only fields. */
export function loadReplayPackets(next: RawPacket[]): void {
  packets = next.map(toReplayPacket);
  replayActive = true;
  emit();
}

/** Leave replay. Live store is untouched. */
export function clearReplayPackets(): void {
  if (!replayActive && packets.length === 0) {
    return;
  }
  packets = [];
  replayActive = false;
  emit();
}

/** Full reset for tests. */
export function resetRawPacketReplayStore(): void {
  packets = [];
  replayActive = false;
  emit();
}

export function useReplayPackets(): RawPacket[] {
  return useSyncExternalStore(subscribe, getReplayPackets);
}

export function useReplayActive(): boolean {
  return useSyncExternalStore(subscribe, isReplayActive);
}
