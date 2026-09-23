import { useEffect, useMemo, useRef } from 'react';
import { useTranslation } from 'react-i18next';
import {
  CircleMarker,
  MapContainer,
  Marker,
  Polyline,
  Popup,
  Tooltip,
  useMap,
} from 'react-leaflet';
import { divIcon } from 'leaflet';
import 'leaflet/dist/leaflet.css';

import { nodeRoleStyle } from './live/liveRender';
import { MAP_MAX_ZOOM, MAP_MIN_ZOOM, MapBasemapLayers, useMapBasemap } from './mapBasemap';
import { cn } from '../lib/utils';
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
const BACKING = '#000';
const FAINT_PATH = { color: '#94a3b8', weight: 2, opacity: 0.55 };
const SELECTED_PATH = { color: '#8b5cf6', weight: 3.5, opacity: 0.95 };
const EAR_RADIUS = 9;
const BACKING_PAD = 4;

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

const MLC_ICON_SIZE = 36;

function mlcIcon(selected: boolean) {
  const size = selected ? MLC_ICON_SIZE + 8 : MLC_ICON_SIZE;
  const logo = size - 10;
  return divIcon({
    className: 'mesh-test-mlc-marker',
    html: `<img src="./meshloom-mark.svg" alt="" width="${logo}" height="${logo}" />`,
    iconSize: [size, size],
    iconAnchor: [size / 2, size / 2],
  });
}

function backingOptions() {
  return { color: BACKING, fillColor: BACKING, fillOpacity: 1, weight: 0 };
}

export function MeshTestMap({ origin, observers, selectedKey, onSelect }: MeshTestMapProps) {
  const { t } = useTranslation();
  const basemap = useMapBasemap();

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
        minZoom={MAP_MIN_ZOOM}
        maxZoom={MAP_MAX_ZOOM}
        className={cn('h-full w-full', basemap.inverted && 'basemap-inverted')}
        style={{ background: basemap.activeLayer.background }}
      >
        <InvalidateOnResize />
        <FitBounds points={points} />
        <MapBasemapLayers
          selectedLayerId={basemap.selectedLayerId}
          onLayerChange={basemap.handleLayerChange}
          layerName={basemap.layerName}
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
            radius={12}
            pathOptions={backingOptions()}
            interactive={false}
          />
        )}
        {origin && (
          <CircleMarker
            center={[origin.lat, origin.lon]}
            radius={8}
            pathOptions={{ color: '#fff', fillColor: ORIGIN_COLOR, fillOpacity: 1, weight: 2 }}
          >
            <Popup>
              <span className="text-sm">{t('meshTest.origin')}</span>
            </Popup>
          </CircleMarker>
        )}

        {selectedPath?.points
          .filter((point) => point.hopIndex !== null)
          .map((point) => (
            <CircleMarker
              key={`hop-back-${selectedPath.observer.key}-${point.hopIndex}`}
              center={[point.lat, point.lon]}
              radius={11}
              pathOptions={backingOptions()}
              interactive={false}
            />
          ))}
        {selectedPath?.points
          .filter((point) => point.hopIndex !== null)
          .map((point) => (
            <CircleMarker
              key={`hop-${selectedPath.observer.key}-${point.hopIndex}`}
              center={[point.lat, point.lon]}
              radius={8}
              pathOptions={{ color: '#fff', fillColor: HOP_COLOR, fillOpacity: 1, weight: 2 }}
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
          if (observer.lat == null || observer.lon == null || observer.isMLC) return null;
          const selected = observer.key === selectedKey;
          return (
            <CircleMarker
              key={`back-${observer.key}`}
              center={[observer.lat, observer.lon]}
              radius={(selected ? EAR_RADIUS + 3 : EAR_RADIUS) + BACKING_PAD}
              pathOptions={backingOptions()}
              interactive={false}
            />
          );
        })}

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
              radius={selected ? EAR_RADIUS + 3 : EAR_RADIUS}
              pathOptions={{
                color: '#fff',
                fillColor: style.color,
                fillOpacity: 1,
                weight: 2,
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
