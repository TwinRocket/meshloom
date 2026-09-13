/**
 * Keeps the last values a repeater gave us across page reloads.
 *
 * The dashboard cache is an in-memory Map, so reloading emptied every pane back to
 * "not fetched" and the only way to see anything again was to query the repeater
 * over the air — for values like node info, radio settings or regions that had not
 * changed. This stores the last answer per repeater so the dashboard opens with
 * what it already knew. Nothing here talks to the radio, and each pane keeps
 * showing its own `fetched_at`, so how old the values are stays visible.
 *
 * The console history is not stored here — it already has its own localStorage
 * entry in the dashboard hook.
 *
 * A durable, server-side cache would be better still — the backend already does
 * exactly that for telemetry (`repeater_telemetry_history`) — but that is a
 * backend change, not a frontend one.
 */

const STORAGE_KEY_PREFIX = 'meshloom-repeater-dashboard';
/** Values older than this are dropped rather than shown as if they were current. */
const TTL_MS = 24 * 60 * 60 * 1000;

interface PersistedEntry<TPaneData, TPaneStates> {
  savedAt: number;
  loggedIn: boolean;
  paneData: TPaneData;
  paneStates: TPaneStates;
}

function storageKey(publicKey: string): string {
  return `${STORAGE_KEY_PREFIX}:${publicKey}`;
}

export function loadPersistedDashboard<TPaneData, TPaneStates>(
  publicKey: string | null
): { loggedIn: boolean; paneData: TPaneData; paneStates: TPaneStates } | null {
  if (!publicKey) return null;
  try {
    const raw = localStorage.getItem(storageKey(publicKey));
    if (!raw) return null;
    const parsed = JSON.parse(raw) as PersistedEntry<TPaneData, TPaneStates>;
    if (!parsed || typeof parsed.savedAt !== 'number') return null;
    if (Date.now() - parsed.savedAt > TTL_MS) {
      localStorage.removeItem(storageKey(publicKey));
      return null;
    }
    if (!parsed.paneData || !parsed.paneStates) return null;
    return {
      loggedIn: parsed.loggedIn === true,
      paneData: parsed.paneData,
      paneStates: parsed.paneStates,
    };
  } catch {
    return null;
  }
}

export function persistDashboard<TPaneData, TPaneStates>(
  publicKey: string | null,
  entry: { loggedIn: boolean; paneData: TPaneData; paneStates: TPaneStates }
): void {
  if (!publicKey) return;
  try {
    const payload: PersistedEntry<TPaneData, TPaneStates> = {
      savedAt: Date.now(),
      loggedIn: entry.loggedIn,
      paneData: entry.paneData,
      paneStates: entry.paneStates,
    };
    localStorage.setItem(storageKey(publicKey), JSON.stringify(payload));
  } catch {
    // Quota or private mode: the in-memory cache still works for this session.
  }
}

export function forgetPersistedDashboard(publicKey: string | null): void {
  if (!publicKey) return;
  try {
    localStorage.removeItem(storageKey(publicKey));
  } catch {
    // Nothing to do.
  }
}
