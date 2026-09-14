import { useTranslation } from 'react-i18next';
import {
  ANCHORED_RAIL_ITEMS,
  type BottomNavTarget,
  type RailItemId,
  resolveRail,
  UNREAD_BADGE_MAX,
} from './navDestinations';
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
  /** Opens the read-out the dot summarises. */
  onOpenRadioStatus: () => void;
  /** The rail's contents, in order, as configured in Settings. */
  order?: string[];
  /** Which tool is open, so a pinned tool lights up like a destination does. */
  activeToolId?: string | null;
  onSelectTool: (id: RailItemId) => void;
}

/** The bottom group's controls: same size as a destination, never marked current. */
const DOOR_CLASS =
  'inline-flex h-10 w-10 items-center justify-center rounded-xl text-muted-foreground transition-colors hover:bg-accent/50 hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring';

export function DesktopRail({
  active,
  unreadTotal,
  onSelect,
  health,
  onOpenRadioStatus,
  order,
  activeToolId,
  onSelectTool,
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

          Settings is marked like any destination — it is a place, with a list and
          a pane of its own. The rule this group needs is narrower than "never mark
          the bottom": what must not be marked is a catalogue, which would light up
          beside the thing opened from it. There is no catalogue here. */}
      <div className="mt-auto flex flex-col items-center gap-1 pt-2">
        {/* The dot is enough to notice something is wrong and never enough to act
            on it, so it opens what it is a summary of rather than the settings —
            which is where the gear beneath it already goes. */}
        <RadioStatusChip health={health} compact onOpenStatus={onOpenRadioStatus} />

        {ANCHORED_RAIL_ITEMS.map(({ target, labelKey, Icon }) => (
          <button
            key={target}
            type="button"
            onClick={() => onSelect(target)}
            aria-current={active === target ? 'page' : undefined}
            aria-label={t(labelKey)}
            title={t(labelKey)}
            className={cn(
              DOOR_CLASS,
              active === target && 'bg-primary/15 text-primary hover:text-primary'
            )}
          >
            <Icon className="h-[1.25rem] w-[1.25rem]" aria-hidden="true" />
          </button>
        ))}
      </div>
    </nav>
  );
}
