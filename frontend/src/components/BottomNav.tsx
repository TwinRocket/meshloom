import { useTranslation } from 'react-i18next';
import { type BottomNavTarget, NAV_ITEMS, UNREAD_BADGE_MAX } from './navDestinations';
import { cn } from '../lib/utils';

export type { BottomNavTarget };

/**
 * Primary navigation for narrow screens.
 *
 * Installed on a phone, the app has no browser chrome: no address bar, no back
 * button, and iOS blurs whatever sits under the status bar. A drawer behind a
 * burger was the only way between the app's modes, which meant two gestures and a
 * hidden list to answer "where else can I go".
 *
 * It is deliberately absent inside a conversation. There the composer owns the
 * bottom of the screen, and a floating bar over it would either cover the send
 * control or push the history up for the whole session.
 */

interface Props {
  active: BottomNavTarget | null;
  unreadTotal: number;
  pendingCount?: number;
  onSelect: (target: BottomNavTarget) => void;
  className?: string;
  updateAvailable?: boolean;
  onOpenUpdate?: () => void;
}

export function BottomNav({
  active,
  unreadTotal,
  pendingCount = 0,
  onSelect,
  className,
  updateAvailable = false,
  onOpenUpdate,
}: Props) {
  const { t } = useTranslation();

  return (
    <nav
      aria-label={t('bottomNav.label')}
      data-bottom-nav=""
      className={cn(
        // Absolute, not fixed: `fixed` anchors to the layout viewport, which an
        // installed app reports shorter than the screen. Anchored to the shell it
        // follows whatever the shell's real height turns out to be.
        'pointer-events-none absolute inset-x-0 bottom-0 z-40 flex justify-center md:hidden',
        // Clear of the home indicator with a little air, the way the system's own
        // floating bars sit — not pressed against the edge.
        'px-2 pb-[calc(var(--safe-area-bottom)+0.5rem)]',
        className
      )}
    >
      <div className="liquid-surface pointer-events-auto flex w-full max-w-md items-stretch justify-between gap-0 rounded-[1.75rem] p-1.5">
        {NAV_ITEMS.map(({ target, labelKey, Icon }) => {
          const isActive = active === target;
          const badge =
            target === 'conversations' ? unreadTotal : target === 'tools' ? pendingCount : 0;
          const showUpdate = target === 'settings' && updateAvailable;
          return (
            <div key={target} className="relative flex min-w-0 flex-1">
              <button
                type="button"
                onClick={() => onSelect(target)}
                aria-current={isActive ? 'page' : undefined}
                className={cn(
                  'relative flex min-w-0 flex-1 flex-col items-center justify-center gap-1 rounded-[1.375rem] px-0.5 py-2.5',
                  'text-[0.625rem] leading-none transition-colors',
                  'focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring',
                  isActive
                    ? 'bg-primary/15 text-primary'
                    : 'text-muted-foreground hover:text-foreground'
                )}
              >
                <span className="relative">
                  <Icon className="h-[1.375rem] w-[1.375rem]" aria-hidden="true" />
                  {badge > 0 && (
                    <span
                      className="absolute -right-2.5 -top-1.5 min-w-[1.05rem] rounded-full bg-primary px-1 py-px text-[0.5625rem] font-semibold leading-tight text-primary-foreground"
                      aria-hidden="true"
                    >
                      {badge > UNREAD_BADGE_MAX ? `${UNREAD_BADGE_MAX}+` : badge}
                    </span>
                  )}
                </span>
                <span className="max-w-full truncate">{t(labelKey)}</span>
                {badge > 0 && (
                  <span className="sr-only">{t('bottomNav.unread', { count: badge })}</span>
                )}
              </button>
              {showUpdate && (
                <button
                  type="button"
                  onClick={() => onOpenUpdate?.()}
                  aria-label={t('updates.badgeLabel')}
                  className="absolute right-[calc(50%-1.35rem)] top-1.5 z-10 min-w-[1.15rem] rounded-full bg-primary px-1 py-px text-[0.5625rem] font-semibold leading-tight text-primary-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
                >
                  !
                </button>
              )}
            </div>
          );
        })}
      </div>
    </nav>
  );
}
