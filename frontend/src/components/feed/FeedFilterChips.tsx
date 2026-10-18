import { useTranslation } from 'react-i18next';

import { formatNumber } from '../../utils/formatNumber';
import type { FeedFilters } from '../../utils/feedFilters';
import { cn } from '@/lib/utils';

interface FeedFilterChipsProps {
  filters: FeedFilters;
  hidden: { muted: number; pending: number };
  onChange: (next: FeedFilters) => void;
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
        'inline-flex items-center gap-1.5 rounded-full border px-2.5 py-1 text-xs transition-colors',
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

/** The Sources and Show chip rows above the timeline. */
export function FeedFilterChips({ filters, hidden, onChange }: FeedFilterChipsProps) {
  const { t } = useTranslation();
  const set = (patch: Partial<FeedFilters>) => onChange({ ...filters, ...patch });
  const soon = t('feed.filters.soon');
  const hiddenHint = (key: 'hiddenMuted' | 'hiddenPending', count: number, shown: boolean) =>
    !shown && count > 0 ? t(`feed.${key}`, { count, formatted: formatNumber(count) }) : undefined;

  return (
    <div className="flex flex-col gap-1.5 border-b border-border px-4 py-2">
      <div role="group" aria-label={t('feed.filters.sources')} className="flex flex-wrap gap-1.5">
        <Chip
          label={t('feed.filters.messages')}
          pressed={filters.sources.messages}
          onToggle={() =>
            set({ sources: { ...filters.sources, messages: !filters.sources.messages } })
          }
        />
        <Chip label={t('feed.filters.control')} pressed={false} disabled title={soon} />
        <Chip label={t('feed.filters.events')} pressed={false} disabled title={soon} />
      </div>
      <div role="group" aria-label={t('feed.filters.show')} className="flex flex-wrap gap-1.5">
        <Chip
          label={t('feed.filters.channels')}
          pressed={filters.chan}
          onToggle={() => set({ chan: !filters.chan })}
        />
        <Chip
          label={t('feed.filters.direct')}
          pressed={filters.priv}
          onToggle={() => set({ priv: !filters.priv })}
        />
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
