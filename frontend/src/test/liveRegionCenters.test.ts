import { afterEach, describe, expect, it, vi } from 'vitest';

import type { CommunityAirportHit } from '../types';
import { locateLiveRegions, resetLocatedLiveRegions } from '../utils/liveRegionCenters';

function hit(iata: string, lat: number | null, lon: number | null): CommunityAirportHit {
  return { iata, name: iata, city: '', country: '', label: iata, lat, lon };
}

describe('locateLiveRegions', () => {
  afterEach(() => resetLocatedLiveRegions());

  it('places each code at its own airport, not at a fuzzy match', async () => {
    const search = vi.fn(async (query: string) =>
      query === 'NCE' ? [hit('NCX', 1, 1), hit('NCE', 43.66, 7.21)] : [hit('LYS', 45.72, 5.08)]
    );
    expect(await locateLiveRegions(['NCE', 'LYS'], search)).toEqual([
      [7.21, 43.66],
      [5.08, 45.72],
    ]);
  });

  it('leaves out codes without coordinates and asks once per code', async () => {
    const search = vi.fn(async (query: string) => [hit(query, null, null)]);
    expect(await locateLiveRegions(['ZZZ'], search)).toEqual([]);
    expect(await locateLiveRegions(['ZZZ'], search)).toEqual([]);
    expect(search).toHaveBeenCalledTimes(1);
  });

  it('asks again after a failed request', async () => {
    const search = vi
      .fn<(query: string) => Promise<CommunityAirportHit[]>>()
      .mockRejectedValueOnce(new Error('down'))
      .mockResolvedValueOnce([hit('LYS', 45.72, 5.08)]);
    expect(await locateLiveRegions(['LYS'], search)).toEqual([]);
    expect(await locateLiveRegions(['LYS'], search)).toEqual([[5.08, 45.72]]);
  });
});
