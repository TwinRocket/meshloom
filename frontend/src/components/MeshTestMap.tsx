import { useEffect, useMemo, useRef } from 'react';
import { useTranslation } from 'react-i18next';
import {
  CircleMarker,
  MapContainer,
  Marker,
  Polyline,
  Popup,
  TileLayer,
  Tooltip,
  useMap,
} from 'react-leaflet';
import { divIcon } from 'leaflet';
import 'leaflet/dist/leaflet.css';

import {
  OSM_RASTER_REFERRER_POLICY,
  OSM_RASTER_TILE_ATTRIBUTION,
  OSM_RASTER_TILE_URL,
} from '../utils/mapTiles';
import { nodeRoleStyle } from './live/liveRender';
import { meshTestPathPoints, type MeshTestObserver } from '../utils/meshTest';

/**
 * The reach of one test flood, drawn.
 *
 * Separate from MapView because it answers a different question: not where the
 * mesh is, but which of it heard one packet and by what route. Every line here
 * belongs to a path, and a path is only ever drawn between points that reported a
 * position — a hop that reported none leaves a longer straight segment rather than
 * a plausible-looking detour through somewhere it may never have been.
 */

const ORIGIN_COLOR = '#3b82f6';
const HOP_COLOR = '#f97316';
const FAINT_PATH = { color: '#94a3b8', weight: 1.5, opacity: 0.28 };
const SELECTED_PATH = { color: '#8b5cf6', weight: 3, opacity: 0.95 };

export interface MeshTestMapProps {
  origin: { lat: number; lon: number } | null;
  observers: MeshTestObserver[];
  selectedKey: string | null;
  onSelect: (key: string) => void;
}

function InvalidateOnResize() {
  const map = useMap();
  useEffect(() => {
    const container = map.getContainer();
    const observer = new ResizeObserver(() => {
      map.invalidateSize();
    });
    observer.observe(container);
    const timer = window.setTimeout(() => {
      map.invalidateSize();
    }, 150);
    return () => {
      observer.disconnect();
      window.clearTimeout(timer);
    };
  }, [map]);
  return null;
}

/** Fit once per distinct point set, so a poll that changes nothing does not re-zoom. */
function FitBounds({ points }: { points: [number, number][] }) {
  const map = useMap();
  const fittedKey = useRef<string | null>(null);
  const key = points.map((point) => point.join(',')).join('|');

  useEffect(() => {
    if (points.length === 0 || fittedKey.current === key) return;
    fittedKey.current = key;
    if (points.length === 1) {
      map.setView(points[0], 11);
      return;
    }
    map.fitBounds(points, { padding: [32, 32], maxZoom: 13 });
  }, [map, key, points]);

  return null;
}

const MLC_ICON_SIZE = 18;

function mlcIcon(selected: boolean) {
  const size = selected ? MLC_ICON_SIZE + 6 : MLC_ICON_SIZE;
  return divIcon({
    className: 'mesh-test-mlc-marker',
    html: `<img src="./meshloom-mark.svg" alt="" style="width:${size}px;height:${size}px" />`,
    iconSize: [size, size],
    iconAnchor: [size / 2, size / 2],
  });
}

export function MeshTestMap({ origin, observers, selectedKey, onSelect }: MeshTestMapProps) {
  const { t } = useTranslation();

  const paths = useMemo(
    () =>
      observers.map((observer) => ({
        observer,
        points: meshTestPathPoints(observer, origin),
      })),
    [observers, origin]
  );

  const points: [number, number][] = [];
  if (origin) points.push([origin.lat, origin.lon]);
  for (const path of paths) {
    for (const point of path.points) {
      points.push([point.lat, point.lon]);
    }
  }
  if (points.length === 0) {
    return (
      <div className="flex h-full items-center justify-center bg-muted/20 px-6 text-center text-sm text-muted-foreground">
        {t('meshTest.mapEmpty')}
      </div>
    );
  }

  const selectedPath = paths.find(({ observer }) => observer.key === selectedKey);

  return (
    <div className="h-full w-full" role="img" aria-label={t('meshTest.mapAria')}>
      <MapContainer
        center={points[0]}
        zoom={6}
        className="h-full w-full"
        style={{ background: '#1a1a2e' }}
      >
        <InvalidateOnResize />
        <FitBounds points={points} />
        <TileLayer
          attribution={OSM_RASTER_TILE_ATTRIBUTION}
          url={OSM_RASTER_TILE_URL}
          referrerPolicy={OSM_RASTER_REFERRER_POLICY}
        />

        {paths.map(({ observer, points: pathPoints }) =>
          pathPoints.length >= 2 ? (
            <Polyline
              key={`path-${observer.key}`}
              positions={pathPoints.map((point) => [point.lat, point.lon] as [number, number])}
              pathOptions={observer.key === selectedKey ? SELECTED_PATH : FAINT_PATH}
            />
          ) : null
        )}

        {origin && (
          <CircleMarker
            center={[origin.lat, origin.lon]}
            radius={8}
            pathOptions={{ color: '#fff', fillColor: ORIGIN_COLOR, fillOpacity: 0.95, weight: 2 }}
          >
            <Popup>
              <span className="text-sm">{t('meshTest.origin')}</span>
            </Popup>
          </CircleMarker>
        )}

        {/* Numbered hops belong to the path being read, not to all of them at once:
            eight overlapping "1"s say less than none. */}
        {selectedPath?.points
          .filter((point) => point.hopIndex !== null)
          .map((point) => (
            <CircleMarker
              key={`hop-${selectedPath.observer.key}-${point.hopIndex}`}
              center={[point.lat, point.lon]}
              radius={7}
              pathOptions={{ color: '#fff', fillColor: HOP_COLOR, fillOpacity: 0.92, weight: 1 }}
            >
              <Tooltip permanent direction="center" className="observer-reach-hop-label">
                {String((point.hopIndex ?? 0) + 1)}
              </Tooltip>
              <Popup>
                <span className="text-sm">
                  {t('path.hop', { n: (point.hopIndex ?? 0) + 1 })}
                  {point.label ? ` · ${point.label}` : ''}
                </span>
              </Popup>
            </CircleMarker>
          ))}

        {observers.map((observer) => {
          if (observer.lat == null || observer.lon == null) return null;
          const selected = observer.key === selectedKey;
          const center: [number, number] = [observer.lat, observer.lon];
          const popup = (
            <Popup>
              <span className="text-sm">{observer.name}</span>
            </Popup>
          );
          if (observer.isMLC) {
            return (
              <Marker
                key={observer.key}
                position={center}
                icon={mlcIcon(selected)}
                eventHandlers={{ click: () => onSelect(observer.key) }}
              >
                {popup}
              </Marker>
            );
          }
          const style = nodeRoleStyle(observer.role ?? undefined);
          return (
            <CircleMarker
              key={observer.key}
              center={center}
              radius={selected ? 9 : 6}
              pathOptions={{
                color: selected ? '#fff' : '#000',
                fillColor: style.color,
                fillOpacity: 0.9,
                weight: selected ? 2 : 1,
              }}
              eventHandlers={{ click: () => onSelect(observer.key) }}
            >
              {popup}
            </CircleMarker>
          );
        })}
      </MapContainer>
    </div>
  );
}
