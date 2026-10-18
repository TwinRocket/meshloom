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

function emit(): void {
  for (const listener of listeners) listener();
}

function subscribe(listener: () => void): () => void {
  listeners.add(listener);
  return () => {
    listeners.delete(listener);
  };
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
  metas.set(id, { entry: { state: 'loading' }, at: now });
  emit();
  try {
    const meta = await api.getShareMeta(id);
    metas.set(id, { entry: { state: 'ready', meta }, at: Date.now() });
  } catch {
    metas.set(id, { entry: { state: 'missing' }, at: Date.now() });
  }
  emit();
}

export function getShareMetaEntry(id: string): ShareMetaEntry | null {
  return metas.get(id)?.entry ?? null;
}

/** The entry of one id; re-renders when it changes. */
export function useShareMetaEntry(id: string): ShareMetaEntry | null {
  const read = () => getShareMetaEntry(id);
  return useSyncExternalStore(subscribe, read, read);
}

export function __resetShareMetaStoreForTests(): void {
  status = null;
  statusAt = 0;
  statusPending = null;
  metas.clear();
}
