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
 * every Community settings change.
 *
 * `enabled` is `null` until something has answered. App settings seed it from
 * `directory_available` (the backend computes it from the same flag) so routing
 * can decide on startup; `GET /api/community` then replaces the seed.
 */
export interface CommunityStoreState {
  status: CommunityStatus | null;
  enabled: boolean | null;
}

const INITIAL: CommunityStoreState = { status: null, enabled: null };

let state: CommunityStoreState = INITIAL;
let pending: Promise<CommunityStatus | null> | null = null;
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

/** Store an answer from the backend (GET or PATCH /api/community). */
export function setCommunityStatus(status: CommunityStatus): void {
  if (state.status === status) return;
  emit({ status, enabled: status.enabled });
}

/** Startup hint from app settings. Ignored once a real status is known. */
export function seedCommunityEnabled(enabled: boolean): void {
  if (state.enabled !== null) return;
  emit({ ...state, enabled });
}

/** Ask the backend. Concurrent callers share one request; failures keep the last state. */
export function refreshCommunityStatus(): Promise<CommunityStatus | null> {
  if (pending) return pending;
  pending = Promise.resolve(api.getCommunity?.())
    .then(
      (status) => {
        if (status) setCommunityStatus(status);
        return status ?? null;
      },
      () => null
    )
    .finally(() => {
      pending = null;
    });
  return pending;
}

/**
 * A `community_live` status arrived. Its `opted_out` flag follows the Community
 * switch, so a mismatch means another tab (or the API) changed it: re-read.
 */
export function noteCommunityLiveOptedOut(optedOut: boolean | undefined): void {
  if (typeof optedOut !== 'boolean' || state.enabled === null) return;
  if (state.enabled === !optedOut) return;
  void refreshCommunityStatus();
}

export function useCommunityState(): CommunityStoreState {
  return useSyncExternalStore(subscribeCommunity, getCommunitySnapshot, getCommunitySnapshot);
}

export function useCommunityStatus(): CommunityStatus | null {
  return useCommunityState().status;
}

/** `true`/`false` once known, `null` before anything answered. */
export function useCommunityEnabled(): boolean | null {
  return useCommunityState().enabled;
}

export function resetCommunityStoreForTests(): void {
  pending = null;
  emit(INITIAL);
}
