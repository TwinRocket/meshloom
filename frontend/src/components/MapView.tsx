import { Fragment, useEffect, useState, useMemo, useRef, useCallback } from 'react';
import { useTranslation } from 'react-i18next';
import {
  MapContainer,
  TileLayer,
  CircleMarker,
  Popup,
  useMap,
  useMapEvents,
  LayersControl,
} from 'react-leaflet';
import type { LatLngBoundsExpression, CircleMarker as LeafletCircleMarker } from 'leaflet';
import 'leaflet/dist/leaflet.css';
import type { Contact, DirectoryMapNode, RadioConfig } from '../types';
import { api } from '../api';
import { formatTime } from '../utils/messageParser';
import { isValidLocation } from '../utils/pathUtils';
import { CONTACT_TYPE_REPEATER } from '../types';
import { DirectoryGlobeIcon } from './messagePath/DirectoryGlobeIcon';
import { cn } from '@/lib/utils';
import {
  OSM_RASTER_REFERRER_POLICY,
  OSM_RASTER_TILE_ATTRIBUTION,
  OSM_RASTER_TILE_URL,
} from '../utils/mapTiles';
import { getSavedCartoApiKey } from '../utils/cartoPreference';
import { readSavedMapCamera, writeSavedMapCamera } from '../utils/livePackets';

interface MapViewProps {
  contacts: Contact[];
  /** Public key of contact to focus on and open popup */
  focusedKey?: string | null;
  config?: RadioConfig | null;
  blockedKeys?: string[];
  blockedNames?: string[];
  /** When provided, the contact name in each popup becomes a clickable link
   *  that opens the conversation for that contact (DM, repeater, or room). */
  onSelectContact?: (contact: Contact) => void;
  /** Settings → Radio-App CoreScope switch. Overlay stays off until the map checkbox is ticked. */
  directoryEnabled?: boolean;
}

// --- Tile layer presets ---
// Providers here are free. CARTO dark raster tiles optionally take a free API
// key (Settings → Local); without one they may show a watermark. Other layers
// work without a key. Attribution strings follow each provider's requirements;
// do not remove them. If you add a new provider, verify its terms of service
// (especially for Esri / Google-style satellite tiles) before committing.
// Raster-only transitional fix: Neighbors / Locate / Path stay on OSM.
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
const MAP_MIN_ZOOM = 2;
const MAP_MAX_ZOOM = 19;

/** CARTO dark raster URL. `{r}` stays before `.png`; `?key=` is appended when set. */
export const CARTO_DARK_TILE_URL = 'https://{s}.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}{r}.png';

export function cartoDarkTileUrl(apiKey: string): string {
  const trimmed = apiKey.trim();
  return trimmed
    ? `${CARTO_DARK_TILE_URL}?key=${encodeURIComponent(trimmed)}`
    : CARTO_DARK_TILE_URL;
}

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
    id: 'dark',
    url: CARTO_DARK_TILE_URL,
    attribution:
      '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> &copy; <a href="https://carto.com/">CARTO</a>',
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
const MAP_CAMERA_STORAGE_KEY = 'meshloom-map-camera';
const LEGACY_DARK_MAP_STORAGE_KEY = 'meshloom-dark-map';

function getSavedLayerId(): string {
  try {
    const stored = localStorage.getItem(MAP_LAYER_STORAGE_KEY);
    if (stored && TILE_LAYERS.some((l) => l.id === stored)) return stored;
    // Legacy migration: boolean dark-map flag predates multi-layer support.
    const legacyDark = localStorage.getItem(LEGACY_DARK_MAP_STORAGE_KEY) === 'true';
    return legacyDark ? 'dark' : 'light';
  } catch {
    return 'light';
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
 * initial mount (see `MAP_MIN_ZOOM`/`MAP_MAX_ZOOM` below).
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

const MAP_RECENCY_COLORS = {
  recent: '#06b6d4',
  today: '#2563eb',
  stale: '#f59e0b',
  old: '#64748b',
} as const;
const MAP_MARKER_STROKE = '#0f172a';
const MAP_REPEATER_RING = '#f8fafc';
const MAP_DIRECTORY_COLOR = '#f97316';

// --- "Heard since" filter ---
// Relative presets mirror the marker recency legend so the chips and the dot
// colors describe the same buckets. `seconds: null` means "no lower bound".
const MAP_SINCE_PRESETS = [
  { id: '1h', label: '<1h', windowLabel: '1 hour', seconds: 3600 },
  { id: '1d', label: '<1d', windowLabel: '24 hours', seconds: 24 * 60 * 60 },
  { id: '3d', label: '<3d', windowLabel: '3 days', seconds: 3 * 24 * 60 * 60 },
  { id: '7d', label: '7d', windowLabel: '7 days', seconds: 7 * 24 * 60 * 60 },
  { id: 'all', label: 'All', windowLabel: null, seconds: null },
] as const;

type MapSinceId = (typeof MAP_SINCE_PRESETS)[number]['id'] | 'custom';

const DEFAULT_MAP_SINCE_ID: MapSinceId = '7d';
const MAP_SINCE_STORAGE_KEY = 'meshloom-map-since';

/** Relative presets drift as time passes, so recompute the cutoff on this cadence. */
const MAP_SINCE_TICK_MS = 60_000;

function getSavedSinceId(): MapSinceId {
  try {
    const stored = localStorage.getItem(MAP_SINCE_STORAGE_KEY);
    // 'custom' is deliberately not restored: a stale absolute timestamp from a
    // previous session would silently filter the map on load.
    if (stored && MAP_SINCE_PRESETS.some((p) => p.id === stored)) {
      return stored as MapSinceId;
    }
  } catch {
    // localStorage may be disabled; fall through to the default.
  }
  return DEFAULT_MAP_SINCE_ID;
}

/**
 * Convert a `datetime-local` value (local wall clock, no offset) to epoch
 * seconds. Per spec `new Date()` interprets the date-time form in the browser's
 * local zone, which is what we want to compare against UTC-anchored `last_seen`.
 */
function localDateTimeToEpochSec(value: string): number | null {
  if (!value) return null;
  const ms = new Date(value).getTime();
  return Number.isNaN(ms) ? null : ms / 1000;
}

// --- Helpers ---

function getMarkerColor(lastSeen: number | null | undefined): string {
  if (lastSeen == null) return MAP_RECENCY_COLORS.old;
  const now = Date.now() / 1000;
  const age = now - lastSeen;
  const hour = 3600;
  const day = 86400;

  if (age < hour) return MAP_RECENCY_COLORS.recent;
  if (age < day) return MAP_RECENCY_COLORS.today;
  if (age < 3 * day) return MAP_RECENCY_COLORS.stale;
  return MAP_RECENCY_COLORS.old;
}

// --- Map bounds handler ---

function PersistMapCamera() {
  const map = useMap();
  useEffect(() => {
    const persist = () => {
      const center = map.getCenter();
      writeSavedMapCamera(MAP_CAMERA_STORAGE_KEY, {
        lat: center.lat,
        lon: center.lng,
        zoom: map.getZoom(),
      });
    };
    map.on('moveend', persist);
    map.on('zoomend', persist);
    return () => {
      map.off('moveend', persist);
      map.off('zoomend', persist);
    };
  }, [map]);
  return null;
}

function MapBoundsHandler({
  contacts,
  focusedContact,
}: {
  contacts: Contact[];
  focusedContact: Contact | null;
}) {
  const map = useMap();
  const [hasInitialized, setHasInitialized] = useState(false);
  const lastFocusKey = useRef<string | null>(null);

  useEffect(() => {
    if (focusedContact && focusedContact.lat != null && focusedContact.lon != null) {
      if (lastFocusKey.current !== focusedContact.public_key) {
        map.setView([focusedContact.lat, focusedContact.lon], 12);
        lastFocusKey.current = focusedContact.public_key;
      }
      setHasInitialized(true);
      return;
    }
    lastFocusKey.current = null;

    if (hasInitialized) return;

    const saved = readSavedMapCamera(MAP_CAMERA_STORAGE_KEY);
    if (saved) {
      map.setView([saved.lat, saved.lon], saved.zoom);
      setHasInitialized(true);
      return;
    }

    const fitToContacts = () => {
      if (contacts.length === 0) {
        map.setView([20, 0], 2);
        setHasInitialized(true);
        return;
      }

      if (contacts.length === 1) {
        map.setView([contacts[0].lat!, contacts[0].lon!], 10);
        setHasInitialized(true);
        return;
      }

      const bounds: LatLngBoundsExpression = contacts.map(
        (c) => [c.lat!, c.lon!] as [number, number]
      );
      map.fitBounds(bounds, { padding: [50, 50], maxZoom: 12 });
      setHasInitialized(true);
    };

    if ('geolocation' in navigator) {
      navigator.geolocation.getCurrentPosition(
        (position) => {
          map.setView([position.coords.latitude, position.coords.longitude], 8);
          setHasInitialized(true);
        },
        () => {
          fitToContacts();
        },
        { timeout: 5000, maximumAge: 300000 }
      );
    } else {
      fitToContacts();
    }
  }, [map, contacts, hasInitialized, focusedContact]);

  return null;
}


export function MapView({
  contacts,
  focusedKey,
  blockedKeys,
  blockedNames,
  onSelectContact,
  directoryEnabled = false,
}: MapViewProps) {
  const { t } = useTranslation();
  const [sinceId, setSinceId] = useState<MapSinceId>(getSavedSinceId);
  const [customSince, setCustomSince] = useState('');
  const [nowSec, setNowSec] = useState(() => Date.now() / 1000);
  const [selectedLayerId, setSelectedLayerId] = useState<string>(getSavedLayerId);
  const activeLayer = TILE_LAYERS.find((l) => l.id === selectedLayerId) ?? TILE_LAYERS[0];

  // Sync layer selection across tabs and windows.
  useEffect(() => {
    const onStorage = (e: StorageEvent) => {
      if (e.key !== MAP_LAYER_STORAGE_KEY) return;
      const next = e.newValue ?? '';
      if (TILE_LAYERS.some((l) => l.id === next)) {
        setSelectedLayerId(next);
      }
    };
    window.addEventListener('storage', onStorage);
    return () => window.removeEventListener('storage', onStorage);
  }, []);

  const layerNameById = useCallback(
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
    (layerName: string) => {
      const match = TILE_LAYERS.find((l) => layerNameById(l.id) === layerName);
      if (!match) return;
      setSelectedLayerId(match.id);
      try {
        localStorage.setItem(MAP_LAYER_STORAGE_KEY, match.id);
        // Clear the legacy key so a future downgrade-rollback doesn't revert us.
        localStorage.removeItem(LEGACY_DARK_MAP_STORAGE_KEY);
      } catch {
        // localStorage may be disabled; selection stays in memory only.
      }
    },
    [layerNameById]
  );

  const [showInternetRelays, setShowInternetRelays] = useState(false);
  const [directoryNodes, setDirectoryNodes] = useState<DirectoryMapNode[]>([]);
  const [directoryLoadError, setDirectoryLoadError] = useState(false);

  const activeSincePreset = MAP_SINCE_PRESETS.find((p) => p.id === sinceId) ?? null;
  const sinceIsRelative = activeSincePreset != null && activeSincePreset.seconds != null;

  // Only tick while a relative preset is active — "All" and absolute custom
  // cutoffs are fixed, so re-rendering the map on a timer would be pure waste.
  useEffect(() => {
    if (!sinceIsRelative) return;
    const timer = setInterval(() => setNowSec(Date.now() / 1000), MAP_SINCE_TICK_MS);
    return () => clearInterval(timer);
  }, [sinceIsRelative]);

  useEffect(() => {
    try {
      if (sinceId === 'custom') return; // session-only; see getSavedSinceId
      localStorage.setItem(MAP_SINCE_STORAGE_KEY, sinceId);
    } catch {
      // localStorage may be disabled; selection stays in memory only.
    }
  }, [sinceId]);

  /** Epoch seconds; `null` means no lower bound (show everything ever heard). */
  const sinceCutoffSec = useMemo(() => {
    if (sinceId === 'custom') return localDateTimeToEpochSec(customSince);
    if (!activeSincePreset || activeSincePreset.seconds == null) return null;
    return nowSec - activeSincePreset.seconds;
  }, [sinceId, customSince, activeSincePreset, nowSec]);

  const isWithinSinceWindow = useCallback(
    (lastSeen: number | null | undefined) => {
      if (sinceCutoffSec == null) return true;
      return lastSeen != null && lastSeen > sinceCutoffSec;
    },
    [sinceCutoffSec]
  );

  // Filter contacts for map display
  const mappableContacts = useMemo(() => {
    const isBlocked = (c: Contact) =>
      (blockedKeys?.length && blockedKeys.includes(c.public_key.toLowerCase())) ||
      (blockedNames?.length && c.name != null && blockedNames.includes(c.name));

    return contacts.filter(
      (c) =>
        isValidLocation(c.lat, c.lon) &&
        !isBlocked(c) &&
        (c.public_key === focusedKey || isWithinSinceWindow(c.last_seen))
    );
  }, [contacts, focusedKey, isWithinSinceWindow, blockedKeys, blockedNames]);

  const localContactKeys = useMemo(
    () => new Set(contacts.map((contact) => contact.public_key.toLowerCase())),
    [contacts]
  );

  const overlayDirectoryNodes = useMemo(() => {
    if (!showInternetRelays) return [];
    return directoryNodes.filter((node) => !localContactKeys.has(node.public_key.toLowerCase()));
  }, [showInternetRelays, directoryNodes, localContactKeys]);

  useEffect(() => {
    if (!directoryEnabled) {
      setShowInternetRelays(false);
      setDirectoryNodes([]);
      setDirectoryLoadError(false);
    }
  }, [directoryEnabled]);

  useEffect(() => {
    if (!directoryEnabled || !showInternetRelays) {
      if (!showInternetRelays) {
        setDirectoryNodes([]);
        setDirectoryLoadError(false);
      }
      return;
    }
    let cancelled = false;
    void api.getDirectoryMapNodes().then(
      (res) => {
        if (cancelled) return;
        setDirectoryNodes(res.nodes);
        setDirectoryLoadError(false);
      },
      () => {
        if (cancelled) return;
        setDirectoryNodes([]);
        setDirectoryLoadError(true);
      }
    );
    return () => {
      cancelled = true;
    };
  }, [directoryEnabled, showInternetRelays]);

  // Find the focused contact by key
  const focusedContact = useMemo(() => {
    if (!focusedKey) return null;
    return mappableContacts.find((c) => c.public_key === focusedKey) || null;
  }, [focusedKey, mappableContacts]);

  const includesFocusedOutsideWindow =
    focusedContact != null && !isWithinSinceWindow(focusedContact.last_seen);

  // Track marker refs to open popup programmatically
  const markerRefs = useRef<Record<string, LeafletCircleMarker | null>>({});

  const setMarkerRef = useCallback((key: string, ref: LeafletCircleMarker | null) => {
    if (ref === null) {
      delete markerRefs.current[key];
      return;
    }
    markerRefs.current[key] = ref;
  }, []);

  useEffect(() => {
    const currentKeys = new Set(mappableContacts.map((contact) => contact.public_key));
    for (const key of Object.keys(markerRefs.current)) {
      if (!currentKeys.has(key)) {
        delete markerRefs.current[key];
      }
    }
  }, [mappableContacts]);

  useEffect(() => {
    if (focusedContact && markerRefs.current[focusedContact.public_key]) {
      const timer = setTimeout(() => {
        markerRefs.current[focusedContact.public_key]?.openPopup();
      }, 100);
      return () => clearTimeout(timer);
    }
  }, [focusedContact]);

  const sinceLabel = useMemo(() => {
    if (sinceId === 'custom') {
      return sinceCutoffSec == null
        ? t('map.atAnyTime')
        : t('map.sinceDate', { date: new Date(sinceCutoffSec * 1000).toLocaleString() });
    }
    if (!activeSincePreset || activeSincePreset.windowLabel == null) return t('map.atAnyTime');
    if (activeSincePreset.id === '1h') return t('map.inLast1h');
    if (activeSincePreset.id === '1d') return t('map.inLast1d');
    if (activeSincePreset.id === '3d') return t('map.inLast3d');
    return t('map.inLast7d');
  }, [sinceId, sinceCutoffSec, activeSincePreset, t]);

  const infoLabel = includesFocusedOutsideWindow
    ? t('map.showingHeardFocused', { count: mappableContacts.length, since: sinceLabel })
    : t('map.showingHeard', { count: mappableContacts.length, since: sinceLabel });

  return (
    <div className="flex flex-col h-full">
      {/* Info bar: stacks vertically on narrow viewports (info label, legend
          row, controls row) so nothing truncates; flattens to a single row
          with right-aligned cluster at md and up. */}
      <div className="px-4 py-2 bg-muted/50 text-xs text-muted-foreground flex flex-col gap-1 md:flex-row md:items-center md:justify-between md:gap-3">
        <span>{infoLabel}</span>
        <div className="flex flex-wrap items-center gap-x-3 gap-y-1 md:justify-end">
          <div
            className="flex flex-wrap items-center gap-x-3 gap-y-1"
            role="group"
            aria-label={t('map.recencyLegend')}
          >
              <span className="flex items-center gap-1">
                <span
                  className="w-3 h-3 rounded-full"
                  style={{ backgroundColor: MAP_RECENCY_COLORS.recent }}
                  aria-hidden="true"
                />{' '}
                &lt;1h
              </span>
              <span className="flex items-center gap-1">
                <span
                  className="w-3 h-3 rounded-full"
                  style={{ backgroundColor: MAP_RECENCY_COLORS.today }}
                  aria-hidden="true"
                />{' '}
                &lt;1d
              </span>
              <span className="flex items-center gap-1">
                <span
                  className="w-3 h-3 rounded-full"
                  style={{ backgroundColor: MAP_RECENCY_COLORS.stale }}
                  aria-hidden="true"
                />{' '}
                &lt;3d
              </span>
              <span className="flex items-center gap-1">
                <span
                  className="w-3 h-3 rounded-full"
                  style={{ backgroundColor: MAP_RECENCY_COLORS.old }}
                  aria-hidden="true"
                />{' '}
                {t('map.older')}
              </span>
            </div>
          <span className="flex items-center gap-1">
            <span
              className="w-3 h-3 rounded-full border-2"
              style={{ borderColor: MAP_REPEATER_RING, backgroundColor: MAP_RECENCY_COLORS.today }}
              aria-hidden="true"
            />{' '}
            {t('map.legendRepeater')}
          </span>
          {showInternetRelays && (
            <span className="flex items-center gap-1">
              <span
                className="w-3 h-3 rounded-full border-2"
                style={{
                  borderColor: MAP_DIRECTORY_COLOR,
                  backgroundColor: MAP_RECENCY_COLORS.today,
                }}
                aria-hidden="true"
              />{' '}
              {t('map.internetRelaysLegend')}
            </span>
          )}
          <div
              className="flex flex-wrap items-center gap-1"
              role="group"
              aria-label={t('map.sinceAria')}
            >
              <span className="text-[0.6875rem] text-muted-foreground">{t('map.since')}</span>
              {MAP_SINCE_PRESETS.map((preset) => (
                <button
                  key={preset.id}
                  type="button"
                  onClick={() => setSinceId(preset.id)}
                  aria-pressed={sinceId === preset.id}
                  className={cn(
                    'rounded px-1.5 py-0.5 text-[0.625rem] uppercase tracking-wider transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring',
                    sinceId === preset.id
                      ? 'bg-primary/10 text-primary font-medium'
                      : 'bg-muted hover:bg-accent'
                  )}
                >
                  {preset.id === 'all' ? t('map.sinceAll') : preset.label}
                </button>
              ))}
              <button
                type="button"
                onClick={() => setSinceId('custom')}
                aria-pressed={sinceId === 'custom'}
                className={cn(
                  'rounded px-1.5 py-0.5 text-[0.625rem] uppercase tracking-wider transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring',
                  sinceId === 'custom'
                    ? 'bg-primary/10 text-primary font-medium'
                    : 'bg-muted hover:bg-accent'
                )}
              >
                {t('map.sinceCustom')}
              </button>
              {sinceId === 'custom' && (
                <>
                  <input
                    type="datetime-local"
                    value={customSince}
                    onChange={(e) => setCustomSince(e.target.value)}
                    aria-label={t('map.sinceCustomAria')}
                    className="rounded border border-input bg-background px-1.5 py-0.5 text-[0.6875rem] text-foreground outline-none focus-visible:ring-2 focus-visible:ring-ring"
                  />
                  {customSince && (
                    <button
                      type="button"
                      onClick={() => setCustomSince('')}
                      className="rounded px-1.5 py-0.5 text-[0.625rem] uppercase tracking-wider bg-muted hover:bg-accent transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
                    >
                      {t('map.sinceClear')}
                    </button>
                  )}
                </>
              )}
            </div>
          {directoryEnabled && (
            <label className="flex items-center gap-1.5 cursor-pointer">
              <input
                type="checkbox"
                checked={showInternetRelays}
                onChange={(e) => setShowInternetRelays(e.target.checked)}
                className="rounded border-border"
              />
              <span className="text-[0.6875rem]">{t('map.internetRelays')}</span>
            </label>
          )}
          {directoryEnabled && directoryLoadError && (
            <span className="text-[0.6875rem] text-destructive">{t('map.internetLoadFailed')}</span>
          )}
        </div>
      </div>

      {/* Map */}
      <div
        className="flex-1 relative"
        style={{ zIndex: 0 }}
        role="img"
        aria-label={t('map.mapAria')}
      >
        <MapContainer
          center={[20, 0]}
          zoom={2}
          minZoom={MAP_MIN_ZOOM}
          maxZoom={MAP_MAX_ZOOM}
          className="h-full w-full"
          style={{ background: activeLayer.background }}
        >
          {/* Collapsed: the expanded radio list sat permanently over the top-right
              corner of the map. The layers icon is the convention here and gives
              that corner back to the thing people came to look at. */}
          <LayersControl position="topright">
            {TILE_LAYERS.map((layer) => (
              <LayersControl.BaseLayer
                key={layer.id}
                name={layerNameById(layer.id)}
                checked={layer.id === selectedLayerId}
              >
                <TileLayer
                  url={layer.id === 'dark' ? cartoDarkTileUrl(getSavedCartoApiKey()) : layer.url}
                  attribution={layer.attribution}
                  maxZoom={layer.maxZoom}
                  referrerPolicy={layer.referrerPolicy}
                />
              </LayersControl.BaseLayer>
            ))}
          </LayersControl>
          <LayerChangeWatcher onChange={handleLayerChange} />
          <MaxZoomByActiveLayer maxZoom={activeLayer.maxZoom ?? MAP_MAX_ZOOM} />
          <PersistMapCamera />
          <MapBoundsHandler contacts={mappableContacts} focusedContact={focusedContact} />

          {mappableContacts.map((contact) => {
            const isRepeater = contact.type === CONTACT_TYPE_REPEATER;
            const color = getMarkerColor(contact.last_seen);
            const displayName = contact.name || contact.public_key.slice(0, 12);
            const lastHeardLabel =
              contact.last_seen != null ? formatTime(contact.last_seen) : t('map.neverHeard');
            const radius = isRepeater ? 10 : 7;

            return (
              <Fragment key={contact.public_key}>
                <CircleMarker
                  key={contact.public_key}
                  ref={(ref) => setMarkerRef(contact.public_key, ref)}
                  center={[contact.lat!, contact.lon!]}
                  radius={radius}
                  pathOptions={{
                    color: isRepeater ? MAP_REPEATER_RING : MAP_MARKER_STROKE,
                    fillColor: color,
                    fillOpacity: 0.9,
                    weight: isRepeater ? 3 : 2,
                  }}
                >
                  <Popup>
                    <div className="text-sm">
                      <div className="font-medium flex items-center gap-1">
                        {isRepeater && (
                          <span title={t('map.repeaterTitle')} aria-hidden="true">
                            🛜
                          </span>
                        )}
                        {onSelectContact ? (
                          <button
                            type="button"
                            className="p-0 bg-transparent border-0 font-inherit text-primary underline hover:text-primary/80 cursor-pointer"
                            onClick={(event) => {
                              event.stopPropagation();
                              onSelectContact(contact);
                            }}
                            title={t('map.openConversation', { name: displayName })}
                          >
                            {displayName}
                          </button>
                        ) : (
                          displayName
                        )}
                      </div>
                      <div className="text-xs text-gray-500 mt-1">
                        {t('map.lastHeard', { time: lastHeardLabel })}
                      </div>
                      <div className="text-xs text-gray-400 mt-1 font-mono">
                        {contact.lat!.toFixed(5)}, {contact.lon!.toFixed(5)}
                      </div>
                    </div>
                  </Popup>
                </CircleMarker>
              </Fragment>
            );
          })}

          {overlayDirectoryNodes.map((node) => (
            <CircleMarker
              key={`directory-${node.public_key}`}
              center={[node.lat, node.lon]}
              radius={10}
              pathOptions={{
                color: MAP_DIRECTORY_COLOR,
                fillColor: MAP_RECENCY_COLORS.today,
                fillOpacity: 0.9,
                weight: 3,
              }}
            >
              <Popup>
                <div className="text-sm" data-testid="directory-map-marker">
                  <div className="font-medium flex items-center gap-1">
                    <DirectoryGlobeIcon />
                    {node.name}
                  </div>
                  <div className="text-xs text-gray-500 mt-1">{t('map.internetSource')}</div>
                  <div className="text-xs text-gray-400 mt-1 font-mono">
                    {node.lat.toFixed(5)}, {node.lon.toFixed(5)}
                  </div>
                </div>
              </Popup>
            </CircleMarker>
          ))}
        </MapContainer>
      </div>
    </div>
  );
}
