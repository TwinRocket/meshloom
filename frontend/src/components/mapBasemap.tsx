import { useCallback, useEffect, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { LayersControl, TileLayer, useMap, useMapEvents } from 'react-leaflet';

import {
  OSM_RASTER_REFERRER_POLICY,
  OSM_RASTER_TILE_ATTRIBUTION,
  OSM_RASTER_TILE_URL,
} from '../utils/mapTiles';

interface TileLayerPreset {
  id: string;
  url: string;
  attribution: string;
  background: string;
  referrerPolicy?: typeof OSM_RASTER_REFERRER_POLICY;
  /** Highest zoom the provider publishes tiles at. When the layer is active,
   *  the map's zoom ceiling is tightened to this value via
   *  `MaxZoomByActiveLayer` so the user cannot zoom into a grey void. */
  maxZoom?: number;
}

// Global zoom bounds for the MapContainer itself. These are pinned to the
// container so Leaflet's internal tile-range math never has to guess when
// layers swap in/out via LayersControl. Without this, an initial-mount race
// between MapContainer layout and LayersControl.BaseLayer addition has been
// observed to throw "Attempted to load an infinite number of tiles".
export const MAP_MIN_ZOOM = 2;
export const MAP_MAX_ZOOM = 19;

/**
 * The dark basemap is OpenStreetMap inverted by CSS, not a second provider.
 *
 * CARTO's keyless raster endpoint now answers every tile past zoom 7 with one
 * 1970-byte "API KEY REQUIRED" placeholder — the same bytes worldwide, so the map
 * was legible only when fully zoomed out. Darkening the tiles we already fetch
 * keeps the map keyless and keeps one provider to attribute.
 */
export const DARK_BASEMAP_LAYER_ID = 'dark';

const TILE_LAYERS: readonly TileLayerPreset[] = [
  {
    id: 'light',
    url: OSM_RASTER_TILE_URL,
    attribution: OSM_RASTER_TILE_ATTRIBUTION,
    referrerPolicy: OSM_RASTER_REFERRER_POLICY,
    background: '#1a1a2e',
    maxZoom: 19,
  },
  {
    id: DARK_BASEMAP_LAYER_ID,
    url: OSM_RASTER_TILE_URL,
    attribution: OSM_RASTER_TILE_ATTRIBUTION,
    referrerPolicy: OSM_RASTER_REFERRER_POLICY,
    background: '#0d0d0d',
    maxZoom: 19,
  },
  {
    id: 'topographic',
    url: 'https://{s}.tile.opentopomap.org/{z}/{x}/{y}.png',
    attribution:
      'Map data: &copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors, <a href="http://viewfinderpanoramas.org">SRTM</a> | Map style: &copy; <a href="https://opentopomap.org">OpenTopoMap</a> (<a href="https://creativecommons.org/licenses/by-sa/3.0/">CC-BY-SA</a>)',
    background: '#a3b3bc',
    maxZoom: 17,
  },
  {
    id: 'satellite',
    url: 'https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}',
    attribution:
      'Tiles &copy; <a href="https://www.esri.com/">Esri</a> &mdash; Source: Esri, Maxar, Earthstar Geographics, and the GIS User Community',
    background: '#1a1f2e',
    // Esri's tile service advertises LODs up to 23 and returns HTTP 200 for
    // every tile request, but the underlying imagery is only high-resolution
    // up to ~18 in most developed areas and shallower in rural regions. We
    // cap at 18 rather than 19 so users don't zoom into visibly-empty or
    // severely-upscaled tiles. Remote regions may still be sparse at 18.
    maxZoom: 18,
  },
] as const;

const MAP_LAYER_STORAGE_KEY = 'meshloom-map-layer';
/** Set the first time the reader picks a basemap from the control themselves. */
const MAP_LAYER_CHOSEN_STORAGE_KEY = 'meshloom-map-layer-chosen';
const LEGACY_DARK_MAP_STORAGE_KEY = 'meshloom-dark-map';

/**
 * The basemap a theme implies, until the reader picks one.
 *
 * A daylight map inside a dark app is the one surface that does not belong to it,
 * and it drags everything floating above it along: the bar over it has to blur a
 * bright backdrop and comes out pale. Read from the background token rather than a
 * class name, so it holds for all the themes rather than the two obvious ones.
 */
function defaultLayerIdForTheme(): string {
  if (typeof window === 'undefined') return 'light';
  const raw = getComputedStyle(document.documentElement).getPropertyValue('--background').trim();
  // HSL triplet, "H S% L%": the third component is the one that decides.
  const lightness = Number.parseFloat(raw.split(/\s+/)[2] ?? '');
  if (!Number.isFinite(lightness)) return 'light';
  return lightness < 50 ? 'dark' : 'light';
}

function getSavedLayerId(): string {
  try {
    const stored = localStorage.getItem(MAP_LAYER_STORAGE_KEY);
    const chosen = localStorage.getItem(MAP_LAYER_CHOSEN_STORAGE_KEY) === 'true';
    // A stored light/dark that nobody chose is a leftover of when light was the
    // only default: the theme outranks it, or switching to a dark theme leaves a
    // daylight map behind for good. An explicit pick, and every styled basemap,
    // stands.
    const themed = stored === 'light' || stored === DARK_BASEMAP_LAYER_ID;
    if (stored && TILE_LAYERS.some((l) => l.id === stored) && (chosen || !themed)) return stored;
    // Legacy migration: boolean dark-map flag predates multi-layer support.
    const legacyDark = localStorage.getItem(LEGACY_DARK_MAP_STORAGE_KEY) === 'true';
    return legacyDark ? 'dark' : defaultLayerIdForTheme();
  } catch {
    return defaultLayerIdForTheme();
  }
}

/**
 * Leaflet-internal companion component: listens for base-layer changes driven
 * by Leaflet's own LayersControl UI and pipes the selection back to React.
 * Kept separate so the persistence/state logic stays out of the render tree.
 */
function LayerChangeWatcher({ onChange }: { onChange: (name: string) => void }) {
  useMapEvents({
    baselayerchange: (event) => {
      if (event.name) onChange(event.name);
    },
  });
  return null;
}

/**
 * Enforces the active layer's zoom ceiling on the underlying Leaflet map.
 *
 * Leaflet's `map.getMaxZoom()` prefers `options.maxZoom` (set on MapContainer)
 * over per-layer `maxZoom`, so a per-TileLayer cap is silently ignored unless
 * we push it down to the map itself. We do that here whenever the active
 * layer changes, and clamp the current zoom if the user happened to be zoomed
 * past the new cap at the moment of the switch.
 *
 * The MapContainer's fixed `minZoom`/`maxZoom` remain the absolute hull that
 * prevents the "Attempted to load an infinite number of tiles" race during
 * initial mount (see `MAP_MIN_ZOOM`/`MAP_MAX_ZOOM`).
 */
function MaxZoomByActiveLayer({ maxZoom }: { maxZoom: number }) {
  const map = useMap();
  useEffect(() => {
    map.setMaxZoom(maxZoom);
    if (map.getZoom() > maxZoom) {
      map.setZoom(maxZoom);
    }
  }, [map, maxZoom]);
  return null;
}

export interface MapBasemapState {
  selectedLayerId: string;
  activeLayer: TileLayerPreset;
  inverted: boolean;
  handleLayerChange: (layerName: string) => void;
  layerName: (id: string) => string;
}

/** The basemap `#map` is showing, including a choice saved from that page. */
export function useMapBasemap(): MapBasemapState {
  const { t } = useTranslation();
  const [selectedLayerId, setSelectedLayerId] = useState<string>(getSavedLayerId);
  const activeLayer = TILE_LAYERS.find((layer) => layer.id === selectedLayerId) ?? TILE_LAYERS[0];

  useEffect(() => {
    const onStorage = (event: StorageEvent) => {
      if (event.key !== MAP_LAYER_STORAGE_KEY) return;
      const next = event.newValue ?? '';
      if (TILE_LAYERS.some((layer) => layer.id === next)) {
        setSelectedLayerId(next);
      }
    };
    window.addEventListener('storage', onStorage);
    return () => window.removeEventListener('storage', onStorage);
  }, []);

  const layerName = useCallback(
    (id: string) => {
      if (id === 'light') return t('map.layerLight');
      if (id === 'dark') return t('map.layerDark');
      if (id === 'topographic') return t('map.layerTopo');
      if (id === 'satellite') return t('map.layerSatellite');
      return id;
    },
    [t]
  );

  const handleLayerChange = useCallback(
    (layerNameLabel: string) => {
      const match = TILE_LAYERS.find((layer) => layerName(layer.id) === layerNameLabel);
      if (!match) return;
      setSelectedLayerId(match.id);
      try {
        localStorage.setItem(MAP_LAYER_STORAGE_KEY, match.id);
        localStorage.setItem(MAP_LAYER_CHOSEN_STORAGE_KEY, 'true');
        // Clear the legacy key so a future downgrade-rollback doesn't revert us.
        localStorage.removeItem(LEGACY_DARK_MAP_STORAGE_KEY);
      } catch {
        // localStorage may be disabled; selection stays in memory only.
      }
    },
    [layerName]
  );

  return {
    selectedLayerId,
    activeLayer,
    inverted: selectedLayerId === DARK_BASEMAP_LAYER_ID,
    handleLayerChange,
    layerName,
  };
}

/** Same layer control `#map` uses, so a choice there is the choice here. */
export function MapBasemapLayers({
  selectedLayerId,
  onLayerChange,
  layerName,
}: {
  selectedLayerId: string;
  onLayerChange: (layerName: string) => void;
  layerName: (id: string) => string;
}) {
  const activeLayer = TILE_LAYERS.find((layer) => layer.id === selectedLayerId) ?? TILE_LAYERS[0];
  return (
    <>
      <LayersControl position="topright">
        {TILE_LAYERS.map((layer) => (
          <LayersControl.BaseLayer
            key={layer.id}
            name={layerName(layer.id)}
            checked={layer.id === selectedLayerId}
          >
            <TileLayer
              url={layer.url}
              attribution={layer.attribution}
              maxZoom={layer.maxZoom}
              referrerPolicy={layer.referrerPolicy}
            />
          </LayersControl.BaseLayer>
        ))}
      </LayersControl>
      <LayerChangeWatcher onChange={onLayerChange} />
      <MaxZoomByActiveLayer maxZoom={activeLayer.maxZoom ?? MAP_MAX_ZOOM} />
    </>
  );
}
