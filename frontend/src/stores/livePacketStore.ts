import { useSyncExternalStore } from 'react';

import type { CommunityLiveStatus, CommunityPacket, LiveAuthError, LiveCloseCode } from '../types';
import {
  LIVE_CLOSE_INACTIVE,
  LIVE_CLOSE_JWT_EXPIRED,
  LIVE_CLOSE_RATE_LIMIT,
  LIVE_CLOSE_SLOT_BUSY,
  LIVE_CLOSE_SUPERSEDED,
} from '../types';
import { asCommunityPacket } from '../utils/livePackets';

export const MAX_LIVE_COMMUNITY_PACKETS = 200;

export type LiveBannerKind = 'opt_out' | 'auth_rejected';

export interface LiveConnectionState {
  closeCode: LiveCloseCode | null;
  connected: boolean;
  reconnecting: boolean;
  optOut: boolean;
  inactiveObserver: boolean;
  /** Community refused the live token and the relay gave up (until Relancer). */
  authError: LiveAuthError | null;
  /** Community clock minus ours, seconds, when known. */
  clockSkewS: number | null;
  /** Community opt-out or a given-up token refusal. Never set for 4003/4005. */
  banner: LiveBannerKind | null;
}

const listeners = new Set<() => void>();

let packets: CommunityPacket[] = [];
let connection: LiveConnectionState = {
  closeCode: null,
  connected: false,
  reconnecting: false,
  optOut: false,
  inactiveObserver: false,
  authError: null,
  clockSkewS: null,
  banner: null,
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

export function isSilentLiveClose(code: LiveCloseCode | number | null | undefined): boolean {
  return code === LIVE_CLOSE_SLOT_BUSY || code === LIVE_CLOSE_SUPERSEDED;
}

export function normalizeLiveCloseCode(value: unknown): LiveCloseCode | null {
  if (value === LIVE_CLOSE_SLOT_BUSY) return LIVE_CLOSE_SUPERSEDED;
  if (
    value === LIVE_CLOSE_JWT_EXPIRED ||
    value === LIVE_CLOSE_INACTIVE ||
    value === LIVE_CLOSE_RATE_LIMIT ||
    value === LIVE_CLOSE_SUPERSEDED
  ) {
    return value;
  }
  return null;
}

function liveBanner(optOut: boolean, authError: LiveAuthError | null): LiveBannerKind | null {
  if (optOut) return 'opt_out';
  if (authError) return 'auth_rejected';
  return null;
}

function normalizeAuthError(value: unknown): LiveAuthError | null {
  return value === 'clock_skew' || value === 'token_rejected' ? value : null;
}

function deriveConnection(input: {
  closeCode: LiveCloseCode | null;
  optOut: boolean;
  connected?: boolean;
  reconnecting?: boolean;
  authError?: LiveAuthError | null;
  clockSkewS?: number | null;
}): LiveConnectionState {
  const closeCode = input.closeCode;
  const optOut = input.optOut;
  const authError = optOut ? null : (input.authError ?? null);
  const clockSkewS =
    authError && typeof input.clockSkewS === 'number' ? Math.round(input.clockSkewS) : null;
  const inactiveObserver = closeCode === LIVE_CLOSE_INACTIVE;
  const connected = !optOut && input.connected === true && closeCode == null;
  const reconnecting =
    !optOut &&
    !connected &&
    authError == null &&
    (input.reconnecting === true ||
      closeCode === LIVE_CLOSE_JWT_EXPIRED ||
      closeCode === LIVE_CLOSE_INACTIVE ||
      closeCode === LIVE_CLOSE_RATE_LIMIT ||
      closeCode === LIVE_CLOSE_SUPERSEDED ||
      (closeCode == null && input.connected !== true));
  return {
    closeCode,
    connected,
    reconnecting,
    optOut,
    inactiveObserver,
    authError,
    clockSkewS,
    banner: liveBanner(optOut, authError),
  };
}

function sameConnection(a: LiveConnectionState, b: LiveConnectionState): boolean {
  return (
    a.closeCode === b.closeCode &&
    a.connected === b.connected &&
    a.reconnecting === b.reconnecting &&
    a.optOut === b.optOut &&
    a.inactiveObserver === b.inactiveObserver &&
    a.authError === b.authError &&
    a.clockSkewS === b.clockSkewS &&
    a.banner === b.banner
  );
}

function setConnection(next: LiveConnectionState): void {
  if (sameConnection(connection, next)) return;
  connection = next;
  emit();
}

export type LiveBannerI18nKey =
  'live.bannerOptOut' | 'live.bannerClockSkew' | 'live.bannerTokenRejected';

export function liveBannerI18nKey(state: LiveConnectionState): LiveBannerI18nKey | null {
  if (state.banner === 'opt_out') return 'live.bannerOptOut';
  if (state.banner === 'auth_rejected') {
    return state.authError === 'clock_skew' ? 'live.bannerClockSkew' : 'live.bannerTokenRejected';
  }
  return null;
}

export function recordCommunityPacket(packet: CommunityPacket): void {
  const frame = asCommunityPacket(packet);
  if (!frame) return;
  if (packets.some((existing) => existing.event_id === frame.event_id)) {
    return;
  }
  const next = [...packets, frame];
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
  setConnection(
    deriveConnection({
      closeCode: normalizeLiveCloseCode(code),
      optOut: connection.optOut,
      connected: false,
    })
  );
}

export function setLiveOptOut(optOut: boolean): void {
  setConnection(
    deriveConnection({
      closeCode: optOut ? null : connection.closeCode,
      optOut,
      connected: optOut ? false : connection.connected,
      reconnecting: optOut ? false : connection.reconnecting,
      authError: optOut ? null : connection.authError,
      clockSkewS: connection.clockSkewS,
    })
  );
}

export function setLiveInactiveObserver(inactiveObserver: boolean): void {
  setConnection(
    deriveConnection({
      closeCode: inactiveObserver ? LIVE_CLOSE_INACTIVE : null,
      optOut: connection.optOut,
      connected: false,
    })
  );
}

export function clearLiveBanners(): void {
  setConnection(
    deriveConnection({
      closeCode: null,
      optOut: connection.optOut,
      connected: false,
    })
  );
}

export function relancerLive(): void {
  setConnection(
    deriveConnection({
      closeCode: null,
      optOut: connection.optOut,
      connected: false,
      reconnecting: !connection.optOut,
    })
  );
}

export function applyLiveStatus(
  status:
    | Partial<
        Pick<
          CommunityLiveStatus,
          | 'close_code'
          | 'opted_out'
          | 'connected'
          | 'reconnecting'
          | 'state'
          | 'auth_error'
          | 'clock_skew_s'
        >
      >
    | null
    | undefined
): void {
  const optOut = status?.opted_out === true;
  const closeCode = normalizeLiveCloseCode(status?.close_code);
  // An older relay has no auth_error: treat auth_rejected as a generic refusal.
  const authError =
    normalizeAuthError(status?.auth_error) ??
    (status?.state === 'auth_rejected' ? 'token_rejected' : null);
  setConnection(
    deriveConnection({
      closeCode,
      optOut,
      connected: status?.connected,
      reconnecting: status?.reconnecting,
      authError,
      clockSkewS: status?.clock_skew_s,
    })
  );
}

export function resetLivePacketStore(): void {
  packets = [];
  connection = {
    closeCode: null,
    connected: false,
    reconnecting: false,
    optOut: false,
    inactiveObserver: false,
    authError: null,
    clockSkewS: null,
    banner: null,
  };
  emit();
}

export function useCommunityPackets(): CommunityPacket[] {
  return useSyncExternalStore(subscribe, getCommunityPackets);
}

export function useLiveConnectionState(): LiveConnectionState {
  return useSyncExternalStore(subscribe, getLiveConnectionState);
}

export {
  LIVE_CLOSE_INACTIVE,
  LIVE_CLOSE_JWT_EXPIRED,
  LIVE_CLOSE_RATE_LIMIT,
  LIVE_CLOSE_SLOT_BUSY,
  LIVE_CLOSE_SUPERSEDED,
};
