import { useSyncExternalStore } from 'react';

import { api } from '../api';
import type { ShareStatus } from '../types';

/**
 * The one source of truth for "is sharing on" in the browser: `GET /api/share/status`,
 * which makes no request to the share service. The settings section reads it and
 * refreshes it after every change of the share settings; later surfaces (composer,
 * message rendering) read the same snapshot. `status` is `null` until something has
 * answered.
 */
export interface ShareStatusState {
  status: ShareStatus | null;
}

const INITIAL: ShareStatusState = { status: null };

let state: ShareStatusState = INITIAL;
let pending: Promise<ShareStatus> | null = null;
const listeners = new Set<() => void>();

function emit(next: ShareStatusState): void {
  state = next;
  for (const listener of listeners) listener();
}

export function subscribeShareStatus(listener: () => void): () => void {
  listeners.add(listener);
  return () => {
    listeners.delete(listener);
  };
}

export function getShareStatusSnapshot(): ShareStatusState {
  return state;
}

/** Store an answer from the backend. */
export function setShareStatus(status: ShareStatus): void {
  if (state.status === status) return;
  emit({ status });
}

/** Ask the backend. Concurrent callers share one request; a failure keeps the last state and rejects. */
export function loadShareStatus(): Promise<ShareStatus> {
  if (pending) return pending;
  pending = api
    .getShareStatus()
    .then((status) => {
      setShareStatus(status);
      return status;
    })
    .finally(() => {
      pending = null;
    });
  return pending;
}

/** Same as `loadShareStatus`, for callers that only need the store updated. */
export function refreshShareStatus(): Promise<ShareStatus | null> {
  return loadShareStatus().catch(() => null);
}

export function useShareStatus(): ShareStatus | null {
  return useSyncExternalStore(subscribeShareStatus, getShareStatusSnapshot, getShareStatusSnapshot)
    .status;
}

export function resetShareStatusStoreForTests(): void {
  pending = null;
  emit(INITIAL);
}
