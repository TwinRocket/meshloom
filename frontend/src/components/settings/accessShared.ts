import type { AuthRole } from '../../auth/types';

export const ACCESS_ROLES: readonly AuthRole[] = ['admin', 'member', 'viewer', 'kiosk'];

export const SELECT_CLASS =
  'h-10 rounded-md border border-input bg-background px-2 text-base md:text-sm focus-visible:outline-hidden focus-visible:ring-2 focus-visible:ring-ring disabled:opacity-50';

/** "Chrome on macOS" from a user agent; the raw text is never shown. */
export function deviceLabel(userAgent: string | null, unknown: string): string {
  if (!userAgent) return unknown;
  const browser = /Edg\//.test(userAgent)
    ? 'Edge'
    : /OPR\/|Opera/.test(userAgent)
      ? 'Opera'
      : /Firefox\//.test(userAgent)
        ? 'Firefox'
        : /Chrome\/|CriOS\//.test(userAgent)
          ? 'Chrome'
          : /Safari\//.test(userAgent)
            ? 'Safari'
            : null;
  const os = /Android/.test(userAgent)
    ? 'Android'
    : /iPhone|iPad|iPod/.test(userAgent)
      ? 'iOS'
      : /Windows/.test(userAgent)
        ? 'Windows'
        : /Mac OS X|Macintosh/.test(userAgent)
          ? 'macOS'
          : /Linux|X11/.test(userAgent)
            ? 'Linux'
            : null;
  if (browser && os) return `${browser} · ${os}`;
  return browser ?? os ?? unknown;
}

/** Epoch seconds as a local date and time. */
export function formatEpoch(seconds: number, language: string): string {
  return new Date(seconds * 1000).toLocaleString(language, {
    dateStyle: 'medium',
    timeStyle: 'short',
  });
}
