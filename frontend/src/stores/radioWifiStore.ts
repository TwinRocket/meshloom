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
 * dropped. A radio that goes away resets it. When the automatic fetch fails,
 * `loadFailed` says so (the panel then offers Retry).
 */

const PENDING_RELOAD_MS = 3000;
// How long after a Wi-Fi reboot the "switch to TCP" toast may still come.
const REBOOT_WATCH_MS = 5 * 60_000;

let current: RadioWifiState | null = null;
let version = 0;
let radioReady = false;
let loadFailed = false;
let pendingReloadTimer: ReturnType<typeof setTimeout> | null = null;
const listeners = new Set<() => void>();
// A reboot that should end on Wi-Fi was sent from this tab: `armed` until the
// radio goes away (health), then `returning` until the first state with an
// address, which calls the handler (the "switch to TCP" toast). States from
// before the radio went away (a broadcast racing the reboot) never count.
let rebootWatch: 'idle' | 'armed' | 'returning' = 'idle';
let rebootWatchDeadline = 0;
let backWithIpHandler: ((state: RadioWifiState) => void) | null = null;

function notify(): void {
  for (const listener of listeners) listener();
}

function emit(next: RadioWifiState | null): void {
  current = next;
  loadFailed = false;
  version += 1;
  notify();
}

function setLoadFailed(next: boolean): void {
  if (loadFailed === next) return;
  loadFailed = next;
  notify();
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
  if (rebootWatch !== 'returning' || !state || state.check !== 'done') return;
  if (Date.now() > rebootWatchDeadline || state.supported === false || state.enabled === false) {
    rebootWatch = 'idle';
  } else if (state.connected && state.ip) {
    rebootWatch = 'idle';
    backWithIpHandler?.(state);
  }
}

/**
 * A reboot that should bring the radio up on Wi-Fi (new credentials, Wi-Fi
 * turned on) was just sent from this tab. Not for a reboot that turns Wi-Fi off.
 */
export function noteWifiRebootStarted(): void {
  rebootWatch = 'armed';
  rebootWatchDeadline = Date.now() + REBOOT_WATCH_MS;
}

/** Who reacts when the radio is back on Wi-Fi after such a reboot (one handler). */
export function setWifiBackWithIpHandler(handler: ((state: RadioWifiState) => void) | null): void {
  backWithIpHandler = handler;
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
    if (radioReady && version === started && current === null) setLoadFailed(true);
    if (throwOnError) throw err;
    return null;
  }
  // A late answer never brings back a radio that went away, or overwrites a
  // newer state (a refresh broadcasts its own result first).
  if (!radioReady) return null;
  if (version !== started) return null;
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
    if (rebootWatch === 'armed') rebootWatch = 'returning';
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

function getLoadFailedSnapshot(): boolean {
  return loadFailed;
}

/** The radio is ready but its Wi-Fi state could not be fetched. */
export function useRadioWifiLoadFailed(): boolean {
  return useSyncExternalStore(subscribeRadioWifi, getLoadFailedSnapshot, getLoadFailedSnapshot);
}

export function resetRadioWifiStoreForTests(): void {
  cancelPendingReload();
  current = null;
  version = 0;
  radioReady = false;
  loadFailed = false;
  rebootWatch = 'idle';
  rebootWatchDeadline = 0;
  backWithIpHandler = null;
  listeners.clear();
}
