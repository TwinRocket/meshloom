import { useCallback, useEffect, useMemo, useRef, useState, type ReactNode } from 'react';
import { useTranslation } from 'react-i18next';
import { Pause, Play } from 'lucide-react';
import { CircleMarker, MapContainer, Polyline, TileLayer, useMap } from 'react-leaflet';
import L from 'leaflet';
import 'leaflet/dist/leaflet.css';

import { api } from '../api';
import type { Contact, RadioConfig } from '../types';
import {
  LIVE_CLOSE_INACTIVE,
  LIVE_CLOSE_JWT_EXPIRED,
  LIVE_CLOSE_RATE_LIMIT,
  LIVE_CLOSE_SLOT_BUSY,
} from '../types';
import { useRawPackets } from '../stores/rawPacketStore';
import {
  applyLiveStatus,
  relancerLive,
  useCommunityPackets,
  useLiveConnectionState,
} from '../stores/livePacketStore';
import {
  applyHopJitter,
  buildPrefixIndex,
  LIVE_CAMERA_STORAGE_KEY,
  isStaleLiveTime,
  liveBoundsShouldFit,
  readSavedMapCamera,
  writeSavedMapCamera,
  LIVE_SEGMENT_MS,
  LIVE_STAGGER_MS,
  MAX_LIVE_PARTICLES,
  liveOpacity,
  liveTypeColor,
  observationFromCommunity,
  observationFromRaw,
  polylineLifetimeMs,
  polylinePositions,
  snrWeight,
  type LiveObservation,
  type LiveWaypoint,
} from '../utils/livePackets';
import { isValidLocation } from '../utils/pathUtils';
import { getSavedCartoApiKey } from '../utils/cartoPreference';
import { Button } from './ui/button';
import { cn } from '@/lib/utils';

const CARTO_DARK_TILE_URL = 'https://{s}.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}{r}.png';

function cartoDarkTileUrl(apiKey: string): string {
  const trimmed = apiKey.trim();
  return trimmed
    ? `${CARTO_DARK_TILE_URL}?key=${encodeURIComponent(trimmed)}`
    : CARTO_DARK_TILE_URL;
}

interface LiveViewProps {
  contacts: Contact[];
  config: RadioConfig | null;
  communityEnabled?: boolean;
}

interface RainParticle {
  id: string;
  color: string;
  opacity: number;
  radius: number;
  waypoints: LiveWaypoint[];
  startedAt: number;
  staggerMs: number;
}

interface FadeLine {
  id: string;
  path: [number, number][];
  color: string;
  startedAt: number;
  lifetimeMs: number;
}

interface EarPulse {
  id: string;
  lat: number;
  lon: number;
  lastAt: number;
  iata: string | null;
  color?: string;
}

function LiveRainCanvas({
  particles,
  onFinished,
}: {
  particles: RainParticle[];
  onFinished: (particle: RainParticle) => void;
}) {
  const map = useMap();
  const canvasRef = useRef<HTMLCanvasElement | null>(null);
  const animRef = useRef<number>(0);
  const finishedRef = useRef<Set<string>>(new Set());

  useEffect(() => {
    finishedRef.current.clear();
  }, [particles]);

  useEffect(() => {
    const container = map.getContainer();
    const canvas = document.createElement('canvas');
    canvas.style.position = 'absolute';
    canvas.style.top = '0';
    canvas.style.left = '0';
    canvas.style.pointerEvents = 'none';
    canvas.style.zIndex = '450';
    canvas.getContext('2d', { alpha: true, desynchronized: true });
    container.appendChild(canvas);
    canvasRef.current = canvas;

    const resize = () => {
      const size = map.getSize();
      canvas.width = size.x * window.devicePixelRatio;
      canvas.height = size.y * window.devicePixelRatio;
      canvas.style.width = `${size.x}px`;
      canvas.style.height = `${size.y}px`;
    };
    resize();
    map.on('resize', resize);
    map.on('zoom', resize);

    return () => {
      cancelAnimationFrame(animRef.current);
      map.off('resize', resize);
      map.off('zoom', resize);
      container.removeChild(canvas);
      canvasRef.current = null;
    };
  }, [map]);

  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;
    const ctx = canvas.getContext('2d', { alpha: true, desynchronized: true });
    if (!ctx) return;

    const draw = () => {
      const now = Date.now();
      const dpr = window.devicePixelRatio;
      ctx.clearRect(0, 0, canvas.width, canvas.height);
      ctx.save();
      ctx.scale(dpr, dpr);

      for (const particle of particles) {
        const elapsed = now - particle.startedAt - particle.staggerMs;
        if (elapsed < 0) continue;

        const path = particle.waypoints;
        if (path.length === 0) {
          if (!finishedRef.current.has(particle.id)) {
            finishedRef.current.add(particle.id);
            onFinished(particle);
          }
          continue;
        }

        const segmentCount = Math.max(1, path.length - 1);
        const totalMs = segmentCount * LIVE_SEGMENT_MS;
        if (elapsed >= totalMs) {
          if (!finishedRef.current.has(particle.id)) {
            finishedRef.current.add(particle.id);
            onFinished(particle);
          }
          continue;
        }

        const segIndex = Math.min(segmentCount - 1, Math.floor(elapsed / LIVE_SEGMENT_MS));
        const segT = (elapsed - segIndex * LIVE_SEGMENT_MS) / LIVE_SEGMENT_MS;
        const from = path[Math.min(segIndex, path.length - 1)];
        const to = path[Math.min(segIndex + 1, path.length - 1)];
        const fading = to.kind === 'fade' || from.kind === 'fade';
        const fadeMul = fading ? 1 - segT : 1;

        const a = map.latLngToContainerPoint(L.latLng(from.lat, from.lon));
        const b = map.latLngToContainerPoint(L.latLng(to.lat, to.lon));
        const x = a.x + (b.x - a.x) * segT;
        const y = a.y + (b.y - a.y) * segT;
        const alpha = Math.round(particle.opacity * fadeMul * 255)
          .toString(16)
          .padStart(2, '0');

        ctx.save();
        ctx.globalCompositeOperation = 'lighter';
        ctx.strokeStyle = particle.color + alpha;
        ctx.shadowColor = particle.color;
        ctx.shadowBlur = 16 * fadeMul;
        ctx.lineWidth = 2.4;
        ctx.lineCap = 'round';
        ctx.beginPath();
        ctx.moveTo(a.x, a.y);
        ctx.lineTo(x, y);
        ctx.stroke();
        ctx.beginPath();
        ctx.arc(x, y, particle.radius + 4, 0, Math.PI * 2);
        ctx.fillStyle = `${particle.color}28`;
        ctx.fill();
        ctx.beginPath();
        ctx.arc(x, y, particle.radius, 0, Math.PI * 2);
        ctx.fillStyle = particle.color + alpha;
        ctx.fill();
        ctx.restore();
      }

      ctx.restore();
      animRef.current = requestAnimationFrame(draw);
    };

    animRef.current = requestAnimationFrame(draw);
    return () => cancelAnimationFrame(animRef.current);
  }, [map, onFinished, particles]);

  return null;
}

function PersistLiveCamera() {
  const map = useMap();
  useEffect(() => {
    const persist = () => {
      const center = map.getCenter();
      writeSavedMapCamera(LIVE_CAMERA_STORAGE_KEY, {
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

const LIVE_FIT_DELAY_MS = 1200;

function FitLiveBounds({ ears }: { ears: EarPulse[] }) {
  const map = useMap();
  const fitted = useRef(false);
  const communityFitted = useRef(false);
  const userMoved = useRef(false);

  useEffect(() => {
    const saved = readSavedMapCamera(LIVE_CAMERA_STORAGE_KEY);
    if (saved) {
      map.setView([saved.lat, saved.lon], saved.zoom);
      fitted.current = true;
      userMoved.current = true;
    }
    const markUser = () => {
      userMoved.current = true;
    };
    map.on('dragend', markUser);
    return () => {
      map.off('dragend', markUser);
    };
  }, [map]);

  useEffect(() => {
    if (userMoved.current || !liveBoundsShouldFit(false, ears.length)) return;
    const hasCommunity = ears.some((ear) => ear.id !== 'local-ear');

    const apply = () => {
      if (userMoved.current) return;
      if (ears.length === 1) {
        map.setView([ears[0].lat, ears[0].lon], 7);
      } else {
        const bounds = L.latLngBounds(ears.map((ear) => [ear.lat, ear.lon] as [number, number]));
        map.fitBounds(bounds.pad(0.35), { maxZoom: 9 });
      }
      fitted.current = true;
      if (hasCommunity) communityFitted.current = true;
    };

    if (hasCommunity && !communityFitted.current) {
      apply();
      return;
    }
    if (fitted.current) return;
    const timer = window.setTimeout(apply, LIVE_FIT_DELAY_MS);
    return () => window.clearTimeout(timer);
  }, [ears, map]);

  return null;
}

function LiveBanner({
  tone,
  children,
}: {
  tone: 'warning' | 'destructive' | 'muted';
  children: ReactNode;
}) {
  const toneClass =
    tone === 'destructive'
      ? 'border-destructive/30 bg-destructive/10 text-destructive'
      : tone === 'warning'
        ? 'border-warning/30 bg-warning/10 text-warning'
        : 'border-border bg-muted/60 text-muted-foreground';
  return (
    <div className={cn('mx-3 mt-2 rounded-md border px-3 py-2 text-sm', toneClass)}>{children}</div>
  );
}

export function LiveView({ contacts, config, communityEnabled = true }: LiveViewProps) {
  const { t } = useTranslation();
  const rawPackets = useRawPackets();
  const communityPackets = useCommunityPackets();
  const connection = useLiveConnectionState();
  const [playing, setPlaying] = useState(true);
  const [iataFilter, setIataFilter] = useState('');
  const [particles, setParticles] = useState<RainParticle[]>([]);
  const [lines, setLines] = useState<FadeLine[]>([]);
  const [ears, setEars] = useState<EarPulse[]>([]);
  const [hopDots, setHopDots] = useState<EarPulse[]>([]);
  const [nowTick, setNowTick] = useState(() => Date.now());
  const seenRef = useRef<Set<string>>(new Set());
  const spawnIndexRef = useRef(0);

  const optedOut = connection.optOut || !communityEnabled;
  const prefixIndex = useMemo(() => buildPrefixIndex(contacts), [contacts]);

  useEffect(() => {
    if (!communityEnabled) {
      applyLiveStatus({ close_code: null, opted_out: true });
      return;
    }
    let sessionId: string | null = null;
    let cancelled = false;
    void api.subscribeCommunityLive().then(
      (status) => {
        if (cancelled) {
          if (status.session_id) void api.unsubscribeCommunityLive(status.session_id);
          return;
        }
        sessionId = status.session_id ?? null;
        applyLiveStatus(status);
      },
      () => {
        if (!cancelled) applyLiveStatus({ close_code: null, opted_out: false });
      }
    );
    const beat = window.setInterval(() => {
      if (sessionId) void api.subscribeCommunityLive(sessionId).catch(() => {});
    }, 30_000);
    return () => {
      cancelled = true;
      window.clearInterval(beat);
      if (sessionId) void api.unsubscribeCommunityLive(sessionId);
    };
  }, [communityEnabled]);

  const localHash8 = useMemo(() => {
    const set = new Set<string>();
    for (const packet of rawPackets) {
      const obs = observationFromRaw(packet, prefixIndex, config);
      if (obs) set.add(obs.hash8);
    }
    return set;
  }, [config, prefixIndex, rawPackets]);

  const observations = useMemo(() => {
    const list: LiveObservation[] = [];
    if (!optedOut) {
      for (const packet of communityPackets) {
        const obs = observationFromCommunity(packet);
        if (obs) list.push(obs);
      }
    }
    for (const packet of rawPackets) {
      const obs = observationFromRaw(packet, prefixIndex, config);
      if (obs) list.push(obs);
    }
    const filter = iataFilter.trim().toUpperCase();
    if (!filter) return list;
    return list.filter((obs) => !obs.iata || obs.iata.toUpperCase() === filter);
  }, [communityPackets, config, iataFilter, optedOut, prefixIndex, rawPackets]);

  const iataOptions = useMemo(() => {
    const codes = new Set<string>();
    for (const packet of communityPackets) {
      if (packet.iata) codes.add(packet.iata.toUpperCase());
    }
    return [...codes].sort();
  }, [communityPackets]);

  useEffect(() => {
    const now = Date.now();
    const newcomers = observations.filter((obs) => !seenRef.current.has(obs.id));
    if (newcomers.length === 0) return;

    const nextParticles: RainParticle[] = [];
    const nextEars: EarPulse[] = [];
    const nextHops: EarPulse[] = [];
    for (const obs of newcomers) {
      seenRef.current.add(obs.id);
      const staggerMs = (spawnIndexRef.current++ % 8) * LIVE_STAGGER_MS;
      const twin = obs.source === 'community' && localHash8.has(obs.hash8);
      const jittered = applyHopJitter(obs.waypoints, obs.id);
      if (playing) {
        nextParticles.push({
          id: obs.id,
          color: liveTypeColor(obs.type),
          opacity: liveOpacity(obs.source, obs.snr, twin),
          radius: 3.5 + snrWeight(obs.snr) * 4,
          waypoints: jittered,
          startedAt: now,
          staggerMs,
        });
      }
      if (obs.ear) {
        nextEars.push({
          id: obs.earId,
          lat: obs.ear.lat,
          lon: obs.ear.lon,
          lastAt: now,
          iata: obs.iata,
        });
      }
      const packetColor = liveTypeColor(obs.type);
      for (const point of jittered) {
        if (point.kind !== 'hop') continue;
        nextHops.push({
          id: `hop:${point.token}:${point.lat.toFixed(4)}:${point.lon.toFixed(4)}`,
          lat: point.lat,
          lon: point.lon,
          lastAt: now,
          iata: obs.iata,
          color: packetColor,
        });
      }
    }

    setParticles((prev) => {
      const combined = [...prev, ...nextParticles];
      return combined.length > MAX_LIVE_PARTICLES
        ? combined.slice(combined.length - MAX_LIVE_PARTICLES)
        : combined;
    });
    setEars((prev) => {
      const map = new Map(prev.map((ear) => [ear.id, ear]));
      for (const ear of nextEars) map.set(ear.id, ear);
      return [...map.values()];
    });
    setHopDots((prev) => {
      const map = new Map(prev.map((hop) => [hop.id, hop]));
      for (const hop of nextHops) map.set(hop.id, hop);
      return [...map.values()].slice(-200);
    });
  }, [localHash8, observations, playing]);

  const onFinished = useCallback((particle: RainParticle) => {
    const path = polylinePositions(particle.waypoints);
    if (path.length >= 2) {
      const line: FadeLine = {
        id: `line-${particle.id}`,
        path,
        color: particle.color,
        startedAt: Date.now(),
        lifetimeMs: polylineLifetimeMs(particle.id),
      };
      setLines((prev) => [...prev, line].slice(-MAX_LIVE_PARTICLES));
    }
    setParticles((prev) => prev.filter((item) => item.id !== particle.id));
  }, []);

  useEffect(() => {
    const timer = window.setInterval(() => {
      const now = Date.now();
      setNowTick(now);
      setLines((prev) => prev.filter((line) => now - line.startedAt < line.lifetimeMs));
    }, 400);
    return () => window.clearInterval(timer);
  }, []);

  const localEar =
    config && isValidLocation(config.lat, config.lon)
      ? { lat: config.lat, lon: config.lon, id: 'local-ear', lastAt: Date.now(), iata: null }
      : null;

  const earMarkers = useMemo(() => {
    const map = new Map<string, EarPulse>();
    if (localEar) map.set(localEar.id, localEar);
    for (const ear of ears) map.set(ear.id, ear);
    return [...map.values()];
  }, [ears, localEar]);

  return (
    <div className="flex h-full min-h-0 flex-col">
      {connection.closeCode === LIVE_CLOSE_JWT_EXPIRED && (
        <LiveBanner tone="warning">
          <div className="flex items-center justify-between gap-3">
            <span>{t('live.bannerExpired')}</span>
            <Button
              size="sm"
              variant="outline"
              onClick={() => {
                relancerLive();
                void api.relancerCommunityLive().then(applyLiveStatus, () => {});
              }}
            >
              {t('live.relancer')}
            </Button>
          </div>
        </LiveBanner>
      )}
      {(connection.closeCode === LIVE_CLOSE_INACTIVE || connection.inactiveObserver) && (
        <LiveBanner tone="muted">{t('live.bannerInactive')}</LiveBanner>
      )}
      {optedOut && <LiveBanner tone="muted">{t('live.bannerOptOut')}</LiveBanner>}
      {connection.closeCode === LIVE_CLOSE_SLOT_BUSY && (
        <LiveBanner tone="destructive">{t('live.bannerSlotBusy')}</LiveBanner>
      )}
      {connection.closeCode === LIVE_CLOSE_RATE_LIMIT && (
        <LiveBanner tone="muted">{t('live.bannerRateLimit')}</LiveBanner>
      )}

      <div className="flex flex-wrap items-center gap-2 border-b border-border px-3 py-2 text-xs">
        <Button
          size="sm"
          variant="secondary"
          aria-label={t('live.playPause')}
          aria-pressed={playing}
          onClick={() => setPlaying((current) => !current)}
        >
          {playing ? (
            <>
              <Pause className="mr-1 h-3.5 w-3.5" aria-hidden="true" />
              {t('live.pause')}
            </>
          ) : (
            <>
              <Play className="mr-1 h-3.5 w-3.5" aria-hidden="true" />
              {t('live.play')}
            </>
          )}
        </Button>
        <label className="flex items-center gap-1.5">
          <span className="text-muted-foreground">{t('live.iataFilter')}</span>
          <select
            value={iataFilter}
            onChange={(event) => setIataFilter(event.target.value)}
            className="rounded border border-border bg-background px-1.5 py-0.5"
            aria-label={t('live.iataFilter')}
          >
            <option value="">{t('live.iataAll')}</option>
            {iataOptions.map((code) => (
              <option key={code} value={code}>
                {code}
              </option>
            ))}
          </select>
        </label>
        <div className="ml-auto flex flex-wrap items-center gap-2 text-[0.6875rem]">
          {(['advert', 'text', 'ack', 'trace', 'other'] as const).map((type) => (
            <span key={type} className="flex items-center gap-1">
              <span
                className="inline-block h-2 w-2 rounded-full"
                style={{ backgroundColor: liveTypeColor(type) }}
              />
              {t(`live.legend.${type}`)}
            </span>
          ))}
        </div>
      </div>

      <div className="relative min-h-0 flex-1" role="img" aria-label={t('live.mapAria')}>
        <MapContainer
          center={[46.2, 5.2]}
          zoom={6}
          minZoom={2}
          maxZoom={18}
          className="h-full w-full"
          style={{ background: '#0d0d0d' }}
        >
          <TileLayer
            url={cartoDarkTileUrl(getSavedCartoApiKey())}
            attribution={
              '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> &copy; <a href="https://carto.com/">CARTO</a>'
            }
            maxZoom={19}
          />
          <PersistLiveCamera />
          <FitLiveBounds ears={earMarkers} />
          {lines.map((line) => {
            const age = nowTick - line.startedAt;
            const opacity = Math.max(0, 0.88 * (1 - age / line.lifetimeMs));
            return (
              <Polyline
                key={line.id}
                positions={line.path}
                pathOptions={{ color: line.color, weight: 3, opacity }}
              />
            );
          })}
          {hopDots.map((hop) => (
            <CircleMarker
              key={hop.id}
              center={[hop.lat, hop.lon]}
              radius={5}
              pathOptions={{
                color: hop.color ?? liveTypeColor('other'),
                fillColor: hop.color ?? liveTypeColor('other'),
                fillOpacity: isStaleLiveTime(hop.lastAt, nowTick) ? 0.15 : 0.7,
                weight: 1.5,
              }}
            />
          ))}
          {earMarkers.map((ear) => {
            const dim = isStaleLiveTime(ear.lastAt, nowTick);
            const pulse = nowTick - ear.lastAt < 1800;
            return (
              <CircleMarker
                key={ear.id}
                center={[ear.lat, ear.lon]}
                radius={pulse ? 11 : 7}
                pathOptions={{
                  color: '#e2e8f0',
                  fillColor: '#38bdf8',
                  fillOpacity: dim ? 0.2 : pulse ? 0.85 : 0.45,
                  weight: pulse ? 3 : 1,
                }}
              />
            );
          })}
          <LiveRainCanvas particles={particles} onFinished={onFinished} />
        </MapContainer>
      </div>
    </div>
  );
}
