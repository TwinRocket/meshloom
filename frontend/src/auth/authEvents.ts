/**
 * How `api.ts` tells the session gate that the server no longer knows us.
 *
 * `api.ts` cannot import React state, so a 401 on an app call is published here and
 * `SessionProvider` listens. Calls to `/auth/login` and `/auth/setup` never report:
 * a wrong password is that form's own error, not a lost session.
 */

type Listener = (code: string | null) => void;

const listeners = new Set<Listener>();

export function onAuthRequired(listener: Listener): () => void {
  listeners.add(listener);
  return () => {
    listeners.delete(listener);
  };
}

/** `code` is the API error code of the 401 (`auth_required`, `auth_setup_required`, ...). */
export function reportAuthRequired(code: string | null): void {
  for (const listener of listeners) listener(code);
}

/** Dispatched on `window` once the user signed in again: the WebSocket reconnects at once. */
export const AUTH_RESTORED_EVENT = 'meshloom:auth-restored';
