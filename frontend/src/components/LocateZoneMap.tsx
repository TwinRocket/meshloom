import { useEffect, useMemo } from 'react';
import {
  Circle,
  CircleMarker,
  MapContainer,
  Polygon,
  Popup,
  TileLayer,
  useMap,
} from 'react-leaflet';
import 'leaflet/dist/leaflet.css';

import type { LocateAnchor, LocateDeclaredGps } from '../types';
import { diskBounds } from '../utils/locateOverlay';
import { disksToIntersectionRing, snrToOpacity } from '../utils/locateZone';
import {
  OSM_RASTER_REFERRER_POLICY,
  OSM_RASTER_TILE_ATTRIBUTION,
  OSM_RASTER_TILE_URL,
} from '../utils/mapTiles';

function InvalidateOnResize() {
  const map = useMap();
  useEffect(() => {
    const container = map.getContainer();
    const ro = new ResizeObserver(() => {
      map.invalidateSize();
    });
    ro.observe(container);
    return () => ro.disconnect();
  }, [map]);
  return null;
}

function FitDisks({
  anchors,
  declaredGps,
}: {
  anchors: LocateAnchor[];
  declaredGps: LocateDeclaredGps | null;
}) {
  const map = useMap();
  useEffect(() => {
    const points = diskBounds(anchors, declaredGps);
    if (points.length === 0) return;
    if (points.length === 1) {
      map.setView(points[0], 9);
      return;
    }
    map.fitBounds(points, { padding: [28, 28], maxZoom: 11 });
  }, [anchors, declaredGps, map]);
  return null;
}

const KIND_COLOR: Record<LocateAnchor['kind'], string> = {
  local_0hop: '#38bdf8',
  first_hop: '#fb923c',
  corescope_0hop: '#a78bfa',
};

interface LocateZoneMapProps {
  anchors: LocateAnchor[];
  declaredGps: LocateDeclaredGps | null;
  showDisks: boolean;
}

export function LocateZoneMap({ anchors, declaredGps, showDisks }: LocateZoneMapProps) {
  const ring = useMemo(
    () =>
      disksToIntersectionRing(
        anchors.map((anchor) => ({ lat: anchor.lat, lon: anchor.lon, radiusKm: anchor.radius_km }))
      ),
    [anchors]
  );
  const center: [number, number] = anchors[0]
    ? [anchors[0].lat, anchors[0].lon]
    : declaredGps
      ? [declaredGps.lat, declaredGps.lon]
      : [20, 0];

  return (
    <div
      className="min-h-72 flex-1 overflow-hidden rounded border border-border"
      role="img"
      aria-label="RF locate coverage zone"
      data-testid="locate-zone-map"
    >
      <MapContainer
        center={center}
        zoom={anchors.length ? 8 : 3}
        className="h-full w-full"
        style={{ background: '#1a1a2e' }}
      >
        <InvalidateOnResize />
        <FitDisks anchors={anchors} declaredGps={declaredGps} />
        <TileLayer
          attribution={OSM_RASTER_TILE_ATTRIBUTION}
          url={OSM_RASTER_TILE_URL}
          referrerPolicy={OSM_RASTER_REFERRER_POLICY}
        />
        {showDisks &&
          anchors.map((anchor, index) => (
            <Circle
              key={`disk-${anchor.kind}-${anchor.public_key ?? index}`}
              center={[anchor.lat, anchor.lon]}
              radius={anchor.radius_km * 1000}
              pathOptions={{
                color: KIND_COLOR[anchor.kind],
                weight: 1,
                fillColor: KIND_COLOR[anchor.kind],
                fillOpacity: 0.08,
                opacity: 0.55,
              }}
            />
          ))}
        {ring && (
          <Polygon
            positions={ring}
            pathOptions={{
              color: '#eab308',
              weight: 2,
              fillColor: '#eab308',
              fillOpacity: 0.28,
              dashArray: '6 4',
            }}
          />
        )}
        {anchors.map((anchor, index) => (
          <CircleMarker
            key={`pin-${anchor.kind}-${anchor.public_key ?? index}`}
            center={[anchor.lat, anchor.lon]}
            radius={7}
            pathOptions={{
              color: '#0f172a',
              weight: 1,
              fillColor: KIND_COLOR[anchor.kind],
              fillOpacity: snrToOpacity(anchor.snr),
            }}
          >
            <Popup>
              <div className="text-sm">{anchor.name}</div>
              <div className="text-xs text-muted-foreground">{anchor.kind}</div>
            </Popup>
          </CircleMarker>
        ))}
        {declaredGps && (
          <CircleMarker
            center={[declaredGps.lat, declaredGps.lon]}
            radius={8}
            pathOptions={{ color: '#fff', weight: 2, fillColor: '#22c55e', fillOpacity: 1 }}
          >
            <Popup>
              <div className="text-sm" data-testid="locate-declared-gps">
                {declaredGps.source}
              </div>
            </Popup>
          </CircleMarker>
        )}
      </MapContainer>
    </div>
  );
}
