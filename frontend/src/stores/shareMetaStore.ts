import { useSyncExternalStore } from 'react';

import { api } from '../api';
import type { ShareMeta, ShareStatus } from '../types';

/**
 * Browser-side memory of the share service, for service links in messages.
 *
 * Nothing here runs on render. `loadShareStatus()` reads `GET /api/share/status`,
 * a local route that makes no request to the share service; it tells whether
 * sharing is on and which hosts are service hosts. `requestShareMeta(id)` is
 * called on hover or focus only, and does nothing unless sharing is on, so a
 * link received in a channel never costs a request, and with sharing off nothing
 * leaves at all. One request per id: answers (and failures, kept as negative
 * entries) are remembered for ten minutes and concurrent calls share one request.
 */
export const META_TTL_MS = 10 * 60 * 1000;
export const STATUS_TTL_MS = 60 * 1000;
/** Most ids remembered; the least recently used is dropped first. */
export const META_MAX_ENTRIES = 200;

export type ShareMetaEntry =
  { state: 'loading' } | { state: 'ready'; meta: ShareMeta } | { state: 'missing' };

interface Stored {
  entry: ShareMetaEntry;
  at: number;
}

let status: ShareStatus | null = null;
let statusAt = 0;
let statusPending: Promise<ShareStatus | null> | null = null;
const metas = new Map<string, Stored>();
const listeners = new Set<() => void>();
const metaListeners = new Map<string, Set<() => void>>();

/** Wake the status subscribers (`id` omitted) or only those of one link id. */
function emit(id?: string): void {
  const targets = id === undefined ? listeners : metaListeners.get(id);
  if (targets) for (const listener of [...targets]) listener();
}

function subscribe(listener: () => void): () => void {
  listeners.add(listener);
  return () => {
    listeners.delete(listener);
  };
}

function subscribeMeta(id: string, listener: () => void): () => void {
  let set = metaListeners.get(id);
  if (!set) metaListeners.set(id, (set = new Set()));
  set.add(listener);
  return () => {
    set.delete(listener);
    if (set.size === 0 && metaListeners.get(id) === set) metaListeners.delete(id);
  };
}

function remember(id: string, entry: ShareMetaEntry, at: number): void {
  metas.delete(id); // re-insert last: Map order is the LRU order
  metas.set(id, { entry, at });
  for (const key of metas.keys()) {
    if (metas.size <= META_MAX_ENTRIES) break;
    if (metas.get(key)?.entry.state !== 'loading') metas.delete(key);
  }
}

export function getShareStatusSnapshot(): ShareStatus | null {
  return status;
}

/** Read the (local) share status, at most once per `STATUS_TTL_MS`. */
export function loadShareStatus(force = false): Promise<ShareStatus | null> {
  if (statusPending) return statusPending;
  if (!force && status && Date.now() - statusAt < STATUS_TTL_MS) return Promise.resolve(status);
  statusPending = api
    .getShareStatus()
    .then((next) => {
      status = next;
      statusAt = Date.now();
      emit();
      return next;
    })
    .catch(() => {
      // Unknown means off: keep the last answer if any, never assume "on".
      statusAt = Date.now();
      return status;
    })
    .finally(() => {
      statusPending = null;
    });
  return statusPending;
}

export function useShareStatus(): ShareStatus | null {
  return useSyncExternalStore(subscribe, getShareStatusSnapshot, getShareStatusSnapshot);
}

/** Fetch the meta of a link once; call from hover/focus only, never from render. */
export async function requestShareMeta(id: string): Promise<void> {
  const current =
    status && Date.now() - statusAt < STATUS_TTL_MS ? status : await loadShareStatus();
  if (!current?.enabled) return;
  const now = Date.now();
  const stored = metas.get(id);
  if (stored && (stored.entry.state === 'loading' || now - stored.at < META_TTL_MS)) return;
  remember(id, { state: 'loading' }, now);
  emit(id);
  try {
    const meta = await api.getShareMeta(id);
    remember(id, { state: 'ready', meta }, Date.now());
  } catch {
    remember(id, { state: 'missing' }, Date.now());
  }
  emit(id);
}

export function getShareMetaEntry(id: string): ShareMetaEntry | null {
  return metas.get(id)?.entry ?? null;
}

/** The entry of one id; re-renders when it changes. */
export function useShareMetaEntry(id: string | null): ShareMetaEntry | null {
  const read = () => (id ? getShareMetaEntry(id) : null);
  const sub = (listener: () => void) => (id ? subscribeMeta(id, listener) : () => {});
  return useSyncExternalStore(sub, read, read);
}

export function __resetShareMetaStoreForTests(): void {
  status = null;
  statusAt = 0;
  statusPending = null;
  metas.clear();
  metaListeners.clear();
}
