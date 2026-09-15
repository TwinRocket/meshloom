import { useEffect, useRef } from 'react';
import { useTranslation } from 'react-i18next';
import { MapContainer, TileLayer, Marker, Tooltip, useMap } from 'react-leaflet';
import L from 'leaflet';
import 'leaflet/dist/leaflet.css';
import { directoryHopLocation, isValidLocation } from '../utils/pathUtils';
import {
  OSM_RASTER_REFERRER_POLICY,
  OSM_RASTER_TILE_ATTRIBUTION,
  OSM_RASTER_TILE_URL,
} from '../utils/mapTiles';
import type { DirectoryHopHit, ResolvedPath, SenderInfo } from '../utils/pathUtils';

interface PathRouteMapProps {
  resolved: ResolvedPath;
  senderInfo: SenderInfo;
  directoryHits?: Record<string, DirectoryHopHit>;
  height?: number;
}

// Colors for hop markers (indexed by hop number - 1)
const HOP_COLORS = [
  '#f97316', // Hop 1: orange
  '#eab308', // Hop 2: yellow
  '#22c55e', // Hop 3: green
  '#06b6d4', // Hop 4: cyan
  '#ec4899', // Hop 5: pink
  '#f43f5e', // Hop 6: rose
  '#a855f7', // Hop 7: purple
  '#64748b', // Hop 8: slate
];

const SENDER_COLOR = '#3b82f6'; // blue
const RECEIVER_COLOR = '#8b5cf6'; // violet
const DIRECTORY_RING = '#f97316';
const DIRECTORY_FILL = '#2563eb';

function makeIcon(label: string, color: string, ring = 'rgba(255,255,255,0.8)'): L.DivIcon {
  return L.divIcon({
    className: '',
    iconSize: [24, 24],
    iconAnchor: [12, 12],
    html: `<div style="
      width:24px;height:24px;border-radius:50%;
      background:${color};color:#fff;
      display:flex;align-items:center;justify-content:center;
      font-size:11px;font-weight:700;
      border:2px solid ${ring};
      box-shadow:0 1px 4px rgba(0,0,0,0.4);
    ">${label}</div>`,
  });
}

function getHopColor(hopIndex: number): string {
  return HOP_COLORS[hopIndex % HOP_COLORS.length];
}

/** Collect all valid [lat, lon] points for bounds fitting */
function collectPoints(
  resolved: ResolvedPath,
  directoryHits: Record<string, DirectoryHopHit>
): [number, number][] {
  const pts: [number, number][] = [];
  if (isValidLocation(resolved.sender.lat, resolved.sender.lon)) {
    pts.push([resolved.sender.lat!, resolved.sender.lon!]);
  }
  for (const hop of resolved.hops) {
    let usedLocal = false;
    for (const m of hop.matches) {
      if (isValidLocation(m.lat, m.lon)) {
        pts.push([m.lat!, m.lon!]);
        usedLocal = true;
      }
    }
    if (!usedLocal) {
      const directory = directoryHopLocation(hop, directoryHits[hop.prefix]);
      if (directory) pts.push([directory.lat, directory.lon]);
    }
  }
  if (isValidLocation(resolved.receiver.lat, resolved.receiver.lon)) {
    pts.push([resolved.receiver.lat!, resolved.receiver.lon!]);
  }
  return pts;
}

/** Fit map bounds once on mount, then let the user pan/zoom freely */
function RouteMapBounds({ points }: { points: [number, number][] }) {
  const map = useMap();
  const fitted = useRef(false);

  useEffect(() => {
    if (fitted.current || points.length === 0) return;
    fitted.current = true;
    if (points.length === 1) {
      map.setView(points[0], 12);
    } else {
      map.fitBounds(points as L.LatLngBoundsExpression, { padding: [30, 30], maxZoom: 14 });
    }
  }, [map, points]);

  return null;
}

export function PathRouteMap({
  resolved,
  senderInfo,
  directoryHits = {},
  height = 220,
}: PathRouteMapProps) {
  const { t } = useTranslation();
  const points = collectPoints(resolved, directoryHits);
  const hasAnyGps = points.length > 0;

  // Check if some nodes are missing GPS
  let totalNodes = 2; // sender + receiver
  let nodesWithGps = 0;
  if (isValidLocation(resolved.sender.lat, resolved.sender.lon)) nodesWithGps++;
  if (isValidLocation(resolved.receiver.lat, resolved.receiver.lon)) nodesWithGps++;
  for (const hop of resolved.hops) {
    const directory = directoryHopLocation(hop, directoryHits[hop.prefix]);
    if (hop.matches.length === 0) {
      totalNodes++;
      if (directory) nodesWithGps++;
    } else {
      totalNodes += hop.matches.length;
      nodesWithGps += hop.matches.filter((m) => isValidLocation(m.lat, m.lon)).length;
    }
  }
  const someMissingGps = hasAnyGps && nodesWithGps < totalNodes;

  if (!hasAnyGps) {
    return (
      <div className="h-14 rounded border border-border bg-muted/30 flex items-center justify-center text-sm text-muted-foreground">
        {t('path.noGps')}
      </div>
    );
  }

  const center: [number, number] = points[0];

  return (
    <div>
      <div
        className="rounded border border-border overflow-hidden"
        role="img"
        aria-label={t('path.routeMapAria')}
        style={{ height }}
      >
        <MapContainer
          center={center}
          zoom={10}
          className="h-full w-full"
          style={{ background: '#1a1a2e' }}
        >
          <TileLayer
            attribution={OSM_RASTER_TILE_ATTRIBUTION}
            url={OSM_RASTER_TILE_URL}
            referrerPolicy={OSM_RASTER_REFERRER_POLICY}
          />
          <RouteMapBounds points={points} />

          {/* Sender marker */}
          {isValidLocation(resolved.sender.lat, resolved.sender.lon) && (
            <Marker
              position={[resolved.sender.lat!, resolved.sender.lon!]}
              icon={makeIcon('S', SENDER_COLOR)}
            >
              <Tooltip direction="top" offset={[0, -14]}>
                <span className="font-mono">{resolved.sender.prefix}</span>
                {' · '}
                {senderInfo.name || t('path.sender')}
              </Tooltip>
            </Marker>
          )}

          {/* Hop markers — local RF GPS first; directory only when radio does not know the hop */}
          {resolved.hops.map((hop, hopIdx) => {
            const localGps = hop.matches.filter((m) => isValidLocation(m.lat, m.lon));
            if (localGps.length > 0) {
              return localGps.map((m, mIdx) => (
                <Marker
                  key={`hop-${hopIdx}-${mIdx}`}
                  position={[m.lat!, m.lon!]}
                  icon={makeIcon(String(hopIdx + 1), getHopColor(hopIdx))}
                >
                  <Tooltip direction="top" offset={[0, -14]}>
                    <span className="font-mono">{hop.prefix}</span>
                    {' · '}
                    {m.name || m.public_key.slice(0, 12)}
                  </Tooltip>
                </Marker>
              ));
            }
            const directory = directoryHopLocation(hop, directoryHits[hop.prefix]);
            if (!directory) return null;
            return (
              <Marker
                key={`hop-directory-${hopIdx}`}
                position={[directory.lat, directory.lon]}
                icon={makeIcon(String(hopIdx + 1), DIRECTORY_FILL, DIRECTORY_RING)}
              >
                <Tooltip direction="top" offset={[0, -14]}>
                  <span className="font-mono">{hop.prefix}</span>
                  {' · '}
                  {directory.name}
                </Tooltip>
              </Marker>
            );
          })}

          {/* Receiver marker */}
          {isValidLocation(resolved.receiver.lat, resolved.receiver.lon) && (
            <Marker
              position={[resolved.receiver.lat!, resolved.receiver.lon!]}
              icon={makeIcon('R', RECEIVER_COLOR)}
            >
              <Tooltip direction="top" offset={[0, -14]}>
                <span className="font-mono">{resolved.receiver.prefix}</span>
                {' · '}
                {resolved.receiver.name || t('path.receiver')}
              </Tooltip>
            </Marker>
          )}
        </MapContainer>
      </div>
      {someMissingGps && (
        <p className="text-xs text-muted-foreground mt-1">{t('path.someMissingGps')}</p>
      )}
    </div>
  );
}
