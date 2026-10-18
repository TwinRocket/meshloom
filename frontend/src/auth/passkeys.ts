import type { TFunction } from 'i18next';

import { api } from '../api';
import { formatAccessError } from './accessErrors';
import type { AuthSessionInfo } from './types';

/**
 * Passkeys in the browser. `@simplewebauthn/browser` is imported dynamically, here and
 * nowhere else, so it stays in its own chunk and the entry chunk does not grow
 * (frontend/AGENTS.md, "Lazy loading"). Do not import it statically.
 */
const loadWebAuthn = () => import('@simplewebauthn/browser');

/**
 * The same test as the library's `browserSupportsWebAuthn()`, written out so the login page
 * can decide whether to show its button without loading the library: a secure context with
 * `PublicKeyCredential`.
 */
export function passkeysSupported(): boolean {
  return (
    typeof window !== 'undefined' &&
    window.isSecureContext === true &&
    typeof window.PublicKeyCredential === 'function'
  );
}

/** The sign-in button shows only when the server offers passkeys here and the browser can run them. */
export function passkeyLoginAvailable(
  session: Pick<AuthSessionInfo, 'methods'> | null | undefined
) {
  return session?.methods.passkey === true && passkeysSupported();
}

/** Passwordless: the device picks a discoverable passkey and opens the session. */
export async function signInWithPasskey(): Promise<AuthSessionInfo> {
  const optionsJSON = await api.passkeyLoginOptions();
  const { startAuthentication } = await loadWebAuthn();
  // Cast: the server's options are the library's own JSON type.
  const credential = await startAuthentication({
    optionsJSON: optionsJSON as Parameters<typeof startAuthentication>[0]['optionsJSON'],
  });
  return api.passkeyLoginVerify(credential);
}

/** The second factor of a pending login (its token) answered by one of the account's passkeys. */
export async function passkeySecondFactor(pendingToken: string): Promise<AuthSessionInfo> {
  const optionsJSON = await api.passkeyLoginOptions(pendingToken);
  const { startAuthentication } = await loadWebAuthn();
  const credential = await startAuthentication({
    optionsJSON: optionsJSON as Parameters<typeof startAuthentication>[0]['optionsJSON'],
  });
  return api.authLogin2fa({ pending_token: pendingToken, passkey: credential });
}

/** Creates a passkey: the proofs the server asks, the browser ceremony, then the name. */
export async function registerPasskey(proof: { password: string; code?: string; name: string }) {
  const optionsJSON = await api.passkeyRegisterOptions(proof.password, proof.code);
  const { startRegistration } = await loadWebAuthn();
  const credential = await startRegistration({
    optionsJSON: optionsJSON as Parameters<typeof startRegistration>[0]['optionsJSON'],
  });
  return api.passkeyRegisterVerify(credential, proof.name);
}

/**
 * The `auth.passkey.errors.*` key for a failure of the browser ceremony, or null when the
 * error is not one (an API error: `formatApiError` handles it). The library wraps the
 * DOMException as `cause` under `ERROR_PASSTHROUGH_SEE_CAUSE_PROPERTY`; a cancelled prompt
 * and a timeout are both `NotAllowedError`, which cannot be told apart.
 */
export function passkeyCeremonyErrorKey(err: unknown): string | null {
  if (typeof err !== 'object' || err === null) return null;
  const { name, code, cause } = err as { name?: unknown; code?: unknown; cause?: unknown };
  const causeName =
    typeof cause === 'object' && cause !== null ? (cause as { name?: unknown }).name : undefined;
  if (code === 'ERROR_AUTHENTICATOR_PREVIOUSLY_REGISTERED' || causeName === 'InvalidStateError') {
    return 'alreadyRegistered';
  }
  if (code === 'ERROR_CEREMONY_ABORTED' || name === 'AbortError' || causeName === 'AbortError') {
    return 'cancelled';
  }
  if (name === 'NotAllowedError' || causeName === 'NotAllowedError') return 'notAllowed';
  if (name === 'WebAuthnError' || name === 'NotSupportedError' || causeName === 'NotSupportedError')
    return 'failed';
  return null;
}

/** Text for any failure of a passkey action: the browser ceremony's, else the API's. */
export function formatPasskeyError(err: unknown, t: TFunction): string {
  const key = passkeyCeremonyErrorKey(err);
  return key ? t(`auth.passkey.errors.${key}`) : formatAccessError(err, t);
}
