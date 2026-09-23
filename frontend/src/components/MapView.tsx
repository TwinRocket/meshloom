import { Fragment, useEffect, useState, useMemo, useRef, useCallback } from 'react';
import type { MutableRefObject } from 'react';
import { useTranslation } from 'react-i18next';
import { MapContainer, CircleMarker, Marker, Popup, useMap } from 'react-leaflet';
import L from 'leaflet';
import type {
  LatLngBoundsExpression,
  CircleMarker as LeafletCircleMarker,
  Marker as LeafletMarker,
} from 'leaflet';
import 'leaflet/dist/leaflet.css';
import type { Contact, DirectoryMapNode, RadioConfig } from '../types';
import { api } from '../api';
import { formatTime } from '../utils/messageParser';
import { isValidLocation } from '../utils/pathUtils';
import { CONTACT_TYPE_REPEATER, CONTACT_TYPE_SENSOR } from '../types';
import { NODE_ROLE_STYLE } from './live/liveRender';
import { Maximize2 } from 'lucide-react';
import { DirectoryGlobeIcon } from './messagePath/DirectoryGlobeIcon';
import { cn } from '@/lib/utils';
import { MAP_MAX_ZOOM, MAP_MIN_ZOOM, MapBasemapLayers, useMapBasemap } from './mapBasemap';

export { DARK_BASEMAP_LAYER_ID } from './mapBasemap';

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
  /** True when the Community directory answers. Overlay stays off until the map checkbox is ticked. */
  directoryEnabled?: boolean;
}

// --- Tile layer presets ---
// Providers here are free. CARTO dark raster tiles optionally take a free API
// key (Settings → Local); without one they may show a watermark. Other layers
// work without a key. Attribution strings follow each provider's requirements;
// do not remove them. If you add a new provider, verify its terms of service
// (especially for Esri / Google-style satellite tiles) before committing.
// Raster-only transitional fix: Neighbors / Locate / Path stay on OSM.

const MAP_RECENCY_COLORS = {
  recent: '#06b6d4',
  today: '#2563eb',
  stale: '#f59e0b',
  old: '#64748b',
} as const;
const MAP_MARKER_STROKE = '#0f172a';
const MAP_REPEATER_RING = '#f8fafc';
const MAP_DIRECTORY_COLOR = '#f97316';
/** Same teal as `#live` NODE_ROLE_STYLE.sensor — role colour, not a second palette. */
const MAP_SENSOR_FILL = NODE_ROLE_STYLE.sensor.color;

type MapFocusMarker = Pick<LeafletCircleMarker | LeafletMarker, 'openPopup'>;

function makeSensorTriangleIcon(): L.DivIcon {
  // CircleMarker cannot be a triangle. DivIcon SVG matches `#live` RoleShapeIcon.
  return L.divIcon({
    className: 'map-sensor-marker',
    iconSize: [16, 16],
    iconAnchor: [8, 10],
    html: `<svg width="16" height="16" viewBox="0 0 10 10" aria-hidden="true"><polygon points="5,1.4 8.8,8.4 1.2,8.4" fill="${MAP_SENSOR_FILL}" stroke="${MAP_MARKER_STROKE}" stroke-width="0.8" stroke-linejoin="round"/></svg>`,
  });
}

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

/** Padding around the fitted bounds, and how close in a fit is allowed to go. */
const FIT_PADDING: [number, number] = [50, 50];
const FIT_MAX_ZOOM = 12;
const SINGLE_POINT_ZOOM = 10;
const EMPTY_MAP_VIEW: { center: [number, number]; zoom: number } = { center: [20, 0], zoom: 2 };

/**
 * Frames every node on arrival, and says so when the view has left some behind.
 *
 * Arriving on the map means wanting to see the mesh, so the map always opens on
 * all of it rather than on wherever the last visit happened to end — a restored
 * camera could be a rooftop in another country, with nothing on screen and no
 * hint that anything was missing. Panning away afterwards is deliberate and is
 * left alone; the button is how you get the whole picture back.
 */
function FitAllNodes({
  contacts,
  focusedContact,
  onDriftChange,
  fitRef,
}: {
  contacts: Contact[];
  focusedContact: Contact | null;
  onDriftChange: (drifted: boolean) => void;
  fitRef: MutableRefObject<(() => void) | null>;
}) {
  const map = useMap();
  const lastFocusKey = useRef<string | null>(null);
  const hasFitted = useRef(false);
  const readerTookOver = useRef(false);

  const points = useMemo(
    () => contacts.map((c) => [c.lat!, c.lon!] as [number, number]),
    [contacts]
  );

  const fitAll = useCallback(() => {
    // The pane's height settles after the map is created — on iOS a frame or two
    // later — and a fit computed against the wrong box lands somewhere with no
    // nodes in it at all.
    map.invalidateSize({ animate: false });
    if (points.length === 0) {
      map.setView(EMPTY_MAP_VIEW.center, EMPTY_MAP_VIEW.zoom);
      return;
    }
    if (points.length === 1) {
      map.setView(points[0], SINGLE_POINT_ZOOM);
      return;
    }
    map.fitBounds(points as LatLngBoundsExpression, {
      padding: FIT_PADDING,
      maxZoom: FIT_MAX_ZOOM,
    });
  }, [map, points]);

  useEffect(() => {
    fitRef.current = fitAll;
    return () => {
      fitRef.current = null;
    };
  }, [fitAll, fitRef]);

  useEffect(() => {
    if (focusedContact && focusedContact.lat != null && focusedContact.lon != null) {
      if (lastFocusKey.current !== focusedContact.public_key) {
        map.setView([focusedContact.lat, focusedContact.lon], FIT_MAX_ZOOM);
        lastFocusKey.current = focusedContact.public_key;
      }
      hasFitted.current = true;
      return;
    }
    lastFocusKey.current = null;

    // Points arrive in batches as contacts load; refit until there are some to
    // frame, then leave the view to the reader.
    if (hasFitted.current && points.length > 0) return;
    fitAll();
    if (points.length > 0) hasFitted.current = true;
  }, [map, fitAll, points, focusedContact]);

  // Until the reader takes the map over, a resize means the first fit was measured
  // against a box that has since changed, so it is worth redoing.
  useEffect(() => {
    const refit = () => {
      if (readerTookOver.current) return;
      fitAll();
    };
    const takeOver = () => {
      readerTookOver.current = true;
    };
    map.on('resize', refit);
    map.on('dragstart', takeOver);
    return () => {
      map.off('resize', refit);
      map.off('dragstart', takeOver);
    };
  }, [map, fitAll]);

  // Drift is "a node is off screen", not "the camera moved": zooming into a
  // cluster that still holds everything is not something to offer undoing.
  useEffect(() => {
    const check = () => {
      if (points.length === 0) {
        onDriftChange(false);
        return;
      }
      const view = map.getBounds();
      onDriftChange(points.some(([lat, lon]) => !view.contains([lat, lon])));
    };
    check();
    map.on('moveend', check);
    map.on('zoomend', check);
    return () => {
      map.off('moveend', check);
      map.off('zoomend', check);
    };
  }, [map, points, onDriftChange]);

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
  const [nodesOffScreen, setNodesOffScreen] = useState(false);
  const fitAllRef = useRef<(() => void) | null>(null);
  const [sinceId, setSinceId] = useState<MapSinceId>(getSavedSinceId);
  const [customSince, setCustomSince] = useState('');
  const [nowSec, setNowSec] = useState(() => Date.now() / 1000);
  const basemap = useMapBasemap();

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
  const markerRefs = useRef<Record<string, MapFocusMarker | null>>({});

  const setMarkerRef = useCallback((key: string, ref: MapFocusMarker | null) => {
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
        {/* Only once something is actually out of frame: a control that is always
            there is one more thing between the reader and the map. */}
        {nodesOffScreen && (
          <button
            type="button"
            onClick={() => fitAllRef.current?.()}
            className="liquid-surface absolute bottom-[calc(var(--bottom-nav-height)+0.75rem)] left-1/2 z-[500] inline-flex -translate-x-1/2 items-center gap-2 rounded-full px-4 py-2 text-sm font-medium text-foreground shadow-lg focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring md:bottom-4"
          >
            <Maximize2 className="h-4 w-4" aria-hidden="true" />
            {t('map.fitAllNodes')}
          </button>
        )}
        <MapContainer
          center={[20, 0]}
          zoom={2}
          minZoom={MAP_MIN_ZOOM}
          maxZoom={MAP_MAX_ZOOM}
          className={cn('h-full w-full', basemap.inverted && 'basemap-inverted')}
          style={{ background: basemap.activeLayer.background }}
        >
          {/* Collapsed: the expanded radio list sat permanently over the top-right
              corner of the map. The layers icon is the convention here and gives
              that corner back to the thing people came to look at. */}
          <MapBasemapLayers
            selectedLayerId={basemap.selectedLayerId}
            onLayerChange={basemap.handleLayerChange}
            layerName={basemap.layerName}
          />
          <FitAllNodes
            contacts={mappableContacts}
            focusedContact={focusedContact}
            onDriftChange={setNodesOffScreen}
            fitRef={fitAllRef}
          />

          {mappableContacts.map((contact) => {
            const isRepeater = contact.type === CONTACT_TYPE_REPEATER;
            const isSensor = contact.type === CONTACT_TYPE_SENSOR;
            const color = getMarkerColor(contact.last_seen);
            const displayName = contact.name || contact.public_key.slice(0, 12);
            const lastHeardLabel =
              contact.last_seen != null ? formatTime(contact.last_seen) : t('map.neverHeard');
            const radius = isRepeater ? 10 : 7;
            const popup = (
              <Popup>
                <div className="text-sm" data-testid={isSensor ? 'map-sensor-marker' : undefined}>
                  <div className="font-medium flex items-center gap-1">
                    {isRepeater && (
                      <span title={t('map.repeaterTitle')} aria-hidden="true">
                        🛜
                      </span>
                    )}
                    {isSensor && (
                      <span title={t('map.sensorTitle')} aria-hidden="true">
                        <svg width="10" height="10" viewBox="0 0 10 10">
                          <polygon points="5,1.4 8.8,8.4 1.2,8.4" fill={MAP_SENSOR_FILL} />
                        </svg>
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
            );

            return (
              <Fragment key={contact.public_key}>
                {isSensor ? (
                  <Marker
                    ref={(ref) => setMarkerRef(contact.public_key, ref)}
                    position={[contact.lat!, contact.lon!]}
                    icon={makeSensorTriangleIcon()}
                  >
                    {popup}
                  </Marker>
                ) : (
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
                    {popup}
                  </CircleMarker>
                )}
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
