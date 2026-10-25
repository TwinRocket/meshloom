import { describe, expect, it } from 'vitest';

import { isPublicOrWideCidr } from '../lib/cidr';

describe('isPublicOrWideCidr', () => {
  it.each([
    '192.168.1.0/24',
    '10.1.2.3',
    '172.16.0.0/12',
    '100.64.0.0/10',
    'fd00::/48',
    'fe80::/64',
  ])('accepts %s', (cidr) => expect(isPublicOrWideCidr(cidr)).toBe(false));
  it.each(['8.8.8.0/24', '10.0.0.0/8', '172.0.0.0/8', '2001:4860::/32', 'fd00::/16', '1.1.1.1'])(
    'warns for %s',
    (cidr) => expect(isPublicOrWideCidr(cidr)).toBe(true)
  );
  it.each(['', '8.8.', '10.0.0.0/', '10.0.0.0/33', 'abc', '1.2.3.4/8/9'])(
    'stays silent for %s',
    (cidr) => expect(isPublicOrWideCidr(cidr)).toBe(false)
  );
});
