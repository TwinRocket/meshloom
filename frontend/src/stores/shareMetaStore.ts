import { useSyncExternalStore } from 'react';

import { ApiError, api } from '../api';
import type { ShareMeta, ShareStatus } from '../types';

/**
 * Browser-side memory of the share service, for service links in messages.
 *
 * Nothing here runs on render. `loadShareStatus()` reads `GET /api/share/status`,
 * a local route that makes no request to the share service; it tells whether
 * sharing is on and which hosts are service hosts. `requestShareMeta(id)` is
 * called on hover or focus, or by a shared image once the user taps it (or the
 * preview policy lets it load), and does nothing unless sharing is on, so a
 * link received in a channel never costs a request, and with sharing off nothing
 * leaves at all. One request per id: answers (and failures, kept as negative
 * entries) are remembered for ten minutes and concurrent calls share one request.
 * An encrypted file's meta depends on the message it was seen in (the backend
 * decrypts it with that conversation's secret), so it is asked and remembered per
 * `(id, messageId)` (`shareMetaKey`). A request that does not answer within
 * `META_TIMEOUT_MS` is dropped as a failure: a card never waits forever.
 */
export const META_TTL_MS = 10 * 60 * 1000;
export const STATUS_TTL_MS = 60 * 1000;
/** Most ids remembered; the least recently used is dropped first. */
export const META_MAX_ENTRIES = 200;
/** A meta request still unanswered after this is abandoned (entry `missing`). */
export const META_TIMEOUT_MS = 20 * 1000;

/** The cache key of a meta: the id, or the id seen in one message (encrypted files). */
export function shareMetaKey(id: string, messageId?: number): string {
  return messageId ? `${id}@${messageId}` : id;
}

/** `gone`: the service answered 410 (expired or removed), not merely unreachable. */
export type ShareMetaEntry =
  { state: 'loading' } | { state: 'ready'; meta: ShareMeta } | { state: 'missing'; gone?: boolean };

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

/**
 * Fetch the meta of a link once; call from hover/focus or a user's tap (or a shared
 * image the preview policy lets load), never from a plain render. `messageId` (an
 * encrypted file) asks the backend to decrypt it for that message's conversation;
 * the entry is then stored under `shareMetaKey(id, messageId)`.
 */
export async function requestShareMeta(id: string, messageId?: number): Promise<void> {
  const current =
    status && Date.now() - statusAt < STATUS_TTL_MS ? status : await loadShareStatus();
  if (!current?.enabled) return;
  const key = shareMetaKey(id, messageId);
  const now = Date.now();
  const stored = metas.get(key);
  if (stored && (stored.entry.state === 'loading' || now - stored.at < META_TTL_MS)) return;
  remember(key, { state: 'loading' }, now);
  emit(key);
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), META_TIMEOUT_MS);
  try {
    const meta = await api.getShareMeta(id, { messageId, signal: controller.signal });
    remember(key, { state: 'ready', meta }, Date.now());
  } catch (err) {
    const gone = err instanceof ApiError && err.status === 410;
    remember(key, gone ? { state: 'missing', gone } : { state: 'missing' }, Date.now());
  } finally {
    clearTimeout(timer);
  }
  emit(key);
}

/** The entry of a cache key (`shareMetaKey`; a plain id for a link or a public file). */
export function getShareMetaEntry(id: string): ShareMetaEntry | null {
  return metas.get(id)?.entry ?? null;
}

/** The entry of one cache key (`shareMetaKey`); re-renders when it changes. */
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
