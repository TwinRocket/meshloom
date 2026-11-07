import { useEffect, useSyncExternalStore } from 'react';

import { api } from '../api';

/**
 * How many installed plugins have a newer release cached by their source
 * (`GET /api/store/updates`, which makes no request). The Settings index reads it for the
 * badge of the Extensions row; the Updates tab of Settings > Extensions writes it after
 * each load and action, so the badge never lags behind what the tab shows.
 */
let count = 0;
const listeners = new Set<() => void>();

export function setPluginUpdateCount(next: number): void {
  if (next === count) return;
  count = next;
  for (const listener of listeners) listener();
}

export function getPluginUpdateCount(): number {
  return count;
}

function subscribe(listener: () => void): () => void {
  listeners.add(listener);
  return () => {
    listeners.delete(listener);
  };
}

export function usePluginUpdateCount(): number {
  return useSyncExternalStore(subscribe, getPluginUpdateCount, getPluginUpdateCount);
}

/** Re-read the count (administrators only: the route is admin-only). Failures keep the last value. */
export async function refreshPluginUpdateCount(): Promise<void> {
  try {
    setPluginUpdateCount((await api.getStoreUpdates('en')).length);
  } catch {
    /* the badge is a hint: a failed read leaves it as it was */
  }
}

/**
 * Keeps the badge fresh for an administrator: once at mount, then whenever `signature`
 * (the plugin statuses of the health snapshot) moves, and every ten minutes (the Store
 * refresh runs every six hours, so a quiet tab only needs a slow poll).
 */
export function usePluginUpdateBadge(enabled: boolean, signature: string): void {
  useEffect(() => {
    if (!enabled) {
      setPluginUpdateCount(0);
      return;
    }
    void refreshPluginUpdateCount();
    const timer = window.setInterval(() => void refreshPluginUpdateCount(), 10 * 60 * 1000);
    return () => window.clearInterval(timer);
  }, [enabled, signature]);
}

/**
 * A link from another Settings section (Meshloom's Updates) to a tab of Extensions: the
 * section reads it once when it mounts, so the tab opens even though it is not mounted yet.
 */
let pendingTab: 'installed' | 'store' | 'updates' | null = null;

export function requestExtensionsTab(tab: 'installed' | 'store' | 'updates'): void {
  pendingTab = tab;
}

export function takeRequestedExtensionsTab(): 'installed' | 'store' | 'updates' | null {
  const tab = pendingTab;
  pendingTab = null;
  return tab;
}
