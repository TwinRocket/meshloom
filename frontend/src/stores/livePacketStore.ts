import { useSyncExternalStore } from 'react';

import type { CommunityLiveStatus, CommunityPacket, LiveAuthError } from '../types';
import { asCommunityPacket } from '../utils/livePackets';

export const MAX_LIVE_COMMUNITY_PACKETS = 200;

export type LiveBannerKind = 'opt_out' | 'auth_rejected';

/** What LiveView needs from the relay status: close codes never reach the user. */
export interface LiveConnectionState {
  optOut: boolean;
  /** Community refused the live token and the relay gave up (until Relancer). */
  authError: LiveAuthError | null;
  /** Community clock minus ours, seconds, when known. */
  clockSkewS: number | null;
  /** Community opt-out or a given-up token refusal. */
  banner: LiveBannerKind | null;
}

const INITIAL_CONNECTION: LiveConnectionState = {
  optOut: false,
  authError: null,
  clockSkewS: null,
  banner: null,
};

const listeners = new Set<() => void>();

let packets: CommunityPacket[] = [];
let connection: LiveConnectionState = INITIAL_CONNECTION;

function emit(): void {
  for (const listener of listeners) listener();
}

function subscribe(listener: () => void): () => void {
  listeners.add(listener);
  return () => {
    listeners.delete(listener);
  };
}

function liveBanner(optOut: boolean, authError: LiveAuthError | null): LiveBannerKind | null {
  if (optOut) return 'opt_out';
  if (authError) return 'auth_rejected';
  return null;
}

function normalizeAuthError(value: unknown): LiveAuthError | null {
  return value === 'clock_skew' || value === 'token_rejected' ? value : null;
}

function sameConnection(a: LiveConnectionState, b: LiveConnectionState): boolean {
  return (
    a.optOut === b.optOut &&
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

export function applyLiveStatus(status: Partial<CommunityLiveStatus> | null | undefined): void {
  const optOut = status?.opted_out === true;
  // An older relay has no auth_error: treat auth_rejected as a generic refusal.
  const authError = optOut
    ? null
    : (normalizeAuthError(status?.auth_error) ??
      (status?.state === 'auth_rejected' ? 'token_rejected' : null));
  const clockSkewS =
    authError && typeof status?.clock_skew_s === 'number' ? Math.round(status.clock_skew_s) : null;
  setConnection({ optOut, authError, clockSkewS, banner: liveBanner(optOut, authError) });
}

export function resetLivePacketStore(): void {
  packets = [];
  connection = INITIAL_CONNECTION;
  emit();
}

export function useCommunityPackets(): CommunityPacket[] {
  return useSyncExternalStore(subscribe, getCommunityPackets);
}

export function useLiveConnectionState(): LiveConnectionState {
  return useSyncExternalStore(subscribe, getLiveConnectionState);
}
