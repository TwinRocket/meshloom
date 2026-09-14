import {
  MessageCircle,
  Map as MapIcon,
  LayoutGrid,
  Settings,
  List,
  CloudRain,
  Waypoints,
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

export const RAIL_ITEMS: RailItem[] = [
  ...NAV_ITEMS.map(({ target, labelKey, Icon }) => ({
    id: target as RailItemId,
    labelKey,
    Icon,
    permanent: true,
  })),
  tool('raw', 'sidebar.packetFeed', List),
  tool('live', 'sidebar.live', CloudRain),
  tool('visualizer', 'sidebar.meshVisualizer', Waypoints),
  tool('trace', 'sidebar.trace', Waypoints),
  tool('locate', 'locate.title', Crosshair),
  tool('search', 'sidebar.messageSearch', Search),
];

const RAIL_BY_ID = new Map(RAIL_ITEMS.map((item) => [item.id, item]));

export const DEFAULT_RAIL: RailItemId[] = NAV_ITEMS.map(({ target }) => target);

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
  if (items.length === 0) return RAIL_ITEMS.filter((item) => item.permanent);
  for (const item of RAIL_ITEMS) {
    if (item.permanent && !seen.has(item.id)) items.push(item);
  }
  return items;
}
