/**
 * Sign-in with the OpenID Connect provider (`docs/dev/auth.md`, "OpenID Connect").
 *
 * The sign-in is a browser navigation, not a fetch: `oidcStartUrl()` sends the browser to
 * the server, which redirects to the provider and back to `<base>api/auth/oidc/callback`.
 * `base` is the app's own address (`appBase()`, path prefix included behind a reverse
 * proxy under a subpath); the server uses it only when it is the request's origin or a
 * declared public address. The callback returns to the app with one of:
 * - `?oidc_error=<code>`: a refusal, shown by the login page as `errors.<code>`;
 * - `#oidc_2fa=<pending token>&methods=...&expires_in=...`: the account owes its TOTP
 *   code (a fragment: never sent to a server), finished by `SecondFactorStep`;
 * - nothing, with the session cookie set.
 *
 * `takeOidcReturn()` reads that once, removes it from the address bar and the history
 * entry (`replaceState`), and keeps it for the login page (a StrictMode double render
 * reads the same value).
 */
import type { SecondFactorChallenge } from './types';

export interface OidcReturn {
  error?: string;
  challenge?: SecondFactorChallenge;
}

const METHODS = new Set(['totp', 'recovery', 'passkey']);
const CODE = /^[a-z0-9_]{1,64}$/;

let taken: OidcReturn | null | undefined;

/** The app's own address, with its path prefix: where `./api/...` resolves from. */
export function appBase(): string {
  return new URL('./', window.location.href).href;
}

export function oidcStartUrl(remember: boolean): string {
  const query = new URLSearchParams({ remember: remember ? 'true' : 'false', base: appBase() });
  return `./api/auth/oidc/start?${query.toString()}`;
}

/** Leave for the provider (a full navigation: the page is replaced). */
export function startOidcSignIn(remember: boolean): void {
  window.location.assign(oidcStartUrl(remember));
}

export function parseOidcReturn(search: string, hash: string): OidcReturn | null {
  const query = new URLSearchParams(search.startsWith('?') ? search.slice(1) : search);
  const error = query.get('oidc_error');
  if (error !== null) return CODE.test(error) ? { error } : { error: 'auth_oidc_failed' };
  const fragment = new URLSearchParams(hash.startsWith('#') ? hash.slice(1) : hash);
  const token = fragment.get('oidc_2fa');
  if (!token) return null;
  const methods = (fragment.get('methods') ?? '')
    .split(',')
    .filter((m): m is SecondFactorChallenge['methods'][number] => METHODS.has(m));
  const expiresIn = Number(fragment.get('expires_in'));
  return {
    challenge: {
      pending_token: token,
      methods: methods.length ? methods : ['totp', 'recovery'],
      expires_in: Number.isFinite(expiresIn) && expiresIn > 0 ? expiresIn : 300,
    },
  };
}

/** The OpenID Connect return of this page load, once; the URL is cleaned at once. */
export function takeOidcReturn(): OidcReturn | null {
  if (taken !== undefined) return taken;
  taken = parseOidcReturn(window.location.search, window.location.hash);
  if (taken) {
    const query = new URLSearchParams(window.location.search);
    query.delete('oidc_error');
    const rest = query.toString();
    window.history.replaceState(
      window.history.state,
      '',
      `${window.location.pathname}${rest ? `?${rest}` : ''}`
    );
  }
  return taken;
}

/** Tests: forget the value read on this page load. */
export function resetOidcReturn(): void {
  taken = undefined;
}
