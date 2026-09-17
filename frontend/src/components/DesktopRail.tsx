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
 * The Meshloom mark sits above the destinations and opens the public site.
 * A wide gap keeps it from being hit when reaching for Chat. The rest is
 * icons alone, with the name as a tooltip and an accessible label.
 */

export const MESHLOOM_SITE_URL = 'https://meshloom.app';

interface Props {
  active: BottomNavTarget | null;
  unreadTotal: number;
  onSelect: (target: BottomNavTarget) => void;
  health: HealthStatus | null;
  /** Opens the read-out the rail status summarises. */
  onOpenRadioStatus: () => void;
  /** The rail's contents, in order, as configured in Settings. */
  order?: string[];
  /** Which tool is open, so a pinned tool lights up like a destination does. */
  activeToolId?: string | null;
  /** Overlay toggles (not conversations) that should look pressed. */
  overlayPressed?: Partial<Record<RailItemId, boolean>>;
  onSelectTool: (id: RailItemId) => void;
  updateAvailable?: boolean;
  onOpenUpdate?: () => void;
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
  overlayPressed,
  onSelectTool,
  updateAvailable = false,
  onOpenUpdate,
}: Props) {
  const { t } = useTranslation();

  return (
    <nav
      aria-label={t('bottomNav.label')}
      data-desktop-rail=""
      className="group/rail hidden w-14 shrink-0 flex-col items-center gap-1 overflow-y-auto border-r border-border bg-muted/30 py-3 md:flex"
    >
      {/* Brand, not a destination: Chat sits below after a full extra hit-target
          of empty space so a reach for discussions cannot open the site. */}
      <a
        href={MESHLOOM_SITE_URL}
        target="_blank"
        rel="noopener noreferrer"
        aria-label={t('desktopRail.siteLink')}
        title={t('desktopRail.siteLink')}
        className="mb-16 inline-flex h-10 w-10 items-center justify-center rounded-xl transition-colors hover:bg-accent/50 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
      >
        <img
          src="./meshloom-mark.svg"
          alt=""
          className="h-7 w-7 object-contain [filter:drop-shadow(0_0_6px_rgba(34,211,238,0.28))]"
        />
      </a>
      {resolveRail(order).map(({ id, labelKey, Icon, permanent, overlay }) => {
        const target = id as BottomNavTarget;
        const current = overlay
          ? Boolean(overlayPressed?.[id])
          : permanent
            ? active === target
            : activeToolId === id;
        const badge = id === 'conversations' && unreadTotal > 0;
        return (
          <button
            key={id}
            type="button"
            onClick={() => (permanent ? onSelect(target) : onSelectTool(id))}
            aria-current={!overlay && current ? 'page' : undefined}
            aria-pressed={overlay ? current : undefined}
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
        {/* Word and transport, not a coloured dot: this is the one desktop
            status, so it has to say what it is before anyone opens it. The
            gear beneath it still goes to the settings. */}
        <RadioStatusChip
          health={health}
          compact
          onOpenStatus={onOpenRadioStatus}
          updateAvailable={updateAvailable}
        />

        {ANCHORED_RAIL_ITEMS.map(({ target, labelKey, Icon }) => (
          <div key={target} className="relative">
            <button
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
            {target === 'settings' && updateAvailable && (
              <button
                type="button"
                onClick={() => onOpenUpdate?.()}
                aria-label={t('updates.badgeLabel')}
                className="absolute -right-0.5 -top-0.5 z-10 min-w-[1.15rem] rounded-full bg-primary px-1 text-[0.625rem] font-semibold leading-[1.05rem] text-primary-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
              >
                !
              </button>
            )}
          </div>
        ))}
      </div>
    </nav>
  );
}
