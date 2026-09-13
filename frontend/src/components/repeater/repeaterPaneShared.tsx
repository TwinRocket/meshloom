import type { ReactNode } from 'react';
import { useTranslation } from 'react-i18next';
import { cn } from '@/lib/utils';
import i18n from '../../i18n';
import type { LppSensor, PaneState } from '../../types';

// --- Shared Icons ---

export function RefreshIcon({ className }: { className?: string }) {
  return (
    <svg
      className={className}
      fill="none"
      viewBox="0 0 24 24"
      stroke="currentColor"
      strokeWidth={2}
    >
      <path
        strokeLinecap="round"
        strokeLinejoin="round"
        d="M4 4v5h.582m15.356 2A8.001 8.001 0 004.582 9m0 0H9m11 11v-5h-.581m0 0a8.003 8.003 0 01-15.357-2m15.357 2H15"
      />
    </svg>
  );
}

// --- Utility ---

export function formatDuration(seconds: number): string {
  if (seconds < 60) return `${seconds}s`;
  const days = Math.floor(seconds / 86400);
  const hours = Math.floor((seconds % 86400) / 3600);
  const mins = Math.floor((seconds % 3600) / 60);
  if (days > 0) {
    if (hours > 0 && mins > 0) return `${days}d${hours}h${mins}m`;
    if (hours > 0) return `${days}d${hours}h`;
    if (mins > 0) return `${days}d${mins}m`;
    return `${days}d`;
  }
  if (hours > 0) return mins > 0 ? `${hours}h${mins}m` : `${hours}h`;
  return `${mins}m`;
}

export function formatClockDrift(
  clockUtc: string,
  referenceTimeMs: number = Date.now()
): { text: string; isLarge: boolean } {
  // Firmware format: "HH:MM - D/M/YYYY UTC" or "HH:MM:SS - D/M/YYYY UTC"
  // Also handle ISO-like: "YYYY-MM-DD HH:MM:SS"
  let parsed: Date;
  const fwMatch = clockUtc.match(
    /^(\d{1,2}):(\d{2})(?::(\d{2}))?\s*-\s*(\d{1,2})\/(\d{1,2})\/(\d{4})/
  );
  if (fwMatch) {
    const [, hh, mm, ss, dd, mo, yyyy] = fwMatch;
    parsed = new Date(Date.UTC(+yyyy, +mo - 1, +dd, +hh, +mm, +(ss ?? 0)));
  } else {
    parsed = new Date(
      clockUtc.replace(' ', 'T') + (clockUtc.includes('Z') || clockUtc.includes('UTC') ? '' : 'Z')
    );
  }
  if (isNaN(parsed.getTime())) return { text: i18n.t('repeater.clockInvalid'), isLarge: false };

  const driftMs = Math.abs(referenceTimeMs - parsed.getTime());
  const driftSec = Math.floor(driftMs / 1000);

  if (driftSec >= 86400) return { text: i18n.t('repeater.clockDriftLarge'), isLarge: true };

  const h = Math.floor(driftSec / 3600);
  const m = Math.floor((driftSec % 3600) / 60);
  const s = driftSec % 60;

  const parts: string[] = [];
  if (h > 0) parts.push(`${h}h`);
  if (m > 0) parts.push(`${m}m`);
  parts.push(`${s}s`);

  return { text: parts.join(''), isLarge: false };
}

export function formatAdvertInterval(
  val: string | null,
  unit: 'minutes' | 'hours' = 'hours'
): string {
  if (val == null) return '—';
  const trimmed = val.trim();
  if (trimmed === '0') return i18n.t('repeater.advertDisabled');
  if (unit === 'hours') return `${trimmed}h`;
  const mins = parseInt(trimmed, 10);
  if (isNaN(mins)) return trimmed;
  if (mins >= 60 && mins % 60 === 0) return `${mins / 60}h`;
  if (mins >= 60) return `${Math.floor(mins / 60)}h${mins % 60}m`;
  return `${mins}m`;
}

function formatFetchedRelative(fetchedAt: number): string {
  const elapsedSeconds = Math.max(0, Math.floor((Date.now() - fetchedAt) / 1000));

  if (elapsedSeconds < 60) return i18n.t('repeater.justNow');

  const elapsedMinutes = Math.floor(elapsedSeconds / 60);
  if (elapsedMinutes < 60) {
    return i18n.t('repeater.minutesAgo', { count: elapsedMinutes });
  }

  const elapsedHours = Math.floor(elapsedMinutes / 60);
  return i18n.t('repeater.hoursAgo', { count: elapsedHours });
}

function formatFetchedTime(fetchedAt: number): string {
  return new Date(fetchedAt).toLocaleTimeString(i18n.language, {
    hour: 'numeric',
    minute: '2-digit',
    second: '2-digit',
  });
}

// --- Generic Pane Wrapper ---

export function RepeaterPane({
  title,
  headerNote,
  state,
  onRefresh,
  disabled,
  children,
  className,
  contentClassName,
}: {
  title: string;
  headerNote?: ReactNode;
  state: PaneState;
  onRefresh?: () => void;
  disabled?: boolean;
  children: ReactNode;
  className?: string;
  contentClassName?: string;
}) {
  const { t } = useTranslation();
  const fetchedAt = state.fetched_at ?? null;
  // Load All is serial, so a queued pane otherwise looks exactly like one nobody
  // asked for: a single spinner and eight panes reading "not fetched".
  const paneState = state.loading
    ? state.attempt > 1
      ? t('repeater.fetchingAttempt', { attempt: state.attempt, max: 3 })
      : t('repeater.fetching')
    : state.queued
      ? t('repeater.queuedBadge')
      : null;

  return (
    <div className={cn('border border-border rounded-lg overflow-hidden', className)}>
      <div className="flex items-center justify-between px-3 py-2 bg-muted/50 border-b border-border">
        <div className="min-w-0">
          <div className="flex min-w-0 items-center gap-2">
            <h3 className="truncate text-sm font-medium">{title}</h3>
            {/* One place for the state, whatever the pane holds. Announcing it in
                the body instead meant a pane with cached values never showed it —
                and a pane without values showed it somewhere else. */}
            {paneState && (
              <span className="shrink-0 rounded-full bg-muted px-1.5 py-0.5 text-[0.625rem] font-medium text-muted-foreground">
                {paneState}
              </span>
            )}
          </div>
          {headerNote && <p className="text-[0.6875rem] text-muted-foreground">{headerNote}</p>}
          {fetchedAt && (
            <p
              className="text-[0.6875rem] text-muted-foreground"
              title={new Date(fetchedAt).toLocaleString()}
            >
              {t('repeater.fetched', {
                time: formatFetchedTime(fetchedAt),
                relative: formatFetchedRelative(fetchedAt),
              })}
            </p>
          )}
        </div>
        {onRefresh && (
          <button
            type="button"
            onClick={onRefresh}
            disabled={disabled || state.loading}
            className={cn(
              'p-1 rounded transition-colors disabled:opacity-50 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring',
              disabled || state.loading
                ? 'text-muted-foreground'
                : 'text-success hover:bg-accent hover:text-success'
            )}
            title={t('repeater.refresh')}
            aria-label={t('repeater.refreshTitle', { title })}
          >
            <RefreshIcon
              className={cn(
                'w-3.5 h-3.5',
                state.loading && 'animate-spin [animation-direction:reverse]'
              )}
            />
          </button>
        )}
      </div>
      {state.error && (
        <div className="px-3 py-1.5 text-xs text-destructive bg-destructive/5 border-b border-border">
          {state.error}
        </div>
      )}
      <div className={cn('p-3', contentClassName)}>{children}</div>
    </div>
  );
}

export function NotFetched() {
  const { t } = useTranslation();
  return <p className="text-sm italic text-muted-foreground">{t('repeater.notFetched')}</p>;
}

export function KvRow({ label, value }: { label: string; value: ReactNode }) {
  return (
    <div className="flex justify-between items-center text-sm py-0.5">
      <span className="text-muted-foreground">{label}</span>
      <span className="font-medium text-right">{value}</span>
    </div>
  );
}

// --- LPP Utilities ---

export const LPP_UNIT_MAP: Record<string, string> = {
  temperature: '°C',
  humidity: '%',
  barometer: 'hPa',
  voltage: 'V',
  current: 'A',
  luminosity: 'lux',
  altitude: 'm',
  power: 'W',
  distance: 'm',
  energy: 'kWh',
  direction: '°',
  concentration: 'ppm',
  colour: '',
};

/**
 * Return the display unit and converted value for an LPP sensor,
 * respecting the user's unit preference for temperature.
 */
export function lppDisplayUnit(
  typeName: string,
  value: number,
  unitPref: 'metric' | 'imperial' | string
): { unit: string; value: number } {
  if (typeName === 'temperature' && unitPref === 'imperial') {
    return { unit: '°F', value: (value * 9) / 5 + 32 };
  }
  if (typeName === 'current') {
    if (value <= 1) return { unit: 'mA', value: value * 1000 };
  }
  return { unit: LPP_UNIT_MAP[typeName] ?? '', value };
}

export function formatLppLabel(typeName: string): string {
  return typeName.charAt(0).toUpperCase() + typeName.slice(1).replace(/_/g, ' ');
}

export function LppSensorRow({
  sensor,
  unitPref,
  label: labelOverride,
}: {
  sensor: LppSensor;
  unitPref?: string;
  label?: string;
}) {
  const label = labelOverride ?? formatLppLabel(sensor.type_name);

  if (typeof sensor.value === 'object' && sensor.value !== null) {
    // Multi-value sensor (GPS, accelerometer, etc.)
    return (
      <div className="py-0.5">
        <span className="text-sm text-muted-foreground">{label}</span>
        <div className="pl-3">
          {Object.entries(sensor.value).map(([k, v]) => (
            <KvRow
              key={k}
              label={k.charAt(0).toUpperCase() + k.slice(1)}
              value={typeof v === 'number' ? v.toFixed(2) : String(v)}
            />
          ))}
        </div>
      </div>
    );
  }

  const display = lppDisplayUnit(sensor.type_name, sensor.value as number, unitPref ?? 'metric');
  const formatted =
    typeof sensor.value === 'number'
      ? `${display.value % 1 === 0 ? display.value : display.value.toFixed(2)}${display.unit ? ` ${display.unit}` : ''}`
      : String(sensor.value);

  return <KvRow label={label} value={formatted} />;
}
