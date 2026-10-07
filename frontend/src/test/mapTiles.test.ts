import { describe, expect, it } from 'vitest';
import {
  OSM_RASTER_REFERRER_POLICY,
  OSM_RASTER_TILE_ATTRIBUTION,
  OSM_RASTER_TILE_URL,
  osmRasterTransformRequest,
} from '../utils/mapTiles';

describe('OSM raster tiles', () => {
  it('uses the official OSMF host without a {s} subdomain', () => {
    expect(OSM_RASTER_TILE_URL).toBe('https://tile.openstreetmap.org/{z}/{x}/{y}.png');
    expect(OSM_RASTER_TILE_URL).not.toContain('{s}');
  });

  it('keeps licence attribution and a Referer-capable policy', () => {
    expect(OSM_RASTER_TILE_ATTRIBUTION).toContain('openstreetmap.org/copyright');
    expect(OSM_RASTER_REFERRER_POLICY).toBe('strict-origin-when-cross-origin');
  });

  it('sets a referrer policy on OSM tile requests', () => {
    const url = 'https://tile.openstreetmap.org/3/4/2.png';
    expect(osmRasterTransformRequest(url, 'Tile')).toEqual({
      url,
      referrerPolicy: 'strict-origin-when-cross-origin',
    });
  });

  it('leaves non-tile resources and other tile hosts alone', () => {
    expect(
      osmRasterTransformRequest('https://tile.openstreetmap.org/3/4/2.png', 'Style')
    ).toBeUndefined();
    expect(osmRasterTransformRequest('https://tile.openstreetmap.org/3/4/2.png')).toBeUndefined();
    expect(osmRasterTransformRequest('https://example.com/3/4/2.png', 'Tile')).toBeUndefined();
  });
});
