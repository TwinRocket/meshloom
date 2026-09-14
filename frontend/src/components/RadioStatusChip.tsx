import { useTranslation } from 'react-i18next';
import type { HealthStatus } from '../types';
import { cn } from '../lib/utils';

/**
 * Whether the radio is up, on the screens that no longer sit under the app header.
 *
 * This is the one piece of state a radio client cannot be coy about: everything a
 * reader might try to do next depends on it, and "my message did not send" is a
 * miserable way to find out. The header used to carry it on every screen; with the
 * header gone from phones it has to be somewhere the phone actually shows.
 *
 * Shape as well as colour, and a word either way — a coloured dot alone says nothing
 * to a reader who cannot separate the two hues, and nothing at all to a screen reader.
 */

interface Props {
  health: HealthStatus | null;
  onOpenRadioSettings?: () => void;
  /** Dot only, for the rail, where there is no room for the word. */
  compact?: boolean;
  className?: string;
}

export function RadioStatusChip({ health, onOpenRadioSettings, compact, className }: Props) {
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

  const content = (
    <>
      <span
        className={cn(
          'h-2 w-2 shrink-0 rounded-full',
          settling ? 'bg-warning' : connected ? 'bg-status-connected' : 'bg-status-disconnected'
        )}
        aria-hidden="true"
      />
      {/* The word is the accessible name when it cannot be shown. */}
      <span className={cn('truncate', compact && 'sr-only')}>{label}</span>
    </>
  );

  const classes = cn(
    'inline-flex items-center gap-1.5 rounded-full border border-border/60 bg-muted/40',
    compact ? 'h-7 w-7 justify-center' : 'px-2 py-1',
    'text-[0.6875rem] text-muted-foreground',
    className
  );

  if (!onOpenRadioSettings) {
    return (
      <span className={classes} role="status">
        {content}
      </span>
    );
  }

  return (
    <button
      type="button"
      onClick={onOpenRadioSettings}
      className={cn(
        classes,
        'transition-colors hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring'
      )}
      aria-label={t('statusBar.radioStatusOpensSettings', { status: label })}
    >
      {content}
    </button>
  );
}
