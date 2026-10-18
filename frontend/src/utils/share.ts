import { DEFAULT_SHARE_HOSTS } from './shareLinks';

/**
 * Link shortening in the composer. Purely local: the grammar and the byte maths
 * here make no request; the composer calls `api.shortenLink` for what they find.
 */

/** URL regex for linkifying plain text, shared with the message renderer. */
export const URL_PATTERN =
  /https?:\/\/(www\.)?[-a-zA-Z0-9@:%._+~#=]{1,256}\.[a-zA-Z0-9()]{1,6}\b([-a-zA-Z0-9()@:%_+.~#?&//=]*)/g;

// `https://` + host + `/` + the seven-character id (the contract's id grammar).
const SHORT_FORM_FIXED_BYTES = 'https://'.length + '/'.length + 7;

// `g:<gifId>`, `m:<lat>,<lon>|...`, `r:<hash>:<index>`: wire payloads, never rewritten.
const OPEN_PAYLOAD_RE = /^(?:@\[[^\]]*\] )?[gmr]:/;

const textEncoder = new TextEncoder();

/** UTF-8 byte length (LoRa packets are byte-constrained). */
export function byteLength(s: string): number {
  return textEncoder.encode(s).length;
}

/** Bytes of a short link on `host`: `https://<host>/<7-char id>`. */
export function shortFormBytes(host: string): number {
  return byteLength(host) + SHORT_FORM_FIXED_BYTES;
}

function hostOf(url: string): string | null {
  try {
    return new URL(url).hostname.toLowerCase().replace(/\.$/, '');
  } catch {
    return null;
  }
}

/** True for any URL on a host of the share service (links, files, its own pages). */
export function isShareLink(url: string, hosts: readonly string[] = DEFAULT_SHARE_HOSTS): boolean {
  const host = hostOf(url);
  return host !== null && hosts.some((h) => h.toLowerCase() === host);
}

/** True when the text is a `g:`/`m:`/`r:` payload (optionally behind a reply prefix). */
export function isOpenPayload(text: string): boolean {
  return OPEN_PAYLOAD_RE.test(text.trimStart());
}

// The pattern's tail class includes `.`, `:` and `)`: drop the ones prose adds after a link.
function trimUrl(raw: string): string {
  let url = raw.replace(/[.:]+$/, '');
  while (url.endsWith(')') && !url.includes('(')) url = url.slice(0, -1);
  return url;
}

function matchUrls(text: string): { url: string; start: number }[] {
  const re = new RegExp(URL_PATTERN.source, 'g');
  const found: { url: string; start: number }[] = [];
  let m: RegExpExecArray | null;
  while ((m = re.exec(text)) !== null) {
    const url = trimUrl(m[0]);
    if (url) found.push({ url, start: m.index });
  }
  return found;
}

/**
 * The distinct URLs of `text` worth shortening, in order: not on a share host,
 * and longer than a short link. Nothing inside a `g:`/`m:`/`r:` payload.
 */
export function findLongUrls(
  text: string,
  hosts: readonly string[] = DEFAULT_SHARE_HOSTS
): string[] {
  if (isOpenPayload(text)) return [];
  const shortBytes = shortFormBytes(hosts[0] ?? DEFAULT_SHARE_HOSTS[0]);
  const seen = new Set<string>();
  for (const { url } of matchUrls(text)) {
    if (isShareLink(url, hosts) || byteLength(url) <= shortBytes) continue;
    seen.add(url);
  }
  return [...seen];
}

/** Replace each URL found in `mapping` (long -> short); everything else is untouched. */
export function rewriteUrls(text: string, mapping: ReadonlyMap<string, string>): string {
  if (isOpenPayload(text)) return text;
  let out = '';
  let last = 0;
  for (const { url, start } of matchUrls(text)) {
    const short = mapping.get(url);
    if (short === undefined) continue;
    out += text.slice(last, start) + short;
    last = start + url.length;
  }
  return out + text.slice(last);
}
