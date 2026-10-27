import { describe, expect, it } from 'vitest';

import { isPublicOrWideCidr } from '../lib/cidr';

describe('isPublicOrWideCidr', () => {
  it.each([
    '192.168.1.0/24',
    '10.1.2.3',
    '172.16.0.0/12',
    '100.64.1.5',
    '100.64.1.5/32',
    'fd00::/48',
    'fe80::/64',
    '169.254.0.0/24',
    '::1',
  ])('accepts %s', (cidr) => expect(isPublicOrWideCidr(cidr)).toBe(false));
  it.each([
    '100.64.0.0/10',
    '100.64.0.0/24',
    '100.100.1.0/31',
    '8.8.8.0/24',
    '10.0.0.0/8',
    '172.0.0.0/8',
    '2001:4860::/32',
    'fd00::/16',
    '1.1.1.1',
    // Documentation and reserved ranges are public (app/net/local_address.py).
    '192.0.2.0/24',
    '198.18.0.0/24',
    '2001:db8::/32',
    '::/128',
  ])('warns for %s', (cidr) => expect(isPublicOrWideCidr(cidr)).toBe(true));
  it.each(['', '8.8.', '10.0.0.0/', '10.0.0.0/33', 'abc', '1.2.3.4/8/9'])(
    'stays silent for %s',
    (cidr) => expect(isPublicOrWideCidr(cidr)).toBe(false)
  );
});
