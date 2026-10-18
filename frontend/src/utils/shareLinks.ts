/** The default share host, used before `/api/share/status` has answered. */
export const DEFAULT_SHARE_HOSTS: readonly string[] = ['meshl.ink'];

// Seven characters of the Bitcoin base58 alphabet (the contract's id grammar,
// `SHARE_ID_RE` in app/services/share.py), optionally behind a `/l/` path.
const SHARE_PATH = /^\/(?:l\/)?([1-9A-HJ-NP-Za-km-z]{7})\/?$/;

/**
 * The id of a service link, or null. Purely syntactic: no network, no store.
 * `hosts` comes from `/api/share/status`; the host is compared without case.
 */
export function parseShareLink(
  url: string,
  hosts: readonly string[] = DEFAULT_SHARE_HOSTS
): string | null {
  let parsed: URL;
  try {
    parsed = new URL(url);
  } catch {
    return null;
  }
  if (parsed.protocol !== 'https:' || parsed.username || parsed.password || parsed.port) {
    return null;
  }
  if (parsed.search || parsed.hash) return null;
  const host = parsed.hostname.toLowerCase().replace(/\.$/, '');
  if (!hosts.some((h) => h.toLowerCase() === host)) return null;
  return SHARE_PATH.exec(parsed.pathname)?.[1] ?? null;
}

/** Cheap pre-check: could this URL be a service link on any host? */
export function looksLikeShareLink(url: string): boolean {
  try {
    const parsed = new URL(url);
    return parsed.protocol === 'https:' && SHARE_PATH.test(parsed.pathname);
  } catch {
    return false;
  }
}

/**
 * Split a message body that is a service link, optionally followed by a caption
 * (`https://meshl.ink/AbCdEfG caption`). Null when the body is anything else.
 */
export function splitShareLinkBody(
  body: string,
  hosts: readonly string[] = DEFAULT_SHARE_HOSTS
): { id: string; caption: string } | null {
  const trimmed = body.trim();
  const space = trimmed.search(/\s/);
  const url = space === -1 ? trimmed : trimmed.slice(0, space);
  const id = parseShareLink(url, hosts);
  if (!id) return null;
  return { id, caption: space === -1 ? '' : trimmed.slice(space).trim() };
}
