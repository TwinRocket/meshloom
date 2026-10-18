/** Wire types of `/api/auth/*` (docs/dev/auth.md). Kept here, not in `types.ts`. */

export type AuthRole = 'admin' | 'member' | 'viewer' | 'kiosk';
export type AuthVia = 'lan' | 'ingress' | 'trusted' | 'relay';

export interface AuthUser {
  id: number;
  username: string;
  display_name: string;
  role: AuthRole;
}

/** `GET /auth/session`, and the answer of `POST /auth/login` and `POST /auth/setup`. */
export interface AuthSessionInfo {
  authenticated: boolean;
  setup_required: boolean;
  setup_code_required: boolean;
  methods: { password: boolean; ingress: boolean };
  auth_compat: { basic: boolean };
  user?: AuthUser;
  via?: AuthVia;
  instance?: { name?: string; version: string };
}

export interface AuthSetupRequest {
  username: string;
  password: string;
  display_name?: string;
  setup_code?: string;
}

export interface AuthLoginRequest {
  username: string;
  password: string;
}

/** Error code under `errors.` explaining a login or setup that opened no session. */
export function noSessionReason(session: AuthSessionInfo): string | null {
  return session.authenticated ? null : 'auth_no_session';
}

/**
 * Reached through Home Assistant ingress without a Home Assistant user: there is no
 * password form there (no cookie can keep a session on Home Assistant's origin).
 */
export function ingressOnly(session: AuthSessionInfo): boolean {
  return session.methods.ingress && !session.methods.password;
}

/** The shortest password the backend accepts (`password_policy_violation`). */
export const PASSWORD_MIN_LENGTH = 12;
