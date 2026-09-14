import { useCallback, useEffect, useMemo, useRef, useState, type ReactNode } from 'react';
import { useTranslation } from 'react-i18next';
import { Pause, Play } from 'lucide-react';
import 'maplibre-gl/dist/maplibre-gl.css';

import { api } from '../api';
import type { CommunityPacketType, Contact, DirectoryNodeRole, RadioConfig } from '../types';
import { useRawPackets } from '../stores/rawPacketStore';
import {
  applyLiveStatus,
  liveBannerI18nKey,
  useCommunityPackets,
  useLiveConnectionState,
} from '../stores/livePacketStore';
import {
  buildPrefixIndex,
  liveTypeColor,
  observationFromCommunity,
  observationFromRaw,
  type LiveObservation,
} from '../utils/livePackets';
import { Button } from './ui/button';
import { cn } from '@/lib/utils';
import { LiveMapController, type LiveHoverPayload } from './live/liveMap';
import {
  LIVE_PACKET_TYPES,
  collectIataCodes,
  emptyLiveFilters,
  localHash8Set,
} from './live/liveRender';

interface LiveViewProps {
  contacts: Contact[];
  config: RadioConfig | null;
  communityEnabled?: boolean;
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
  const [hiddenTypes, setHiddenTypes] = useState<Set<CommunityPacketType>>(() => new Set());
  const [certainOnly, setCertainOnly] = useState(false);
  const [hover, setHover] = useState<LiveHoverPayload | null>(null);
  const mapHostRef = useRef<HTMLDivElement | null>(null);
  const engineRef = useRef<LiveMapController | null>(null);
  const hoverRef = useRef<(payload: LiveHoverPayload | null) => void>(() => {});

  const optedOut = connection.optOut || !communityEnabled;
  const prefixIndex = useMemo(() => buildPrefixIndex(contacts), [contacts]);

  hoverRef.current = setHover;

  useEffect(() => {
    const host = mapHostRef.current;
    if (!host) return;
    const engine = new LiveMapController(host, {
      onHover: (payload) => hoverRef.current(payload),
    });
    engineRef.current = engine;
    return () => {
      engine.destroy();
      engineRef.current = null;
    };
  }, []);

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

  useEffect(() => {
    let cancelled = false;
    void api.getDirectoryMapNodes().then(
      (res) => {
        if (!cancelled) engineRef.current?.setDirectoryNodes(res.nodes);
      },
      () => {
        if (!cancelled) engineRef.current?.setDirectoryNodes([]);
      }
    );
    return () => {
      cancelled = true;
    };
  }, []);

  useEffect(() => {
    engineRef.current?.setLocalEar(config);
  }, [config]);

  useEffect(() => {
    engineRef.current?.setPlaying(playing);
  }, [playing]);

  const filters = useMemo(
    () => ({ ...emptyLiveFilters(), iata: iataFilter, hiddenTypes, exactOnly: certainOnly }),
    [certainOnly, hiddenTypes, iataFilter]
  );

  useEffect(() => {
    engineRef.current?.setFilters(filters);
  }, [filters]);

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
    return list;
  }, [communityPackets, config, optedOut, prefixIndex, rawPackets]);

  const hash8Local = useMemo(() => localHash8Set(observations), [observations]);
  const iataOptions = useMemo(() => collectIataCodes(observations), [observations]);

  useEffect(() => {
    engineRef.current?.syncObservations(observations, hash8Local);
  }, [hash8Local, observations]);

  const toggleType = useCallback((type: CommunityPacketType) => {
    setHiddenTypes((current) => {
      const next = new Set(current);
      if (next.has(type)) next.delete(type);
      else next.add(type);
      return next;
    });
  }, []);

  const bannerKey = !communityEnabled ? 'live.bannerOptOut' : liveBannerI18nKey(connection);

  return (
    <div className="flex h-full min-h-0 flex-col">
      {bannerKey && <LiveBanner tone="muted">{t(bannerKey)}</LiveBanner>}

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
        <div
          className="flex flex-wrap items-center gap-1"
          role="group"
          aria-label={t('live.typeFilter')}
        >
          {LIVE_PACKET_TYPES.map((type) => {
            const visible = !hiddenTypes.has(type);
            return (
              <button
                key={type}
                type="button"
                aria-pressed={visible}
                onClick={() => toggleType(type)}
                className={cn(
                  'flex items-center gap-1 rounded border px-1.5 py-0.5 text-[0.6875rem] transition-colors',
                  visible
                    ? 'border-primary/40 bg-primary/10 text-foreground'
                    : 'border-border text-muted-foreground'
                )}
              >
                <span
                  className="inline-block h-2 w-2 rounded-full"
                  style={{ backgroundColor: liveTypeColor(type), opacity: visible ? 1 : 0.35 }}
                />
                {t(`live.legend.${type}`)}
              </button>
            );
          })}
        </div>
        <label className="flex items-center gap-1.5" title={t('live.certainOnlyHelp')}>
          <input
            type="checkbox"
            checked={certainOnly}
            onChange={(event) => setCertainOnly(event.target.checked)}
            aria-label={t('live.certainOnly')}
          />
          <span>{t('live.certainOnly')}</span>
        </label>
        <div className="ml-auto flex flex-wrap items-center gap-2 text-[0.6875rem]">
          {LIVE_PACKET_TYPES.map((type) => (
            <span key={type} className="flex items-center gap-1">
              <span
                className="inline-block h-2 w-2 rounded-full"
                style={{ backgroundColor: liveTypeColor(type) }}
              />
              {t(`live.legend.${type}`)}
            </span>
          ))}
          <span className="flex items-center gap-1 text-muted-foreground">
            <span className="inline-block h-0.5 w-3 bg-foreground" />
            {t('live.confidence.exact')}
          </span>
          <span className="flex items-center gap-1 text-muted-foreground">
            <span className="inline-block h-0.5 w-3 border-t border-dashed border-foreground/70" />
            {t('live.confidence.probable')}
          </span>
        </div>
      </div>

      <div className="relative min-h-0 flex-1" role="img" aria-label={t('live.mapAria')}>
        {/* Sized, not positioned: maplibre-gl.css forces position:relative on its own
            root, so an absolute inset-0 host collapses to zero height. */}
        <div ref={mapHostRef} className="h-full w-full bg-[#0b0f14]" />
        {hover && (
          <div
            className="pointer-events-none absolute z-10 max-w-64 rounded-md border border-border bg-background/90 px-2 py-1.5 text-xs shadow-md"
            style={{ left: hover.x + 12, top: hover.y + 12 }}
          >
            <LiveHoverCard hover={hover} />
          </div>
        )}
      </div>
    </div>
  );
}

function LiveHoverCard({ hover }: { hover: LiveHoverPayload }) {
  const { t } = useTranslation();
  if (hover.kind === 'node') {
    return (
      <div>
        <div className="font-medium">{hover.name}</div>
        <div className="text-muted-foreground">{nodeRoleLabel(hover.role, t)}</div>
      </div>
    );
  }
  if (hover.kind === 'probable') {
    const reasonKey = hover.reason ? `live.reason.${hover.reason}` : '';
    const reasonLabel = reasonKey && t(reasonKey) !== reasonKey ? t(reasonKey) : hover.reason;
    return (
      <div>
        {hover.label && <div className="font-medium">{hover.label}</div>}
        <div className="text-muted-foreground">{reasonLabel}</div>
      </div>
    );
  }
  if (hover.kind === 'exact') {
    return <div className="font-medium">{hover.label}</div>;
  }
  return (
    <div>
      <div className="font-medium">{t(`live.ears.${hover.source}`)}</div>
      {hover.iata && <div className="text-muted-foreground">{hover.iata}</div>}
    </div>
  );
}

function nodeRoleLabel(role: DirectoryNodeRole, t: (key: string) => string): string {
  return t(`live.nodes.${role}`);
}
