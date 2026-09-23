import { lazy, Suspense, useCallback, useEffect, useMemo, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { RadioTower } from 'lucide-react';

import { api, formatApiError } from '../api';
import i18n from '../i18n';
import { toast } from './ui/sonner';
import { Button } from './ui/button';
import { ToolPaneHeader } from './ToolPaneHeader';
import { MeshTestRunDialog } from './MeshTestRunDialog';
import { useDistanceUnit } from '../contexts/DistanceUnitContext';
import { nodeRoleStyle, normalizeDirectoryRole } from './live/liveRender';
import type { Contact, PacketObserverReachResponse } from '../types';
import { formatDistance, type DirectoryHopHit } from '../utils/pathUtils';
import {
  hopDisplayName,
  hopMapLocation,
  hopNeedsDirectoryGps,
  toPathHop,
} from '../utils/observerHops';
import { observerReachPollIntervalMs } from '../utils/observerReach';
import {
  clearMeshTestRun,
  meshTestDistanceKm,
  meshTestListenState,
  meshTestOrigin,
  meshTestSummary,
  observerRowKey,
  placedObserverCoordinates,
  contactCoordinatesByName,
  readMeshTestRun,
  saveMeshTestRun,
  sortMeshTestObservers,
  type MeshTestObserver,
  type MeshTestRun,
} from '../utils/meshTest';
import { cn } from '../lib/utils';

/**
 * Send one flood, then listen for ten minutes to see who heard it.
 *
 * The listening is the feature, not the sending: a test packet's whole value is the
 * reach report that arrives over the following minutes, which is why the run is
 * kept in session storage. Walking off to read a conversation and coming back must
 * not mean starting again — the flood has already been spent.
 *
 * The reach endpoint has no Message row for this packet, so it returns no origin
 * and no distance. Both come from the send response and the browser instead.
 */

const MeshTestMap = lazy(() => import('./MeshTestMap').then((m) => ({ default: m.MeshTestMap })));

interface MeshTestViewProps {
  /** Leaves this sub-screen for the Tools screen. Phones only. */
  onBackToTools?: () => void;
  contacts: Contact[];
  /** Regions the radio knows, from radio settings. Empty means nothing to send to. */
  knownRegions: string[];
  floodScope?: string;
  radioConnected: boolean;
  onOpenRadioSettings?: () => void;
}

/** mm:ss rather than "3 minutes ago": the ten-minute window is what is being read. */
function formatAge(ms: number): string {
  const total = Math.max(0, Math.floor(ms / 1000));
  const minutes = Math.floor(total / 60);
  const seconds = total % 60;
  return `${minutes}:${String(seconds).padStart(2, '0')}`;
}

function SummaryCell({ label, value }: { label: string; value: string }) {
  return (
    <div className="min-w-0">
      <div className="text-[0.625rem] font-medium uppercase tracking-wider text-muted-foreground">
        {label}
      </div>
      <div className="truncate text-sm tabular-nums">{value}</div>
    </div>
  );
}

export function MeshTestView({
  onBackToTools,
  contacts,
  knownRegions,
  floodScope,
  radioConnected,
  onOpenRadioSettings,
}: MeshTestViewProps) {
  const { t } = useTranslation();
  const { distanceUnit } = useDistanceUnit();

  const [run, setRun] = useState<MeshTestRun | null>(() => readMeshTestRun());
  const [dialogOpen, setDialogOpen] = useState(false);
  const [sending, setSending] = useState(false);
  const [detail, setDetail] = useState<PacketObserverReachResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [sealed, setSealed] = useState(false);
  const [selectedKey, setSelectedKey] = useState<string | null>(null);
  const [directoryHits, setDirectoryHits] = useState<Record<string, DirectoryHopHit>>({});
  const [pollEpoch, setPollEpoch] = useState(0);
  const [nowMs, setNowMs] = useState(() => Date.now());

  // The age drives both the poll cadence and the listen state, so it has to move on
  // its own — otherwise a run with no new observers never reports that it is done.
  useEffect(() => {
    const timer = window.setInterval(() => setNowMs(Date.now()), 5_000);
    return () => window.clearInterval(timer);
  }, []);

  const ageMs = run ? nowMs - run.sentAt * 1000 : 0;
  const listenState = run ? meshTestListenState(ageMs, sealed) : 'stopped';
  const origin = useMemo(() => meshTestOrigin(run), [run]);

  useEffect(() => {
    if (!run) return;
    let cancelled = false;
    let timer: number | undefined;

    const poll = async () => {
      try {
        const payload = await api.getPacketObserverReach(run.packetHash);
        if (cancelled) return;
        setDetail(payload);
        setError(null);
        if (payload.sealed === true) {
          // Sealed means the window closed for good; it cannot be true inside the
          // first ten minutes, so there is nothing more to wait for.
          setSealed(true);
          return;
        }
      } catch (err) {
        if (cancelled) return;
        setError(formatApiError(err, i18n.t));
      }
      if (cancelled) return;
      const interval = observerReachPollIntervalMs(Date.now() - run.sentAt * 1000);
      if (interval == null) return;
      timer = window.setTimeout(() => void poll(), interval);
    };

    void poll();
    return () => {
      cancelled = true;
      if (timer != null) window.clearTimeout(timer);
    };
  }, [run, pollEpoch]);

  // Hops the radio does not know get their name and position from the directory,
  // exactly as the per-message reach modal resolves them.
  useEffect(() => {
    if (!detail) {
      setDirectoryHits((current) => (Object.keys(current).length === 0 ? current : {}));
      return;
    }
    const prefixes = new Set<string>();
    for (const observer of detail.observers) {
      for (const raw of observer.path ?? []) {
        const hop = toPathHop(raw, contacts);
        if (hopNeedsDirectoryGps(hop)) {
          prefixes.add(hop.prefix);
        }
      }
    }
    if (prefixes.size === 0) {
      setDirectoryHits((current) => (Object.keys(current).length === 0 ? current : {}));
      return;
    }
    let cancelled = false;
    void api.resolveDirectoryHops([...prefixes]).then(
      (res) => {
        if (!cancelled) setDirectoryHits(res.resolved);
      },
      () => {
        if (!cancelled) setDirectoryHits({});
      }
    );
    return () => {
      cancelled = true;
    };
  }, [detail, contacts]);

  const observers = useMemo<MeshTestObserver[]>(() => {
    const entries = detail?.observers ?? [];
    return sortMeshTestObservers(
      entries.map((entry, index) => {
        const placed = placedObserverCoordinates(entry, contacts);
        return {
          key: observerRowKey(entry, index),
          name: entry.name,
          isMLC: entry.isMLC === true,
          role: entry.role ?? null,
          hops: entry.hops ?? null,
          snr: entry.snr ?? null,
          rssi: entry.rssi ?? null,
          lat: placed.lat,
          lon: placed.lon,
          distanceKm: meshTestDistanceKm(origin, placed.lat, placed.lon),
          path: (entry.path ?? []).map((prefix, hopIndex) => {
            const hop = toPathHop(prefix, contacts);
            const hit = directoryHits[hop.prefix];
            const name = hopDisplayName(hop, hit);
            const location = hopMapLocation(hop, hit) ?? contactCoordinatesByName(name, contacts);
            return {
              prefix: hop.prefix,
              hopIndex,
              name,
              lat: location?.lat ?? null,
              lon: location?.lon ?? null,
            };
          }),
        };
      })
    );
  }, [detail, contacts, directoryHits, origin]);

  const summary = useMemo(() => meshTestSummary(observers), [observers]);

  const handleRun = useCallback(
    async (region: string) => {
      setSending(true);
      try {
        const response = await api.runMeshTest(region);
        const next: MeshTestRun = {
          packetHash: response.packet_hash,
          sentAt: response.sent_at,
          floodScope: response.flood_scope,
          originLat: response.origin_lat,
          originLon: response.origin_lon,
        };
        saveMeshTestRun(next);
        setDetail(null);
        setSealed(false);
        setError(null);
        setSelectedKey(null);
        setDirectoryHits({});
        setNowMs(Date.now());
        setRun(next);
        setDialogOpen(false);
        toast.success(t('meshTest.sent'));
      } catch (err) {
        toast.error(t('meshTest.sendFailed'), { description: formatApiError(err, i18n.t) });
      } finally {
        setSending(false);
      }
    },
    [t]
  );

  const handleForget = useCallback(() => {
    clearMeshTestRun();
    setRun(null);
    setDetail(null);
    setSealed(false);
    setError(null);
    setSelectedKey(null);
    setDirectoryHits({});
  }, []);

  const sendDisabled = sending || !radioConnected;

  return (
    <div className="flex h-full min-h-0 flex-col bg-background">
      <ToolPaneHeader
        title={t('meshTest.title')}
        onBack={onBackToTools}
        actions={
          <Button
            type="button"
            size="sm"
            disabled={sendDisabled}
            onClick={() => setDialogOpen(true)}
            title={radioConnected ? t('meshTest.runHelp') : t('radioStatus.disconnectedHelp')}
            className="h-8 gap-1.5 px-2.5 text-xs"
          >
            <RadioTower
              className={cn('h-3.5 w-3.5 shrink-0', sending && 'animate-pulse')}
              aria-hidden="true"
            />
            <span>{sending ? t('meshTest.sending') : t('meshTest.run')}</span>
          </Button>
        }
      />

      <div className="flex min-h-0 flex-1 flex-col lg:flex-row">
        <div className="h-64 shrink-0 overflow-hidden border-b border-border lg:h-auto lg:min-w-0 lg:flex-1 lg:border-b-0 lg:border-r">
          <Suspense
            fallback={<div className="h-full w-full animate-pulse bg-muted/30" aria-hidden />}
          >
            <MeshTestMap
              origin={origin}
              observers={observers}
              selectedKey={selectedKey}
              onSelect={(key) => setSelectedKey((current) => (current === key ? null : key))}
            />
          </Suspense>
        </div>

        <aside className="flex min-h-0 w-full flex-1 flex-col overflow-hidden lg:w-2/5 lg:min-w-[22rem] lg:max-w-[38rem] lg:flex-none">
          {!run ? (
            <div className="flex flex-1 items-center justify-center px-6 py-8 text-center text-sm text-muted-foreground">
              {t('meshTest.idle')}
            </div>
          ) : (
            <>
              <div className="shrink-0 space-y-3 border-b border-border px-3 py-3">
                <div className="grid grid-cols-3 gap-x-3 gap-y-2">
                  <SummaryCell
                    label={t('meshTest.observers')}
                    value={String(summary.observerCount)}
                  />
                  <SummaryCell
                    label={t('meshTest.withGps')}
                    value={String(summary.positionedCount)}
                  />
                  <SummaryCell
                    label={t('meshTest.maxHops')}
                    value={summary.maxHops == null ? '—' : String(summary.maxHops)}
                  />
                  <SummaryCell
                    label={t('meshTest.maxDistance')}
                    value={
                      summary.maxDistanceKm == null
                        ? '—'
                        : formatDistance(summary.maxDistanceKm, distanceUnit)
                    }
                  />
                  <SummaryCell label={t('meshTest.scope')} value={run.floodScope || '—'} />
                  <SummaryCell label={t('meshTest.age')} value={formatAge(ageMs)} />
                </div>
                <div className="flex flex-wrap items-center gap-2">
                  <span
                    className={cn(
                      'inline-flex items-center gap-1.5 rounded-full px-2 py-0.5 text-[0.6875rem] font-medium',
                      listenState === 'listening'
                        ? 'bg-primary/15 text-primary'
                        : 'bg-muted text-muted-foreground'
                    )}
                  >
                    <span
                      className={cn(
                        'h-1.5 w-1.5 rounded-full',
                        listenState === 'listening'
                          ? 'animate-pulse bg-primary'
                          : 'bg-muted-foreground/60'
                      )}
                      aria-hidden="true"
                    />
                    {listenState === 'listening'
                      ? t('meshTest.listening')
                      : t('meshTest.listenDone')}
                  </span>
                  {/* "Final" is the sealed flag and nothing else. A listen that simply
                      ran out of time is finished, which is not the same claim. */}
                  {sealed && (
                    <span className="rounded-full bg-muted px-2 py-0.5 text-[0.6875rem] font-medium text-muted-foreground">
                      {t('meshTest.final')}
                    </span>
                  )}
                  {listenState !== 'listening' && (
                    <Button
                      type="button"
                      variant="outline"
                      size="sm"
                      className="h-7 px-2 text-xs"
                      onClick={() => setPollEpoch((epoch) => epoch + 1)}
                    >
                      {t('meshTest.resumeListening')}
                    </Button>
                  )}
                  <Button
                    type="button"
                    variant="ghost"
                    size="sm"
                    className="h-7 px-2 text-xs text-muted-foreground"
                    onClick={handleForget}
                  >
                    {t('meshTest.forget')}
                  </Button>
                </div>
                {error && <p className="text-xs text-destructive">{error}</p>}
              </div>

              <div className="min-h-0 flex-1 overflow-y-auto">
                {observers.length === 0 ? (
                  <p className="px-3 py-4 text-sm text-muted-foreground">
                    {listenState === 'listening'
                      ? t('meshTest.waiting')
                      : t('meshTest.noObservers')}
                  </p>
                ) : (
                  <ul className="divide-y divide-border">
                    {observers.map((observer) => (
                      <li key={observer.key}>
                        <button
                          type="button"
                          aria-pressed={selectedKey === observer.key}
                          onClick={() =>
                            setSelectedKey((current) =>
                              current === observer.key ? null : observer.key
                            )
                          }
                          className={cn(
                            'w-full px-3 py-2 text-left transition-colors hover:bg-accent/40',
                            'focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-ring',
                            selectedKey === observer.key && 'bg-accent/60'
                          )}
                        >
                          <div className="flex items-center gap-1.5">
                            {observer.isMLC ? (
                              <img
                                src="./meshloom-mark.svg"
                                alt={t('messageList.observerMlcMark')}
                                title={t('messageList.observerMlcMark')}
                                data-testid="mesh-test-mlc-mark"
                                className="h-3.5 w-3.5 shrink-0"
                              />
                            ) : (
                              <span
                                className="h-2.5 w-2.5 shrink-0 rounded-full"
                                style={{
                                  backgroundColor: roleColor(observer.role),
                                }}
                                aria-hidden="true"
                              />
                            )}
                            <span className="truncate text-sm font-medium">{observer.name}</span>
                            <span className="ml-auto shrink-0 text-[0.6875rem] text-muted-foreground">
                              {t(
                                `live.nodes.${normalizeDirectoryRole(observer.role ?? undefined)}`
                              )}
                            </span>
                          </div>

                          <div className="mt-0.5 flex flex-wrap gap-x-3 gap-y-0.5 text-[0.6875rem] tabular-nums text-muted-foreground">
                            <span>
                              {t('meshTest.hopsLabel')}{' '}
                              {observer.hops == null ? '—' : observer.hops}
                            </span>
                            <span>
                              {t('meshTest.distanceLabel')}{' '}
                              {observer.distanceKm == null
                                ? '—'
                                : formatDistance(observer.distanceKm, distanceUnit)}
                            </span>
                            <span>
                              SNR {observer.snr == null ? '—' : `${observer.snr.toFixed(1)} dB`}
                            </span>
                            <span>RSSI {observer.rssi == null ? '—' : `${observer.rssi} dBm`}</span>
                            {observer.lat == null || observer.lon == null ? (
                              <span>{t('meshTest.noPosition')}</span>
                            ) : null}
                          </div>

                          {observer.path.length > 0 && (
                            <ol className="mt-1 space-y-0.5">
                              {observer.path.map((hop) => (
                                <li
                                  key={`${observer.key}-${hop.hopIndex}-${hop.prefix}`}
                                  className="text-[0.6875rem] text-muted-foreground"
                                >
                                  <span className="text-foreground/80">
                                    {t('path.hop', { n: hop.hopIndex + 1 })}
                                  </span>{' '}
                                  <span className="font-mono text-primary">{hop.prefix}</span>
                                  {hop.name ? ` · ${hop.name}` : ''}
                                  {/* Every hop is listed, positioned or not: the route
                                      is what it is, and a hop dropped for having no
                                      GPS would silently shorten it. */}
                                  {hop.lat == null || hop.lon == null
                                    ? ` · ${t('meshTest.noPosition')}`
                                    : ''}
                                </li>
                              ))}
                            </ol>
                          )}
                        </button>
                      </li>
                    ))}
                  </ul>
                )}
              </div>
            </>
          )}
        </aside>
      </div>

      <MeshTestRunDialog
        open={dialogOpen}
        onOpenChange={setDialogOpen}
        knownRegions={knownRegions}
        floodScope={floodScope}
        sending={sending}
        onRun={(region) => void handleRun(region)}
        onOpenRadioSettings={onOpenRadioSettings}
      />
    </div>
  );
}

/** Same role palette as the live map, so a colour means one thing across the app. */
function roleColor(role: string | null): string {
  return nodeRoleStyle(role ?? undefined).color;
}
