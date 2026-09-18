import { useCallback, useEffect, useMemo, useRef, useState, type ReactNode } from 'react';
import { useTranslation } from 'react-i18next';
import { ChevronDown } from 'lucide-react';
import 'maplibre-gl/dist/maplibre-gl.css';

import { api } from '../api';
import type {
  CommunityPacketType,
  Contact,
  Conversation,
  DirectoryMapNode,
  DirectoryNodeRole,
  RadioConfig,
} from '../types';
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
import {
  getSavedLivePacketLegendOpen,
  setSavedLivePacketLegendOpen,
} from '../utils/liveLegendPreference';
import { LiveSoundEngine } from '../utils/liveSound';
import { LIVE_SOUND_THEMES, type LiveSoundTheme } from '../utils/liveSoundPreference';
import { Button } from './ui/button';
import { cn } from '@/lib/utils';
import { LiveMapController, type LiveHoverPayload } from './live/liveMap';
import {
  DIRECTORY_NODES_REFRESH_MS,
  LIVE_PACKET_TYPES,
  LIVE_ROLE_LEGEND,
  NODE_ROLE_STYLE,
  collectIataCodes,
  emptyLiveFilters,
  heardDirectoryPins,
  localContactsToMapNodes,
  localHash8Set,
  mergeLocalOverDirectory,
  resolveLiveKnownNodeAction,
  type LiveRoleShape,
} from './live/liveRender';

interface LiveViewProps {
  contacts: Contact[];
  config: RadioConfig | null;
  communityEnabled?: boolean;
  /** Undefined until Community status is known. Empty means IATA is missing. */
  communityIata?: string;
  blockedKeys?: string[];
  blockedNames?: string[];
  onOpenContactInfo?: (publicKey: string) => void;
  onSelectConversation?: (conversation: Conversation) => void;
  onOpenCommunitySettings?: () => void;
}

function communityReadyForLive(enabled: boolean, iata: string | undefined): boolean {
  return enabled && typeof iata === 'string' && iata.trim() !== '';
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

export function LiveView({
  contacts,
  config,
  communityEnabled = true,
  communityIata,
  blockedKeys = [],
  blockedNames = [],
  onOpenContactInfo,
  onSelectConversation,
  onOpenCommunitySettings,
}: LiveViewProps) {
  const { t } = useTranslation();
  const rawPackets = useRawPackets();
  const communityPackets = useCommunityPackets();
  const connection = useLiveConnectionState();
  const [iataFilter, setIataFilter] = useState('');
  const [hiddenTypes, setHiddenTypes] = useState<Set<CommunityPacketType>>(() => new Set());
  const [certainOnly, setCertainOnly] = useState(false);
  const [soundTheme, setSoundTheme] = useState<LiveSoundTheme>('off');
  const [hover, setHover] = useState<LiveHoverPayload | null>(null);
  const mapHostRef = useRef<HTMLDivElement | null>(null);
  const engineRef = useRef<LiveMapController | null>(null);
  const soundEngineRef = useRef<LiveSoundEngine | null>(null);
  const soundThemeRef = useRef(soundTheme);
  const hoverRef = useRef<(payload: LiveHoverPayload | null) => void>(() => {});
  const nodeClickRef = useRef<(publicKey: string) => void>(() => {});
  // The directory fetch and the engine race each other; whichever lands second
  // applies the nodes, so the payload is never dropped on the floor.
  const directoryNodesRef = useRef<DirectoryMapNode[]>([]);
  const heardDirectoryPinsRef = useRef<DirectoryMapNode[]>([]);
  const seenContactKeysRef = useRef<Set<string>>(new Set());
  const tombstonesRef = useRef<Set<string>>(new Set());

  const communityReady = communityReadyForLive(communityEnabled, communityIata);
  const optedOut = connection.optOut || !communityEnabled;
  const prefixIndex = useMemo(() => buildPrefixIndex(contacts), [contacts]);

  soundThemeRef.current = soundTheme;
  hoverRef.current = setHover;
  nodeClickRef.current = (publicKey) => {
    const action = resolveLiveKnownNodeAction(publicKey, contacts);
    if (!action) return;
    if (action.kind === 'info') {
      onOpenContactInfo?.(action.publicKey);
      return;
    }
    onSelectConversation?.({
      type: 'contact',
      id: action.publicKey,
      name: action.name,
    });
  };

  const publishPins = useCallback(
    (directory: DirectoryMapNode[], heard: DirectoryMapNode[] = []) => {
      const local = localContactsToMapNodes(contacts, blockedKeys, blockedNames);
      const current = new Set(local.map((node) => node.public_key));
      for (const key of current) {
        seenContactKeysRef.current.add(key);
        tombstonesRef.current.delete(key);
      }
      for (const key of seenContactKeysRef.current) {
        if (!current.has(key)) tombstonesRef.current.add(key);
      }
      const withHeard = mergeLocalOverDirectory(directory, heard, new Set());
      engineRef.current?.setDirectoryNodes(
        mergeLocalOverDirectory(withHeard, local, tombstonesRef.current)
      );
    },
    [blockedKeys, blockedNames, contacts]
  );
  const publishPinsRef = useRef(publishPins);
  publishPinsRef.current = publishPins;

  useEffect(() => {
    const sound = new LiveSoundEngine();
    soundEngineRef.current = sound;
    return () => {
      sound.destroy();
      soundEngineRef.current = null;
    };
  }, []);

  useEffect(() => {
    const host = mapHostRef.current;
    if (!host) return;
    const engine = new LiveMapController(host, {
      onHover: (payload) => hoverRef.current(payload),
      onNodeClick: (publicKey) => nodeClickRef.current(publicKey),
      soundTheme: soundThemeRef.current,
      onLaserSegment: (event) => {
        soundEngineRef.current?.play(soundThemeRef.current, event.type, event.durationMs);
      },
    });
    engineRef.current = engine;
    publishPinsRef.current(directoryNodesRef.current, heardDirectoryPinsRef.current);
    return () => {
      engine.destroy();
      engineRef.current = null;
    };
  }, []);

  useEffect(() => {
    engineRef.current?.setSoundTheme(soundTheme);
  }, [soundTheme]);

  useEffect(() => {
    if (!communityReady) {
      applyLiveStatus({ close_code: null, opted_out: !communityEnabled });
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
  }, [communityEnabled, communityReady]);

  useEffect(() => {
    let cancelled = false;
    const load = () => {
      void api.getLiveDirectoryMapNodes().then(
        (res) => {
          if (cancelled) return;
          directoryNodesRef.current = res.nodes;
          publishPinsRef.current(res.nodes, heardDirectoryPinsRef.current);
        },
        () => {
          if (cancelled) return;
          directoryNodesRef.current = [];
          publishPinsRef.current([], heardDirectoryPinsRef.current);
        }
      );
    };
    load();
    const refresh = window.setInterval(load, DIRECTORY_NODES_REFRESH_MS);
    return () => {
      cancelled = true;
      window.clearInterval(refresh);
    };
  }, []);

  useEffect(() => {
    engineRef.current?.setLocalRadio(config);
  }, [config]);

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

  const heardPins = useMemo(() => heardDirectoryPins(observations), [observations]);
  heardDirectoryPinsRef.current = heardPins;

  useEffect(() => {
    publishPins(directoryNodesRef.current, heardPins);
  }, [heardPins, publishPins]);

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

  const iataKnown = communityIata !== undefined;
  const needsCommunitySetup =
    iataKnown && (!communityEnabled || communityIata.trim() === '' || connection.optOut);
  const bannerKey = needsCommunitySetup ? 'live.bannerOptOut' : liveBannerI18nKey(connection);

  return (
    <div className="flex h-full min-h-0 flex-col">
      {bannerKey && (
        <LiveBanner tone="muted">
          <div className="flex flex-wrap items-center gap-2">
            <span>{t(bannerKey)}</span>
            {onOpenCommunitySettings && (
              <Button type="button" size="sm" variant="outline" onClick={onOpenCommunitySettings}>
                {t('settings.community.bannerOpenSettings')}
              </Button>
            )}
          </div>
        </LiveBanner>
      )}

      <div className="flex flex-wrap items-center gap-2 border-b border-border px-3 py-2 text-xs">
        <label className="flex items-center gap-1.5">
          <span className="text-muted-foreground">{t('live.soundTheme')}</span>
          <select
            value={soundTheme}
            onChange={(event) => {
              const next = event.target.value as LiveSoundTheme;
              setSoundTheme(next);
              if (next === 'off') soundEngineRef.current?.stopAll();
              else void soundEngineRef.current?.resume();
            }}
            className="rounded border border-border bg-background px-1.5 py-0.5"
            aria-label={t('live.soundTheme')}
          >
            {LIVE_SOUND_THEMES.map((theme) => (
              <option key={theme} value={theme}>
                {t(
                  theme === 'off'
                    ? 'live.soundOff'
                    : theme === 'bubbles'
                      ? 'live.soundBubbles'
                      : theme === 'laser'
                        ? 'live.soundLaser'
                        : 'live.soundBit8'
                )}
              </option>
            ))}
          </select>
        </label>
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
      </div>

      <div className="relative min-h-0 flex-1" role="img" aria-label={t('live.mapAria')}>
        {/* Sized, not positioned: maplibre-gl.css forces position:relative on its own
            root, so an absolute inset-0 host collapses to zero height. */}
        <div ref={mapHostRef} className="live-map-osm h-full w-full bg-[#05070a]" />
        <LiveDualLegend />
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

function LiveDualLegend() {
  const { t } = useTranslation();
  const [legendOpen, setLegendOpen] = useState(getSavedLivePacketLegendOpen);
  return (
    <aside
      className="pointer-events-none absolute bottom-3 left-3 z-10 max-w-72 rounded-md border border-border bg-background/90 px-2.5 py-2 text-[0.6875rem] shadow-md"
      aria-label={t('live.legendTitle')}
    >
      <button
        type="button"
        className="pointer-events-auto flex items-center gap-1 font-medium uppercase tracking-wider text-muted-foreground hover:text-foreground transition-colors"
        aria-expanded={legendOpen}
        aria-controls="live-legend-content"
        aria-label={t('live.packetLegendToggle')}
        onClick={() => {
          const next = !legendOpen;
          setLegendOpen(next);
          setSavedLivePacketLegendOpen(next);
        }}
      >
        <ChevronDown
          className={cn('h-3 w-3 transition-transform', legendOpen ? '' : '-rotate-90')}
          aria-hidden="true"
        />
        {t('live.legendTitle')}
      </button>
      {legendOpen && (
        <div id="live-legend-content" className="mt-2 space-y-2 pointer-events-auto">
          <div>
            <div className="font-medium uppercase tracking-wider text-muted-foreground">
              {t('live.packetLegend')}
            </div>
            <div
              id="live-packet-legend"
              className="mt-1 flex flex-wrap items-center gap-x-2 gap-y-1"
              role="group"
              aria-label={t('live.packetLegend')}
            >
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
          <div>
            <div className="mt-2 font-medium uppercase tracking-wider text-muted-foreground">
              {t('live.roleLegend')}
            </div>
            <div
              className="mt-1 flex flex-wrap items-center gap-x-2 gap-y-1"
              role="group"
              aria-label={t('live.roleLegend')}
            >
              {LIVE_ROLE_LEGEND.map(({ role, shape }) => (
                <span key={role} className="flex items-center gap-1">
                  <RoleShapeIcon shape={shape} color={NODE_ROLE_STYLE[role].color} />
                  {t(`live.nodes.${role}`)}
                </span>
              ))}
            </div>
          </div>
        </div>
      )}
    </aside>
  );
}

function RoleShapeIcon({ shape, color }: { shape: LiveRoleShape; color: string }) {
  return (
    <svg width="10" height="10" viewBox="0 0 10 10" aria-hidden="true">
      {shape === 'circle' && <circle cx="5" cy="5" r="3.6" fill={color} />}
      {shape === 'square' && <rect x="1.8" y="1.8" width="6.4" height="6.4" fill={color} />}
      {shape === 'hexagon' && <polygon points="5,1 8.5,3 8.5,7 5,9 1.5,7 1.5,3" fill={color} />}
      {shape === 'triangle' && <polygon points="5,1.4 8.8,8.4 1.2,8.4" fill={color} />}
    </svg>
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
  return <div className="font-medium">{t('live.localRadio')}</div>;
}

function nodeRoleLabel(role: DirectoryNodeRole, t: (key: string) => string): string {
  return t(`live.nodes.${role}`);
}
