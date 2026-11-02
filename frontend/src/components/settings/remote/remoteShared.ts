import type { HealthStatus } from '../../../types';
import { REMOTE_LINK_STATES, type RemoteLinkStatus, type RemoteStatus } from './remoteTypes';

/** `app/services/remote.py` `ENROLL_TOKEN_LIFETIME_S`: an enrolment QR code lives 15 min. */
export const ENROLL_LIFETIME_S = 15 * 60;
/** `app/services/remote.py` `LABEL_MAX`. */
export const REMOTE_LABEL_MAX = 64;
/** The longest piece of a device's user agent shown on screen. */
export const USER_AGENT_SHOWN_MAX = 160;

export type RemoteBadgeTone = 'ok' | 'neutral' | 'warn' | 'danger';

/** What the reader can do about a state; the section draws the matching button. */
export type RemoteBadgeAction = 'enable' | 'restart' | 'rotate' | 'update' | null;

/**
 * The state badge: `off` and `unregistered` come from `state`, every other id is a
 * `link_status` value (`REMOTE_LINK_STATES`). Each id has `settings.remote.badge.<id>`
 * (label) and `settings.remote.badgeHelp.<id>` (what it means and what to do).
 */
export type RemoteBadgeId = 'off' | 'unregistered' | RemoteLinkStatus | 'unknown';

export interface RemoteBadge {
  id: RemoteBadgeId;
  tone: RemoteBadgeTone;
  action: RemoteBadgeAction;
}

const LINK_BADGES: Record<RemoteLinkStatus, Omit<RemoteBadge, 'id'>> = {
  connecting: { tone: 'neutral', action: null },
  connected: { tone: 'ok', action: null },
  // Offline: the client retries on its own with a backoff.
  reconnecting: { tone: 'warn', action: null },
  // The relay no longer knows this instance and registering again failed.
  not_registered: { tone: 'danger', action: 'restart' },
  // The operator paused this instance: retried in 15 to 20 min.
  paused: { tone: 'warn', action: null },
  // Another install connected with the same key (a copied database or backup).
  replaced: { tone: 'danger', action: 'rotate' },
  banned: { tone: 'danger', action: null },
  update_required: { tone: 'danger', action: 'update' },
  // The whole service is frozen (maintenance, capacity): retried in 5 to 15 min.
  relay_paused: { tone: 'warn', action: null },
};

export function isRemoteLinkStatus(value: string | null): value is RemoteLinkStatus {
  return value !== null && (REMOTE_LINK_STATES as readonly string[]).includes(value);
}

export function remoteBadge(status: RemoteStatus): RemoteBadge {
  if (status.state === 'disabled') return { id: 'off', tone: 'neutral', action: 'enable' };
  if (status.state === 'unregistered') {
    return { id: 'unregistered', tone: 'danger', action: 'enable' };
  }
  const link = status.link_status ?? 'connecting';
  if (!isRemoteLinkStatus(link)) return { id: 'unknown', tone: 'warn', action: null };
  return { id: link, ...LINK_BADGES[link] };
}

export const BADGE_TONE_CLASS: Record<RemoteBadgeTone, string> = {
  ok: 'border-success/50 bg-success/10 text-success',
  neutral: 'border-border bg-muted/40 text-muted-foreground',
  warn: 'border-warning/50 bg-warning/10 text-warning',
  danger: 'border-destructive/50 bg-destructive/10 text-destructive',
};

/**
 * Mirrors `app/routers/auth.py` `_plugins_isolation_reduced`: plugins on (or only
 * paused) with the `none` level. An unknown state counts as reduced.
 */
export function pluginsIsolationReduced(health: HealthStatus | null): boolean {
  if (!health) return true;
  const source = health.plugins_disabled_source ?? null;
  const isolation = health.plugins_isolation ?? null;
  return (source === null || source === 'paused') && (isolation === null || isolation === 'none');
}

/**
 * C0/C1 controls, and the bidi and zero-width marks that could make text read as
 * something else (right-to-left overrides, isolates).
 */
function isUnsafeChar(code: number): boolean {
  return (
    code <= 0x1f ||
    (code >= 0x7f && code <= 0x9f) ||
    (code >= 0x200b && code <= 0x200f) ||
    (code >= 0x2028 && code <= 0x202e) ||
    (code >= 0x2060 && code <= 0x2069) ||
    code === 0xfeff
  );
}

/**
 * Untrusted text (an unknown device's user agent) made safe to show as plain text:
 * React escapes the markup, this drops the control and direction characters and
 * shortens it. Never pass the result to an HTML sink.
 */
export function untrustedText(value: string, max = USER_AGENT_SHOWN_MAX): string {
  const kept = Array.from(value, (char) => (isUnsafeChar(char.codePointAt(0) ?? 0) ? ' ' : char));
  const clean = kept.join('').replace(/\s+/g, ' ').trim();
  return clean.length > max ? `${clean.slice(0, max - 1)}…` : clean;
}
