import { useSyncExternalStore } from 'react';

import { api } from '../api';
import type { HealthStatus, RadioWifiState } from '../types';

/**
 * Wi-Fi state of the connected radio (companion CLI), shared by the Settings
 * panel and the one-time credentials prompt.
 *
 * The server sends only `health` when a socket connects, so the store fetches
 * `GET /api/radio/wifi` itself: when health first says the radio is connected
 * with setup complete (app load and every reconnect of the radio), once more a
 * little later when the answer is still `pending` (the post-connect check runs
 * in the background), and after a WebSocket reconnect. The `radio_wifi` event
 * keeps it current in between; a fetch answer older than the last event is
 * dropped. A radio that goes away resets it.
 */

const PENDING_RELOAD_MS = 3000;

let current: RadioWifiState | null = null;
let version = 0;
let radioReady = false;
let pendingReloadTimer: ReturnType<typeof setTimeout> | null = null;
const listeners = new Set<() => void>();

function emit(next: RadioWifiState | null): void {
  current = next;
  version += 1;
  for (const listener of listeners) listener();
}

function cancelPendingReload(): void {
  if (pendingReloadTimer !== null) {
    clearTimeout(pendingReloadTimer);
    pendingReloadTimer = null;
  }
}

export function subscribeRadioWifi(listener: () => void): () => void {
  listeners.add(listener);
  return () => {
    listeners.delete(listener);
  };
}

export function getRadioWifiSnapshot(): RadioWifiState | null {
  return current;
}

/** A state from the server (WS event, or a Wi-Fi API answer). */
export function setRadioWifiState(state: RadioWifiState | null): void {
  if (state === current) return;
  if (state && state.check !== 'pending') cancelPendingReload();
  emit(state);
}

/**
 * Fetch the state. `refresh` asks the radio again (the full check when none
 * completed). Resolves with the answer, or null when the request failed or a
 * newer state arrived meanwhile; with `throwOnError` a failure rejects.
 */
export async function loadRadioWifi(
  refresh = false,
  { throwOnError = false }: { throwOnError?: boolean } = {}
): Promise<RadioWifiState | null> {
  const started = version;
  let state: RadioWifiState;
  try {
    state = await api.getRadioWifi(refresh);
  } catch (err) {
    if (throwOnError) throw err;
    return null;
  }
  if (!radioReady && !refresh) return null;
  if (version !== started && !refresh) return null;
  setRadioWifiState(state);
  if (state.check === 'pending' && !refresh && pendingReloadTimer === null) {
    pendingReloadTimer = setTimeout(() => {
      pendingReloadTimer = null;
      if (radioReady) void loadRadioWifi();
    }, PENDING_RELOAD_MS);
  }
  return state;
}

function healthReady(health: HealthStatus | null): boolean {
  if (!health || !health.radio_connected || health.radio_initializing) return false;
  return health.radio_state === undefined || health.radio_state === 'connected';
}

/** Follow health: fetch when the radio becomes connected and set up, reset when it goes. */
export function noteRadioWifiHealth(health: HealthStatus | null): void {
  const ready = healthReady(health);
  const wasReady = radioReady;
  radioReady = ready;
  if (ready && !wasReady) {
    void loadRadioWifi();
  } else if (!ready && wasReady) {
    cancelPendingReload();
    emit(null);
  }
}

/** The WebSocket reconnected: events sent while it was down are lost. */
export function refreshRadioWifiAfterReconnect(): void {
  if (radioReady) void loadRadioWifi();
}

export function useRadioWifiState(): RadioWifiState | null {
  return useSyncExternalStore(subscribeRadioWifi, getRadioWifiSnapshot, getRadioWifiSnapshot);
}

export function resetRadioWifiStoreForTests(): void {
  cancelPendingReload();
  current = null;
  version = 0;
  radioReady = false;
  listeners.clear();
}
