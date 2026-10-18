/**
 * The audit vocabulary as the viewer knows it (`AuditAction`, `AuditOutcome`, `AuditVia`
 * in `app/auth/audit.py`). A name the server adds later still shows, as its raw key.
 */
export const AUDIT_FAMILIES = {
  signin: ['setup', 'login', 'logout', 'session.revoked'],
  accounts: [
    'user.created',
    'user.changed',
    'user.disabled',
    'user.enabled',
    'user.deleted',
    'users.listed',
    'password.changed',
    'setup_code.issued',
    'settings.changed',
  ],
  devices: [
    'token.created',
    'token.revoked',
    'token.used',
    'pairing.used',
    'totp.enrolled',
    'totp.disabled',
    'passkey.added',
    'passkey.removed',
  ],
  code: [
    'bot.created',
    'bot.changed',
    'bot.deleted',
    'bots.disabled',
    'plugin.created',
    'plugin.changed',
    'plugin.disabled',
    'plugin.installed',
    'plugin.updated',
    'plugin.removed',
  ],
  instance: ['backup.restored', 'private_key.imported', 'factory_reset', 'update.triggered'],
  remote: ['remote.linked', 'remote.enabled', 'remote.disabled'],
} as const;

export type AuditFamily = keyof typeof AUDIT_FAMILIES;

export const AUDIT_FAMILY_IDS = Object.keys(AUDIT_FAMILIES) as AuditFamily[];
export const AUDIT_OUTCOMES = ['success', 'failure', 'refused', 'delayed', 'error'] as const;
const KNOWN_VIA = new Set(['lan', 'ingress', 'trusted', 'relay', 'cli', 'system']);
const KNOWN_ACTIONS = new Set<string>(AUDIT_FAMILY_IDS.flatMap((f) => AUDIT_FAMILIES[f]));

export const isKnownAuditAction = (action: string) => KNOWN_ACTIONS.has(action);
export const isKnownAuditOutcome = (outcome: string) =>
  (AUDIT_OUTCOMES as readonly string[]).includes(outcome);
export const isKnownAuditVia = (via: string) => KNOWN_VIA.has(via);

export function auditFamilyOf(action: string): AuditFamily | null {
  return (
    AUDIT_FAMILY_IDS.find((f) => (AUDIT_FAMILIES[f] as readonly string[]).includes(action)) ?? null
  );
}

/** `user:7` -> 7; any other target (a setting name, a bot) -> null. */
export function userIdOfTarget(target: string | null): number | null {
  const match = target ? /^user:(\d+)$/.exec(target) : null;
  return match ? Number(match[1]) : null;
}

export interface AuditFilters {
  family: AuditFamily | '';
  action: string;
  outcome: string;
  /** `''` all, `'anonymous'` no account, else a user id. */
  actor: string;
  /** `YYYY-MM-DD` in local time, inclusive. */
  from: string;
  to: string;
}

export const EMPTY_AUDIT_FILTERS: AuditFilters = {
  family: '',
  action: '',
  outcome: '',
  actor: '',
  from: '',
  to: '',
};

function localDayStart(day: string): number {
  const [y, m, d] = day.split('-').map(Number);
  return new Date(y, m - 1, d).getTime() / 1000;
}

export function matchesAuditFilters(
  entry: {
    ts: number;
    action: string;
    outcome: string;
    actor_user_id: number | null;
    actor_token_id: string | null;
  },
  f: AuditFilters
): boolean {
  if (f.family && auditFamilyOf(entry.action) !== f.family) return false;
  if (f.action && entry.action !== f.action) return false;
  if (f.outcome && entry.outcome !== f.outcome) return false;
  if (f.actor === 'anonymous') {
    if (entry.actor_user_id !== null || entry.actor_token_id !== null) return false;
  } else if (f.actor && entry.actor_user_id !== Number(f.actor)) return false;
  if (f.from && entry.ts < localDayStart(f.from)) return false;
  if (f.to && entry.ts >= localDayStart(f.to) + 86400) return false;
  return true;
}
