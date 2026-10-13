import { useSyncExternalStore } from 'react';

import { api } from '../api';
import type { CommunityStatus } from '../types';

/**
 * The one source of truth for "is Community on" in the browser.
 *
 * Everything that depends on Community (the directory, observer reach, the radio
 * test, the Live view, hashtag names) reads this store, so turning Community off
 * in the settings is seen everywhere at once, without a reload. Other tabs follow
 * through the `community_live` WebSocket status, which the backend broadcasts on
 * every Community settings change, and re-read on a WebSocket reconnect, since a
 * change made while the socket was down sends them nothing.
 *
 * `enabled` is `null` until something has answered. App settings seed it from
 * `directory_available` (the backend computes it the same way) so routing
 * can decide on startup; `GET /api/community` then replaces the seed.
 *
 * `enabled` means "Community features may run": on and not waiting for a
 * position (`communityStatusActive`). While Community waits for the radio's
 * position, every surface treats it as off; only the settings read
 * `status.enabled` (the opt-in itself) to offer "Leave" and the IATA form.
 */
export interface CommunityStoreState {
  status: CommunityStatus | null;
  enabled: boolean | null;
}

const INITIAL: CommunityStoreState = { status: null, enabled: null };

let state: CommunityStoreState = INITIAL;
let pending: Promise<CommunityStatus> | null = null;
const listeners = new Set<() => void>();

function emit(next: CommunityStoreState): void {
  state = next;
  for (const listener of listeners) listener();
}

export function subscribeCommunity(listener: () => void): () => void {
  listeners.add(listener);
  return () => {
    listeners.delete(listener);
  };
}

export function getCommunitySnapshot(): CommunityStoreState {
  return state;
}

/** On and not waiting for a position: the Community features may run. */
export function communityStatusActive(status: CommunityStatus | null | undefined): boolean {
  return Boolean(status?.enabled) && status?.state !== 'waiting_for_position';
}

/** On, but the radio has no position Community may publish. */
export function communityWaitingForPosition(status: CommunityStatus | null | undefined): boolean {
  return Boolean(status?.enabled) && status?.state === 'waiting_for_position';
}

/** Store an answer from the backend (GET or PATCH /api/community). */
export function setCommunityStatus(status: CommunityStatus): void {
  if (state.status === status) return;
  emit({ status, enabled: communityStatusActive(status) });
}

/** Startup hint from app settings. Ignored once a real status is known. */
export function seedCommunityEnabled(enabled: boolean): void {
  if (state.enabled !== null) return;
  emit({ ...state, enabled });
}

/**
 * Ask the backend. Concurrent callers share one request. A failure keeps the
 * last state and rejects, so a caller can show why (`formatApiError`).
 */
export function loadCommunityStatus(): Promise<CommunityStatus> {
  if (pending) return pending;
  pending = Promise.resolve(api.getCommunity?.())
    .then((status) => {
      if (!status) throw new Error('No Community status');
      setCommunityStatus(status);
      return status;
    })
    .finally(() => {
      pending = null;
    });
  return pending;
}

/** Same as `loadCommunityStatus`, for callers that only need the store updated. */
export function refreshCommunityStatus(): Promise<CommunityStatus | null> {
  return loadCommunityStatus().catch(() => null);
}

/**
 * A `community_live` status arrived. The backend broadcasts one on every
 * Community settings change (on, off, IATA) and on every change of the
 * waiting-for-position state, so re-read: another tab, the API or the radio may
 * have changed more than the switch. `GET /api/community` is local
 * and concurrent re-reads share one request.
 */
export function noteCommunityLiveOptedOut(optedOut: boolean | undefined): void {
  if (typeof optedOut !== 'boolean' || state.enabled === null) return;
  void refreshCommunityStatus();
}

export function useCommunityState(): CommunityStoreState {
  return useSyncExternalStore(subscribeCommunity, getCommunitySnapshot, getCommunitySnapshot);
}

export function useCommunityStatus(): CommunityStatus | null {
  return useCommunityState().status;
}

/** `true`/`false` once known, `null` before anything answered. False while waiting for a position. */
export function useCommunityEnabled(): boolean | null {
  return useCommunityState().enabled;
}

export function useCommunityWaitingForPosition(): boolean {
  return communityWaitingForPosition(useCommunityState().status);
}

export function resetCommunityStoreForTests(): void {
  pending = null;
  emit(INITIAL);
}
