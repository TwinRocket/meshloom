import { useEffect, useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';
import type { HealthStatus } from '../types';
import { cn } from '../lib/utils';
import {
  BATTERY_DISPLAY_CHANGE_EVENT,
  formatBatteryChip,
  formatBatteryLabel,
  getShowBatteryPercent,
  getShowBatteryVoltage,
  radioBatteryMv,
} from '../utils/batteryDisplay';
import {
  STATUS_DOT_PULSE_CHANGE_EVENT,
  STATUS_DOT_PULSE_DURATION_MS,
  STATUS_DOT_PULSE_PACKET_EVENT,
  STATUS_DOT_PULSE_SIZE_PX,
  getStatusDotPulseEnabled,
  pulseColorFor,
  type StatusDotPulseKind,
} from '../utils/statusDotPulse';

/**
 * Whether the radio is up, and how this app is reaching it.
 *
 * On a phone this is a labelled pill in the list header — the only place the
 * status can live once the app header is gone. On the desktop rail it is a
 * stacked tile: the word and the transport, never a coloured dot alone.
 * Optional battery % / voltage rides on the same tile when Local settings ask
 * for it — the old status bar that used to show those values is gone.
 */

/** Applied to the status pip when a Meshloom update is available. */
export const STATUS_DOT_UPDATE_AVAILABLE_CLASS = 'status-dot-update-available';

interface Props {
  health: HealthStatus | null;
  onOpenRadioSettings?: () => void;
  /** Opens the read-out this chip summarises. Takes precedence over the settings. */
  onOpenStatus?: () => void;
  /** Rail form: short word + transport, sized like a destination. */
  compact?: boolean;
  /** Pulse the pip when a newer Meshloom release is published. */
  updateAvailable?: boolean;
  className?: string;
}

export function radioTransportHint(connectionInfo: string | null | undefined): string | null {
  if (!connectionInfo) return null;
  const head = connectionInfo.split(/[:\s]/)[0]?.trim();
  return head || null;
}

function statusDotClass(settling: boolean, connected: boolean, updateAvailable?: boolean): string {
  return cn(
    'h-2 w-2 shrink-0 rounded-full transition-[width,height,background-color] duration-150',
    settling ? 'bg-warning' : connected ? 'bg-status-connected' : 'bg-status-disconnected',
    updateAvailable && STATUS_DOT_UPDATE_AVAILABLE_CLASS
  );
}

function prefersReducedMotion(): boolean {
  return window.matchMedia?.('(prefers-reduced-motion: reduce)').matches ?? false;
}

function useStatusDotPulse(): StatusDotPulseKind | null {
  const [kind, setKind] = useState<StatusDotPulseKind | null>(null);
  const timeoutRef = useRef<number | null>(null);

  useEffect(() => {
    const clearPulse = () => {
      if (timeoutRef.current != null) {
        window.clearTimeout(timeoutRef.current);
        timeoutRef.current = null;
      }
      setKind(null);
    };

    const onPacket = (event: Event) => {
      if (!getStatusDotPulseEnabled() || prefersReducedMotion()) return;
      const next = (event as CustomEvent<StatusDotPulseKind>).detail;
      if (!next) return;
      setKind(next);
      if (timeoutRef.current != null) window.clearTimeout(timeoutRef.current);
      timeoutRef.current = window.setTimeout(() => {
        setKind(null);
        timeoutRef.current = null;
      }, STATUS_DOT_PULSE_DURATION_MS);
    };

    const onPrefChange = () => {
      if (!getStatusDotPulseEnabled()) clearPulse();
    };

    window.addEventListener(STATUS_DOT_PULSE_PACKET_EVENT, onPacket);
    window.addEventListener(STATUS_DOT_PULSE_CHANGE_EVENT, onPrefChange);
    return () => {
      window.removeEventListener(STATUS_DOT_PULSE_PACKET_EVENT, onPacket);
      window.removeEventListener(STATUS_DOT_PULSE_CHANGE_EVENT, onPrefChange);
      if (timeoutRef.current != null) window.clearTimeout(timeoutRef.current);
    };
  }, []);

  return kind;
}

function StatusDot({
  settling,
  connected,
  updateAvailable,
  pulseKind,
}: {
  settling: boolean;
  connected: boolean;
  updateAvailable?: boolean;
  pulseKind: StatusDotPulseKind | null;
}) {
  return (
    <span
      className={statusDotClass(settling, connected, updateAvailable)}
      style={
        pulseKind
          ? {
              backgroundColor: pulseColorFor(pulseKind),
              width: STATUS_DOT_PULSE_SIZE_PX,
              height: STATUS_DOT_PULSE_SIZE_PX,
            }
          : undefined
      }
      data-pulse-kind={pulseKind ?? undefined}
      aria-hidden="true"
    />
  );
}

function useBatteryDisplayPrefs() {
  const [prefs, setPrefs] = useState(() => ({
    percent: getShowBatteryPercent(),
    voltage: getShowBatteryVoltage(),
  }));

  useEffect(() => {
    const sync = () =>
      setPrefs({
        percent: getShowBatteryPercent(),
        voltage: getShowBatteryVoltage(),
      });
    window.addEventListener(BATTERY_DISPLAY_CHANGE_EVENT, sync);
    return () => window.removeEventListener(BATTERY_DISPLAY_CHANGE_EVENT, sync);
  }, []);

  return prefs;
}

export function RadioStatusChip({
  health,
  onOpenRadioSettings,
  onOpenStatus,
  compact,
  updateAvailable,
  className,
}: Props) {
  const { t } = useTranslation();
  const batteryPrefs = useBatteryDisplayPrefs();
  const pulseKind = useStatusDotPulse();

  const state = health?.radio_state;
  const connected = health?.radio_connected === true;
  const settling = state === 'connecting' || state === 'initializing';
  const batteryMv = radioBatteryMv(health);
  const batteryChip =
    batteryMv != null
      ? formatBatteryChip(batteryMv, batteryPrefs.percent, batteryPrefs.voltage)
      : null;
  const batteryTitle =
    batteryMv != null
      ? formatBatteryLabel(batteryMv, batteryPrefs.percent, batteryPrefs.voltage)
      : null;

  const label = settling
    ? t('statusBar.radioConnecting')
    : state === 'paused'
      ? t('statusBar.radioPaused')
      : connected
        ? t('statusBar.radioOk')
        : t('statusBar.radioDisconnected');

  const shortLabel = settling
    ? t('statusBar.radioConnectingShort')
    : state === 'paused'
      ? t('statusBar.radioPausedShort')
      : connected
        ? t('statusBar.radioOkShort')
        : t('statusBar.radioDisconnectedShort');

  const hint = radioTransportHint(health?.connection_info);
  const title = [label, health?.connection_info, batteryTitle].filter(Boolean).join(' — ');

  const content = compact ? (
    <>
      <span className="inline-flex min-w-0 items-center gap-1">
        <StatusDot
          settling={settling}
          connected={connected}
          updateAvailable={updateAvailable}
          pulseKind={pulseKind}
        />
        <span className="truncate text-[0.625rem] font-medium leading-none">{shortLabel}</span>
      </span>
      {hint && (
        <span className="max-w-full truncate text-[0.5625rem] leading-none text-muted-foreground">
          {hint}
        </span>
      )}
      {batteryChip && (
        <span className="max-w-full truncate text-[0.5625rem] leading-none tabular-nums text-muted-foreground">
          {batteryChip}
        </span>
      )}
    </>
  ) : (
    <>
      <StatusDot
        settling={settling}
        connected={connected}
        updateAvailable={updateAvailable}
        pulseKind={pulseKind}
      />
      <span className="truncate">{label}</span>
      {batteryChip && (
        <span className="shrink-0 tabular-nums text-muted-foreground">{batteryChip}</span>
      )}
    </>
  );

  const classes = cn(
    'inline-flex items-center border border-border/60 bg-muted/40',
    compact
      ? 'h-auto min-h-10 w-10 flex-col justify-center gap-0.5 px-0.5 py-1'
      : 'gap-1.5 rounded-full px-2 py-1',
    compact && 'rounded-xl',
    'text-[0.6875rem] text-muted-foreground',
    className
  );

  const action = onOpenStatus ?? onOpenRadioSettings;

  if (!action) {
    return (
      <span className={classes} role="status" title={title}>
        {content}
      </span>
    );
  }

  return (
    <button
      type="button"
      onClick={action}
      className={cn(
        classes,
        'transition-colors hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring'
      )}
      title={title}
      aria-label={
        onOpenStatus
          ? t('radioStatus.openStatus', { status: title })
          : t('statusBar.radioStatusOpensSettings', { status: title })
      }
    >
      {content}
    </button>
  );
}
