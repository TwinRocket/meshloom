import { getCommunitySnapshot, subscribeCommunity } from '../stores/communityStore';
import type { CommunityAirportHit } from '../types';

/** `[lon, lat]`, as the Live map takes them. */
export type RegionCenter = [number, number];

type AirportSearch = (query: string) => Promise<CommunityAirportHit[]>;

/** Airports do not move: one lookup per code for the life of the page. */
const located = new Map<string, RegionCenter | null>();

// Community turned (back) on: forget what was looked up before, so a lookup
// that failed meanwhile never sticks.
let communityWasEnabled = getCommunitySnapshot().enabled;
subscribeCommunity(() => {
  const enabled = getCommunitySnapshot().enabled;
  if (enabled === true && communityWasEnabled !== true) located.clear();
  communityWasEnabled = enabled;
});

/**
 * Where the selected Live regions are, from their IATA airport.
 *
 * Codes the bundled airport list (`GET /api/community/airports`, no request
 * leaves the server) does not place exactly are left out, so the result can be
 * shorter than `codes`, or empty. A failed request or an empty answer is not
 * remembered, so the next opening asks again.
 */
export async function locateLiveRegions(
  codes: readonly string[],
  search: AirportSearch
): Promise<RegionCenter[]> {
  const centers = await Promise.all(
    codes.map(async (code): Promise<RegionCenter | null> => {
      if (located.has(code)) return located.get(code) ?? null;
      try {
        const hits = await search(code);
        if (hits.length === 0) return null;
        const hit = hits.find((candidate) => candidate.iata === code);
        const center: RegionCenter | null =
          hit && Number.isFinite(hit.lat) && Number.isFinite(hit.lon)
            ? [hit.lon as number, hit.lat as number]
            : null;
        located.set(code, center);
        return center;
      } catch {
        return null;
      }
    })
  );
  return centers.filter((center): center is RegionCenter => center !== null);
}

/** Tests only. */
export function resetLocatedLiveRegions(): void {
  located.clear();
}
