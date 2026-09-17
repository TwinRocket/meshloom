import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';
import {
  MapContainer,
  TileLayer,
  CircleMarker,
  Popup,
  Tooltip,
  Polyline,
  useMap,
} from 'react-leaflet';
import type { LatLngBoundsExpression } from 'leaflet';
import 'leaflet/dist/leaflet.css';
import {
  OSM_RASTER_REFERRER_POLICY,
  OSM_RASTER_TILE_ATTRIBUTION,
  OSM_RASTER_TILE_URL,
} from '../utils/mapTiles';

/**
 * Re-tiles on resize, and reports when the container first gains a real size.
 *
 * The map stays mounted while the list view is showing, so it is laid out at 0x0.
 * Leaflet answers a fitBounds on a zero-sized container with its maximum zoom —
 * which is why the map opened on the repeater at street level with every link
 * running off the edge. Framing has to wait for a real size.
 */
function ViewportWatcher({ onSized }: { onSized: () => void }) {
  const map = useMap();
  const sizedRef = useRef(false);
  useEffect(() => {
    const container = map.getContainer();
    const check = () => {
      const { width, height } = container.getBoundingClientRect();
      if (!sizedRef.current && width > 0 && height > 0) {
        sizedRef.current = true;
        onSized();
      }
    };
    const ro = new ResizeObserver(() => {
      map.invalidateSize();
      check();
    });
    ro.observe(container);
    check();
    return () => ro.disconnect();
  }, [map, onSized]);
  return null;
}

/**
 * Frames the nodes once, then leaves the view alone until asked again.
 *
 * The pane used to remount the whole map whenever the neighbour list changed,
 * which threw away pan and zoom and re-fetched every tile on each refresh. The
 * map now survives refreshes — so it must not yank the view on each one either.
 */
function FitBounds({
  bounds,
  neighbourCount,
  recenterToken,
  sizeToken,
  padded,
}: {
  bounds: LatLngBoundsExpression | null;
  neighbourCount: number;
  recenterToken: number;
  sizeToken: number;
  padded?: boolean;
}) {
  const map = useMap();
  // Reframe whenever there is more to show than the last time we framed, and on
  // request. Neighbours arrive after the map mounts — from the cache, then from the
  // radio — so framing only once left the view on the repeater alone at street
  // level with every link running off the edges. Pruning, which lowers the count,
  // deliberately leaves the view alone.
  const framedCountRef = useRef(-1);
  const lastTokenRef = useRef(recenterToken);
  useEffect(() => {
    if (!bounds) return;
    const asked = recenterToken !== lastTokenRef.current;
    lastTokenRef.current = recenterToken;
    const { width, height } = map.getContainer().getBoundingClientRect();
    if (width === 0 || height === 0) return;
    const firstFraming = framedCountRef.current < 0;
    if (!asked && !firstFraming && neighbourCount <= framedCountRef.current) return;
    map.fitBounds(bounds, {
      padding: padded ? [48, 48] : [24, 24],
      maxZoom: 15,
      animate: !firstFraming,
    });
    framedCountRef.current = neighbourCount;
  }, [bounds, map, neighbourCount, padded, recenterToken, sizeToken]);
  return null;
}

/** Leaflet paints SVG attributes, so hand it colours already resolved from the theme. */
function useThemeColors() {
  return useMemo(() => {
    const read = (name: string, fallback: string) => {
      if (typeof window === 'undefined') return fallback;
      const raw = getComputedStyle(document.documentElement).getPropertyValue(name).trim();
      return raw ? `hsl(${raw})` : fallback;
    };
    return {
      self: read('--primary', 'hsl(280 85% 65%)'),
      good: read('--success', 'hsl(142 71% 45%)'),
      fair: read('--warning', 'hsl(46 100% 52%)'),
      poor: read('--destructive', 'hsl(0 72% 51%)'),
      link: read('--muted-foreground', 'hsl(226 10% 60%)'),
      backdrop: read('--background', 'hsl(220 23% 5%)'),
    };
  }, []);
}

/** Share of neighbours the initial view aims to contain. */
const NEIGHBOUR_SHARE = 0.8;
/** Never frame tighter than this, in degrees, or a tight cluster zooms to street level. */
const MIN_SPAN_DEG = 0.02;
/** Breathing room so the outermost included node is not glued to the edge. */
const EDGE_MARGIN = 1.15;

interface Neighbor {
  lat: number | null;
  lon: number | null;
  name: string | null;
  pubkey_prefix: string;
  snr: number;
  distance?: string | null;
}

interface Props {
  neighbors: Neighbor[];
  radioLat?: number | null;
  radioLon?: number | null;
  radioName?: string | null;
  /** Click popups (and permanent labels) include SNR, distance, and GPS. */
  detailed?: boolean;
  /** Keep a label above every mapped point, not only the clicked one. */
  permanent?: boolean;
  /** Bumping this reframes the view on the current nodes. */
  recenterToken?: number;
  className?: string;
}

function formatSnr(snr: number): string {
  return `${snr >= 0 ? '+' : ''}${snr.toFixed(1)} dB`;
}

function NeighborLabel({
  title,
  detailed,
  snr,
  distance,
  lat,
  lon,
}: {
  title: string;
  detailed: boolean;
  snr?: number | null;
  distance?: string | null;
  lat: number;
  lon: number;
}) {
  const { t } = useTranslation();
  if (!detailed) {
    return <span className="text-sm font-medium">{title}</span>;
  }
  return (
    <div className="space-y-0.5 text-left">
      <p className="text-sm font-medium">{title}</p>
      {snr != null && (
        <p className="font-mono text-[0.6875rem]">
          {t('repeater.snr')}: {formatSnr(snr)}
        </p>
      )}
      {distance && (
        <p className="font-mono text-[0.6875rem]">
          {t('repeater.dist')}: {distance}
        </p>
      )}
      <p className="font-mono text-[0.6875rem] text-muted-foreground">
        {t('repeater.gps')}: {lat.toFixed(5)}, {lon.toFixed(5)}
      </p>
    </div>
  );
}

export function NeighborsMiniMap({
  neighbors,
  radioLat,
  radioLon,
  radioName,
  detailed = false,
  permanent = false,
  recenterToken = 0,
  className,
}: Props) {
  const { t } = useTranslation();
  const colors = useThemeColors();
  const [sizeToken, setSizeToken] = useState(0);
  const handleSized = useCallback(() => setSizeToken((n) => n + 1), []);

  const valid = useMemo(
    () =>
      neighbors.filter(
        (n): n is Neighbor & { lat: number; lon: number } => n.lat != null && n.lon != null
      ),
    [neighbors]
  );

  const hasRadio = radioLat != null && radioLon != null && !(radioLat === 0 && radioLon === 0);

  /**
   * Framed on the managed repeater, zoomed to hold most of its neighbours.
   *
   * Fitting every node put the repeater wherever the extremes happened to leave it
   * and let one distant neighbour pull the zoom out to a continental view. The box
   * is symmetric around the repeater — so it stays centred — and sized on the
   * NEIGHBOUR_SHARE-th nearest neighbour, which keeps the far outliers off screen
   * instead of letting them dictate the scale.
   */
  const bounds = useMemo<LatLngBoundsExpression | null>(() => {
    if (!hasRadio) {
      const points: [number, number][] = valid.map((n) => [n.lat, n.lon]);
      return points.length ? points : null;
    }
    const lat0 = radioLat as number;
    const lon0 = radioLon as number;
    if (valid.length === 0) {
      const pad = 0.05;
      return [
        [lat0 - pad, lon0 - pad],
        [lat0 + pad, lon0 + pad],
      ];
    }
    const dLats = valid.map((n) => Math.abs(n.lat - lat0)).sort((a, b) => a - b);
    const dLons = valid.map((n) => Math.abs(n.lon - lon0)).sort((a, b) => a - b);
    const idx = Math.max(0, Math.ceil(NEIGHBOUR_SHARE * valid.length) - 1);
    // A floor keeps a cluster of very close neighbours from zooming to street level.
    const spanLat = Math.max(dLats[idx], MIN_SPAN_DEG);
    const spanLon = Math.max(dLons[idx], MIN_SPAN_DEG);
    return [
      [lat0 - spanLat * EDGE_MARGIN, lon0 - spanLon * EDGE_MARGIN],
      [lat0 + spanLat * EDGE_MARGIN, lon0 + spanLon * EDGE_MARGIN],
    ];
  }, [hasRadio, radioLat, radioLon, valid]);

  if (valid.length === 0 && !hasRadio) return null;

  const center: [number, number] = hasRadio
    ? [radioLat as number, radioLon as number]
    : [valid[0].lat, valid[0].lon];
  const snrColor = (snr: number) => (snr >= 6 ? colors.good : snr >= 0 ? colors.fair : colors.poor);
  const labelClass = detailed
    ? 'neighbor-map-label neighbor-map-label-detailed'
    : 'neighbor-map-label';

  return (
    <div
      className={className ?? 'min-h-48 flex-1 overflow-hidden rounded border border-border'}
      role="img"
      aria-label={t('repeater.mapAria')}
    >
      <MapContainer
        center={center}
        zoom={10}
        className="h-full w-full"
        style={{ background: colors.backdrop }}
      >
        <ViewportWatcher onSized={handleSized} />
        <FitBounds
          bounds={bounds}
          neighbourCount={valid.length}
          recenterToken={recenterToken}
          sizeToken={sizeToken}
          padded={permanent || detailed}
        />
        <TileLayer
          attribution={OSM_RASTER_TILE_ATTRIBUTION}
          url={OSM_RASTER_TILE_URL}
          referrerPolicy={OSM_RASTER_REFERRER_POLICY}
        />
        {hasRadio &&
          valid.map((n) => (
            <Polyline
              key={`line-${n.pubkey_prefix}`}
              positions={[
                [radioLat as number, radioLon as number],
                [n.lat, n.lon],
              ]}
              // Coloured by signal and drawn solid: as a faint dashed grey they were
              // invisible over map tiles, and these lines are the zero-hop links —
              // the whole point of the view.
              pathOptions={{ color: snrColor(n.snr), weight: 2.5, opacity: 0.9 }}
            />
          ))}
        {hasRadio && (
          <CircleMarker
            center={[radioLat as number, radioLon as number]}
            radius={8}
            pathOptions={{
              color: colors.self,
              fillColor: colors.self,
              fillOpacity: 1,
              weight: 2,
            }}
          >
            {permanent && (
              <Tooltip
                permanent
                direction="top"
                offset={[0, -10]}
                interactive={false}
                className={labelClass}
              >
                <NeighborLabel
                  title={radioName || t('repeater.ourRadio')}
                  detailed={detailed}
                  lat={radioLat as number}
                  lon={radioLon as number}
                />
              </Tooltip>
            )}
            <Popup>
              <NeighborLabel
                title={radioName || t('repeater.ourRadio')}
                detailed={detailed}
                lat={radioLat as number}
                lon={radioLon as number}
              />
            </Popup>
          </CircleMarker>
        )}
        {valid.map((n) => (
          <CircleMarker
            key={n.pubkey_prefix}
            center={[n.lat, n.lon]}
            radius={6}
            pathOptions={{
              color: colors.backdrop,
              fillColor: snrColor(n.snr),
              fillOpacity: 0.9,
              weight: 1,
            }}
          >
            {permanent && (
              <Tooltip
                permanent
                direction="top"
                offset={[0, -8]}
                interactive={false}
                className={labelClass}
              >
                <NeighborLabel
                  title={n.name || n.pubkey_prefix}
                  detailed={detailed}
                  snr={n.snr}
                  distance={n.distance}
                  lat={n.lat}
                  lon={n.lon}
                />
              </Tooltip>
            )}
            <Popup>
              <NeighborLabel
                title={n.name || n.pubkey_prefix}
                detailed={detailed}
                snr={n.snr}
                distance={n.distance}
                lat={n.lat}
                lon={n.lon}
              />
            </Popup>
          </CircleMarker>
        ))}
      </MapContainer>
    </div>
  );
}
