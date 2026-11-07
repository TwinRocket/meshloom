// A code or locale file can fail to load for two reasons that a reload fixes: after an update the
// old hashed /assets files 404, and the Remote relay refuses a request while too many are open
// (503 relay_busy). So wait, reload, and count the attempts: 3 reloads at most, and the count only
// starts over a minute after the last one. A boot does not clear it, since a file that stays
// refused fails again after every boot. That makes one reload a minute the worst lasting rate.
// Without sessionStorage nothing can be counted, so nothing reloads by itself.
// What fails when none of this code runs is in the boot guard of index.html: same rule, same key.

const COUNT_KEY = 'meshloom-boot-reloads';
const DELAYS_MS = [1000, 2000, 4000];
const CALM_MS = 60_000;

let pending = false;

/**
 * Reload after a delay that grows with each attempt. Returns false when no reload will happen, and
 * the caller shows its error. Failures while a reload is already waiting are the same attempt.
 */
export function scheduleReload(reload: () => void = () => window.location.reload()): boolean {
  if (pending) return true;
  let attempt: number;
  try {
    const store = window.sessionStorage;
    const [count, at] = (store.getItem(COUNT_KEY) ?? '').split(':');
    attempt = Date.now() - Number(at) > CALM_MS ? 0 : Number(count) || 0;
    if (attempt >= DELAYS_MS.length) return false;
    store.setItem(COUNT_KEY, `${attempt + 1}:${Date.now()}`);
  } catch {
    return false;
  }
  pending = true;
  window.setTimeout(reload, DELAYS_MS[attempt]);
  return true;
}

/** Tests only: forget the reload waiting in this page. */
export function resetPendingReload(): void {
  pending = false;
}

let optionalLoads = 0;

/**
 * Run a lazy import the page can do without: a failure gives null and never reloads, which would
 * throw away what the user is working on. A required chunk failing meanwhile gets no reload either,
 * and shows its own error.
 */
export async function loadOptional<T>(load: () => Promise<T>): Promise<T | null> {
  optionalLoads += 1;
  try {
    return await load();
  } catch {
    return null;
  } finally {
    optionalLoads -= 1;
  }
}

export function installPreloadErrorHandler(target: Window = window): void {
  target.addEventListener('vite:preloadError', (event) => {
    if (optionalLoads === 0 && scheduleReload()) event.preventDefault();
  });
}
