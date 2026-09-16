import { useEffect, useRef, type ReactNode } from 'react';
import { useTranslation } from 'react-i18next';
import {
  CircleMarker,
  MapContainer,
  Polyline,
  Popup,
  TileLayer,
  Tooltip,
  useMap,
} from 'react-leaflet';
import 'leaflet/dist/leaflet.css';

import type { ObserverReachEntry, ObserverReachMapHop } from '../types';
import {
  OSM_RASTER_REFERRER_POLICY,
  OSM_RASTER_TILE_ATTRIBUTION,
  OSM_RASTER_TILE_URL,
} from '../utils/mapTiles';

const HOP_COLORS = [
  '#f97316',
  '#eab308',
  '#22c55e',
  '#06b6d4',
  '#ec4899',
  '#f43f5e',
  '#a855f7',
  '#64748b',
];
const ORIGIN_COLOR = '#3b82f6';
const SELECTED_OBSERVER_COLOR = '#8b5cf6';

export interface ObserverReachMapProps {
  observers: ObserverReachEntry[];
  origin: { lat: number; lon: number } | null;
  selectedKey: string | null;
  selectedHops: ObserverReachMapHop[];
  observerKey: (observer: ObserverReachEntry, index: number) => string;
  onSelect: (key: string) => void;
}

function hopColor(index: number): string {
  return HOP_COLORS[index % HOP_COLORS.length];
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

function RouteMapBounds({ points }: { points: [number, number][] }) {
  const map = useMap();
  const fittedKey = useRef<string | null>(null);
  const key = points.map((point) => point.join(',')).join('|');

  useEffect(() => {
    if (points.length === 0 || fittedKey.current === key) return;
    fittedKey.current = key;
    if (points.length === 1) {
      map.setView(points[0], 12);
      return;
    }
    map.fitBounds(points, { padding: [28, 28], maxZoom: 14 });
  }, [map, key, points]);

  return null;
}

function NumberedMarker({
  center,
  label,
  color,
  selected = false,
  onClick,
  children,
}: {
  center: [number, number];
  label: string;
  color: string;
  selected?: boolean;
  onClick?: () => void;
  children?: ReactNode;
}) {
  return (
    <CircleMarker
      center={center}
      radius={selected ? 11 : 8}
      pathOptions={{
        color: selected ? '#fff' : '#000',
        fillColor: color,
        fillOpacity: 0.92,
        weight: selected ? 2 : 1,
      }}
      eventHandlers={onClick ? { click: onClick } : undefined}
    >
      <Tooltip permanent direction="center" className="observer-reach-hop-label">
        {label}
      </Tooltip>
      {children}
    </CircleMarker>
  );
}

export function ObserverReachMap({
  observers,
  origin,
  selectedKey,
  selectedHops,
  observerKey,
  onSelect,
}: ObserverReachMapProps) {
  const { t } = useTranslation();
  const selected = observers.find(
    (observer, index) => observerKey(observer, index) === selectedKey
  );
  const mappedObservers = observers
    .map((observer, index) => ({ observer, index }))
    .filter(({ observer, index }) => !selectedKey || observerKey(observer, index) === selectedKey);

  const points: [number, number][] = [];
  if (origin) points.push([origin.lat, origin.lon]);
  for (const { observer } of mappedObservers) {
    if (observer.lat != null && observer.lon != null) {
      points.push([observer.lat, observer.lon]);
    }
  }
  for (const hop of selectedHops) {
    points.push([hop.lat, hop.lon]);
  }
  if (points.length === 0) return null;

  const linePoints: [number, number][] = [];
  if (origin) linePoints.push([origin.lat, origin.lon]);
  for (const hop of selectedHops) {
    linePoints.push([hop.lat, hop.lon]);
  }
  if (selected?.lat != null && selected.lon != null) {
    linePoints.push([selected.lat, selected.lon]);
  }

  return (
    <div
      className="h-56 rounded border border-border overflow-hidden"
      role="img"
      aria-label={t('messageList.observerReachMapAria')}
    >
      <MapContainer
        center={points[0]}
        zoom={6}
        className="h-56 w-full"
        style={{ background: '#1a1a2e' }}
      >
        <InvalidateOnResize />
        <RouteMapBounds points={points} />
        <TileLayer
          attribution={OSM_RASTER_TILE_ATTRIBUTION}
          url={OSM_RASTER_TILE_URL}
          referrerPolicy={OSM_RASTER_REFERRER_POLICY}
        />
        {linePoints.length >= 2 && (
          <Polyline
            positions={linePoints}
            pathOptions={{ color: '#94a3b8', weight: 2, opacity: 0.7 }}
          />
        )}
        {origin && (
          <NumberedMarker center={[origin.lat, origin.lon]} label="S" color={ORIGIN_COLOR}>
            <Popup>
              <span className="text-sm">{t('messageList.observerReachOrigin')}</span>
            </Popup>
          </NumberedMarker>
        )}
        {selectedHops.map((hop) => (
          <NumberedMarker
            key={`hop-${hop.hopIndex}-${hop.prefix}`}
            center={[hop.lat, hop.lon]}
            label={String(hop.hopIndex + 1)}
            color={hopColor(hop.hopIndex)}
          >
            <Popup>
              <span className="font-mono">{hop.prefix}</span>
              {hop.name ? ` · ${hop.name}` : ''}
            </Popup>
          </NumberedMarker>
        ))}
        {mappedObservers.map(({ observer, index }) => {
          if (observer.lat == null || observer.lon == null) return null;
          const key = observerKey(observer, index);
          const selectedMarker = key === selectedKey;
          const hopLabel = observer.hops != null ? String(observer.hops) : '?';
          return (
            <NumberedMarker
              key={key}
              center={[observer.lat, observer.lon]}
              label={hopLabel}
              color={
                selectedMarker
                  ? SELECTED_OBSERVER_COLOR
                  : hopColor(Math.max(0, (observer.hops ?? 1) - 1))
              }
              selected={selectedMarker}
              onClick={() => onSelect(key)}
            >
              <Popup>
                <span className="text-sm">{observer.name}</span>
                {observer.hops != null && (
                  <span className="block text-xs text-muted-foreground">
                    {observer.hops === 0
                      ? t('messageList.observerReachDirect')
                      : t('messageList.observerReachHops', { count: observer.hops })}
                  </span>
                )}
              </Popup>
            </NumberedMarker>
          );
        })}
      </MapContainer>
    </div>
  );
}
