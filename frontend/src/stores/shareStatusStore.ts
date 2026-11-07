import { useSyncExternalStore } from 'react';

import { api } from '../api';
import type { ShareStatus } from '../types';

/**
 * The one source of truth for "is sharing on" in the browser: `GET /api/share/status`,
 * which makes no request to the share service. The settings section reads it and
 * refreshes it after every change of the share settings; later surfaces (composer,
 * message rendering) read the same snapshot. `status` is `null` until something has
 * answered. `error` is true when the last request failed or timed out (after
 * `STATUS_TIMEOUT_MS`), so a surface never waits forever; the next success clears it.
 */
export interface ShareStatusState {
  status: ShareStatus | null;
  error: boolean;
}

/** A status request that has not answered by then is aborted and counts as a failure. */
export const STATUS_TIMEOUT_MS = 10 * 1000;

const INITIAL: ShareStatusState = { status: null, error: false };

let state: ShareStatusState = INITIAL;
let pending: Promise<ShareStatus> | null = null;
/** Bumped by every stored answer, so a failing request can tell whether a newer status landed meanwhile. */
let version = 0;
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
  version += 1;
  if (state.status === status && !state.error) return;
  emit({ status, error: false });
}

/** Ask the backend. Concurrent callers share one request; a failure or a timeout keeps the last status, sets `error` and rejects. */
export function loadShareStatus(): Promise<ShareStatus> {
  if (pending) return pending;
  const started = version;
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), STATUS_TIMEOUT_MS);
  pending = api
    .getShareStatus(controller.signal)
    .then((status) => {
      setShareStatus(status);
      return status;
    })
    .catch((err: unknown) => {
      // Only the latest request may flag an error: a status stored while this one
      // was in flight is newer and already decides what the surfaces show.
      if (version === started && !state.error) emit({ status: state.status, error: true });
      throw err;
    })
    .finally(() => {
      clearTimeout(timer);
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

/** True when the last status request failed or timed out. */
export function useShareStatusError(): boolean {
  return useSyncExternalStore(subscribeShareStatus, getShareStatusSnapshot, getShareStatusSnapshot)
    .error;
}

export function resetShareStatusStoreForTests(): void {
  pending = null;
  version += 1;
  emit(INITIAL);
}
