import { useTranslation } from 'react-i18next';
import type { HealthStatus } from '../types';
import { cn } from '../lib/utils';

/**
 * Whether the radio is up, and how this app is reaching it.
 *
 * On a phone this is a labelled pill in the list header — the only place the
 * status can live once the app header is gone. On the desktop rail it is a
 * stacked tile: the word and the transport, never a coloured dot alone.
 */

interface Props {
  health: HealthStatus | null;
  onOpenRadioSettings?: () => void;
  /** Opens the read-out this chip summarises. Takes precedence over the settings. */
  onOpenStatus?: () => void;
  /** Rail form: short word + transport, sized like a destination. */
  compact?: boolean;
  className?: string;
}

export function radioTransportHint(connectionInfo: string | null | undefined): string | null {
  if (!connectionInfo) return null;
  const head = connectionInfo.split(/[:\s]/)[0]?.trim();
  return head || null;
}

export function RadioStatusChip({
  health,
  onOpenRadioSettings,
  onOpenStatus,
  compact,
  className,
}: Props) {
  const { t } = useTranslation();

  const state = health?.radio_state;
  const connected = health?.radio_connected === true;
  const settling = state === 'connecting' || state === 'initializing';

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
  const title = health?.connection_info ? `${label} — ${health.connection_info}` : label;

  const content = compact ? (
    <>
      <span className="inline-flex min-w-0 items-center gap-1">
        <span
          className={cn(
            'h-2 w-2 shrink-0 rounded-full',
            settling ? 'bg-warning' : connected ? 'bg-status-connected' : 'bg-status-disconnected'
          )}
          aria-hidden="true"
        />
        <span className="truncate text-[0.625rem] font-medium leading-none">{shortLabel}</span>
      </span>
      {hint && (
        <span className="max-w-full truncate text-[0.5625rem] leading-none text-muted-foreground">
          {hint}
        </span>
      )}
    </>
  ) : (
    <>
      <span
        className={cn(
          'h-2 w-2 shrink-0 rounded-full',
          settling ? 'bg-warning' : connected ? 'bg-status-connected' : 'bg-status-disconnected'
        )}
        aria-hidden="true"
      />
      <span className="truncate">{label}</span>
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
