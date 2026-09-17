import { useMemo, useState, useCallback, useRef, useEffect, lazy, Suspense } from 'react';
import { Map as MapIcon, Maximize2, List, Minimize2, RefreshCw } from 'lucide-react';
import { useTranslation } from 'react-i18next';
import { cn } from '@/lib/utils';
import { RepeaterPane, NotFetched, formatDuration } from './repeaterPaneShared';
import { isValidLocation, calculateDistance, formatDistance } from '../../utils/pathUtils';
import { useDistanceUnit } from '../../contexts/DistanceUnitContext';
import type {
  Contact,
  RepeaterNeighborsResponse,
  PaneState,
  NeighborInfo,
  RepeaterNodeInfoResponse,
} from '../../types';

const NeighborsMiniMap = lazy(() =>
  import('../NeighborsMiniMap').then((m) => ({ default: m.NeighborsMiniMap }))
);

type SortField = 'name' | 'snr' | 'distance' | 'last_heard';
type SortDir = 'asc' | 'desc';

// Direction applied when a column is first selected. Name reads naturally A→Z
// and nearest-first/most-recent-first are the intuitive starting points; SNR
// leads with the strongest signal to preserve the previous default ordering.
const DEFAULT_DIR: Record<SortField, SortDir> = {
  name: 'asc',
  snr: 'desc',
  distance: 'asc',
  last_heard: 'asc',
};

function SortableHeader({
  label,
  field,
  sortField,
  sortDir,
  onSort,
  className,
}: {
  label: string;
  field: SortField;
  sortField: SortField;
  sortDir: SortDir;
  onSort: (field: SortField) => void;
  className?: string;
}) {
  const active = sortField === field;
  return (
    <th
      className={cn(
        'pb-1 font-medium cursor-pointer select-none hover:text-foreground transition-colors',
        className
      )}
      onClick={() => onSort(field)}
      aria-sort={active ? (sortDir === 'asc' ? 'ascending' : 'descending') : 'none'}
    >
      {label} {active ? (sortDir === 'asc' ? '▲' : '▼') : ''}
    </th>
  );
}

export function NeighborsPane({
  data,
  state,
  onRefresh,
  disabled,
  repeaterContact,
  contacts,
  nodeInfo,
  nodeInfoState,
  repeaterName,
}: {
  data: RepeaterNeighborsResponse | null;
  state: PaneState;
  onRefresh: () => void;
  disabled?: boolean;
  repeaterContact: Contact | null;
  contacts: Contact[];
  nodeInfo: RepeaterNodeInfoResponse | null;
  nodeInfoState: PaneState;
  repeaterName: string | null;
}) {
  const { t } = useTranslation();
  const { distanceUnit } = useDistanceUnit();

  // Refreshing neighbours re-fetches node info first, and node info is what carries
  // the coordinates. Letting the map mount depend on the live value tore it down and
  // rebuilt it on every refresh. Once we have had a position, the map stays.
  const everHadGpsRef = useRef(false);

  const advertLat = repeaterContact?.lat ?? null;
  const advertLon = repeaterContact?.lon ?? null;

  const radioLat = useMemo(() => {
    const parsed = nodeInfo?.lat != null ? parseFloat(nodeInfo.lat) : null;
    return Number.isFinite(parsed) ? parsed : null;
  }, [nodeInfo?.lat]);

  const radioLon = useMemo(() => {
    const parsed = nodeInfo?.lon != null ? parseFloat(nodeInfo.lon) : null;
    return Number.isFinite(parsed) ? parsed : null;
  }, [nodeInfo?.lon]);

  const positionSource = useMemo(() => {
    if (isValidLocation(radioLat, radioLon)) {
      return { lat: radioLat, lon: radioLon, source: 'reported' as const };
    }
    if (isValidLocation(advertLat, advertLon)) {
      return { lat: advertLat, lon: advertLon, source: 'advert' as const };
    }
    return { lat: null, lon: null, source: null };
  }, [advertLat, advertLon, radioLat, radioLon]);

  const radioName = nodeInfo?.name || repeaterContact?.name || repeaterName;
  const hasValidRepeaterGps = positionSource.source !== null;
  if (hasValidRepeaterGps) everHadGpsRef.current = true;
  const canShowMap = hasValidRepeaterGps || everHadGpsRef.current;
  const headerNote =
    positionSource.source === 'reported'
      ? t('repeater.posReported')
      : positionSource.source === 'advert'
        ? t('repeater.posAdvert')
        : nodeInfoState.loading
          ? t('repeater.posWaiting')
          : t('repeater.posMissing');

  // The table and the map used to be stacked, which left the map about 190px tall
  // on a phone with no way to grow it. They are now two views of the same data.
  const [view, setView] = useState<'list' | 'map'>('list');
  const [expanded, setExpanded] = useState(false);
  const [detailed, setDetailed] = useState(false);
  const [permanent, setPermanent] = useState(false);
  const [recenterToken, setRecenterToken] = useState(0);

  useEffect(() => {
    if (!expanded) return;
    const onKey = (event: KeyboardEvent) => {
      if (event.key === 'Escape') setExpanded(false);
    };
    document.addEventListener('keydown', onKey);
    return () => document.removeEventListener('keydown', onKey);
  }, [expanded]);

  const [sortField, setSortField] = useState<SortField>('snr');
  const [sortDir, setSortDir] = useState<SortDir>('desc');

  const handleSort = useCallback(
    (field: SortField) => {
      if (sortField === field) {
        setSortDir((d) => (d === 'asc' ? 'desc' : 'asc'));
      } else {
        setSortField(field);
        setSortDir(DEFAULT_DIR[field]);
      }
    },
    [sortField]
  );

  // Resolve contact data for each neighbor in a single pass — used for coords
  // (mini-map) and distances (table column + distance sort). The formatted
  // string drives display; the raw km drives numeric distance sorting.
  const { neighborsWithCoords, enriched, hasDistances } = useMemo(() => {
    if (!data) {
      return {
        neighborsWithCoords: [] as Array<
          NeighborInfo & { lat: number | null; lon: number | null; distance: string | null }
        >,
        enriched: [] as Array<
          NeighborInfo & { distance: string | null; distanceKm: number | null }
        >,
        hasDistances: false,
      };
    }

    const withCoords: Array<
      NeighborInfo & { lat: number | null; lon: number | null; distance: string | null }
    > = [];
    const list: Array<NeighborInfo & { distance: string | null; distanceKm: number | null }> = [];
    let anyDist = false;

    for (const n of data.neighbors) {
      const contact = contacts.find((c) => c.public_key.startsWith(n.pubkey_prefix));
      const nLat = contact?.lat ?? null;
      const nLon = contact?.lon ?? null;

      let dist: string | null = null;
      let distKm: number | null = null;
      if (hasValidRepeaterGps && isValidLocation(nLat, nLon)) {
        const km = calculateDistance(positionSource.lat, positionSource.lon, nLat, nLon);
        if (km != null) {
          distKm = km;
          dist = formatDistance(km, distanceUnit);
          anyDist = true;
        }
      }
      list.push({ ...n, distance: dist, distanceKm: distKm });

      if (isValidLocation(nLat, nLon)) {
        withCoords.push({ ...n, lat: nLat, lon: nLon, distance: dist });
      }
    }

    return {
      neighborsWithCoords: withCoords,
      enriched: list,
      hasDistances: anyDist,
    };
  }, [contacts, data, distanceUnit, hasValidRepeaterGps, positionSource.lat, positionSource.lon]);

  const sorted = useMemo(() => {
    const dir = sortDir === 'asc' ? 1 : -1;
    return [...enriched].sort((a, b) => {
      switch (sortField) {
        case 'name': {
          const an = (a.name || a.pubkey_prefix).toLowerCase();
          const bn = (b.name || b.pubkey_prefix).toLowerCase();
          return an.localeCompare(bn) * dir;
        }
        case 'distance': {
          // Neighbors without a known distance always sort last, regardless of direction.
          if (a.distanceKm == null && b.distanceKm == null) return 0;
          if (a.distanceKm == null) return 1;
          if (b.distanceKm == null) return -1;
          return (a.distanceKm - b.distanceKm) * dir;
        }
        case 'last_heard':
          return (a.last_heard_seconds - b.last_heard_seconds) * dir;
        case 'snr':
        default:
          return (a.snr - b.snr) * dir;
      }
    });
  }, [enriched, sortField, sortDir]);

  return (
    <RepeaterPane
      title={
        !data
          ? t('repeater.neighbors')
          : data.reported_count != null && data.reported_count !== data.neighbors.length
            ? t('repeater.neighborsCountOf', {
                shown: data.neighbors.length,
                total: data.reported_count,
              })
            : t('repeater.neighborsCount', {
                count: data.reported_count ?? data.neighbors.length,
              })
      }
      headerNote={headerNote}
      state={state}
      // The refresh control lives in the view switch below, labelled and at a size
      // a thumb can hit. Two of them for one pane is one too many.
      disabled={disabled}
      className="flex min-h-0 flex-1 flex-col"
      contentClassName="flex min-h-0 flex-1 flex-col"
    >
      <div
        className={cn(
          'flex min-h-0 flex-1 flex-col gap-2',
          expanded && 'fixed inset-0 z-50 bg-background p-3'
        )}
      >
        {/* Always available: with nothing fetched yet, a pane whose only control is
            hidden behind having data is a pane you cannot use. */}
        <div className="flex shrink-0 flex-wrap items-center gap-2">
          <div
            className="inline-flex rounded-md border border-border p-0.5"
            role="tablist"
            aria-label={t('repeater.neighborsViewAria')}
          >
            {(['list', 'map'] as const).map((mode) => (
              <button
                key={mode}
                type="button"
                role="tab"
                aria-selected={view === mode}
                onClick={() => setView(mode)}
                disabled={mode === 'map' && !hasValidRepeaterGps}
                className={cn(
                  'inline-flex items-center gap-1.5 rounded px-2.5 py-1.5 text-xs transition-colors disabled:cursor-not-allowed disabled:opacity-50',
                  'focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring',
                  view === mode
                    ? 'bg-accent text-foreground'
                    : 'text-muted-foreground hover:text-foreground'
                )}
              >
                {mode === 'list' ? (
                  <List className="h-3.5 w-3.5" aria-hidden="true" />
                ) : (
                  <MapIcon className="h-3.5 w-3.5" aria-hidden="true" />
                )}
                {mode === 'list' ? t('repeater.viewList') : t('repeater.viewMap')}
              </button>
            ))}
          </div>

          <button
            type="button"
            onClick={onRefresh}
            disabled={disabled || state.loading}
            className="inline-flex items-center gap-1.5 rounded-md border border-border px-2.5 py-1.5 text-xs text-muted-foreground transition-colors hover:text-foreground disabled:opacity-50 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
          >
            <RefreshCw
              className={cn('h-3.5 w-3.5', state.loading && 'animate-spin')}
              aria-hidden="true"
            />
            {state.loading ? t('repeater.refreshing') : t('repeater.refresh')}
          </button>

          {/* A repeater answers in chunks, so the first fetch often returns fewer
              neighbours than it reports having. The title said "5 of 7" and left
              people to guess that pressing refresh again would help. Say it. */}
          {data?.reported_count != null && data.reported_count > data.neighbors.length && (
            <button
              type="button"
              onClick={onRefresh}
              disabled={disabled || state.loading}
              className="inline-flex items-center gap-1.5 rounded-md border border-warning/40 bg-warning/10 px-2.5 py-1.5 text-xs text-warning transition-colors hover:bg-warning/20 disabled:opacity-50 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
            >
              {t('repeater.loadRemaining', {
                missing: data.reported_count - data.neighbors.length,
              })}
            </button>
          )}

          {view === 'map' && (
            <>
              <button
                type="button"
                onClick={() => setRecenterToken((n) => n + 1)}
                className="inline-flex items-center gap-1.5 rounded-md border border-border px-2.5 py-1.5 text-xs text-muted-foreground transition-colors hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
              >
                {t('repeater.recenter')}
              </button>
              <button
                type="button"
                onClick={() => setDetailed((v) => !v)}
                aria-pressed={detailed}
                className={cn(
                  'ml-auto inline-flex items-center gap-1.5 rounded-md border px-2.5 py-1.5 text-xs transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring',
                  detailed
                    ? 'border-border bg-accent text-foreground'
                    : 'border-border text-muted-foreground hover:text-foreground'
                )}
              >
                {t('repeater.detailedLabels')}
              </button>
              <button
                type="button"
                onClick={() => setPermanent((v) => !v)}
                aria-pressed={permanent}
                className={cn(
                  'inline-flex items-center gap-1.5 rounded-md border px-2.5 py-1.5 text-xs transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring',
                  permanent
                    ? 'border-border bg-accent text-foreground'
                    : 'border-border text-muted-foreground hover:text-foreground'
                )}
              >
                {t('repeater.permanentLabels')}
              </button>
              <button
                type="button"
                onClick={() => setExpanded((v) => !v)}
                aria-pressed={expanded}
                className="inline-flex items-center gap-1.5 rounded-md border border-border px-2.5 py-1.5 text-xs text-muted-foreground transition-colors hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
              >
                {expanded ? (
                  <Minimize2 className="h-3.5 w-3.5" aria-hidden="true" />
                ) : (
                  <Maximize2 className="h-3.5 w-3.5" aria-hidden="true" />
                )}
                {expanded ? t('repeater.shrinkMap') : t('repeater.expandMap')}
              </button>
            </>
          )}
        </div>
        {!data ? (
          <NotFetched />
        ) : sorted.length === 0 ? (
          // An empty list is two very different things. The backend asks once with a
          // 10s timeout (`fetch_all_neighbours(timeout=10)`), so a distant repeater
          // that has not answered yet comes back indistinguishable from one that
          // truly has no neighbours — except that a repeater which did answer sets
          // reported_count. Saying "no neighbours reported" in both cases states a
          // timeout as fact.
          data.reported_count == null ? (
            <div className="space-y-2">
              <p className="text-sm text-foreground">{t('repeater.neighborsNoAnswer')}</p>
              <p className="max-w-prose text-[0.8125rem] text-muted-foreground">
                {t('repeater.neighborsNoAnswerHelp')}
              </p>
              <button
                type="button"
                onClick={onRefresh}
                disabled={disabled || state.loading}
                className="inline-flex items-center gap-1.5 rounded-md border border-border px-2.5 py-1.5 text-xs text-foreground transition-colors hover:bg-accent disabled:opacity-50 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
              >
                <RefreshCw
                  className={cn('h-3.5 w-3.5', state.loading && 'animate-spin')}
                  aria-hidden="true"
                />
                {t('repeater.retry')}
              </button>
            </div>
          ) : (
            <p className="text-sm text-muted-foreground">{t('repeater.noNeighbors')}</p>
          )
        ) : (
          <div className={cn('min-h-0 flex-1 overflow-x-auto', view !== 'list' && 'hidden')}>
            <table className="w-full text-sm">
              <thead>
                <tr className="text-left text-muted-foreground text-xs">
                  <SortableHeader
                    label={t('repeater.name')}
                    field="name"
                    sortField={sortField}
                    sortDir={sortDir}
                    onSort={handleSort}
                  />
                  <SortableHeader
                    label={t('repeater.snr')}
                    field="snr"
                    sortField={sortField}
                    sortDir={sortDir}
                    onSort={handleSort}
                    className="text-right"
                  />
                  {hasDistances && (
                    <SortableHeader
                      label={t('repeater.dist')}
                      field="distance"
                      sortField={sortField}
                      sortDir={sortDir}
                      onSort={handleSort}
                      className="text-right"
                    />
                  )}
                  <SortableHeader
                    label={t('repeater.lastHeard')}
                    field="last_heard"
                    sortField={sortField}
                    sortDir={sortDir}
                    onSort={handleSort}
                    className="text-right"
                  />
                </tr>
              </thead>
              <tbody>
                {sorted.map((n, i) => {
                  const dist = n.distance;
                  const snrStr = n.snr >= 0 ? `+${n.snr.toFixed(1)}` : n.snr.toFixed(1);
                  const snrColor =
                    n.snr >= 6 ? 'text-success' : n.snr >= 0 ? 'text-warning' : 'text-destructive';
                  return (
                    <tr key={i} className="border-t border-border/50">
                      <td className="py-1">
                        {n.name || n.pubkey_prefix}
                        {n.name && (
                          <span className="ml-1 text-muted-foreground font-mono text-[0.6875rem]">
                            {n.pubkey_prefix.substring(0, 6)}
                          </span>
                        )}
                      </td>
                      <td className={cn('py-1 text-right font-mono', snrColor)}>{snrStr} dB</td>
                      {hasDistances && (
                        <td className="py-1 text-right text-muted-foreground font-mono">
                          {dist ?? '—'}
                        </td>
                      )}
                      <td className="py-1 text-right text-muted-foreground">
                        {t('repeater.lastHeardAgo', {
                          duration: formatDuration(n.last_heard_seconds),
                        })}
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        )}

        {/* Outside the data branch on purpose. Inside it, every transition through
          "not fetched" / "no neighbours" / "list" tore the Leaflet instance down and
          Leaflet instance down and rebuilt it on every fetch, losing pan and zoom
          and re-downloading every tile — which is what made refreshing feel rough. */}
        <div className={cn('flex min-h-0 flex-1 flex-col', view !== 'map' && 'hidden')}>
          {/* Rendered unconditionally: a ternary here meant that every change of mind
              about whether coordinates exist tore Leaflet down and rebuilt it. The map
              draws nothing by itself when it has no position to show. */}
          <Suspense
            fallback={
              <div className="flex min-h-48 flex-1 items-center justify-center text-xs text-muted-foreground">
                {t('repeater.loadingMap')}
              </div>
            }
          >
            <NeighborsMiniMap
              neighbors={neighborsWithCoords}
              radioLat={positionSource.lat}
              radioLon={positionSource.lon}
              radioName={radioName}
              detailed={detailed}
              permanent={permanent}
              recenterToken={recenterToken}
              className={cn(
                'overflow-hidden rounded border border-border',
                // The dashboard column has no definite height, so flex-1 alone
                // resolved to zero and the map rendered 0px tall. Give it a real
                // height until it is expanded, where the parent does have one.
                expanded ? 'min-h-0 flex-1' : 'h-72 sm:h-96'
              )}
            />
          </Suspense>
          {!canShowMap && (
            <div className="rounded border border-border/70 bg-muted/20 px-3 py-2 text-xs text-muted-foreground">
              {t('repeater.mapUnavailable')}
            </div>
          )}
        </div>
      </div>
    </RepeaterPane>
  );
}
