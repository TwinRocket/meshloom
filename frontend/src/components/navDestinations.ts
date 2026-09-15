import {
  MessageCircle,
  Map as MapIcon,
  LayoutGrid,
  Settings,
  List,
  Radio,
  Waypoints,
  Spline,
  Crosshair,
  Search,
  type LucideIcon,
} from 'lucide-react';
import type { Conversation } from '../types';

/**
 * Everything the navigation can point at, in one registry.
 *
 * The phone's bar and the desktop rail differ in shape, not in where they can go,
 * and a destination added to one and forgotten in the other is what left the live
 * map reachable by URL and by nothing else.
 */

export type BottomNavTarget = 'conversations' | 'map' | 'tools' | 'settings';

/** The four the bar offers. Permanent: each is the only way back to something. */
export const NAV_ITEMS: { target: BottomNavTarget; labelKey: string; Icon: LucideIcon }[] = [
  { target: 'conversations', labelKey: 'bottomNav.conversations', Icon: MessageCircle },
  { target: 'map', labelKey: 'bottomNav.map', Icon: MapIcon },
  // Everything that is not a conversation or the map lives behind one entry rather
  // than competing for a slot: the feed, the visualiser, trace, locate, search.
  { target: 'tools', labelKey: 'bottomNav.tools', Icon: LayoutGrid },
  { target: 'settings', labelKey: 'bottomNav.settings', Icon: Settings },
];

/** Past this the badge stops being a count and becomes "a lot". */
export const UNREAD_BADGE_MAX = 99;

export type RailItemId =
  BottomNavTarget | 'raw' | 'live' | 'visualizer' | 'trace' | 'locate' | 'search';

interface RailItem {
  id: RailItemId;
  labelKey: string;
  Icon: LucideIcon;
  /** Permanent items cannot be removed from the rail, only reordered.
   *
   *  Without this, the rail can be emptied of the very entry that leads back to the
   *  page where it is configured — the pin with no way to unpin itself. */
  permanent: boolean;
  /** The conversation a tool opens. Absent for the four bar destinations. */
  conversation?: Conversation;
}

const tool = (id: Exclude<RailItemId, BottomNavTarget>, labelKey: string, Icon: LucideIcon) => ({
  id,
  labelKey,
  Icon,
  permanent: false,
  conversation: { type: id, id, name: id } as Conversation,
});

/**
 * The rail's bottom group, and never part of the arrangeable list.
 *
 * A rail holds two natures of thing: places, and the application talking about
 * itself. Everything you can open is a place and lives in the group above;
 * settings and the radio state belong against the bottom edge, which is how the
 * platform's own rails are built.
 *
 * Nothing here carries `aria-current`, and that is the point rather than an
 * omission — it is a door, not a location.
 *
 * The tool catalogue is deliberately absent. Every tool has its own entry on the
 * rail, so a catalogue entry beside them would be a second way to reach places
 * already present, and would light up at the same time as the tool opened from it
 * — "where am I" with two answers. On a phone the bar has four slots and no
 * arranging, so Tools remains a destination there.
 */
export const ANCHORED_RAIL_IDS = ['settings'] as const;

/** Never on the rail: settings is anchored below, and Tools is a phone destination. */
const NOT_ON_RAIL: readonly string[] = ['settings', 'tools'];

export const RAIL_ITEMS: RailItem[] = [
  ...NAV_ITEMS.filter(({ target }) => !NOT_ON_RAIL.includes(target)).map(
    ({ target, labelKey, Icon }) => ({
      id: target as RailItemId,
      labelKey,
      Icon,
      permanent: true,
    })
  ),
  tool('raw', 'sidebar.packetFeed', List),
  tool('live', 'sidebar.live', Radio),
  tool('visualizer', 'sidebar.meshVisualizer', Waypoints),
  tool('trace', 'sidebar.trace', Spline),
  tool('locate', 'locate.title', Crosshair),
  tool('search', 'sidebar.messageSearch', Search),
];

const RAIL_BY_ID = new Map(RAIL_ITEMS.map((item) => [item.id, item]));

/**
 * Everything on the rail to begin with.
 *
 * There is room: nine entries at 44px sit inside a 600px window, and the rail
 * scrolls below that. Starting full means the tools are discoverable without
 * having to learn that the rail can be arranged at all — removing what you do not
 * use is an easier thing to think of than adding what you never saw.
 */
export const DEFAULT_RAIL: RailItemId[] = RAIL_ITEMS.map(({ id }) => id);

/** The bottom group's entries, in the order they are drawn. */
export const ANCHORED_RAIL_ITEMS = ANCHORED_RAIL_IDS.map((id) =>
  NAV_ITEMS.find(({ target }) => target === id)!
);

/** Add an entry to the rail, once. Order is otherwise the reader's business. */
export function addToRail(current: RailItemId[], id: RailItemId): RailItemId[] {
  if (current.includes(id)) return current;
  return [...current, id];
}

/**
 * The rail a stored order describes.
 *
 * Stored ids are data from the database and may be stale — a tool that no longer
 * exists, a duplicate, or a list that dropped a permanent entry. Rather than
 * render something broken, unknown ids are dropped and missing permanent ones are
 * appended, so every stored value resolves to a usable rail.
 */
export function resolveRail(stored: string[] | undefined): RailItem[] {
  const seen = new Set<string>();
  const items: RailItem[] = [];
  for (const id of stored ?? []) {
    const item = RAIL_BY_ID.get(id as RailItemId);
    if (!item || seen.has(id)) continue;
    seen.add(id);
    items.push(item);
  }
  // Nothing stored means the defaults, not an empty rail.
  if (items.length === 0) return DEFAULT_RAIL.map((id) => RAIL_BY_ID.get(id)!);
  for (const item of RAIL_ITEMS) {
    if (item.permanent && !seen.has(item.id)) items.push(item);
  }
  return items;
}
