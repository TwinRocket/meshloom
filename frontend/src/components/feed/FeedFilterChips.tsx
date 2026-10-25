import { useTranslation } from 'react-i18next';

import { formatNumber } from '../../utils/formatNumber';
import type { FeedFilters } from '../../utils/feedFilters';
import { cn } from '@/lib/utils';

interface FeedFilterChipsProps {
  filters: FeedFilters;
  hidden: { muted: number; pending: number };
  onChange: (next: FeedFilters) => void;
  /** False without `read_private`: the Direct messages chip is not rendered at all. */
  showDirect?: boolean;
}

interface ChipProps {
  label: string;
  pressed: boolean;
  onToggle?: () => void;
  disabled?: boolean;
  title?: string;
  hint?: string;
}

function Chip({ label, pressed, onToggle, disabled, title, hint }: ChipProps) {
  return (
    <button
      type="button"
      aria-pressed={pressed}
      disabled={disabled}
      title={title}
      onClick={onToggle}
      className={cn(
        'inline-flex shrink-0 items-center gap-1.5 whitespace-nowrap rounded-full border px-3 py-1.5 text-xs transition-colors md:px-2.5 md:py-1',
        'focus-visible:outline-hidden focus-visible:ring-2 focus-visible:ring-ring',
        pressed
          ? 'border-primary bg-primary/10 text-foreground'
          : 'border-border text-muted-foreground hover:bg-accent',
        disabled && 'cursor-not-allowed opacity-50 hover:bg-transparent'
      )}
    >
      {label}
      {hint && <span className="text-muted-foreground">{hint}</span>}
    </button>
  );
}

/**
 * One chip row: a single swipeable line on a phone, so the two groups cost two rows of
 * the screen instead of five (the timeline keeps the room); wrapped from `md`.
 */
const ROW_CLASS =
  'flex gap-1.5 overflow-x-auto overscroll-x-contain [scrollbar-width:none] [&::-webkit-scrollbar]:hidden -mx-4 px-4 md:mx-0 md:flex-wrap md:overflow-visible md:px-0';

/** The Sources and Show chip rows above the timeline. */
export function FeedFilterChips({
  filters,
  hidden,
  onChange,
  showDirect = true,
}: FeedFilterChipsProps) {
  const { t } = useTranslation();
  const set = (patch: Partial<FeedFilters>) => onChange({ ...filters, ...patch });
  const hiddenHint = (key: 'hiddenMuted' | 'hiddenPending', count: number, shown: boolean) =>
    !shown && count > 0 ? t(`feed.${key}`, { count, formatted: formatNumber(count) }) : undefined;

  return (
    <div className="flex flex-col gap-1.5 border-b border-border px-4 py-2">
      <div role="group" aria-label={t('feed.filters.sources')} className={ROW_CLASS}>
        <Chip
          label={t('feed.filters.messages')}
          pressed={filters.sources.messages}
          onToggle={() =>
            set({ sources: { ...filters.sources, messages: !filters.sources.messages } })
          }
        />
        <Chip
          label={t('feed.filters.control')}
          pressed={filters.sources.control}
          onToggle={() =>
            set({ sources: { ...filters.sources, control: !filters.sources.control } })
          }
        />
        <Chip
          label={t('feed.filters.events')}
          pressed={filters.sources.events}
          onToggle={() => set({ sources: { ...filters.sources, events: !filters.sources.events } })}
        />
      </div>
      {filters.sources.events && (
        <p className="text-xs text-muted-foreground">{t('feed.events.sessionOnly')}</p>
      )}
      <div role="group" aria-label={t('feed.filters.show')} className={ROW_CLASS}>
        <Chip
          label={t('feed.filters.channels')}
          pressed={filters.chan}
          onToggle={() => set({ chan: !filters.chan })}
        />
        {showDirect && (
          <Chip
            label={t('feed.filters.direct')}
            pressed={filters.priv}
            onToggle={() => set({ priv: !filters.priv })}
          />
        )}
        <Chip
          label={t('feed.filters.received')}
          pressed={filters.incoming}
          onToggle={() => set({ incoming: !filters.incoming })}
        />
        <Chip
          label={t('feed.filters.sent')}
          pressed={filters.outgoing}
          onToggle={() => set({ outgoing: !filters.outgoing })}
        />
        <Chip
          label={t('feed.filters.muted')}
          pressed={filters.includeMuted}
          hint={hiddenHint('hiddenMuted', hidden.muted, filters.includeMuted)}
          onToggle={() => set({ includeMuted: !filters.includeMuted })}
        />
        <Chip
          label={t('feed.filters.pending')}
          pressed={filters.includePending}
          hint={hiddenHint('hiddenPending', hidden.pending, filters.includePending)}
          onToggle={() => set({ includePending: !filters.includePending })}
        />
      </div>
    </div>
  );
}
