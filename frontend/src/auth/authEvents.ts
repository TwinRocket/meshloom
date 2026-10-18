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

export interface ForbiddenEvent {
  method: string;
  /** The permission the server says is missing (`params.permission`), when it names one. */
  permission: string | null;
}

type ForbiddenListener = (event: ForbiddenEvent) => void;

const forbiddenListeners = new Set<ForbiddenListener>();

export function onForbidden(listener: ForbiddenListener): () => void {
  forbiddenListeners.add(listener);
  return () => {
    forbiddenListeners.delete(listener);
  };
}

/** A coded 403 (`auth_forbidden`) on an app call: the role may not do that. */
export function reportForbidden(event: ForbiddenEvent): void {
  for (const listener of forbiddenListeners) listener(event);
}

/**
 * Whether a refusal is told to the reader. A refused read (a GET, or anything that needs
 * `read_private`, such as marking a direct message read) is the state of a surface the
 * role does not have, which the interface already hides: never an error. A refused action
 * is something the reader tried to do, so it is told.
 */
export function forbiddenIsToasted({ method, permission }: ForbiddenEvent): boolean {
  if (permission === 'read_private') return false;
  const verb = method.toUpperCase();
  return verb !== 'GET' && verb !== 'HEAD';
}

type PasswordChangeListener = () => void;

const passwordChangeListeners = new Set<PasswordChangeListener>();

export function onPasswordChangeRequired(listener: PasswordChangeListener): () => void {
  passwordChangeListeners.add(listener);
  return () => {
    passwordChangeListeners.delete(listener);
  };
}

/** A 403 `auth_password_change_required`: every route but the change itself is refused. */
export function reportPasswordChangeRequired(): void {
  for (const listener of passwordChangeListeners) listener();
}

/** Dispatched on `window` once the user signed in again: the WebSocket reconnects at once. */
export const AUTH_RESTORED_EVENT = 'meshloom:auth-restored';
