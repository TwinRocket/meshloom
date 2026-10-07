import type { StyleSpecification } from 'maplibre-gl';

/**
 * OSMF volunteer raster tiles. Policy:
 * https://operations.osmfoundation.org/policies/tiles/
 *
 * Leaflet also sets a per-image referrerPolicy so tile requests still
 * send a Referer if a document policy would otherwise omit it.
 */
export const OSM_RASTER_TILE_URL = 'https://tile.openstreetmap.org/{z}/{x}/{y}.png';

export const OSM_RASTER_TILE_ATTRIBUTION =
  '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a>';

export const OSM_RASTER_REFERRER_POLICY = 'strict-origin-when-cross-origin' as const;

/**
 * MapLibre `transformRequest` hook for OSM raster tiles. A per-request
 * referrerPolicy overrides a document policy of no-referrer, which is what
 * Home Assistant forces on ingress responses. Leaflet already does this on
 * its img elements; MapLibre needs it on the tile fetch.
 */
export function osmRasterTransformRequest(
  url: string,
  resourceType?: string
): { url: string; referrerPolicy: typeof OSM_RASTER_REFERRER_POLICY } | undefined {
  if (resourceType === 'Tile' && url.includes('tile.openstreetmap.org')) {
    return { url, referrerPolicy: OSM_RASTER_REFERRER_POLICY };
  }
  return undefined;
}

/** MapLibre raster style for `#live`. Same OSM tiles as `#map`; no CARTO. */
export function osmDarkRasterStyle(): StyleSpecification {
  return {
    version: 8,
    sources: {
      osm: {
        type: 'raster',
        tiles: [OSM_RASTER_TILE_URL],
        tileSize: 256,
        attribution: OSM_RASTER_TILE_ATTRIBUTION,
        maxzoom: 19,
      },
    },
    layers: [
      {
        id: 'background',
        type: 'background',
        paint: { 'background-color': '#05070a' },
      },
      {
        id: 'osm',
        type: 'raster',
        source: 'osm',
      },
    ],
  };
}
