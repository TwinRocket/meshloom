import { MessageCircle, Map as MapIcon, LayoutGrid, Settings } from 'lucide-react';

/**
 * The app's primary destinations, shared by the phone's bar and the desktop rail.
 *
 * One list rather than two: the two navigations differ in shape, not in where they
 * can go, and a destination added to one and forgotten in the other is exactly the
 * kind of drift that left the live map reachable by URL and by nothing else.
 */

export type BottomNavTarget = 'conversations' | 'map' | 'tools' | 'settings';

export const NAV_ITEMS: {
  target: BottomNavTarget;
  labelKey: string;
  Icon: typeof MessageCircle;
}[] = [
  { target: 'conversations', labelKey: 'bottomNav.conversations', Icon: MessageCircle },
  // Everything that is not a conversation or the map lives behind one entry rather
  // than competing for a slot: the feed, the visualiser, trace, locate, search.
  { target: 'map', labelKey: 'bottomNav.map', Icon: MapIcon },
  { target: 'tools', labelKey: 'bottomNav.tools', Icon: LayoutGrid },
  { target: 'settings', labelKey: 'bottomNav.settings', Icon: Settings },
];

/** Past this the badge stops being a count and becomes "a lot". */
export const UNREAD_BADGE_MAX = 99;
