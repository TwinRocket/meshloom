import { useSyncExternalStore } from 'react';

import type { CommunityLiveStatus, CommunityPacket, LiveCloseCode } from '../types';
import {
  LIVE_CLOSE_INACTIVE,
  LIVE_CLOSE_JWT_EXPIRED,
  LIVE_CLOSE_RATE_LIMIT,
  LIVE_CLOSE_SLOT_BUSY,
} from '../types';

export const MAX_LIVE_COMMUNITY_PACKETS = 200;

export interface LiveConnectionState {
  closeCode: LiveCloseCode | null;
  optOut: boolean;
  inactiveObserver: boolean;
}

const listeners = new Set<() => void>();

let packets: CommunityPacket[] = [];
let connection: LiveConnectionState = {
  closeCode: null,
  optOut: false,
  inactiveObserver: false,
};

function emit(): void {
  for (const listener of listeners) listener();
}

function subscribe(listener: () => void): () => void {
  listeners.add(listener);
  return () => {
    listeners.delete(listener);
  };
}

export function recordCommunityPacket(packet: CommunityPacket): void {
  if (packets.some((existing) => existing.event_id === packet.event_id)) {
    return;
  }
  const next = [...packets, packet];
  const overflow = next.length - MAX_LIVE_COMMUNITY_PACKETS;
  packets = overflow > 0 ? next.slice(overflow) : next;
  emit();
}

export function getCommunityPackets(): CommunityPacket[] {
  return packets;
}

export function getLiveConnectionState(): LiveConnectionState {
  return connection;
}

export function setLiveCloseCode(code: LiveCloseCode | null): void {
  if (connection.closeCode === code) return;
  connection = { ...connection, closeCode: code };
  emit();
}

export function setLiveOptOut(optOut: boolean): void {
  if (connection.optOut === optOut) return;
  connection = { ...connection, optOut };
  emit();
}

export function setLiveInactiveObserver(inactiveObserver: boolean): void {
  if (connection.inactiveObserver === inactiveObserver) return;
  connection = {
    ...connection,
    inactiveObserver,
    closeCode: inactiveObserver ? LIVE_CLOSE_INACTIVE : connection.closeCode,
  };
  emit();
}

export function clearLiveBanners(): void {
  connection = { closeCode: null, optOut: connection.optOut, inactiveObserver: false };
  emit();
}

export function relancerLive(): void {
  connection = { ...connection, closeCode: null, inactiveObserver: false };
  emit();
}

export function applyLiveStatus(status: Pick<CommunityLiveStatus, 'close_code' | 'opted_out'>): void {
  const closeCode = status.close_code;
  const optOut = status.opted_out;
  const inactiveObserver = closeCode === LIVE_CLOSE_INACTIVE;
  if (
    connection.closeCode === closeCode &&
    connection.optOut === optOut &&
    connection.inactiveObserver === inactiveObserver
  ) {
    return;
  }
  connection = { closeCode, optOut, inactiveObserver };
  emit();
}

export function resetLivePacketStore(): void {
  packets = [];
  connection = { closeCode: null, optOut: false, inactiveObserver: false };
  emit();
}

export function useCommunityPackets(): CommunityPacket[] {
  return useSyncExternalStore(subscribe, getCommunityPackets);
}

export function useLiveConnectionState(): LiveConnectionState {
  return useSyncExternalStore(subscribe, getLiveConnectionState);
}

export { LIVE_CLOSE_INACTIVE, LIVE_CLOSE_JWT_EXPIRED, LIVE_CLOSE_RATE_LIMIT, LIVE_CLOSE_SLOT_BUSY };
