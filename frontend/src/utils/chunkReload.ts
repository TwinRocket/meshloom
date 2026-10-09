// After an update the old hashed /assets files 404, so a lazy chunk (or locale) import from a
// stale page fails. One guarded reload fetches the fresh index.html; the flag stops loops.

const FLAG = 'meshloom-chunk-reload';

function storage(): Storage | null {
  try {
    return window.sessionStorage;
  } catch {
    return null;
  }
}

/** Reload once per session. Returns false when a reload was already attempted. */
export function reloadOnce(reload: () => void = () => window.location.reload()): boolean {
  const store = storage();
  try {
    if (store?.getItem(FLAG)) return false;
    store?.setItem(FLAG, String(Date.now()));
  } catch {
    return false;
  }
  reload();
  return true;
}

/** Call after a successful boot so a later update can trigger a reload again. */
export function clearReloadFlag(): void {
  try {
    storage()?.removeItem(FLAG);
  } catch {
    // ignore
  }
}

export function installPreloadErrorHandler(target: Window = window): void {
  target.addEventListener('vite:preloadError', (event) => {
    if (reloadOnce()) event.preventDefault();
  });
}
