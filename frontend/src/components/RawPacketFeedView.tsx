import { useEffect, useMemo, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { ChevronLeft, ChevronRight, Search, X } from 'lucide-react';
import {
  BarChart,
  Bar,
  XAxis,
  YAxis,
  CartesianGrid,
  Tooltip as RechartsTooltip,
  ResponsiveContainer,
  Cell,
} from 'recharts';

import { RawPacketList } from './RawPacketList';
import { RawPacketInspectorDialog } from './RawPacketDetailModal';
import { Button } from './ui/button';
import type { Channel, Contact, RawPacket } from '../types';
import {
  KNOWN_PAYLOAD_TYPES,
  PAYLOAD_TYPE_COLORS,
  RAW_PACKET_STATS_WINDOWS,
  buildPayloadTypeColorMap,
  buildRawPacketStatsSnapshot,
  getPacketTypeName,
  type NeighborStat,
  type PacketTimelineBin,
  type RankedPacketStat,
  type RawPacketStatsSessionState,
  type RawPacketStatsWindow,
} from '../utils/rawPacketStats';
import {
  collectGroupDataKeys,
  createDecoderOptions,
  decodePacketSummary,
  isPacketOpen,
} from '../utils/rawPacketInspector';
import { labelPayloadType, labelRoute } from '../utils/rawPacketLabels';
import { useRawPacketStatsSession, useRawPackets } from '../stores/rawPacketStore';
import { getContactDisplayName } from '../utils/pubkey';
import { cn } from '@/lib/utils';
import { ToolPaneHeader } from './ToolPaneHeader';
import i18n from '../i18n';

const ROUTE_FILTER_TYPES = ['Flood', 'Direct', 'TransportFlood', 'TransportDirect'] as const;
type RouteFilterType = (typeof ROUTE_FILTER_TYPES)[number];
type CryptoFilter = 'all' | 'decrypted' | 'encrypted';

function colorForType(colorMap?: Map<string, string>, name?: string): string {
  if (colorMap && name && colorMap.has(name)) {
    return colorMap.get(name)!;
  }
  return PAYLOAD_TYPE_COLORS.Unknown;
}

const PAYLOAD_TYPE_COLOR_MAP = buildPayloadTypeColorMap();

function labeledPayloadColorMap(): Map<string, string> {
  const map = new Map(PAYLOAD_TYPE_COLOR_MAP);
  for (const [name, color] of PAYLOAD_TYPE_COLOR_MAP) {
    map.set(labelPayloadType(name), color);
  }
  return map;
}

/**
 * Normalize a raw-hex filter query. Lowercases, strips whitespace, `:`
 * separators, and a leading `0x` so a pasted key prefix like `A1B2C3`,
 * `a1:b2:c3`, or `0xa1b2` all match. Returns `invalid: true` when non-hex
 * characters remain after cleaning so the UI can hint instead of silently
 * showing zero results. Matches against `RawPacket.data` (stored lowercase hex).
 */
function normalizeHexQuery(raw: string): { query: string; invalid: boolean } {
  const cleaned = raw
    .trim()
    .toLowerCase()
    .replace(/^0x/, '')
    .replace(/[\s:]+/g, '');
  if (cleaned === '') return { query: '', invalid: false };
  return { query: cleaned, invalid: !/^[0-9a-f]+$/.test(cleaned) };
}

interface FeedFilterControlsProps {
  className?: string;
  allTypesEnabled: boolean;
  enabledTypes: Set<string>;
  onToggleAll: () => void;
  onToggleType: (type: string) => void;
  onOnly: (type: string) => void;
  enabledRoutes: Set<string>;
  onToggleRoute: (route: RouteFilterType) => void;
  cryptoFilter: CryptoFilter;
  onCryptoFilterChange: (value: CryptoFilter) => void;
  autoScroll: boolean;
  onAutoScrollChange: (checked: boolean) => void;
  hexFilter: string;
  onHexFilterChange: (value: string) => void;
  hexInvalid: boolean;
  textFilter: string;
  onTextFilterChange: (value: string) => void;
  matchCount: number;
  totalCount: number;
}

/**
 * The feed filter bar: hex/text/route/crypto filters, payload-type checkboxes,
 * and the autoscroll toggle. Rendered twice (mobile + desktop) with only the
 * display classes differing, so the control set lives here to stay in sync.
 * Display is driven entirely by `className` (no base `flex`) to avoid a Tailwind
 * `flex`/`hidden` conflict.
 */
function FeedFilterControls({
  className,
  allTypesEnabled,
  enabledTypes,
  onToggleAll,
  onToggleType,
  onOnly,
  enabledRoutes,
  onToggleRoute,
  cryptoFilter,
  onCryptoFilterChange,
  autoScroll,
  onAutoScrollChange,
  hexFilter,
  onHexFilterChange,
  hexInvalid,
  textFilter,
  onTextFilterChange,
  matchCount,
  totalCount,
}: FeedFilterControlsProps) {
  const { t } = useTranslation();
  return (
    <div className={cn('mt-1.5 flex-wrap items-center gap-x-3 gap-y-1', className)}>
      <div className="relative">
        <input
          type="text"
          value={hexFilter}
          onChange={(event) => onHexFilterChange(event.target.value)}
          placeholder={t('rawPacket.hexPlaceholder')}
          aria-label={t('rawPacket.hexAria')}
          className="w-44 rounded border border-input bg-background px-2 py-0.5 pr-6 text-xs"
        />
        {hexFilter !== '' && (
          <button
            type="button"
            onClick={() => onHexFilterChange('')}
            aria-label={t('rawPacket.clearHex')}
            className="absolute right-1 top-1/2 -translate-y-1/2 text-muted-foreground hover:text-foreground"
          >
            <X className="h-3 w-3" />
          </button>
        )}
      </div>
      <div className="relative">
        <input
          type="text"
          value={textFilter}
          onChange={(event) => onTextFilterChange(event.target.value)}
          placeholder={t('rawPacket.textPlaceholder')}
          aria-label={t('rawPacket.textAria')}
          className="w-52 rounded border border-input bg-background px-2 py-0.5 pr-6 text-xs"
        />
        {textFilter !== '' && (
          <button
            type="button"
            onClick={() => onTextFilterChange('')}
            aria-label={t('rawPacket.clearText')}
            className="absolute right-1 top-1/2 -translate-y-1/2 text-muted-foreground hover:text-foreground"
          >
            <X className="h-3 w-3" />
          </button>
        )}
      </div>
      {hexInvalid ? (
        <span className="text-[0.6875rem] text-warning">{t('rawPacket.hexOnly')}</span>
      ) : (
        <span className="text-[0.6875rem] text-muted-foreground tabular-nums">
          {t('rawPacket.filterMatch', {
            match: matchCount.toLocaleString(),
            total: totalCount.toLocaleString(),
          })}
        </span>
      )}
      <span className="text-[0.625rem] uppercase tracking-wider text-muted-foreground">
        {t('rawPacket.filterRoute')}
      </span>
      {ROUTE_FILTER_TYPES.map((route) => (
        <label
          key={route}
          className="flex items-center gap-1 text-xs text-foreground cursor-pointer"
        >
          <input
            type="checkbox"
            checked={enabledRoutes.has(route)}
            onChange={() => onToggleRoute(route)}
            className="rounded"
          />
          {labelRoute(route)}
        </label>
      ))}
      <label className="flex items-center gap-1 text-xs text-foreground">
        <span className="text-muted-foreground">{t('rawPacket.filterCrypto')}</span>
        <select
          value={cryptoFilter}
          onChange={(event) => onCryptoFilterChange(event.target.value as CryptoFilter)}
          aria-label={t('rawPacket.cryptoAria')}
          className="rounded border border-input bg-background px-1.5 py-0.5 text-xs"
        >
          <option value="all">{t('rawPacket.cryptoAll')}</option>
          <option value="decrypted">{t('rawPacket.cryptoDecrypted')}</option>
          <option value="encrypted">{t('rawPacket.cryptoEncrypted')}</option>
        </select>
      </label>
      <label className="flex items-center gap-1 text-xs text-muted-foreground cursor-pointer">
        <input
          type="checkbox"
          checked={allTypesEnabled}
          onChange={onToggleAll}
          className="rounded"
        />
        {t('rawPacket.all')}
      </label>
      {KNOWN_PAYLOAD_TYPES.map((type) => (
        <span key={type} className="inline-flex items-center gap-1 text-xs">
          <label className="flex items-center gap-1 text-foreground cursor-pointer">
            <input
              type="checkbox"
              checked={enabledTypes.has(type)}
              onChange={() => onToggleType(type)}
              className="rounded"
            />
            {labelPayloadType(type)}
          </label>
          <button
            type="button"
            className="text-[0.625rem] text-muted-foreground hover:text-primary transition-colors"
            onClick={() => onOnly(type)}
          >
            {t('rawPacket.only')}
          </button>
        </span>
      ))}
      <label className="ml-auto flex items-center gap-1 text-xs text-foreground cursor-pointer">
        <input
          type="checkbox"
          checked={autoScroll}
          onChange={(event) => onAutoScrollChange(event.target.checked)}
          className="rounded"
        />
        {t('rawPacket.autoscroll')}
      </label>
    </div>
  );
}

interface RawPacketFeedViewProps {
  /** Leaves this sub-screen for the Tools screen. Phones only. */
  onBackToTools?: () => void;
  contacts: Contact[];
  channels: Channel[];
  radioOffline?: boolean;
}

const TOOLTIP_STYLE = {
  contentStyle: {
    backgroundColor: 'hsl(var(--popover))',
    border: '1px solid hsl(var(--border))',
    borderRadius: '6px',
    fontSize: '11px',
    color: 'hsl(var(--popover-foreground))',
  },
  itemStyle: { color: 'hsl(var(--popover-foreground))' },
  labelStyle: { color: 'hsl(var(--muted-foreground))' },
} as const;

const WINDOW_LABEL_KEYS: Record<RawPacketStatsWindow, string> = {
  '1m': 'rawPacket.window1m',
  '5m': 'rawPacket.window5m',
  '10m': 'rawPacket.window10m',
  '30m': 'rawPacket.window30m',
  session: 'rawPacket.windowSession',
};

const WINDOW_LOWER_KEYS: Record<RawPacketStatsWindow, string> = {
  '1m': 'rawPacket.window1mLower',
  '5m': 'rawPacket.window5mLower',
  '10m': 'rawPacket.window10mLower',
  '30m': 'rawPacket.window30mLower',
  session: 'rawPacket.windowSessionLower',
};

function formatTimestamp(timestampMs: number): string {
  return new Date(timestampMs).toLocaleString([], {
    month: 'short',
    day: 'numeric',
    hour: '2-digit',
    minute: '2-digit',
    second: '2-digit',
  });
}

function formatDuration(seconds: number): string {
  if (seconds < 60) {
    return i18n.t('rawPacket.durationSec', { count: Math.max(1, Math.round(seconds)) });
  }
  if (seconds < 3600) {
    const minutes = Math.floor(seconds / 60);
    const remainder = Math.round(seconds % 60);
    return remainder > 0 ? `${minutes}m ${remainder}s` : `${minutes}m`;
  }
  const hours = Math.floor(seconds / 3600);
  const minutes = Math.round((seconds % 3600) / 60);
  return minutes > 0 ? `${hours}h ${minutes}m` : `${hours}h`;
}

function formatRate(value: number): string {
  if (value >= 100) return value.toFixed(0);
  if (value >= 10) return value.toFixed(1);
  return value.toFixed(2);
}

function formatPercent(value: number): string {
  return `${Math.round(value * 100)}%`;
}

function formatRssi(value: number | null): string {
  return value === null ? '-' : `${Math.round(value)} dBm`;
}

function normalizeResolvableSourceKey(sourceKey: string): string {
  return sourceKey.startsWith('hash1:') ? sourceKey.slice(6) : sourceKey;
}

function resolveContact(sourceKey: string | null, contacts: Contact[]): Contact | null {
  if (!sourceKey || sourceKey.startsWith('name:')) {
    return null;
  }

  const normalizedSourceKey = normalizeResolvableSourceKey(sourceKey).toLowerCase();
  const matches = contacts.filter((contact) =>
    contact.public_key.toLowerCase().startsWith(normalizedSourceKey)
  );
  if (matches.length !== 1) {
    return null;
  }

  return matches[0];
}

function resolveContactLabel(sourceKey: string | null, contacts: Contact[]): string | null {
  const contact = resolveContact(sourceKey, contacts);
  if (!contact) {
    return null;
  }
  return getContactDisplayName(contact.name, contact.public_key, contact.last_advert);
}

function resolveNeighbor(item: NeighborStat, contacts: Contact[]): NeighborStat {
  return {
    ...item,
    label: resolveContactLabel(item.key, contacts) ?? item.label,
  };
}

function mergeResolvedNeighbors(items: NeighborStat[], contacts: Contact[]): NeighborStat[] {
  const merged = new Map<string, NeighborStat>();

  for (const item of items) {
    const contact = resolveContact(item.key, contacts);
    const canonicalKey = contact?.public_key ?? item.key;
    const resolvedLabel =
      contact != null
        ? getContactDisplayName(contact.name, contact.public_key, contact.last_advert)
        : item.label;
    const existing = merged.get(canonicalKey);

    if (!existing) {
      merged.set(canonicalKey, {
        ...item,
        key: canonicalKey,
        label: resolvedLabel,
      });
      continue;
    }

    existing.count += item.count;
    existing.lastSeen = Math.max(existing.lastSeen, item.lastSeen);
    existing.bestRssi =
      existing.bestRssi === null
        ? item.bestRssi
        : item.bestRssi === null
          ? existing.bestRssi
          : Math.max(existing.bestRssi, item.bestRssi);
    existing.label = resolvedLabel;
  }

  return Array.from(merged.values());
}

function isNeighborIdentityResolvable(item: NeighborStat, contacts: Contact[]): boolean {
  if (item.key.startsWith('name:')) {
    return true;
  }
  return resolveContact(item.key, contacts) !== null;
}

function formatStrongestNeighborDetail(
  stats: ReturnType<typeof buildRawPacketStatsSnapshot>,
  contacts: Contact[]
): string | undefined {
  const strongestNeighbor = stats.strongestNeighbors[0];
  if (!strongestNeighbor || strongestNeighbor.bestRssi === null) {
    return undefined;
  }

  const resolvedNeighbor = resolveNeighbor(strongestNeighbor, contacts);
  return i18n.t('rawPacket.bestHeard', { rssi: formatRssi(resolvedNeighbor.bestRssi) });
}

function getCoverageMessage(
  stats: ReturnType<typeof buildRawPacketStatsSnapshot>,
  session: RawPacketStatsSessionState
): { tone: 'default' | 'warning'; message: string } {
  if (session.trimmedObservationCount > 0 && stats.window === 'session') {
    return {
      tone: 'warning',
      message: i18n.t('rawPacket.coverageTrimmed', {
        count: session.totalObservedPackets.toLocaleString(),
      }),
    };
  }

  if (!stats.windowFullyCovered) {
    return {
      tone: 'warning',
      message: i18n.t('rawPacket.coveragePartial', {
        duration: formatDuration(stats.coverageSeconds),
      }),
    };
  }

  return {
    tone: 'default',
    message: i18n.t('rawPacket.coverageTracking', {
      count: session.observations.length.toLocaleString(),
    }),
  };
}

function StatTile({ label, value, detail }: { label: string; value: string; detail?: string }) {
  return (
    <div className="break-inside-avoid rounded-lg border border-border/70 bg-card/80 p-3">
      <div className="text-[0.625rem] uppercase tracking-wider font-medium text-muted-foreground">
        {label}
      </div>
      <div className="mt-1 text-xl font-semibold tabular-nums text-foreground">{value}</div>
      {detail ? <div className="mt-1 text-xs text-muted-foreground">{detail}</div> : null}
    </div>
  );
}

function RankedBars({
  title,
  items,
  emptyLabel,
  formatter,
  colorMap,
}: {
  title: string;
  items: RankedPacketStat[];
  emptyLabel: string;
  formatter?: (item: RankedPacketStat) => string;
  colorMap?: Map<string, string>;
}) {
  const data = items.map((item) => ({
    name: item.label,
    value: item.count,
    detail: formatter
      ? formatter(item)
      : `${item.count.toLocaleString()} · ${formatPercent(item.share)}`,
  }));

  return (
    <section className="mb-4 break-inside-avoid rounded-lg border border-border/70 bg-card/70 p-3">
      <h3 className="text-sm font-semibold text-foreground">{title}</h3>
      {items.length === 0 ? (
        <p className="mt-3 text-sm text-muted-foreground">{emptyLabel}</p>
      ) : (
        <div className="mt-2">
          <ResponsiveContainer width="100%" height={items.length * 28 + 8}>
            <BarChart
              data={data}
              layout="vertical"
              margin={{ top: 0, right: 4, bottom: 0, left: 0 }}
              barCategoryGap="20%"
            >
              <XAxis type="number" hide />
              <YAxis
                type="category"
                dataKey="name"
                tick={{ fontSize: 11, fill: 'hsl(var(--muted-foreground))' }}
                tickLine={false}
                axisLine={false}
                width={80}
              />
              <RechartsTooltip
                {...TOOLTIP_STYLE}
                cursor={{ fill: 'hsl(var(--muted))', opacity: 0.5 }}
                // eslint-disable-next-line @typescript-eslint/no-explicit-any
                formatter={(_v: any, _n: any, props: any) => [props.payload.detail, null]}
              />
              <Bar dataKey="value" radius={[0, 4, 4, 0]} maxBarSize={16}>
                {data.map((entry, i) => (
                  <Cell key={i} fill={colorForType(colorMap, entry.name)} />
                ))}
              </Bar>
            </BarChart>
          </ResponsiveContainer>
        </div>
      )}
    </section>
  );
}

function NeighborList({
  title,
  items,
  emptyLabel,
  mode,
  contacts,
}: {
  title: string;
  items: NeighborStat[];
  emptyLabel: string;
  mode: 'heard' | 'signal' | 'recent';
  contacts: Contact[];
}) {
  const mergedItems = mergeResolvedNeighbors(items, contacts);
  const sortedItems = [...mergedItems].sort((a, b) => {
    if (mode === 'heard') {
      return b.count - a.count || b.lastSeen - a.lastSeen || a.label.localeCompare(b.label);
    }
    if (mode === 'signal') {
      return (
        (b.bestRssi ?? Number.NEGATIVE_INFINITY) - (a.bestRssi ?? Number.NEGATIVE_INFINITY) ||
        b.count - a.count ||
        a.label.localeCompare(b.label)
      );
    }
    return b.lastSeen - a.lastSeen || b.count - a.count || a.label.localeCompare(b.label);
  });

  return (
    <section className="mb-4 break-inside-avoid rounded-lg border border-border/70 bg-card/70 p-3">
      <h3 className="text-sm font-semibold text-foreground">{title}</h3>
      {sortedItems.length === 0 ? (
        <p className="mt-3 text-sm text-muted-foreground">{emptyLabel}</p>
      ) : (
        <div className="mt-3 space-y-2">
          {sortedItems.map((item) => (
            <div
              key={item.key}
              className="flex items-center justify-between gap-3 rounded-md bg-background/70 px-2 py-1.5"
            >
              <div className="min-w-0">
                <div className="truncate text-sm text-foreground">{item.label}</div>
                <div className="text-xs text-muted-foreground">
                  {mode === 'heard'
                    ? i18n.t('rawPacket.packetsCount', { count: item.count.toLocaleString() })
                    : mode === 'signal'
                      ? i18n.t('rawPacket.bestRssi', { rssi: formatRssi(item.bestRssi) })
                      : i18n.t('rawPacket.lastSeen', {
                          time: new Date(item.lastSeen * 1000).toLocaleTimeString([], {
                            hour: '2-digit',
                            minute: '2-digit',
                          }),
                        })}
                </div>
                {!isNeighborIdentityResolvable(item, contacts) ? (
                  <div className="text-[0.6875rem] text-warning">
                    {i18n.t('rawPacket.identityUnresolved')}
                  </div>
                ) : null}
              </div>
              {mode !== 'signal' ? (
                <div className="shrink-0 text-xs tabular-nums text-muted-foreground">
                  {mode === 'recent' ? formatRssi(item.bestRssi) : formatRssi(item.bestRssi)}
                </div>
              ) : null}
            </div>
          ))}
        </div>
      )}
    </section>
  );
}

function TimelineChart({
  bins,
  colorMap,
}: {
  bins: PacketTimelineBin[];
  colorMap: Map<string, string>;
}) {
  const typeOrder = Array.from(new Set(bins.flatMap((bin) => Object.keys(bin.countsByType))));

  const data = bins.map((bin) => {
    const entry: Record<string, string | number> = { label: bin.label };
    for (const type of typeOrder) {
      entry[type] = bin.countsByType[type] ?? 0;
    }
    return entry;
  });

  return (
    <section className="mb-4 break-inside-avoid rounded-lg border border-border/70 bg-card/70 p-3">
      <div className="flex items-center justify-between gap-3">
        <h3 className="text-sm font-semibold text-foreground">{i18n.t('rawPacket.timeline')}</h3>
        <div className="flex flex-wrap justify-end gap-2 text-[0.6875rem] text-muted-foreground">
          {typeOrder.map((type) => (
            <span key={type} className="inline-flex items-center gap-1">
              <span
                className="h-2 w-2 rounded-full"
                style={{ backgroundColor: colorMap.get(type) ?? PAYLOAD_TYPE_COLORS.Unknown }}
              />
              <span>{labelPayloadType(type)}</span>
            </span>
          ))}
        </div>
      </div>
      <div className="mt-2">
        <ResponsiveContainer width="100%" height={110}>
          <BarChart data={data} margin={{ top: 4, right: 0, bottom: 0, left: -24 }}>
            <CartesianGrid strokeDasharray="3 3" stroke="hsl(var(--border))" vertical={false} />
            <XAxis
              dataKey="label"
              tick={{ fontSize: 10, fill: 'hsl(var(--muted-foreground))' }}
              tickLine={false}
              axisLine={false}
            />
            <YAxis
              tick={{ fontSize: 10, fill: 'hsl(var(--muted-foreground))' }}
              tickLine={false}
              axisLine={false}
              allowDecimals={false}
            />
            <RechartsTooltip
              {...TOOLTIP_STYLE}
              cursor={{ fill: 'hsl(var(--muted))', opacity: 0.5 }}
            />
            {typeOrder.map((type, i) => (
              <Bar
                key={type}
                dataKey={type}
                stackId="packets"
                fill={colorMap.get(type) ?? PAYLOAD_TYPE_COLORS.Unknown}
                radius={i === typeOrder.length - 1 ? [2, 2, 0, 0] : undefined}
              />
            ))}
          </BarChart>
        </ResponsiveContainer>
      </div>
    </section>
  );
}

export function RawPacketFeedView({
  onBackToTools,
  contacts,
  channels,
  radioOffline = false,
}: RawPacketFeedViewProps) {
  const { t } = useTranslation();
  const packets = useRawPackets();
  const rawPacketStatsSession = useRawPacketStatsSession();
  const [statsOpen, setStatsOpen] = useState(() =>
    typeof window !== 'undefined' && typeof window.matchMedia === 'function'
      ? window.matchMedia('(min-width: 768px)').matches
      : false
  );
  const [selectedWindow, setSelectedWindow] = useState<RawPacketStatsWindow>('10m');
  const [nowSec, setNowSec] = useState(() => Math.floor(Date.now() / 1000));
  const [selectedPacket, setSelectedPacket] = useState<RawPacket | null>(null);
  const [analyzeModalOpen, setAnalyzeModalOpen] = useState(false);
  const [mobileFiltersOpen, setMobileFiltersOpen] = useState(false);
  const [enabledTypes, setEnabledTypes] = useState<Set<string>>(() => new Set(KNOWN_PAYLOAD_TYPES));
  const [enabledRoutes, setEnabledRoutes] = useState<Set<string>>(
    () => new Set(ROUTE_FILTER_TYPES)
  );
  const [cryptoFilter, setCryptoFilter] = useState<CryptoFilter>('all');
  // Autoscroll defaults on; intentionally not persisted across refreshes.
  const [autoScroll, setAutoScroll] = useState(true);
  // Raw-hex substring filter over the in-memory feed buffer (session-only).
  const [hexFilter, setHexFilter] = useState('');
  const [textFilter, setTextFilter] = useState('');

  const decoderOptions = useMemo(() => createDecoderOptions(channels), [channels]);
  const inspectExtras = useMemo(
    () => ({
      channelKeys: collectGroupDataKeys(channels, []),
      extraSecrets: [] as string[],
    }),
    [channels]
  );

  const packetsWithTypes = useMemo(
    () =>
      packets.map((packet) => {
        const decoded = decodePacketSummary(packet, decoderOptions, inspectExtras);
        return {
          packet,
          payloadType: getPacketTypeName(packet, decoderOptions),
          routeType: decoded.routeType,
          summary: decoded.summary,
          clientDecoded: decoded.clientDecoded,
        };
      }),
    [packets, decoderOptions, inspectExtras]
  );

  const allTypesEnabled = enabledTypes.size === KNOWN_PAYLOAD_TYPES.length;
  const allRoutesEnabled = enabledRoutes.size === ROUTE_FILTER_TYPES.length;

  const { query: hexQuery, invalid: hexInvalid } = useMemo(
    () => normalizeHexQuery(hexFilter),
    [hexFilter]
  );
  const textQuery = textFilter.trim().toLowerCase();

  const filteredPackets = useMemo(() => {
    // A non-hex query matches nothing; the input surfaces a hint instead.
    if (hexInvalid) return [];
    const noTypeFilter = allTypesEnabled;
    const noRouteFilter = allRoutesEnabled;
    const noCryptoFilter = cryptoFilter === 'all';
    const noHexFilter = hexQuery === '';
    const noTextFilter = textQuery === '';
    if (noTypeFilter && noRouteFilter && noCryptoFilter && noHexFilter && noTextFilter) {
      return packets;
    }
    return packetsWithTypes
      .filter(({ packet, payloadType, routeType, summary, clientDecoded }) => {
        if (!noTypeFilter && !enabledTypes.has(payloadType)) return false;
        if (!noRouteFilter && !enabledRoutes.has(routeType)) return false;
        const isOpen = isPacketOpen(payloadType, packet, clientDecoded);
        if (cryptoFilter === 'decrypted' && !isOpen) return false;
        if (cryptoFilter === 'encrypted' && isOpen) return false;
        if (!noHexFilter && !packet.data.toLowerCase().includes(hexQuery)) return false;
        if (!noTextFilter) {
          const haystacks = [
            summary,
            packet.decrypted_info?.sender,
            packet.decrypted_info?.channel_name,
            packet.decrypted_info?.channel_key,
            packet.decrypted_info?.contact_key,
            packet.packet_hash,
          ];
          const matchesText = haystacks.some(
            (value) => value != null && value.toLowerCase().includes(textQuery)
          );
          if (!matchesText) return false;
        }
        return true;
      })
      .map(({ packet }) => packet);
  }, [
    packetsWithTypes,
    enabledTypes,
    enabledRoutes,
    packets,
    allTypesEnabled,
    allRoutesEnabled,
    cryptoFilter,
    hexQuery,
    hexInvalid,
    textQuery,
  ]);

  const handleToggleAll = () => {
    setEnabledTypes(allTypesEnabled ? new Set() : new Set(KNOWN_PAYLOAD_TYPES));
  };

  const handleToggleType = (type: string) => {
    setEnabledTypes((prev) => {
      const next = new Set(prev);
      if (next.has(type)) {
        next.delete(type);
      } else {
        next.add(type);
      }
      return next;
    });
  };

  const handleOnly = (type: string) => {
    setEnabledTypes(new Set([type]));
  };

  const handleToggleRoute = (route: RouteFilterType) => {
    setEnabledRoutes((prev) => {
      const next = new Set(prev);
      if (next.has(route)) {
        next.delete(route);
      } else {
        next.add(route);
      }
      return next;
    });
  };

  const handleRepeatFilter = (hash: string) => {
    setTextFilter(hash);
  };

  useEffect(() => {
    const interval = window.setInterval(() => {
      setNowSec(Math.floor(Date.now() / 1000));
    }, 30000);
    return () => window.clearInterval(interval);
  }, []);

  useEffect(() => {
    setNowSec(Math.floor(Date.now() / 1000));
  }, [packets, rawPacketStatsSession]);

  const stats = useMemo(
    () => buildRawPacketStatsSnapshot(rawPacketStatsSession, selectedWindow, nowSec),
    [nowSec, rawPacketStatsSession, selectedWindow]
  );
  const coverageMessage = getCoverageMessage(stats, rawPacketStatsSession);
  const strongestNeighbor = useMemo(() => {
    const topNeighbor = stats.strongestNeighbors[0];
    return topNeighbor ? resolveNeighbor(topNeighbor, contacts) : null;
  }, [contacts, stats]);

  const strongestNeighborDetail = useMemo(
    () => formatStrongestNeighborDetail(stats, contacts),
    [contacts, stats]
  );
  const strongestNeighbors = useMemo(
    () => stats.strongestNeighbors.map((item) => resolveNeighbor(item, contacts)),
    [contacts, stats.strongestNeighbors]
  );
  const mostActiveNeighbors = useMemo(
    () => stats.mostActiveNeighbors.map((item) => resolveNeighbor(item, contacts)),
    [contacts, stats.mostActiveNeighbors]
  );
  const newestNeighbors = useMemo(
    () => stats.newestNeighbors.map((item) => resolveNeighbor(item, contacts)),
    [contacts, stats.newestNeighbors]
  );
  return (
    <>
      <ToolPaneHeader
        title={t('rawPacket.title')}
        onBack={onBackToTools}
        subtitle={
          <p className="hidden text-xs md:block">
            {t('rawPacket.collectingSince', {
              time: formatTimestamp(rawPacketStatsSession.sessionStartedAt),
            })}
          </p>
        }
        /* Two full labels pushed this row past the right edge at 390px. The
           labels come back as soon as the header has room for them. */
        actions={
          <>
            <Button
              type="button"
              variant="outline"
              size="sm"
              onClick={() => setAnalyzeModalOpen(true)}
              aria-label={t('rawPacket.analyze')}
            >
              <Search className="h-4 w-4 sm:hidden" aria-hidden="true" />
              <span className="hidden sm:inline" aria-hidden="true">
                {t('rawPacket.analyze')}
              </span>
            </Button>
            <Button
              type="button"
              variant="outline"
              size="sm"
              onClick={() => setStatsOpen((current) => !current)}
              aria-expanded={statsOpen}
              aria-label={statsOpen ? t('rawPacket.hideStats') : t('rawPacket.showStats')}
            >
              {statsOpen ? (
                <ChevronRight className="h-4 w-4" aria-hidden="true" />
              ) : (
                <ChevronLeft className="h-4 w-4" aria-hidden="true" />
              )}
              <span className="hidden sm:inline" aria-hidden="true">
                {statsOpen ? t('rawPacket.hideStats') : t('rawPacket.showStats')}
              </span>
            </Button>
          </>
        }
      />

      <div className="shrink-0 bg-background px-4 pb-2 md:border-b md:border-border md:px-4 md:pb-2.5">
        <p className="md:hidden text-xs text-muted-foreground">
          {t('rawPacket.collectingSince', {
            time: formatTimestamp(rawPacketStatsSession.sessionStartedAt),
          })}
          {!mobileFiltersOpen && (
            <>
              {' · '}
              <button
                type="button"
                className="text-primary hover:text-primary/80 transition-colors"
                onClick={() => setMobileFiltersOpen(true)}
              >
                {t('rawPacket.showFilters')}
              </button>
            </>
          )}
        </p>

        {mobileFiltersOpen && (
          <FeedFilterControls
            className="flex md:hidden"
            allTypesEnabled={allTypesEnabled}
            enabledTypes={enabledTypes}
            onToggleAll={handleToggleAll}
            onToggleType={handleToggleType}
            onOnly={handleOnly}
            enabledRoutes={enabledRoutes}
            onToggleRoute={handleToggleRoute}
            cryptoFilter={cryptoFilter}
            onCryptoFilterChange={setCryptoFilter}
            autoScroll={autoScroll}
            onAutoScrollChange={setAutoScroll}
            hexFilter={hexFilter}
            onHexFilterChange={setHexFilter}
            hexInvalid={hexInvalid}
            textFilter={textFilter}
            onTextFilterChange={setTextFilter}
            matchCount={filteredPackets.length}
            totalCount={packets.length}
          />
        )}

        <FeedFilterControls
          className="hidden md:flex"
          allTypesEnabled={allTypesEnabled}
          enabledTypes={enabledTypes}
          onToggleAll={handleToggleAll}
          onToggleType={handleToggleType}
          onOnly={handleOnly}
          enabledRoutes={enabledRoutes}
          onToggleRoute={handleToggleRoute}
          cryptoFilter={cryptoFilter}
          onCryptoFilterChange={setCryptoFilter}
          autoScroll={autoScroll}
          onAutoScrollChange={setAutoScroll}
          hexFilter={hexFilter}
          onHexFilterChange={setHexFilter}
          hexInvalid={hexInvalid}
          textFilter={textFilter}
          onTextFilterChange={setTextFilter}
          matchCount={filteredPackets.length}
          totalCount={packets.length}
        />
      </div>

      <div className="flex min-h-0 flex-1 flex-col md:flex-row">
        <div className={cn('min-h-0 min-w-0 flex-1', statsOpen && 'md:border-r md:border-border')}>
          <RawPacketList
            packets={filteredPackets}
            channels={channels}
            extraSecrets={[]}
            onPacketClick={setSelectedPacket}
            onRepeatFilter={handleRepeatFilter}
            autoScroll={autoScroll}
            radioOffline={radioOffline}
          />
        </div>

        <aside
          className={cn(
            'shrink-0 overflow-hidden border-t border-border transition-all duration-300 md:border-l md:border-t-0',
            statsOpen
              ? 'max-h-[42rem] md:max-h-none md:w-1/2 md:min-w-[30rem]'
              : 'max-h-0 md:w-0 md:min-w-0 border-transparent'
          )}
        >
          {statsOpen ? (
            <div className="h-full overflow-y-auto bg-background p-4 [contain:layout_paint]">
              <div className="break-inside-avoid rounded-lg border border-border/70 bg-card/70 p-3">
                <div className="flex flex-wrap items-center justify-between gap-3">
                  <div>
                    <div className="text-[0.625rem] uppercase tracking-wider font-medium text-muted-foreground">
                      {t('rawPacket.coverage')}
                    </div>
                    <div
                      className={cn(
                        'mt-1 text-sm',
                        coverageMessage.tone === 'warning'
                          ? 'text-warning'
                          : 'text-muted-foreground'
                      )}
                    >
                      {coverageMessage.message}
                    </div>
                  </div>
                  <label className="flex items-center gap-2 text-sm text-foreground">
                    <span className="text-muted-foreground">{t('rawPacket.window')}</span>
                    <select
                      value={selectedWindow}
                      onChange={(event) =>
                        setSelectedWindow(event.target.value as RawPacketStatsWindow)
                      }
                      className="rounded-md border border-input bg-background px-2 py-1 text-sm"
                      aria-label={t('rawPacket.windowAria')}
                    >
                      {RAW_PACKET_STATS_WINDOWS.map((option) => (
                        <option key={option} value={option}>
                          {t(WINDOW_LABEL_KEYS[option])}
                        </option>
                      ))}
                    </select>
                  </label>
                </div>
                <div className="mt-2 text-xs text-muted-foreground">
                  {t('rawPacket.packetsInWindow', {
                    count: stats.packetCount.toLocaleString(),
                    window: t(WINDOW_LOWER_KEYS[selectedWindow]),
                    observed: rawPacketStatsSession.totalObservedPackets.toLocaleString(),
                  })}
                </div>
              </div>

              <div className="mt-4 grid grid-cols-2 gap-3 md:grid-cols-3">
                <StatTile
                  label={t('rawPacket.packetsPerMin')}
                  value={formatRate(stats.packetsPerMinute)}
                  detail={t('rawPacket.packetsTotal', {
                    count: stats.packetCount.toLocaleString(),
                  })}
                />
                <StatTile
                  label={t('rawPacket.uniqueSources')}
                  value={stats.uniqueSources.toLocaleString()}
                  detail={t('rawPacket.uniqueSourcesDetail')}
                />
                <StatTile
                  label={t('rawPacket.decryptRate')}
                  value={formatPercent(stats.decryptRate)}
                  detail={t('rawPacket.decryptRateDetail', {
                    decrypted: stats.decryptedCount.toLocaleString(),
                    locked: stats.undecryptedCount.toLocaleString(),
                  })}
                />
                <StatTile
                  label={t('rawPacket.pathDiversity')}
                  value={stats.distinctPaths.toLocaleString()}
                  detail={t('rawPacket.pathDiversityDetail', {
                    rate: formatPercent(stats.pathBearingRate),
                  })}
                />
                <StatTile
                  label={t('rawPacket.strongestNeighbor')}
                  value={strongestNeighbor?.label ?? '-'}
                  detail={strongestNeighborDetail ?? t('rawPacket.noNeighborRssi')}
                />
                <StatTile
                  label={t('rawPacket.medianRssi')}
                  value={formatRssi(stats.medianRssi)}
                  detail={
                    stats.averageRssi === null
                      ? t('rawPacket.noSignalSample')
                      : t('rawPacket.averageRssi', { rssi: formatRssi(stats.averageRssi) })
                  }
                />
              </div>

              <div className="mt-4">
                <TimelineChart bins={stats.timeline} colorMap={PAYLOAD_TYPE_COLOR_MAP} />
              </div>

              <div className="md:columns-2 md:gap-4">
                <RankedBars
                  title={t('rawPacket.packetTypes')}
                  items={stats.payloadBreakdown.map((item) => ({
                    ...item,
                    label: labelPayloadType(item.label),
                  }))}
                  emptyLabel={t('rawPacket.emptyPackets')}
                  colorMap={labeledPayloadColorMap()}
                />

                <RankedBars
                  title={t('rawPacket.routeMix')}
                  items={stats.routeBreakdown.map((item) => ({
                    ...item,
                    label: labelRoute(item.label),
                  }))}
                  emptyLabel={t('rawPacket.emptyPackets')}
                />

                <RankedBars
                  title={t('rawPacket.hopProfile')}
                  items={stats.hopProfile}
                  emptyLabel={t('rawPacket.emptyPackets')}
                />

                <RankedBars
                  title={t('rawPacket.hopByteWidth')}
                  items={stats.hopByteWidthProfile}
                  emptyLabel={t('rawPacket.emptyPackets')}
                />

                <RankedBars
                  title={t('rawPacket.signalDistribution')}
                  items={stats.rssiBuckets}
                  emptyLabel={t('rawPacket.emptyRssi')}
                />

                <NeighborList
                  title={t('rawPacket.mostHeard')}
                  items={mostActiveNeighbors}
                  emptyLabel={t('rawPacket.emptySenders')}
                  mode="heard"
                  contacts={contacts}
                />

                <NeighborList
                  title={t('rawPacket.strongestRecent')}
                  items={strongestNeighbors}
                  emptyLabel={t('rawPacket.emptyRssiNeighbors')}
                  mode="signal"
                  contacts={contacts}
                />

                <NeighborList
                  title={t('rawPacket.newestHeard')}
                  items={newestNeighbors}
                  emptyLabel={t('rawPacket.emptyNewNeighbors')}
                  mode="recent"
                  contacts={contacts}
                />
              </div>
            </div>
          ) : null}
        </aside>
      </div>

      <RawPacketInspectorDialog
        open={selectedPacket !== null}
        onOpenChange={(isOpen) => !isOpen && setSelectedPacket(null)}
        channels={channels}
        source={
          selectedPacket
            ? { kind: 'packet', packet: selectedPacket }
            : { kind: 'loading', message: t('rawPacket.loading') }
        }
        title={t('rawPacket.details')}
        description={t('rawPacket.detailsDescription')}
      />

      <RawPacketInspectorDialog
        open={analyzeModalOpen}
        onOpenChange={setAnalyzeModalOpen}
        channels={channels}
        source={{ kind: 'paste' }}
        title={t('rawPacket.analyze')}
        description={t('rawPacket.analyzeDescription')}
      />
    </>
  );
}
