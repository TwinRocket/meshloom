import { useEffect, useMemo, useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { ChevronDown, ChevronLeft, ChevronRight, History, Search, X } from 'lucide-react';
import { MeshCoreDecoder } from '@michaelhart/meshcore-decoder';
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
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from './ui/dialog';
import { toast } from './ui/sonner';
import { api, isAbortError } from '../api';
import type {
  Channel,
  Contact,
  Conversation,
  RawPacket,
  RawPacketHistoryQuery,
  RawPacketHistoryResponse,
} from '../types';
import {
  KNOWN_PAYLOAD_TYPES,
  PAYLOAD_TYPE_COLORS,
  RAW_PACKET_STATS_WINDOWS,
  buildPayloadTypeColorMap,
  buildRawPacketStatsSnapshot,
  type NeighborStat,
  type PacketTimelineBin,
  type RankedPacketStat,
  type RawPacketStatsSessionState,
  type RawPacketStatsWindow,
} from '../utils/rawPacketStats';
import {
  getRawPacketDerivedCacheKey,
  useRawPacketDerivedCache,
} from '../utils/rawPacketDerivedCache';
import { getRawPacketObservationKey } from '../utils/rawPacketIdentity';
import { labelPayloadType, labelRoute } from '../utils/rawPacketLabels';
import {
  clearReplayPackets,
  loadReplayPackets,
  useReplayActive,
  useReplayPackets,
} from '../stores/rawPacketReplayStore';
import { useRawPacketStatsSession, useRawPackets } from '../stores/rawPacketStore';
import { setVisualizerFocusHandoff } from '../utils/visualizerFocusHandoff';
import { getContactDisplayName } from '../utils/pubkey';
import { cn } from '@/lib/utils';
import { ToolPaneHeader } from './ToolPaneHeader';
import i18n from '../i18n';

const DISPLAY_CAPS = [200, 500, 2000, 5000] as const;
type DisplayCap = (typeof DISPLAY_CAPS)[number];
const DISPLAY_CAP_STORAGE_KEY = 'meshloom-raw-display-cap';
const DEFAULT_DISPLAY_CAP: DisplayCap = 500;

type RssiFilter = 'all' | 'strong' | 'okay';

function readStoredDisplayCap(): DisplayCap {
  try {
    const raw = window.localStorage.getItem(DISPLAY_CAP_STORAGE_KEY);
    const parsed = Number(raw);
    return DISPLAY_CAPS.includes(parsed as DisplayCap)
      ? (parsed as DisplayCap)
      : DEFAULT_DISPLAY_CAP;
  } catch {
    return DEFAULT_DISPLAY_CAP;
  }
}

function writeStoredDisplayCap(cap: DisplayCap): void {
  try {
    window.localStorage.setItem(DISPLAY_CAP_STORAGE_KEY, String(cap));
  } catch {
    // Persistence is optional when storage is unavailable.
  }
}

function downloadTextFile(filename: string, contents: string, mime: string): void {
  const blob = new Blob([contents], { type: mime });
  const url = URL.createObjectURL(blob);
  const link = document.createElement('a');
  link.href = url;
  link.download = filename;
  link.click();
  URL.revokeObjectURL(url);
}

interface ExportPacketRow {
  hex: string;
  ts: number;
  rssi: number | null;
  snr: number | null;
  type: string;
  route: string;
  hash: string | null;
}

function toExportRow(
  packet: RawPacket,
  derived: { payloadType: string; routeType: string }
): ExportPacketRow {
  return {
    hex: packet.data,
    ts: packet.timestamp,
    rssi: packet.rssi,
    snr: packet.snr,
    type: derived.payloadType,
    route: derived.routeType,
    hash: packet.packet_hash ?? null,
  };
}

function isZeroHopPacket(packet: RawPacket): boolean {
  try {
    const decoded = MeshCoreDecoder.decode(packet.data);
    if (!decoded.isValid) return false;
    const path = decoded.path ?? [];
    return (decoded.pathLength ?? path.length) === 0;
  } catch {
    return false;
  }
}

function toCopyJson(
  packet: RawPacket,
  derived: { payloadType: string; routeType: string },
  includeCleartext: boolean
): Record<string, unknown> {
  const redacted: Record<string, unknown> = {
    id: packet.id,
    observation_id: packet.observation_id ?? null,
    ts: packet.timestamp,
    type: derived.payloadType,
    route: derived.routeType,
    rssi: packet.rssi,
    snr: packet.snr,
    data: packet.data,
  };
  if (!includeCleartext) {
    return redacted;
  }
  return {
    ...redacted,
    decrypted_info: packet.decrypted_info,
  };
}

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
  paused: boolean;
  onPausedChange: (checked: boolean) => void;
  heldCount: number;
  displayCap: DisplayCap;
  onDisplayCapChange: (cap: DisplayCap) => void;
  rssiFilter: RssiFilter;
  onRssiFilterChange: (value: RssiFilter) => void;
  zeroHopOnly: boolean;
  onZeroHopOnlyChange: (checked: boolean) => void;
  onExportJson: () => void;
  onExportJsonl: () => void;
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
  paused,
  onPausedChange,
  heldCount,
  displayCap,
  onDisplayCapChange,
  rssiFilter,
  onRssiFilterChange,
  zeroHopOnly,
  onZeroHopOnlyChange,
  onExportJson,
  onExportJsonl,
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
      <label className="flex items-center gap-1 text-xs text-foreground cursor-pointer">
        <input
          type="checkbox"
          checked={paused}
          onChange={(event) => onPausedChange(event.target.checked)}
          aria-label={paused ? t('rawPacket.resumeAria') : t('rawPacket.pauseAria')}
          className="rounded"
        />
        {t('rawPacket.pause')}
        {heldCount > 0 ? (
          <span
            className="rounded-full bg-primary/15 px-1.5 py-0.5 text-[0.625rem] tabular-nums text-primary"
            aria-label={t('rawPacket.heldAria', { count: heldCount })}
          >
            {t('rawPacket.heldBadge', { count: heldCount })}
          </span>
        ) : null}
      </label>
      <DisplayCapPill value={displayCap} onChange={onDisplayCapChange} />
      <label className="flex items-center gap-1 text-xs text-foreground">
        <span className="text-muted-foreground">{t('rawPacket.rssiFilter')}</span>
        <select
          value={rssiFilter}
          onChange={(event) => onRssiFilterChange(event.target.value as RssiFilter)}
          aria-label={t('rawPacket.rssiAria')}
          className="rounded border border-input bg-background px-1.5 py-0.5 text-xs"
        >
          <option value="all">{t('rawPacket.rssiAll')}</option>
          <option value="strong">{t('rawPacket.rssiStrong')}</option>
          <option value="okay">{t('rawPacket.rssiOkay')}</option>
        </select>
      </label>
      <label className="flex items-center gap-1 text-xs text-foreground cursor-pointer">
        <input
          type="checkbox"
          checked={zeroHopOnly}
          onChange={(event) => onZeroHopOnlyChange(event.target.checked)}
          aria-label={t('rawPacket.zeroHopAria')}
          className="rounded"
        />
        {t('rawPacket.zeroHop')}
      </label>
      <div className="flex items-center gap-1">
        <button
          type="button"
          className="text-xs text-muted-foreground hover:text-foreground"
          aria-label={t('rawPacket.exportAria')}
          onClick={onExportJson}
        >
          {t('rawPacket.exportJson')}
        </button>
        <span className="text-muted-foreground">·</span>
        <button
          type="button"
          className="text-xs text-muted-foreground hover:text-foreground"
          onClick={onExportJsonl}
        >
          {t('rawPacket.exportJsonl')}
        </button>
      </div>
    </div>
  );
}

function DisplayCapPill({
  value,
  onChange,
}: {
  value: DisplayCap;
  onChange: (cap: DisplayCap) => void;
}) {
  const { t } = useTranslation();
  const [open, setOpen] = useState(false);
  const rootRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!open) return;
    const onPointerDown = (event: MouseEvent) => {
      if (!rootRef.current?.contains(event.target as Node)) {
        setOpen(false);
      }
    };
    document.addEventListener('mousedown', onPointerDown);
    return () => document.removeEventListener('mousedown', onPointerDown);
  }, [open]);

  return (
    <div className="relative" ref={rootRef}>
      <button
        type="button"
        className="inline-flex items-center gap-0.5 rounded-full border border-border bg-muted/60 px-2 py-0.5 text-xs tabular-nums text-foreground"
        aria-label={t('rawPacket.displayCapAria')}
        aria-expanded={open}
        onClick={() => setOpen((current) => !current)}
      >
        {t('rawPacket.displayCap', { count: value })}
        <ChevronDown className="h-3 w-3" aria-hidden="true" />
      </button>
      {open ? (
        <div className="absolute right-0 z-20 mt-1 min-w-[8rem] rounded-md border border-border bg-popover p-1 text-popover-foreground shadow-md">
          {DISPLAY_CAPS.map((cap) => (
            <button
              key={cap}
              type="button"
              className={cn(
                'block w-full rounded px-2 py-1 text-left text-xs hover:bg-accent',
                cap === value && 'bg-accent'
              )}
              onClick={() => {
                onChange(cap);
                setOpen(false);
              }}
            >
              {t('rawPacket.displayCapOption', { count: cap })}
            </button>
          ))}
        </div>
      ) : null}
    </div>
  );
}

const HISTORY_PREVIEW_LIMIT = 1;
const HISTORY_API_MAX_LIMIT = 5000;
const HISTORY_PERIODS = ['1h', '24h', '7d', 'custom'] as const;
type HistoryPeriod = (typeof HISTORY_PERIODS)[number];
const HISTORY_PERIOD_SECONDS: Record<Exclude<HistoryPeriod, 'custom'>, number> = {
  '1h': 3600,
  '24h': 86_400,
  '7d': 7 * 86_400,
};

function toUnixSeconds(localValue: string): number | undefined {
  if (!localValue) return undefined;
  const ms = new Date(localValue).getTime();
  if (Number.isNaN(ms)) return undefined;
  return Math.floor(ms / 1000);
}

function resolveHistoryBounds(
  period: HistoryPeriod,
  customSince: string,
  customUntil: string
): { since?: number; until?: number; invalid: boolean } {
  if (period === 'custom') {
    const since = toUnixSeconds(customSince);
    const until = toUnixSeconds(customUntil);
    return {
      since,
      until,
      invalid: since !== undefined && until !== undefined && since > until,
    };
  }
  const now = Math.floor(Date.now() / 1000);
  return { since: now - HISTORY_PERIOD_SECONDS[period], until: now, invalid: false };
}

function matchesHistorySourceFilter(packet: RawPacket, raw: string): boolean {
  const trimmed = raw.trim();
  if (trimmed === '') return true;
  const { query: hexQuery, invalid: hexInvalid } = normalizeHexQuery(trimmed);
  if (!hexInvalid && hexQuery !== '' && packet.data.toLowerCase().includes(hexQuery)) {
    return true;
  }
  const textQuery = trimmed.toLowerCase();
  const haystacks = [
    packet.decrypted_info?.sender,
    packet.decrypted_info?.channel_name,
    packet.decrypted_info?.channel_key,
    packet.decrypted_info?.contact_key,
    packet.packet_hash,
  ];
  return haystacks.some((value) => value != null && value.toLowerCase().includes(textQuery));
}

async function fetchHistoryPage(
  query: RawPacketHistoryQuery,
  signal?: AbortSignal
): Promise<RawPacketHistoryResponse> {
  return api.getPacketsHistory(query, signal);
}

/** Server PayloadType has no Unknown — never send payload_type=Unknown (422). */
const HISTORY_API_PAYLOAD_TYPES = KNOWN_PAYLOAD_TYPES.filter((type) => type !== 'Unknown');
const HISTORY_API_PAYLOAD_TYPE_SET = new Set<string>(HISTORY_API_PAYLOAD_TYPES);

function canonicalHistoryPayloadType(name: string): string {
  const compact = name.replace(/[_-\s]/g, '').toLowerCase();
  for (const known of KNOWN_PAYLOAD_TYPES) {
    if (known.replace(/[_-\s]/g, '').toLowerCase() === compact) {
      return known;
    }
  }
  return 'Unknown';
}

function packetMatchesHistoryTypes(packet: RawPacket, types: Set<string>): boolean {
  return types.has(canonicalHistoryPayloadType(packet.payload_type));
}

function historyApiTypes(types: Set<string>): string[] {
  return [...types].filter((type) => HISTORY_API_PAYLOAD_TYPE_SET.has(type));
}

async function loadHistoryPackets(
  types: Set<string>,
  bounds: { since?: number; until?: number },
  limit: number,
  signal?: AbortSignal
): Promise<RawPacket[]> {
  const capped = Math.min(Math.max(limit, 1), HISTORY_API_MAX_LIMIT);
  const allTypes = types.size === KNOWN_PAYLOAD_TYPES.length;
  const apiTypes = historyApiTypes(types);
  const wantsUnknown = types.has('Unknown');

  if (allTypes || types.size === 0) {
    const page = await fetchHistoryPage(
      { since: bounds.since, until: bounds.until, limit: capped },
      signal
    );
    return page.items;
  }

  // Unknown is not a server enum. Scan the time window, then keep selected types
  // (including unparseable / Unknown rows) on the client.
  if (wantsUnknown) {
    const page = await fetchHistoryPage(
      { since: bounds.since, until: bounds.until, limit: capped },
      signal
    );
    return page.items
      .filter((packet) => packetMatchesHistoryTypes(packet, types))
      .slice(0, capped);
  }

  const pages = await Promise.all(
    apiTypes.map((payloadType) =>
      fetchHistoryPage(
        {
          payload_type: payloadType,
          since: bounds.since,
          until: bounds.until,
          limit: capped,
        },
        signal
      )
    )
  );
  const byId = new Map<number, RawPacket>();
  for (const page of pages) {
    for (const item of page.items) {
      byId.set(item.id, item);
    }
  }
  return [...byId.values()]
    .sort((a, b) => b.timestamp - a.timestamp || b.id - a.id)
    .slice(0, capped);
}

interface HistoryReplayModalProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  displayCap: DisplayCap;
  onLoaded: () => void;
}

function HistoryReplayModal({ open, onOpenChange, displayCap, onLoaded }: HistoryReplayModalProps) {
  const { t } = useTranslation();
  const [enabledTypes, setEnabledTypes] = useState<Set<string>>(
    () => new Set(KNOWN_PAYLOAD_TYPES)
  );
  const [period, setPeriod] = useState<HistoryPeriod>('24h');
  const [customSince, setCustomSince] = useState('');
  const [customUntil, setCustomUntil] = useState('');
  const [sourceFilter, setSourceFilter] = useState('');
  const [preview, setPreview] = useState<RawPacketHistoryResponse | null>(null);
  const [previewLoading, setPreviewLoading] = useState(false);
  const [previewError, setPreviewError] = useState(false);
  const [loading, setLoading] = useState(false);
  const loadAbortRef = useRef<AbortController | null>(null);

  const bounds = useMemo(
    () => resolveHistoryBounds(period, customSince, customUntil),
    [customSince, customUntil, period]
  );
  const allTypesEnabled = enabledTypes.size === KNOWN_PAYLOAD_TYPES.length;

  useEffect(() => {
    if (!open || bounds.invalid) {
      return;
    }
    const controller = new AbortController();
    setPreviewLoading(true);
    setPreviewError(false);
    void fetchHistoryPage(
      {
        since: bounds.since,
        until: bounds.until,
        limit: HISTORY_PREVIEW_LIMIT,
      },
      controller.signal
    )
      .then((page) => {
        if (controller.signal.aborted) return;
        setPreview(page);
        setPreviewLoading(false);
      })
      .catch((error: unknown) => {
        if (controller.signal.aborted) return;
        if (error instanceof DOMException && error.name === 'AbortError') return;
        if (error instanceof Error && error.name === 'AbortError') return;
        setPreview(null);
        setPreviewError(true);
        setPreviewLoading(false);
      });
    return () => controller.abort();
  }, [bounds.invalid, bounds.since, bounds.until, open]);

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

  const handleOpenChange = (next: boolean) => {
    if (!next) {
      loadAbortRef.current?.abort();
      loadAbortRef.current = null;
    }
    onOpenChange(next);
  };

  const handleConfirm = async () => {
    if (bounds.invalid || enabledTypes.size === 0 || loading) {
      return;
    }
    loadAbortRef.current?.abort();
    const controller = new AbortController();
    loadAbortRef.current = controller;
    setLoading(true);
    try {
      const items = await loadHistoryPackets(enabledTypes, bounds, displayCap, controller.signal);
      if (controller.signal.aborted) {
        return;
      }
      const filtered = items.filter((packet) => matchesHistorySourceFilter(packet, sourceFilter));
      loadReplayPackets(filtered);
      onLoaded();
      handleOpenChange(false);
    } catch (error) {
      if (controller.signal.aborted || isAbortError(error)) {
        return;
      }
      toast.error(t('rawPacket.historyLoadError'));
    } finally {
      if (loadAbortRef.current === controller) {
        loadAbortRef.current = null;
      }
      setLoading(false);
    }
  };

  return (
    <Dialog open={open} onOpenChange={handleOpenChange}>
      <DialogContent className="max-w-xl">
        <DialogHeader>
          <DialogTitle>{t('rawPacket.historyTitle')}</DialogTitle>
          <DialogDescription>{t('rawPacket.historyDescription')}</DialogDescription>
        </DialogHeader>

        <div className="space-y-4 text-sm">
          <fieldset className="space-y-2">
            <legend className="text-[0.625rem] uppercase tracking-wider font-medium text-muted-foreground">
              {t('rawPacket.historyTypes')}
            </legend>
            <div className="flex flex-wrap items-center gap-x-3 gap-y-1">
              <label className="flex items-center gap-1 text-xs text-foreground cursor-pointer">
                <input
                  type="checkbox"
                  checked={allTypesEnabled}
                  onChange={handleToggleAll}
                  className="rounded"
                />
                {t('rawPacket.all')}
              </label>
              {KNOWN_PAYLOAD_TYPES.map((type) => (
                <label
                  key={type}
                  className="flex items-center gap-1 text-xs text-foreground cursor-pointer"
                >
                  <input
                    type="checkbox"
                    checked={enabledTypes.has(type)}
                    onChange={() => handleToggleType(type)}
                    className="rounded"
                  />
                  {labelPayloadType(type)}
                </label>
              ))}
            </div>
          </fieldset>

          <fieldset className="space-y-2">
            <legend className="text-[0.625rem] uppercase tracking-wider font-medium text-muted-foreground">
              {t('rawPacket.historyPeriod')}
            </legend>
            <div className="flex flex-wrap gap-3">
              {HISTORY_PERIODS.map((option) => (
                <label
                  key={option}
                  className="flex items-center gap-1 text-xs text-foreground cursor-pointer"
                >
                  <input
                    type="radio"
                    name="raw-history-period"
                    checked={period === option}
                    onChange={() => setPeriod(option)}
                  />
                  {t(
                    option === '1h'
                      ? 'rawPacket.historyPeriod1h'
                      : option === '24h'
                        ? 'rawPacket.historyPeriod24h'
                        : option === '7d'
                          ? 'rawPacket.historyPeriod7d'
                          : 'rawPacket.historyPeriodCustom'
                  )}
                </label>
              ))}
            </div>
            {period === 'custom' ? (
              <div className="flex flex-wrap gap-3">
                <label className="space-y-1 text-xs text-muted-foreground">
                  <span>{t('rawPacket.historySince')}</span>
                  <input
                    type="datetime-local"
                    value={customSince}
                    onChange={(event) => setCustomSince(event.target.value)}
                    className="block h-8 rounded-md border border-input bg-background px-2 text-sm text-foreground"
                  />
                </label>
                <label className="space-y-1 text-xs text-muted-foreground">
                  <span>{t('rawPacket.historyUntil')}</span>
                  <input
                    type="datetime-local"
                    value={customUntil}
                    onChange={(event) => setCustomUntil(event.target.value)}
                    className="block h-8 rounded-md border border-input bg-background px-2 text-sm text-foreground"
                  />
                </label>
              </div>
            ) : null}
            {bounds.invalid ? (
              <p className="text-xs text-warning">{t('rawPacket.historyInvalidRange')}</p>
            ) : null}
          </fieldset>

          <label className="block space-y-1">
            <span className="text-[0.625rem] uppercase tracking-wider font-medium text-muted-foreground">
              {t('rawPacket.historySource')}
            </span>
            <input
              type="text"
              value={sourceFilter}
              onChange={(event) => setSourceFilter(event.target.value)}
              placeholder={t('rawPacket.historySourcePlaceholder')}
              aria-label={t('rawPacket.historySourceAria')}
              className="w-full rounded-md border border-input bg-background px-2 py-1.5 text-sm"
            />
            <span className="block text-[0.6875rem] text-muted-foreground">
              {t('rawPacket.historySourceHint')}
            </span>
          </label>

          <p className="text-[0.6875rem] text-muted-foreground">{t('rawPacket.historyPruneNote')}</p>

          <div className="rounded-md border border-border/70 bg-card/70 p-3 text-xs">
            {previewLoading ? (
              <p className="text-muted-foreground">{t('rawPacket.historyPreviewLoading')}</p>
            ) : previewError ? (
              <p className="text-warning">{t('rawPacket.historyPreviewError')}</p>
            ) : preview ? (
              <>
                <p>
                  {t('rawPacket.historyPreview', {
                    count: preview.total.toLocaleString(),
                  })}
                </p>
                {preview.truncated ? (
                  <p className="mt-1 text-warning">
                    {t('rawPacket.historyPreviewTruncated', {
                      scanned: preview.scanned.toLocaleString(),
                    })}
                  </p>
                ) : null}
                {preview.total > displayCap ? (
                  <p className="mt-1 text-warning">
                    {t('rawPacket.historyCapWarning', {
                      count: preview.total.toLocaleString(),
                      cap: displayCap,
                    })}
                  </p>
                ) : null}
              </>
            ) : null}
          </div>
        </div>

        <DialogFooter>
          <Button type="button" variant="outline" onClick={() => handleOpenChange(false)}>
            {t('rawPacket.historyCancel')}
          </Button>
          <Button
            type="button"
            onClick={() => {
              void handleConfirm();
            }}
            disabled={loading || bounds.invalid || enabledTypes.size === 0}
          >
            {t('rawPacket.historyConfirm')}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

interface RawPacketFeedViewProps {
  /** Leaves this sub-screen for the Tools screen. Phones only. */
  onBackToTools?: () => void;
  contacts: Contact[];
  channels: Channel[];
  radioOffline?: boolean;
  onOpenContactInfo?: (publicKey: string) => void;
  onSelectConversation?: (conversation: Conversation) => void;
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
  onOpenContactInfo,
  onSelectConversation,
}: RawPacketFeedViewProps) {
  const { t } = useTranslation();
  const livePackets = useRawPackets();
  const replayActive = useReplayActive();
  const replayPackets = useReplayPackets();
  const rawPacketStatsSession = useRawPacketStatsSession();
  const [historyModalOpen, setHistoryModalOpen] = useState(false);
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
  // Pause is local to #raw. The global store keeps recording.
  const [paused, setPaused] = useState(false);
  const [snapshotPackets, setSnapshotPackets] = useState<RawPacket[] | null>(null);
  const [heldPackets, setHeldPackets] = useState<RawPacket[]>([]);
  const [displayCap, setDisplayCap] = useState<DisplayCap>(readStoredDisplayCap);
  const [rssiFilter, setRssiFilter] = useState<RssiFilter>('all');
  const [zeroHopOnly, setZeroHopOnly] = useState(false);
  const [capMenuHint, setCapMenuHint] = useState(false);
  // Raw-hex substring filter over the in-memory feed buffer (session-only).
  const [hexFilter, setHexFilter] = useState('');
  const [textFilter, setTextFilter] = useState('');

  const liveDisplaySource = paused && snapshotPackets ? snapshotPackets : livePackets;
  const displaySource = replayActive ? replayPackets : liveDisplaySource;

  useEffect(() => {
    if (!paused || !snapshotPackets) {
      setHeldPackets([]);
      return;
    }
    const snapshotKeys = new Set(
      snapshotPackets.map((packet) => getRawPacketObservationKey(packet))
    );
    setHeldPackets(
      livePackets.filter((packet) => !snapshotKeys.has(getRawPacketObservationKey(packet)))
    );
  }, [livePackets, paused, snapshotPackets]);

  const derivedScope = replayActive ? 'replay' : 'live';
  const derivedEntries = useRawPacketDerivedCache(displaySource, {
    channels,
    communityNames: [],
    scope: derivedScope,
  });

  const allTypesEnabled = enabledTypes.size === KNOWN_PAYLOAD_TYPES.length;
  const allRoutesEnabled = enabledRoutes.size === ROUTE_FILTER_TYPES.length;

  const { query: hexQuery, invalid: hexInvalid } = useMemo(
    () => normalizeHexQuery(hexFilter),
    [hexFilter]
  );
  const textQuery = textFilter.trim().toLowerCase();

  const filteredEntries = useMemo(() => {
    if (hexInvalid) return [];
    const noTypeFilter = allTypesEnabled;
    const noRouteFilter = allRoutesEnabled;
    const noHexFilter = hexQuery === '';
    const noTextFilter = textQuery === '';
    const noRssiFilter = rssiFilter === 'all';
    const noHopFilter = !zeroHopOnly;
    return derivedEntries.filter(({ packet, payloadType, routeType, summary, isOpen }) => {
      if (!noTypeFilter && !enabledTypes.has(payloadType)) return false;
      if (!noRouteFilter && !enabledRoutes.has(routeType)) return false;
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
      if (!noRssiFilter) {
        if (packet.rssi == null) return false;
        if (rssiFilter === 'strong' && packet.rssi <= -70) return false;
        if (rssiFilter === 'okay' && packet.rssi <= -85) return false;
      }
      if (!noHopFilter && !isZeroHopPacket(packet)) return false;
      return true;
    });
  }, [
    derivedEntries,
    enabledTypes,
    enabledRoutes,
    allTypesEnabled,
    allRoutesEnabled,
    cryptoFilter,
    hexQuery,
    hexInvalid,
    textQuery,
    rssiFilter,
    zeroHopOnly,
  ]);

  const visibleEntries = useMemo(
    () =>
      filteredEntries.length > displayCap ? filteredEntries.slice(-displayCap) : filteredEntries,
    [displayCap, filteredEntries]
  );

  const visiblePackets = useMemo(
    () => visibleEntries.map((entry) => entry.packet),
    [visibleEntries]
  );

  const derivedByObservation = useMemo(() => {
    const map = new Map<string, (typeof derivedEntries)[number]>();
    for (const entry of derivedEntries) {
      map.set(entry.cacheKey, entry);
    }
    return map;
  }, [derivedEntries]);

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

  const handlePausedChange = (nextPaused: boolean) => {
    if (nextPaused) {
      setSnapshotPackets([...livePackets]);
      setHeldPackets([]);
      setPaused(true);
      return;
    }
    if (heldPackets.length > displayCap) {
      toast.warning(t('rawPacket.pauseOverflow', { count: heldPackets.length, cap: displayCap }));
    }
    setPaused(false);
    setSnapshotPackets(null);
    setHeldPackets([]);
  };

  const handleDisplayCapChange = (cap: DisplayCap) => {
    setDisplayCap(cap);
    writeStoredDisplayCap(cap);
    setCapMenuHint(cap > 500);
  };

  const handleOpenVisualizer = (packet: RawPacket) => {
    const observationKey = getRawPacketObservationKey(packet);
    setVisualizerFocusHandoff({
      observationKey,
      ...(packet.packet_hash ? { packetHash: packet.packet_hash } : {}),
    });
    const visualizer: Conversation = {
      type: 'visualizer',
      id: 'visualizer',
      name: 'visualizer',
    };
    onSelectConversation?.(visualizer);
  };

  const handlePinAdvert = (publicKey: string) => {
    onSelectConversation?.({
      type: 'map',
      id: 'map',
      name: 'map',
      mapFocusKey: publicKey,
    });
  };

  const handleUnknownAdvert = () => {
    toast.info(t('rawPacket.openContactUnknown'));
  };

  const handleUnresolvedHash = async (hash: string) => {
    try {
      await navigator.clipboard.writeText(hash);
      toast.info(t('rawPacket.hashCopied', { hash }));
    } catch {
      toast.info(t('rawPacket.hashUnresolved', { hash }));
    }
  };

  const lookupDerived = (packet: RawPacket) =>
    derivedByObservation.get(getRawPacketDerivedCacheKey(packet, derivedScope)) ?? {
      payloadType: packet.payload_type,
      routeType: 'Unknown',
    };

  const handleCopyHex = async (packet: RawPacket) => {
    await navigator.clipboard.writeText(packet.data);
    toast.success(t('rawPacket.copiedHex'));
  };

  const handleCopyJson = async (packet: RawPacket, includeCleartext: boolean) => {
    const payload = toCopyJson(packet, lookupDerived(packet), includeCleartext);
    await navigator.clipboard.writeText(JSON.stringify(payload));
    toast.success(t('rawPacket.copiedJson'));
    if (includeCleartext) {
      toast.warning(t('rawPacket.copyJsonCleartextWarning'));
    }
  };

  const handleExport = (format: 'json' | 'jsonl') => {
    const rows = visibleEntries.map((entry) => toExportRow(entry.packet, entry));
    const contents =
      format === 'jsonl' ? rows.map((row) => JSON.stringify(row)).join('\n') : JSON.stringify(rows);
    downloadTextFile(
      `meshloom-raw-packets.${format}`,
      contents,
      format === 'jsonl' ? 'application/x-ndjson' : 'application/json'
    );
  };

  useEffect(() => {
    const interval = window.setInterval(() => {
      setNowSec(Math.floor(Date.now() / 1000));
    }, 30000);
    return () => window.clearInterval(interval);
  }, []);

  useEffect(() => {
    setNowSec(Math.floor(Date.now() / 1000));
  }, [livePackets, rawPacketStatsSession]);

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
              onClick={() => setHistoryModalOpen(true)}
              aria-label={t('rawPacket.historyAria')}
            >
              <History className="h-4 w-4 sm:hidden" aria-hidden="true" />
              <span className="hidden sm:inline" aria-hidden="true">
                {t('rawPacket.history')}
              </span>
            </Button>
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
            paused={paused}
            onPausedChange={handlePausedChange}
            heldCount={heldPackets.length}
            displayCap={displayCap}
            onDisplayCapChange={handleDisplayCapChange}
            rssiFilter={rssiFilter}
            onRssiFilterChange={setRssiFilter}
            zeroHopOnly={zeroHopOnly}
            onZeroHopOnlyChange={setZeroHopOnly}
            onExportJson={() => handleExport('json')}
            onExportJsonl={() => handleExport('jsonl')}
            hexFilter={hexFilter}
            onHexFilterChange={setHexFilter}
            hexInvalid={hexInvalid}
            textFilter={textFilter}
            onTextFilterChange={setTextFilter}
            matchCount={visiblePackets.length}
            totalCount={displaySource.length}
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
          paused={paused}
          onPausedChange={handlePausedChange}
          heldCount={heldPackets.length}
          displayCap={displayCap}
          onDisplayCapChange={handleDisplayCapChange}
          rssiFilter={rssiFilter}
          onRssiFilterChange={setRssiFilter}
          zeroHopOnly={zeroHopOnly}
          onZeroHopOnlyChange={setZeroHopOnly}
          onExportJson={() => handleExport('json')}
          onExportJsonl={() => handleExport('jsonl')}
          hexFilter={hexFilter}
          onHexFilterChange={setHexFilter}
          hexInvalid={hexInvalid}
          textFilter={textFilter}
          onTextFilterChange={setTextFilter}
          matchCount={visiblePackets.length}
          totalCount={displaySource.length}
        />
        {displayCap > 500 || capMenuHint ? (
          <p className="mt-1 text-[0.6875rem] text-warning">{t('rawPacket.displayCapPerf')}</p>
        ) : null}
        {replayActive ? (
          <div
            role="status"
            className="mt-2 flex flex-wrap items-center justify-between gap-2 rounded-md border border-warning/40 bg-warning/10 px-3 py-1.5 text-xs text-foreground"
          >
            <span>{t('rawPacket.historyBanner')}</span>
            <Button
              type="button"
              variant="outline"
              size="sm"
              onClick={() => clearReplayPackets()}
              aria-label={t('rawPacket.historyExitAria')}
            >
              {t('rawPacket.historyExit')}
            </Button>
          </div>
        ) : null}
      </div>

      <div className="flex min-h-0 flex-1 flex-col md:flex-row">
        <div className={cn('min-h-0 min-w-0 flex-1', statsOpen && 'md:border-r md:border-border')}>
          <RawPacketList
            packets={visiblePackets}
            channels={channels}
            contacts={contacts}
            extraSecrets={[]}
            onPacketClick={setSelectedPacket}
            onRepeatFilter={handleRepeatFilter}
            autoScroll={autoScroll}
            radioOffline={radioOffline && !replayActive}
            virtualize={displayCap > 500}
            onOpenContactInfo={onOpenContactInfo}
            onSelectConversation={onSelectConversation}
            onOpenVisualizer={replayActive ? undefined : handleOpenVisualizer}
            onUnknownAdvert={handleUnknownAdvert}
            onUnresolvedHash={(hash) => {
              void handleUnresolvedHash(hash);
            }}
            onPinAdvert={handlePinAdvert}
            onCopyHex={(packet) => {
              void handleCopyHex(packet);
            }}
            onCopyJson={(packet, includeCleartext) => {
              void handleCopyJson(packet, includeCleartext);
            }}
            showVisualizerAction={!replayActive}
            derivedScope={derivedScope}
            emptyMessage={replayActive ? t('rawPacket.historyEmpty') : undefined}
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

      <HistoryReplayModal
        open={historyModalOpen}
        onOpenChange={setHistoryModalOpen}
        displayCap={displayCap}
        onLoaded={() => setSelectedPacket(null)}
      />
    </>
  );
}
