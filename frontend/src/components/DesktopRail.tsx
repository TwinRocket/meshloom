import { useTranslation } from 'react-i18next';
import {
  ANCHORED_RAIL_ITEMS,
  type BottomNavTarget,
  type RailItemId,
  resolveRail,
  UNREAD_BADGE_MAX,
} from './navDestinations';
// The same icon the Navigation settings section carries: one concept, one icon,
// and it does not compete with the gear sitting under it.
import { PanelLeft } from 'lucide-react';
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
  /** The rail's contents, in order, as configured in Settings. */
  order?: string[];
  /** Which tool is open, so a pinned tool lights up like a destination does. */
  activeToolId?: string | null;
  onSelectTool: (id: RailItemId) => void;
  /** Opens the screen where the rail's own contents are arranged. */
  onConfigure: () => void;
}

/** The bottom group's controls: same size as a destination, never marked current. */
const DOOR_CLASS =
  'inline-flex h-10 w-10 items-center justify-center rounded-xl text-muted-foreground transition-colors hover:bg-accent/50 hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring';

export function DesktopRail({
  active,
  unreadTotal,
  onSelect,
  health,
  order,
  activeToolId,
  onSelectTool,
  onConfigure,
}: Props) {
  const { t } = useTranslation();

  return (
    <nav
      aria-label={t('bottomNav.label')}
      data-desktop-rail=""
      className="group/rail hidden w-14 shrink-0 flex-col items-center gap-1 overflow-y-auto border-r border-border bg-muted/30 py-3 md:flex"
    >
      {resolveRail(order).map(({ id, labelKey, Icon, permanent }) => {
        const target = id as BottomNavTarget;
        const current = permanent ? active === target : activeToolId === id;
        const badge = id === 'conversations' && unreadTotal > 0;
        return (
          <button
            key={id}
            type="button"
            onClick={() => (permanent ? onSelect(target) : onSelectTool(id))}
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

      {/* Against the bottom edge: places in the mesh are one group, the application
          talking about itself is another — which is how the platform's own rails
          are built. Anchoring these here also means adding a tool never moves
          them, so their rank stops being a question.

          None of them carries aria-current, and that is the point rather than an
          omission. A catalogue is a door, not a location: marking Tools as current
          while a tool opened from it is also marked gives "where am I" two answers
          at once. What is open is marked in the group above when it is on the
          rail, and by the pane's own title when it is not. */}
      <div className="mt-auto flex flex-col items-center gap-1 pt-2">
        {/* The rail can be arranged and nothing on it said so. The control is the
            place you would change, which is how that gets discovered — quiet until
            the rail is hovered, so the group does not grow a permanent entry. */}
        <button
          type="button"
          onClick={onConfigure}
          aria-label={t('settingsNavigation.configureRail')}
          title={t('settingsNavigation.configureRail')}
          className="inline-flex h-8 w-8 items-center justify-center rounded-lg text-muted-foreground opacity-0 transition-opacity hover:bg-accent/50 hover:text-foreground focus-visible:opacity-100 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring group-hover/rail:opacity-100"
        >
          <PanelLeft className="h-4 w-4" aria-hidden="true" />
        </button>

        {/* Status, not a link. It used to open the radio settings, which is where
            the gear beneath it goes — two neighbouring controls leading to the same
            screen. */}
        <RadioStatusChip health={health} compact className="px-0" />

        {ANCHORED_RAIL_ITEMS.map(({ target, labelKey, Icon }) => (
          <button
            key={target}
            type="button"
            onClick={() => onSelect(target)}
            aria-label={t(labelKey)}
            title={t(labelKey)}
            className={DOOR_CLASS}
          >
            <Icon className="h-[1.25rem] w-[1.25rem]" aria-hidden="true" />
          </button>
        ))}
      </div>
    </nav>
  );
}
