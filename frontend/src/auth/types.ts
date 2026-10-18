/** Wire types of `/api/auth/*` (docs/dev/auth.md). Kept here, not in `types.ts`. */

export type AuthRole = 'admin' | 'member' | 'viewer' | 'kiosk';
export type AuthVia = 'lan' | 'ingress' | 'trusted' | 'relay';

export interface AuthUser {
  id: number;
  username: string;
  display_name: string;
  role: AuthRole;
  /** An administrator set the password: nothing else works until the user changes it. */
  must_change_password?: boolean;
  /** The account's TOTP second factor is on. */
  totp_enabled?: boolean;
  /** `require_2fa_for_admins`: nothing but the 2FA enrolment routes work until it is set up. */
  second_factor_enrolment_required?: boolean;
}

/** `POST /auth/login` for an account with a second factor: no session yet. */
export interface SecondFactorChallenge {
  /** Sent back with a code to `POST /auth/login/2fa`. */
  pending_token: string;
  methods: ('totp' | 'recovery')[];
  /** Seconds before the password has to be entered again. */
  expires_in: number;
}

/** `GET /auth/session`, and the answer of `POST /auth/login` and `POST /auth/setup`. */
export interface AuthSessionInfo {
  authenticated: boolean;
  setup_required: boolean;
  setup_code_required: boolean;
  methods: { password: boolean; ingress: boolean };
  auth_compat: { basic: boolean };
  /** Whether the server saw the request as https (a trusted proxy's `X-Forwarded-Proto`). */
  secure?: boolean;
  /** The installation code, only for the kiosk screen (a direct loopback request, setup open). */
  kiosk_setup_code?: string;
  user?: AuthUser;
  via?: AuthVia;
  instance?: { name?: string; version: string };
  /** `POST /auth/login` only: the password checked, a second factor is owed. */
  second_factor?: SecondFactorChallenge;
}

/** `GET/PATCH /auth/settings` (admin): the access settings. */
export interface AuthSettings {
  /** Reverse proxies whose forwarded headers are believed, canonical CIDRs. Empty trusts none. */
  trusted_proxies: string[];
  /** Audit rows older than this many days are purged (366 by default). */
  audit_retention_days: number;
  /** Role of a Home Assistant user seen for the first time (the very first is always admin). */
  ingress_default_role: AuthRole;
  /** Networks whose direct requests act as an account without a login. Empty by default. */
  trusted_networks?: TrustedNetworkRule[];
  /** The Access security notice, computed by the server (read only). */
  security_notices?: AccessSecurityNotice[];
  /** Declared public addresses, canonical `https://host[:port][/prefix]`. Their hosts pass the Host guard. */
  public_urls?: string[];
  /** `observe` records unknown Host names and lets them through; `enforce` answers 421. */
  host_guard_mode?: HostGuardMode;
  /** Host names the guard would refuse, since the last restart, newest first (read only). */
  rejected_hosts?: RejectedHost[];
  /** Admins with a password and no second factor reach only the 2FA enrolment routes. */
  require_2fa_for_admins?: boolean;
}

export type HostGuardMode = 'observe' | 'enforce';

export interface RejectedHost {
  host: string;
  count: number;
  /** Unix seconds. */
  first_seen: number;
  last_seen: number;
}

/** `cidr` → `user_id`: requests from that network act as that account (never through a proxy). */
export interface TrustedNetworkRule {
  cidr: string;
  user_id: number;
}

export type AccessSecurityNotice =
  'trusted_admin_reduced_plugin_isolation' | 'trusted_loopback_or_gateway_public';

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

/**
 * The browser is on https but the server saw http: a TLS proxy Meshloom does not
 * trust (Settings → Local Configuration → Trusted proxies). The session cookie is then
 * the plain one, without `Secure`. An older server that sends no `secure` says nothing.
 */
export function behindUntrustedHttpsProxy(
  session: Pick<AuthSessionInfo, 'secure'> | null | undefined,
  protocol: string = window.location.protocol
): boolean {
  return protocol === 'https:' && session?.secure === false;
}

/** The shortest password the backend accepts (`password_policy_violation`). */
export const PASSWORD_MIN_LENGTH = 12;

/** `GET /auth/me`: the caller's own account. */
export interface AuthMe {
  id: number;
  username: string;
  display_name: string;
  role: AuthRole;
  provider: string;
  has_password: boolean;
  must_change_password: boolean;
  totp_enabled?: boolean;
  created_at: number;
}

/** One row of `GET /auth/sessions` (the caller's own; times in epoch seconds). */
export interface AuthSessionRow {
  id: string;
  created_at: number;
  last_seen_at: number;
  expires_at: number;
  ip: string | null;
  user_agent: string | null;
  via: string | null;
  current: boolean;
}

/** One row of `GET /auth/users` (admin). `provider` is `local` or `ingress`. */
export interface AuthUserRow {
  id: number;
  username: string;
  display_name: string;
  role: AuthRole;
  provider: string;
  disabled: boolean;
  must_change_password: boolean;
  has_password: boolean;
  totp_enabled: boolean;
  created_at: number;
  updated_at: number;
}

export interface AuthUserCreate {
  username: string;
  role: AuthRole;
  display_name?: string;
  /** Required for every role but `kiosk`. */
  password?: string;
  must_change_password?: boolean;
}

export interface AuthUserPatch {
  display_name?: string;
  role?: AuthRole;
  disabled?: boolean;
  must_change_password?: boolean;
}

/** One row of `GET /auth/audit` (admin; `ts` in epoch seconds). `details` is already secret-filtered server-side. */
export interface AuthAuditEntry {
  id: number;
  ts: number;
  actor_user_id: number | null;
  actor_token_id: string | null;
  action: string;
  target: string | null;
  ip: string | null;
  via: string | null;
  outcome: string;
  details: Record<string, unknown>;
}

/** `GET /auth/audit`: newest first; `next_cursor` is opaque, null on the last page. */
export interface AuthAuditPage {
  entries: AuthAuditEntry[];
  next_cursor: string | null;
}
