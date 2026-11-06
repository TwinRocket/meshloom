// A code or locale file can fail to load for two reasons that a reload fixes: after an update the
// old hashed /assets files 404, and the Remote relay refuses a request while too many are open
// (503 relay_busy). So wait, reload, and count the attempts so a lasting failure cannot loop.
// The module-script failures, when none of this code can run, are in the boot guard of index.html,
// which shares the count.

const COUNT_KEY = 'meshloom-boot-reloads';
const DELAYS_MS = [1000, 2000, 4000];

let pending = false;

function storage(): Storage | null {
  try {
    return window.sessionStorage;
  } catch {
    return null;
  }
}

/**
 * Reload after a delay that grows with each attempt of this session. Returns false once the
 * attempts are used up. Failures while a reload is already waiting count as the same attempt.
 */
export function scheduleReload(reload: () => void = () => window.location.reload()): boolean {
  if (pending) return true;
  const store = storage();
  let attempt: number;
  try {
    attempt = Number(store?.getItem(COUNT_KEY) ?? 0) || 0;
    if (attempt >= DELAYS_MS.length) return false;
    store?.setItem(COUNT_KEY, String(attempt + 1));
  } catch {
    return false;
  }
  pending = true;
  window.setTimeout(reload, DELAYS_MS[attempt]);
  return true;
}

/** Call after a successful boot so a later failure gets its attempts again. */
export function clearReloadFlag(): void {
  try {
    storage()?.removeItem(COUNT_KEY);
  } catch {
    // ignore
  }
}

/** Tests only: forget the reload waiting in this page. */
export function resetPendingReload(): void {
  pending = false;
}

export function installPreloadErrorHandler(target: Window = window): void {
  target.addEventListener('vite:preloadError', (event) => {
    if (scheduleReload()) event.preventDefault();
  });
}
