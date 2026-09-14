import { useTranslation } from 'react-i18next';
import { type BottomNavTarget, NAV_ITEMS, UNREAD_BADGE_MAX } from './navDestinations';
import { RadioStatusChip } from './RadioStatusChip';
import type { HealthStatus } from '../types';
import { cn } from '../lib/utils';

/**
 * Primary navigation for wide screens.
 *
 * The same four destinations the bar offers on a phone, so there is one navigation
 * model rather than two: what the bar lights, the rail lights. It replaces a 240px
 * column that mixed the tools with the conversations in a single scroll — with
 * thirty channels the tools left the screen entirely.
 *
 * Icons alone, with the name as a tooltip and an accessible label: the rail is
 * four items that do not change, and a column of words beside a list of
 * conversations reads as a second list.
 */

interface Props {
  active: BottomNavTarget | null;
  unreadTotal: number;
  onSelect: (target: BottomNavTarget) => void;
  health: HealthStatus | null;
  onOpenRadioSettings: () => void;
}

export function DesktopRail({ active, unreadTotal, onSelect, health, onOpenRadioSettings }: Props) {
  const { t } = useTranslation();

  return (
    <nav
      aria-label={t('bottomNav.label')}
      data-desktop-rail=""
      className="hidden w-14 shrink-0 flex-col items-center gap-1 border-r border-border bg-muted/30 py-3 md:flex"
    >
      {NAV_ITEMS.map(({ target, labelKey, Icon }) => {
        const current = active === target;
        const badge = target === 'conversations' && unreadTotal > 0;
        return (
          <button
            key={target}
            type="button"
            onClick={() => onSelect(target)}
            aria-current={current ? 'page' : undefined}
            aria-label={t(labelKey)}
            title={t(labelKey)}
            className={cn(
              'relative inline-flex h-10 w-10 items-center justify-center rounded-xl transition-colors',
              'focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring',
              current
                ? 'bg-primary/15 text-primary'
                : 'text-muted-foreground hover:bg-accent/50 hover:text-foreground'
            )}
          >
            <Icon className="h-[1.25rem] w-[1.25rem]" aria-hidden="true" />
            {badge && (
              <span
                className="absolute -right-0.5 -top-0.5 min-w-[1.05rem] rounded-full bg-primary px-1 text-[0.625rem] font-semibold leading-[1.05rem] text-primary-foreground"
                aria-hidden="true"
              >
                {unreadTotal > UNREAD_BADGE_MAX ? `${UNREAD_BADGE_MAX}+` : unreadTotal}
              </span>
            )}
          </button>
        );
      })}

      {/* The radio state lost its home with the app header. It belongs wherever the
          reader already looks to move around, and here it is out of the way of the
          conversation rather than in a bar above it. */}
      <div className="mt-auto">
        <RadioStatusChip
          health={health}
          onOpenRadioSettings={onOpenRadioSettings}
          compact
          className="px-0"
        />
      </div>
    </nav>
  );
}
