/**
 * Remembers that a repeater dashboard was opened past its login form.
 *
 * The dashboard state lives in an in-memory cache, so reloading the page dropped
 * straight back to the login form even though nothing about the repeater had
 * changed. This only records "do not ask again for a while" — it is not a claim
 * that the repeater still honours the session. Nothing here sends radio traffic:
 * the next pane fetch is what reveals whether the session is still good, and its
 * error surfaces in the pane as it always did.
 */

const STORAGE_KEY_PREFIX = 'meshloom-repeater-session';
/** How long to skip the login form before asking again. */
const TTL_MS = 12 * 60 * 60 * 1000;

function storageKey(publicKey: string): string {
  return `${STORAGE_KEY_PREFIX}:${publicKey}`;
}

export function rememberRepeaterSession(publicKey: string | null): void {
  if (!publicKey) return;
  try {
    localStorage.setItem(storageKey(publicKey), String(Date.now()));
  } catch {
    // Storage can be unavailable (private mode); the dashboard still works.
  }
}

export function hasRememberedRepeaterSession(publicKey: string | null): boolean {
  if (!publicKey) return false;
  try {
    const raw = localStorage.getItem(storageKey(publicKey));
    if (!raw) return false;
    const at = Number.parseInt(raw, 10);
    if (!Number.isFinite(at)) return false;
    if (Date.now() - at > TTL_MS) {
      localStorage.removeItem(storageKey(publicKey));
      return false;
    }
    return true;
  } catch {
    return false;
  }
}

export function forgetRepeaterSession(publicKey: string | null): void {
  if (!publicKey) return;
  try {
    localStorage.removeItem(storageKey(publicKey));
  } catch {
    // Nothing to do.
  }
}
