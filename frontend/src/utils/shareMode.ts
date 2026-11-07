// Last mode chosen in the composer's File tab for an encryptable conversation.
// Browser-local; the `meshloom-` prefix keeps it apart from other apps under the
// Home Assistant ingress origin.

export type ShareMode = 'public' | 'encrypted';

export const SHARE_MODE_STORAGE = 'meshloom-share-mode';

/** The remembered mode; encrypted when nothing (or something unknown) is stored. */
export function getSavedShareMode(): ShareMode {
  try {
    return localStorage.getItem(SHARE_MODE_STORAGE) === 'public' ? 'public' : 'encrypted';
  } catch {
    // localStorage may be unavailable
    return 'encrypted';
  }
}

export function setSavedShareMode(mode: ShareMode): void {
  try {
    localStorage.setItem(SHARE_MODE_STORAGE, mode);
  } catch {
    // localStorage may be unavailable
  }
}

/**
 * The source file's name for the encrypted envelope (`X-Share-Name`), or undefined.
 * The backend refuses control characters, `/`, `\`, `.`/`..` and over 255 characters.
 */
export function shareFileName(name: string | undefined): string | undefined {
  if (!name) return undefined;
  // eslint-disable-next-line no-control-regex
  const cleaned = name.replace(/[\u0000-\u001F\u007F/\\]/g, '').trim();
  const cut = Array.from(cleaned).slice(0, 255).join('');
  return cut && cut !== '.' && cut !== '..' ? cut : undefined;
}
