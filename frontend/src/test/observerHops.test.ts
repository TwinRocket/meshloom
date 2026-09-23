import { describe, expect, it } from 'vitest';

import { CONTACT_TYPE_REPEATER, type Contact } from '../types';
import { hopMapLocation, hopNeedsDirectoryGps, toPathHop } from '../utils/observerHops';

function repeater(
  publicKey: string,
  name: string,
  lat: number | null,
  lon: number | null
): Contact {
  return {
    public_key: publicKey,
    name,
    type: CONTACT_TYPE_REPEATER,
    flags: 0,
    direct_path: null,
    direct_path_len: 0,
    direct_path_hash_mode: 0,
    route_override_path: null,
    route_override_len: null,
    route_override_hash_mode: null,
    last_advert: null,
    lat,
    lon,
    last_seen: null,
    on_radio: true,
    favorite: false,
    last_contacted: null,
    last_read_at: null,
    first_seen: null,
  };
}

const directory = {
  name: 'Directory Hill',
  source: 'corescope' as const,
  lat: 43.7,
  lon: 7.26,
};

describe('hopMapLocation', () => {
  it('keeps a local repeater that already has GPS', () => {
    const hop = toPathHop('aa11', [repeater('aa11' + 'ab'.repeat(30), 'Local', 48.1, 2.2)]);
    expect(hopNeedsDirectoryGps(hop)).toBe(false);
    expect(hopMapLocation(hop, directory)).toEqual({ lat: 48.1, lon: 2.2, name: 'Local' });
  });

  it('uses directory GPS when the named repeater has no advert position', () => {
    const hop = toPathHop('aa11', [
      repeater('aa11' + 'ab'.repeat(30), 'FR06-CARROS-Village', null, null),
    ]);
    expect(hopNeedsDirectoryGps(hop)).toBe(true);
    expect(hopMapLocation(hop, directory)).toEqual({
      lat: 43.7,
      lon: 7.26,
      name: 'FR06-CARROS-Village',
    });
  });

  it('does not invent a position when nobody has one', () => {
    const hop = toPathHop('bb22', [repeater('bb22' + 'cd'.repeat(30), 'Bare', null, null)]);
    expect(hopMapLocation(hop, { name: 'Bare', source: 'corescope' })).toBeNull();
  });
});
