/**
 * Client-side mirror of `trusted_network.public_or_wide` (app/auth/providers/trusted_network.py),
 * used only to warn live while a trusted network is typed. The server stays authoritative
 * (its `trusted_network_public_or_wide` security notice).
 */

interface Range {
  base: bigint;
  prefix: number;
}

// Python `is_private` for IPv4 (the shared 100.64.0.0/10 counts as private here too).
const PRIVATE_V4: Array<[string, number]> = [
  ['0.0.0.0', 8],
  ['10.0.0.0', 8],
  ['100.64.0.0', 10],
  ['127.0.0.0', 8],
  ['169.254.0.0', 16],
  ['172.16.0.0', 12],
  ['192.0.0.0', 24],
  ['192.0.2.0', 24],
  ['192.168.0.0', 16],
  ['198.18.0.0', 15],
  ['198.51.100.0', 24],
  ['203.0.113.0', 24],
  ['240.0.0.0', 4],
  ['255.255.255.255', 32],
];

// Loopback, unspecified, unique local, link-local, documentation.
const PRIVATE_V6: Array<[string, number]> = [
  ['::', 128],
  ['::1', 128],
  ['fc00::', 7],
  ['fe80::', 10],
  ['2001:db8::', 32],
];

function parseV4(text: string): bigint | null {
  const parts = text.split('.');
  if (parts.length !== 4) return null;
  let value = 0n;
  for (const part of parts) {
    if (!/^\d{1,3}$/.test(part) || Number(part) > 255) return null;
    value = (value << 8n) | BigInt(part);
  }
  return value;
}

function parseV6(text: string): bigint | null {
  if (!/^[0-9a-fA-F:]+$/.test(text) || text.includes(':::')) return null;
  const halves = text.split('::');
  if (halves.length > 2) return null;
  const groups = (part: string) => (part === '' ? [] : part.split(':'));
  const head = groups(halves[0]);
  const tail = halves.length === 2 ? groups(halves[1]) : [];
  const missing = 8 - head.length - tail.length;
  if (halves.length === 2 ? missing < 1 : missing !== 0) return null;
  const all = [...head, ...Array<string>(halves.length === 2 ? missing : 0).fill('0'), ...tail];
  let value = 0n;
  for (const group of all) {
    if (!/^[0-9a-fA-F]{1,4}$/.test(group)) return null;
    value = (value << 16n) | BigInt(`0x${group}`);
  }
  return value;
}

function parseNetwork(raw: string): { version: 4 | 6; range: Range } | null {
  const text = raw.trim();
  if (!text) return null;
  const [address, prefixText, extra] = text.split('/');
  if (extra !== undefined) return null;
  const version = address.includes(':') ? 6 : 4;
  const bits = version === 4 ? 32 : 128;
  const value = version === 4 ? parseV4(address) : parseV6(address);
  if (value === null) return null;
  let prefix = bits;
  if (prefixText !== undefined) {
    if (!/^\d{1,3}$/.test(prefixText)) return null;
    prefix = Number(prefixText);
    if (prefix > bits) return null;
  }
  const shift = BigInt(bits - prefix);
  return { version, range: { base: (value >> shift) << shift, prefix } };
}

function inside(inner: Range, outerBase: bigint, outerPrefix: number, bits: number): boolean {
  if (inner.prefix < outerPrefix) return false;
  const shift = BigInt(bits - outerPrefix);
  return inner.base >> shift === outerBase >> shift;
}

/**
 * True for a network that is public or as wide as allowed (/8 IPv4, /16 IPv6), or a subnet
 * of the shared 100.64.0.0/10 (Tailscale, CGNAT) wider than one address; false for a
 * private one or for text that is not (yet) a network, so nothing flashes while typing.
 */
export function isPublicOrWideCidr(raw: string): boolean {
  const parsed = parseNetwork(raw);
  if (!parsed) return false;
  const { version, range } = parsed;
  if (range.prefix <= (version === 4 ? 8 : 16)) return true;
  const bits = version === 4 ? 32 : 128;
  // The shared 100.64.0.0/10 (Tailscale, CGNAT): anything wider than one address.
  if (version === 4 && inside(range, parseV4('100.64.0.0')!, 10, bits)) return range.prefix < 32;
  const table = version === 4 ? PRIVATE_V4 : PRIVATE_V6;
  return !table.some(([base, prefix]) => {
    const value = version === 4 ? parseV4(base) : parseV6(base);
    return value !== null && inside(range, value, prefix, bits);
  });
}
