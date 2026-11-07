import type { OssUpdateHoldReason, OssUpdateStatus } from '../types';

export const UPDATE_HOLD_DISMISSED_KEY = 'meshloom.updateHoldDismissed';

/**
 * An update that Community asked not to install on its own, on a node that would
 * otherwise have done it: the user has to start it. Also keeps the toast quiet, so
 * this update is announced once.
 */
export function updateHoldNeedsAttention(status: OssUpdateStatus | null | undefined): boolean {
  return (
    status?.update_available === true &&
    status.auto_update === true &&
    status.auto_update_held === true &&
    status.apply_supported === true &&
    Boolean(status.latest)
  );
}

/** The current locale, then English, then the caller's own text. Always plain text. */
export function resolveHoldReason(
  reason: OssUpdateHoldReason | null | undefined,
  language: string | null | undefined,
  fallback: string
): string {
  const locale = (language ?? '').split('-')[0];
  const localized = (reason as Record<string, unknown> | null | undefined)?.[locale];
  for (const text of [localized, reason?.en]) {
    if (typeof text === 'string' && text.trim()) return text;
  }
  return fallback;
}

export function readDismissedHold(): string | null {
  try {
    return localStorage.getItem(UPDATE_HOLD_DISMISSED_KEY);
  } catch {
    return null;
  }
}

export function writeDismissedHold(latest: string): void {
  try {
    localStorage.setItem(UPDATE_HOLD_DISMISSED_KEY, latest);
  } catch {
    // Private mode: stays dismissed until this page reloads.
  }
}
