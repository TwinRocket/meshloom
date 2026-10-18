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

/** The shortest password the backend accepts (`password_policy_violation`). */
export const PASSWORD_MIN_LENGTH = 12;
