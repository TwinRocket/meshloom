/**
 * Noticing that an authentication proxy in front of Meshloom has logged us out.
 *
 * Cloudflare Access, oauth2-proxy and their kind answer an expired session with a
 * redirect to a login page on another origin. A page that is already open never
 * sees that page: a GET follows the redirect and gets HTML where JSON was
 * expected, and a POST carrying a JSON content type is refused by CORS before it
 * arrives anywhere. Either way the app keeps running with every call failing, and
 * nothing in it can log the reader back in, because only a top-level navigation
 * makes the proxy run its redirect.
 */

const PROBE_PATH = './api/health';

type Listener = () => void;

const listeners = new Set<Listener>();
let alreadyReported = false;

/** Called when the proxy has started refusing us. Fires once per page. */
export function onEdgeSessionExpired(listener: Listener): () => void {
  listeners.add(listener);
  return () => {
    listeners.delete(listener);
  };
}

export function reportEdgeSessionExpired(): void {
  if (alreadyReported) return;
  alreadyReported = true;
  for (const listener of listeners) listener();
}

/** Test seam: a page only ever expires once, but a test file runs many. */
export function resetEdgeSessionReport(): void {
  alreadyReported = false;
}

/**
 * A response that came back from somewhere else than where it was sent.
 *
 * `fetch` follows the proxy's redirect for us and reports where it ended up, so a
 * different origin in `res.url` is the login page and nothing else.
 */
export function isEdgeRedirect(res: Response): boolean {
  if (!res.redirected) return false;
  try {
    return new URL(res.url).origin !== window.location.origin;
  } catch {
    return false;
  }
}

/**
 * Ask the server a question that cannot be confused with a network failure.
 *
 * A request that CORS refused throws exactly like an unreachable server, so the
 * failure alone says nothing. `redirect: 'manual'` makes the proxy's redirect
 * visible instead of followed: an opaque redirect is the proxy, and a thrown
 * error is a server that is genuinely not answering.
 */
export async function edgeSessionLost(): Promise<boolean> {
  try {
    const res = await fetch(PROBE_PATH, { redirect: 'manual', cache: 'no-store' });
    return res.type === 'opaqueredirect';
  } catch {
    return false;
  }
}

/** Reauthenticate by navigating, which is the only thing the proxy reacts to. */
export function reauthenticate(): void {
  window.location.assign(window.location.href);
}
