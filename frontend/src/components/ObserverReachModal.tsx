import { lazy, Suspense, useEffect, useMemo, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { ChevronDown, ChevronRight } from 'lucide-react';

import { api, formatApiError } from '../api';
import i18n from '../i18n';
import { useDistanceUnit } from '../contexts/DistanceUnitContext';
import type {
  Contact,
  ObserverReachEntry,
  ObserverReachMapHop,
  PacketObserverReachResponse,
} from '../types';
import { formatDistance, resolveLocalHopDisplay, type DirectoryHopHit } from '../utils/pathUtils';
import { hopDisplayName, hopMapLocation, toPathHop } from '../utils/observerHops';
import { Button } from './ui/button';
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from './ui/dialog';

const EMPTY_CONTACTS: Contact[] = [];

const ObserverReachMap = lazy(() =>
  import('./ObserverReachMap').then((mod) => ({ default: mod.ObserverReachMap }))
);

function observerRowKey(observer: ObserverReachEntry, index: number): string {
  return `${observer.public_key ?? observer.name}-${index}`;
}

function ObserverName({ observer }: { observer: ObserverReachEntry }) {
  const { t } = useTranslation();
  return (
    <span className="flex items-center gap-1.5 min-w-0">
      {observer.isMLC ? (
        <img
          src="./meshloom-mark.svg"
          alt={t('messageList.observerMlcMark')}
          title={t('messageList.observerMlcMark')}
          data-testid="observer-mlc-mark"
          className="h-3.5 w-3.5 shrink-0"
        />
      ) : null}
      <span className="truncate">{observer.name}</span>
    </span>
  );
}

function observerPath(observer: ObserverReachEntry): string[] {
  return observer.path ?? [];
}

interface ObserverReachModalProps {
  packetHash: string | null;
  open: boolean;
  onOpenChange: (open: boolean) => void;
  contacts?: Contact[];
}

export function ObserverReachModal({
  packetHash,
  open,
  onOpenChange,
  contacts = EMPTY_CONTACTS,
}: ObserverReachModalProps) {
  const { t } = useTranslation();
  const { distanceUnit } = useDistanceUnit();
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [detail, setDetail] = useState<PacketObserverReachResponse | null>(null);
  const [expandedKey, setExpandedKey] = useState<string | null>(null);
  const [directoryHits, setDirectoryHits] = useState<Record<string, DirectoryHopHit>>({});

  useEffect(() => {
    if (!open || !packetHash) return;
    let cancelled = false;
    setLoading(true);
    setError(null);
    setDetail(null);
    setExpandedKey(null);
    setDirectoryHits({});
    void api
      .getPacketObserverReach(packetHash)
      .then((payload) => {
        if (cancelled) return;
        setDetail(payload);
      })
      .catch((err: unknown) => {
        if (cancelled) return;
        setError(formatApiError(err, i18n.t));
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [open, packetHash]);

  useEffect(() => {
    if (!detail) {
      setDirectoryHits({});
      return;
    }
    const prefixes = new Set<string>();
    for (const observer of detail.observers) {
      for (const raw of observerPath(observer)) {
        const hop = toPathHop(raw, contacts);
        if (resolveLocalHopDisplay(hop).kind === 'unknown') {
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

  const observers = detail?.observers ?? [];
  const distanceLabel =
    detail?.max_distance_km != null
      ? formatDistance(detail.max_distance_km, distanceUnit)
      : t('messageList.observerReachDistanceUnavailable');
  const origin =
    detail?.origin_lat != null && detail.origin_lon != null
      ? { lat: detail.origin_lat, lon: detail.origin_lon }
      : null;
  const selectedHops = useMemo((): ObserverReachMapHop[] => {
    const selected = observers.find(
      (observer, index) => observerRowKey(observer, index) === expandedKey
    );
    if (!selected) return [];
    const hops: ObserverReachMapHop[] = [];
    for (const [hopIndex, prefix] of observerPath(selected).entries()) {
      const hop = toPathHop(prefix, contacts);
      const location = hopMapLocation(hop, directoryHits[hop.prefix]);
      if (!location) continue;
      hops.push({
        prefix: hop.prefix,
        hopIndex,
        lat: location.lat,
        lon: location.lon,
        name: hopDisplayName(hop, directoryHits[hop.prefix]),
      });
    }
    return hops;
  }, [observers, expandedKey, contacts, directoryHits]);

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-w-lg">
        <DialogHeader>
          <DialogTitle>{t('messageList.observerReachTitle')}</DialogTitle>
          <DialogDescription>{t('messageList.observerReachCaveat')}</DialogDescription>
        </DialogHeader>
        {loading && (
          <p className="text-[0.8125rem] text-muted-foreground">
            {t('messageList.observerReachLoading')}
          </p>
        )}
        {error && <p className="text-sm text-destructive">{error}</p>}
        {!loading && !error && detail && (
          <div className="space-y-3">
            <div className="grid grid-cols-2 gap-2">
              <div>
                <div className="text-[0.625rem] uppercase tracking-wider text-muted-foreground font-medium">
                  {t('messageList.observerReachCount')}
                </div>
                <div className="text-sm tabular-nums">{detail.observer_count}</div>
              </div>
              <div>
                <div className="text-[0.625rem] uppercase tracking-wider text-muted-foreground font-medium">
                  {t('messageList.observerReachMaxHops')}
                </div>
                <div className="text-sm tabular-nums">
                  {detail.max_hops == null ? '—' : detail.max_hops}
                </div>
              </div>
              <div className="col-span-2">
                <div className="text-[0.625rem] uppercase tracking-wider text-muted-foreground font-medium">
                  {t('messageList.observerReachMaxDistance')}
                </div>
                <div className="text-sm">{distanceLabel}</div>
              </div>
            </div>
            {detail.observer_count === 0 && (
              <p className="text-[0.8125rem] text-muted-foreground">
                {t('messageList.observerReachEmpty')}
              </p>
            )}
            {observers.length > 0 && (
              <ul className="max-h-64 overflow-auto rounded border border-border divide-y divide-border">
                {observers.map((observer, index) => {
                  const key = observerRowKey(observer, index);
                  const path = observerPath(observer);
                  const expandable = path.length > 0;
                  const expanded = expandedKey === key;
                  return (
                    <li key={key} className="text-sm">
                      {expandable ? (
                        <button
                          type="button"
                          className="w-full px-2 py-1.5 flex items-center justify-between gap-2 text-left hover:bg-accent/50 transition-colors"
                          aria-expanded={expanded}
                          aria-label={
                            expanded
                              ? t('messageList.observerReachCollapsePath', { name: observer.name })
                              : t('messageList.observerReachExpandPath', { name: observer.name })
                          }
                          onClick={() => setExpandedKey(expanded ? null : key)}
                        >
                          <span className="flex items-center gap-1.5 min-w-0">
                            {expanded ? (
                              <ChevronDown className="h-3 w-3 shrink-0 text-muted-foreground" />
                            ) : (
                              <ChevronRight className="h-3 w-3 shrink-0 text-muted-foreground" />
                            )}
                            <ObserverName observer={observer} />
                          </span>
                          <span className="shrink-0 text-[0.6875rem] text-muted-foreground tabular-nums">
                            {observer.hops != null
                              ? observer.hops === 0
                                ? t('messageList.observerReachDirect')
                                : t('messageList.observerReachHops', { count: observer.hops })
                              : '—'}
                            {observer.snr != null ? ` · ${observer.snr.toFixed(1)} dB` : ''}
                          </span>
                        </button>
                      ) : (
                        <div className="px-2 py-1.5 flex items-center justify-between gap-2">
                          <span className="min-w-0 pl-4">
                            <ObserverName observer={observer} />
                          </span>
                          <span className="shrink-0 text-[0.6875rem] text-muted-foreground tabular-nums">
                            {observer.hops != null
                              ? observer.hops === 0
                                ? t('messageList.observerReachDirect')
                                : t('messageList.observerReachHops', { count: observer.hops })
                              : '—'}
                            {observer.snr != null ? ` · ${observer.snr.toFixed(1)} dB` : ''}
                          </span>
                        </div>
                      )}
                      {expanded && (
                        <ol className="px-2 pb-2 pl-7 space-y-1">
                          {path.map((prefix, hopIndex) => {
                            const hop = toPathHop(prefix, contacts);
                            const name = hopDisplayName(hop, directoryHits[hop.prefix]);
                            return (
                              <li
                                key={`${hop.prefix}-${hopIndex}`}
                                className="text-[0.6875rem] text-muted-foreground"
                              >
                                <span className="text-foreground/80">
                                  {t('path.hop', { n: hopIndex + 1 })}
                                </span>{' '}
                                <span className="font-mono text-primary">{hop.prefix}</span>
                                {name ? ` · ${name}` : ''}
                              </li>
                            );
                          })}
                        </ol>
                      )}
                    </li>
                  );
                })}
              </ul>
            )}
            <Suspense
              fallback={
                <div
                  className="h-56 rounded border border-border bg-muted/30 animate-pulse"
                  aria-hidden
                />
              }
            >
              <ObserverReachMap
                observers={observers}
                origin={origin}
                selectedKey={expandedKey}
                selectedHops={selectedHops}
                observerKey={observerRowKey}
                onSelect={(key) => setExpandedKey((current) => (current === key ? null : key))}
              />
            </Suspense>
          </div>
        )}
        <DialogFooter>
          <Button type="button" variant="outline" onClick={() => onOpenChange(false)}>
            {t('messageList.cancel')}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
