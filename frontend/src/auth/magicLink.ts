/**
 * Sign-in links (MSHL-426, `docs/dev/auth.md`, "E-mail code and sign-in links").
 *
 * The mail carries `<app address>#magic_link=<token>`: a fragment, so a mail scanner
 * that fetches the link never sends the token to the server. The login page reads it
 * once (`takeMagicLink()`), removes it from the address bar and the history entry
 * (`replaceState`), and posts it to `POST /auth/magic-link/verify`.
 */
const FRAGMENT_KEY = 'magic_link';
const TOKEN = /^[A-Za-z0-9_-]{16,128}$/;

let taken: string | null | undefined;

export function parseMagicLink(hash: string): string | null {
  const fragment = new URLSearchParams(hash.startsWith('#') ? hash.slice(1) : hash);
  const token = fragment.get(FRAGMENT_KEY);
  return token && TOKEN.test(token) ? token : null;
}

/** The sign-in link token of this page load, once; the fragment is cleaned at once. */
export function takeMagicLink(): string | null {
  if (taken !== undefined) return taken;
  taken = parseMagicLink(window.location.hash);
  if (window.location.hash.includes(`${FRAGMENT_KEY}=`)) {
    window.history.replaceState(
      window.history.state,
      '',
      `${window.location.pathname}${window.location.search}`
    );
  }
  return taken;
}

/** Tests: forget the value read on this page load. */
export function resetMagicLink(): void {
  taken = undefined;
}
