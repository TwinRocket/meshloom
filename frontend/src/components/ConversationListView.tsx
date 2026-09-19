import { useMemo, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { ChevronDown, ChevronRight, PanelLeftClose, Search, Star, X, Plus } from 'lucide-react';
import type { Channel, Contact, Conversation, HealthStatus } from '../types';
import { ContactAvatar } from './ContactAvatar';
import { RadioStatusChip } from './RadioStatusChip';
import { getStateKey } from '../utils/conversationState';
import { describeMessagePreview } from '../utils/messagePreview';
import { countUnreadConversations } from '../utils/unreadConversations';
import { adoptedChannels } from '../utils/channelMembership';
import { cn } from '../lib/utils';

/**
 * The conversation list, as a screen rather than a drawer.
 *
 * On a phone the drawer was the only way to reach a conversation: a panel over the
 * app, opened from a control in the corner, closing over whatever you had been
 * reading. Channels and direct messages were also two separate sections, so "what
 * happened since I last looked" meant checking two lists and comparing timestamps
 * by hand.
 *
 * One list, newest first, whatever kind of conversation it is — filters and search
 * narrow it instead of the structure doing it permanently.
 */

export type ConversationFilter = 'all' | 'unread' | 'favorites' | 'groups' | 'direct';

interface Props {
  contacts: Contact[];
  channels: Channel[];
  unreadCounts: Record<string, number>;
  mentions: Record<string, boolean>;
  lastMessageTimes: Record<string, number>;
  lastMessagePreviews: Record<string, string>;
  onSelectConversation: (conversation: Conversation) => void;
  /** The conversation open beside this list. Desktop only. */
  activeConversation?: Conversation | null;
  onNewMessage: () => void;
  health?: HealthStatus | null;
  /** Opens the radio read-out. The dot means the same thing on every screen. */
  onOpenRadioStatus?: () => void;
  /** Pulse the header pip when a newer Meshloom release is published. */
  updateAvailable?: boolean;
  /** Desktop: fold the list column so the conversation can use the width. */
  onCollapseList?: () => void;
}

interface Row {
  key: string;
  kind: 'channel' | 'contact';
  name: string;
  conversation: Conversation;
  unread: number;
  mentioned: boolean;
  favorite: boolean;
  lastAt: number;
  preview: string;
  contact?: Contact;
}

const FAVORITES_COLLAPSED_KEY = 'meshloom-conversation-favorites-collapsed';

function readFavoritesCollapsed(): boolean {
  try {
    return localStorage.getItem(FAVORITES_COLLAPSED_KEY) === '1';
  } catch {
    return false;
  }
}

function writeFavoritesCollapsed(collapsed: boolean): void {
  try {
    if (collapsed) {
      localStorage.setItem(FAVORITES_COLLAPSED_KEY, '1');
    } else {
      localStorage.removeItem(FAVORITES_COLLAPSED_KEY);
    }
  } catch {
    // localStorage may be unavailable
  }
}

const FILTERS: { id: ConversationFilter; labelKey: string }[] = [
  { id: 'all', labelKey: 'conversationList.all' },
  { id: 'unread', labelKey: 'conversationList.unread' },
  { id: 'favorites', labelKey: 'conversationList.favorites' },
  { id: 'groups', labelKey: 'conversationList.groups' },
  { id: 'direct', labelKey: 'conversationList.direct' },
];

function ConversationAvatar({
  row,
  size,
  showFavoriteMark = false,
}: {
  row: Row;
  size: number;
  showFavoriteMark?: boolean;
}) {
  return (
    <span className="relative inline-flex shrink-0">
      <ContactAvatar
        name={row.name}
        publicKey={row.key}
        size={size}
        contactType={row.contact?.type}
      />
      {showFavoriteMark && row.favorite && (
        <Star
          data-testid="favorite-mark"
          className="pointer-events-none absolute bottom-0.5 left-0.5 h-3.5 w-3.5 fill-[hsl(var(--favorite))] text-[hsl(var(--favorite))] drop-shadow-[0_0_1px_hsl(var(--background))]"
          strokeWidth={1.25}
          aria-hidden="true"
        />
      )}
    </span>
  );
}

/** Same day shows a time, anything older shows a date — a date at 14:32 says nothing. */
function formatWhen(seconds: number, locale: string): string {
  if (!seconds) return '';
  const date = new Date(seconds * 1000);
  const now = new Date();
  const sameDay =
    date.getDate() === now.getDate() &&
    date.getMonth() === now.getMonth() &&
    date.getFullYear() === now.getFullYear();
  return sameDay
    ? date.toLocaleTimeString(locale, { hour: '2-digit', minute: '2-digit' })
    : date.toLocaleDateString(locale, { day: '2-digit', month: '2-digit' });
}

export function ConversationListView({
  contacts,
  channels,
  unreadCounts,
  mentions,
  lastMessageTimes,
  lastMessagePreviews,
  onSelectConversation,
  activeConversation,
  onNewMessage,
  health,
  onOpenRadioStatus,
  updateAvailable,
  onCollapseList,
}: Props) {
  const { t, i18n } = useTranslation();
  const [query, setQuery] = useState('');
  const [filter, setFilter] = useState<ConversationFilter>('all');

  // Which row the pane beside this list is showing. Nothing is open on a phone
  // while the list is the screen, so this simply never matches there.
  const isOpen = (row: Row) =>
    activeConversation?.type === row.conversation.type && activeConversation?.id === row.key;

  const rows = useMemo<Row[]>(() => {
    // Every one of these maps is keyed by the conversation's state key, not by the
    // channel key or the public key. Reading them raw looked like a list where
    // nothing had ever been said: no previews, no times, no unread badges, and an
    // "unread" filter that was always empty while the bar counted four.
    const channelRows: Row[] = adoptedChannels(channels).map((channel) => {
      const stateKey = getStateKey('channel', channel.key);
      return {
        key: channel.key,
        kind: 'channel',
        name: channel.name,
        conversation: { type: 'channel', id: channel.key, name: channel.name },
        unread: unreadCounts[stateKey] ?? 0,
        mentioned: mentions[stateKey] === true,
        favorite: channel.favorite,
        lastAt: lastMessageTimes[stateKey] ?? 0,
        preview: describeMessagePreview(lastMessagePreviews[stateKey] ?? ''),
      };
    });

    const contactRows: Row[] = contacts.map((contact) => {
      const stateKey = getStateKey('contact', contact.public_key);
      const name = contact.name ?? contact.public_key.slice(0, 12);
      return {
        key: contact.public_key,
        kind: 'contact',
        name,
        conversation: { type: 'contact', id: contact.public_key, name },
        unread: unreadCounts[stateKey] ?? 0,
        mentioned: mentions[stateKey] === true,
        favorite: contact.favorite,
        lastAt: lastMessageTimes[stateKey] ?? 0,
        preview: describeMessagePreview(lastMessagePreviews[stateKey] ?? ''),
        contact,
      };
    });

    // Newest first, and conversations nobody has written in yet fall to the bottom
    // in name order rather than in whatever order the API returned them.
    return [...channelRows, ...contactRows].sort((a, b) => {
      if (a.lastAt !== b.lastAt) return b.lastAt - a.lastAt;
      return a.name.localeCompare(b.name);
    });
  }, [channels, contacts, unreadCounts, mentions, lastMessageTimes, lastMessagePreviews]);

  const visible = useMemo(() => {
    const needle = query.trim().toLowerCase();
    return rows.filter((row) => {
      if (filter === 'unread' && row.unread === 0) return false;
      if (filter === 'favorites' && !row.favorite) return false;
      if (filter === 'groups' && row.kind !== 'channel') return false;
      if (filter === 'direct' && row.kind !== 'contact') return false;
      if (!needle) return true;
      return (
        row.name.toLowerCase().includes(needle) ||
        row.preview.toLowerCase().includes(needle) ||
        row.key.toLowerCase().includes(needle)
      );
    });
  }, [rows, filter, query]);

  // The same count the bar and the rail show, from the same function: three
  // places asking one question rather than three.
  const unreadTotal = useMemo(
    () => countUnreadConversations(channels, contacts, unreadCounts),
    [channels, contacts, unreadCounts]
  );

  // How many conversations each filter would leave. Shown on the chips that answer
  // a question — how many are unread, how many groups — rather than on "all", where
  // the number is just the length of the list underneath it.
  const filterCounts = useMemo(
    () => ({
      all: 0,
      unread: unreadTotal,
      favorites: rows.filter((row) => row.favorite).length,
      groups: rows.filter((row) => row.kind === 'channel').length,
      direct: rows.filter((row) => row.kind === 'contact').length,
    }),
    [rows, unreadTotal]
  );

  // Favourites get a row of their own at the top: the ones you reach for are
  // otherwise scattered down a list sorted by who happened to talk last, and
  // filtering to them is a round trip when all you wanted was one tap.
  const favourites = useMemo(() => rows.filter((row) => row.favorite), [rows]);
  const showFavourites = favourites.length > 0 && filter === 'all' && !query.trim();
  const [favoritesCollapsed, setFavoritesCollapsed] = useState(readFavoritesCollapsed);

  const toggleFavoritesCollapsed = () => {
    setFavoritesCollapsed((current) => {
      const next = !current;
      writeFavoritesCollapsed(next);
      return next;
    });
  };

  return (
    <div className="conversation-list-pane flex h-full min-h-0 flex-col">
      {/* Opaque, and standing clear of the top edge. Installed, iOS treats the strip
          under the status bar as its own: content that reaches into it is blurred
          there, which turned the title and the status into something that looked
          out of focus. A solid surface gives that treatment a flat colour to work
          on, and the breathing room keeps the text itself out of the strip. */}
      <div className="shrink-0 bg-background pt-5 md:pt-2">
        <div className="flex items-center gap-1.5 px-4 pb-2 pt-3">
          {onCollapseList && (
            <button
              type="button"
              onClick={onCollapseList}
              aria-label={t('conversationList.collapseList')}
              className="-ml-1.5 hidden h-8 w-8 shrink-0 items-center justify-center rounded-md text-muted-foreground transition-colors hover:bg-accent/60 hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring md:inline-flex"
            >
              <PanelLeftClose className="h-4 w-4" aria-hidden="true" />
            </button>
          )}
          <h1 className="min-w-0 truncate text-2xl font-semibold tracking-tight">
            {t('conversationList.title')}
          </h1>
          {/* Phone-only: the desktop rail already carries this status, and a second
              pill here sat between the title and New with nothing to say that the
              rail tile does not. */}
          <RadioStatusChip
            health={health ?? null}
            onOpenStatus={onOpenRadioStatus}
            updateAvailable={updateAvailable}
            className="ml-auto max-w-[9rem] md:hidden"
          />
          {/* Both halves are always rendered and the platform stylesheet picks:
              a labelled pill in the header here, a floating action button with the
              icon alone on Android, where that is where the primary action of a
              list lives. Marked rather than branched, so the choice stays in CSS. */}
          <button
            type="button"
            onClick={onNewMessage}
            data-compose-action=""
            aria-label={t('conversationList.new')}
            className="inline-flex h-9 shrink-0 items-center gap-1.5 rounded-full bg-primary px-3 text-sm font-medium text-primary-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring md:ml-auto"
          >
            <Plus className="hidden h-5 w-5" aria-hidden="true" data-compose-icon="" />
            <span data-compose-label="">{t('conversationList.new')}</span>
          </button>
        </div>

        <div className="px-4 pb-2">
          <div className="relative">
            <Search
              className="pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-muted-foreground"
              aria-hidden="true"
            />
            <input
              type="text"
              value={query}
              onChange={(event) => setQuery(event.target.value)}
              placeholder={t('conversationList.searchPlaceholder')}
              aria-label={t('conversationList.searchPlaceholder')}
              className="h-10 w-full rounded-full border border-border bg-muted/40 pl-9 pr-9 text-sm focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
            />
            {query && (
              <button
                type="button"
                onClick={() => setQuery('')}
                aria-label={t('conversationList.clearSearch')}
                className="absolute right-2 top-1/2 inline-flex h-7 w-7 -translate-y-1/2 items-center justify-center rounded-full text-muted-foreground hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
              >
                <X className="h-4 w-4" aria-hidden="true" />
              </button>
            )}
          </div>
        </div>

        <div
          className="flex gap-2 overflow-x-auto px-4 pb-2 [scrollbar-width:none] [&::-webkit-scrollbar]:hidden md:flex-wrap md:overflow-visible"
          role="group"
          aria-label={t('conversationList.filterLabel')}
        >
          {FILTERS.map(({ id, labelKey }) => {
            const active = filter === id;
            return (
              <button
                key={id}
                type="button"
                onClick={() => setFilter(id)}
                aria-pressed={active}
                className={cn(
                  'shrink-0 rounded-full border px-3 py-1.5 text-sm transition-colors',
                  'focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring',
                  active
                    ? 'border-primary/50 bg-primary/15 text-primary'
                    : 'border-border text-muted-foreground'
                )}
              >
                {t(labelKey)}
                {filterCounts[id] > 0 && (
                  <span className="ml-1.5 tabular-nums">{filterCounts[id]}</span>
                )}
              </button>
            );
          })}
        </div>
      </div>

      <div className="min-h-0 flex-1 overflow-y-auto">
        {showFavourites && (
          <div className={cn('border-b border-border/25 pt-1', !favoritesCollapsed && 'pb-3')}>
            <button
              type="button"
              className="flex w-full items-center gap-2 px-4 pb-2 text-left focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-ring"
              aria-expanded={!favoritesCollapsed}
              aria-label={
                favoritesCollapsed
                  ? t('sidebar.expandSection', { title: t('conversationList.favorites') })
                  : t('sidebar.collapseSection', { title: t('conversationList.favorites') })
              }
              onClick={toggleFavoritesCollapsed}
            >
              {favoritesCollapsed ? (
                <ChevronRight
                  className="h-3 w-3 shrink-0 text-muted-foreground"
                  aria-hidden="true"
                />
              ) : (
                <ChevronDown
                  className="h-3 w-3 shrink-0 text-muted-foreground"
                  aria-hidden="true"
                />
              )}
              <h2 className="text-[0.6875rem] font-medium uppercase tracking-wider text-muted-foreground">
                {t('conversationList.favorites')}
              </h2>
              <span className="ml-auto text-[0.6875rem] tabular-nums text-muted-foreground">
                {favourites.length}
              </span>
            </button>
            {!favoritesCollapsed && (
              <ul
                className="conversation-favorites-grid grid gap-2 px-4"
                aria-label={t('conversationList.favorites')}
              >
                {favourites.map((row) => (
                  <li key={`fav-${row.kind}-${row.key}`}>
                    <button
                      type="button"
                      onClick={() => onSelectConversation(row.conversation)}
                      className="flex w-full flex-col items-center gap-1 rounded-lg p-1 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
                    >
                      <span className="relative">
                        <ConversationAvatar row={row} size={48} />
                        {row.unread > 0 && (
                          <span
                            className="absolute -right-1 -top-1 min-w-[1.125rem] rounded-full bg-primary px-1 py-px text-center text-[0.625rem] font-semibold text-primary-foreground"
                            aria-hidden="true"
                          >
                            {row.unread > 99 ? '99+' : row.unread}
                          </span>
                        )}
                      </span>
                      <span className="w-full truncate text-center text-[0.6875rem] text-muted-foreground">
                        {row.name}
                      </span>
                    </button>
                  </li>
                ))}
              </ul>
            )}
          </div>
        )}

        {visible.length === 0 ? (
          <p className="px-4 py-10 text-center text-sm text-muted-foreground">
            {query.trim()
              ? t('conversationList.noMatch', { query: query.trim() })
              : t('conversationList.empty')}
          </p>
        ) : (
          <ul>
            {visible.map((row) => (
              <li key={`${row.kind}-${row.key}`}>
                <button
                  type="button"
                  onClick={() => onSelectConversation(row.conversation)}
                  aria-current={isOpen(row) ? 'page' : undefined}
                  className={cn(
                    'flex w-full items-center gap-3 px-4 py-2.5 text-left focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-ring',
                    isOpen(row) ? 'bg-accent/60' : 'hover:bg-accent/40'
                  )}
                >
                  <ConversationAvatar row={row} size={44} showFavoriteMark />

                  {/* The rule starts after the avatar rather than spanning the
                      screen: a full-width line reads as a division of the page, an
                      inset one as a gap between two rows of the same list. */}
                  <span className="flex min-w-0 flex-1 flex-col gap-0.5 border-b border-border/20 pb-2.5">
                    <span className="flex items-baseline gap-2">
                      <span className="truncate font-medium">{row.name}</span>
                      <span className="ml-auto shrink-0 text-[0.6875rem] text-muted-foreground">
                        {formatWhen(row.lastAt, i18n.language)}
                      </span>
                    </span>
                    <span className="flex items-center gap-2">
                      <span className="truncate text-sm text-muted-foreground">
                        {row.preview || t('conversationList.noMessages')}
                      </span>
                      {row.unread > 0 && (
                        <span
                          className={cn(
                            'ml-auto shrink-0 rounded-full px-1.5 py-0.5 text-[0.6875rem] font-semibold tabular-nums',
                            row.mentioned
                              ? 'bg-[hsl(var(--badge-mention))] text-primary-foreground'
                              : 'bg-primary text-primary-foreground'
                          )}
                        >
                          {row.unread > 99 ? '99+' : row.unread}
                        </span>
                      )}
                    </span>
                  </span>
                  {row.favorite && (
                    <span className="sr-only">{t('conversationList.favorite')}</span>
                  )}
                  {row.unread > 0 && (
                    <span className="sr-only">
                      {t('conversationList.unreadCount', { count: row.unread })}
                    </span>
                  )}
                </button>
              </li>
            ))}
          </ul>
        )}
      </div>
    </div>
  );
}
