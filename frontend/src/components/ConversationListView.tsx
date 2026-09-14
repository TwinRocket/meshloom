import { useMemo, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { Search, Hash, X } from 'lucide-react';
import type { Channel, Contact, Conversation, HealthStatus } from '../types';
import { ContactAvatar } from './ContactAvatar';
import { RadioStatusChip } from './RadioStatusChip';
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
  onNewMessage: () => void;
  health?: HealthStatus | null;
  onOpenRadioSettings?: () => void;
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

const FILTERS: { id: ConversationFilter; labelKey: string }[] = [
  { id: 'all', labelKey: 'conversationList.all' },
  { id: 'unread', labelKey: 'conversationList.unread' },
  { id: 'favorites', labelKey: 'conversationList.favorites' },
  { id: 'groups', labelKey: 'conversationList.groups' },
  { id: 'direct', labelKey: 'conversationList.direct' },
];

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
  onNewMessage,
  health,
  onOpenRadioSettings,
}: Props) {
  const { t, i18n } = useTranslation();
  const [query, setQuery] = useState('');
  const [filter, setFilter] = useState<ConversationFilter>('all');

  const rows = useMemo<Row[]>(() => {
    const channelRows: Row[] = channels.map((channel) => ({
      key: channel.key,
      kind: 'channel',
      name: channel.name,
      conversation: { type: 'channel', id: channel.key, name: channel.name },
      unread: unreadCounts[channel.key] ?? 0,
      mentioned: mentions[channel.key] === true,
      favorite: channel.favorite,
      lastAt: lastMessageTimes[channel.key] ?? 0,
      preview: lastMessagePreviews[channel.key] ?? '',
    }));

    const contactRows: Row[] = contacts.map((contact) => ({
      key: contact.public_key,
      kind: 'contact',
      name: contact.name ?? contact.public_key.slice(0, 12),
      conversation: {
        type: 'contact',
        id: contact.public_key,
        name: contact.name ?? contact.public_key.slice(0, 12),
      },
      unread: unreadCounts[contact.public_key] ?? 0,
      mentioned: mentions[contact.public_key] === true,
      favorite: contact.favorite,
      lastAt: lastMessageTimes[contact.public_key] ?? 0,
      preview: lastMessagePreviews[contact.public_key] ?? '',
      contact,
    }));

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

  const unreadTotal = useMemo(() => rows.filter((row) => row.unread > 0).length, [rows]);

  // Favourites get a row of their own at the top: the ones you reach for are
  // otherwise scattered down a list sorted by who happened to talk last, and
  // filtering to them is a round trip when all you wanted was one tap.
  const favourites = useMemo(() => rows.filter((row) => row.favorite), [rows]);
  const showFavourites = favourites.length > 0 && filter === 'all' && !query.trim();

  return (
    <div className="flex h-full min-h-0 flex-col">
      <div className="flex items-center gap-2 px-4 pb-2 pt-3">
        <h1 className="text-2xl font-semibold tracking-tight">{t('conversationList.title')}</h1>
        <RadioStatusChip
          health={health ?? null}
          onOpenRadioSettings={onOpenRadioSettings}
          className="ml-auto max-w-[9rem]"
        />
        <button
          type="button"
          onClick={onNewMessage}
          className="inline-flex h-9 items-center gap-1.5 rounded-full bg-primary px-3 text-sm font-medium text-primary-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
        >
          {t('conversationList.new')}
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
        className="flex gap-2 overflow-x-auto px-4 pb-2 [scrollbar-width:none] [&::-webkit-scrollbar]:hidden"
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
              {id === 'unread' && unreadTotal > 0 && (
                <span className="ml-1.5 tabular-nums">{unreadTotal}</span>
              )}
            </button>
          );
        })}
      </div>

      <div className="min-h-0 flex-1 overflow-y-auto">
        {showFavourites && (
          <div className="border-b border-border/60 pb-3 pt-1">
            <h2 className="px-4 pb-2 text-[0.6875rem] font-medium uppercase tracking-wider text-muted-foreground">
              {t('conversationList.favorites')}
            </h2>
            <ul className="flex gap-3 overflow-x-auto px-4 [scrollbar-width:none] [&::-webkit-scrollbar]:hidden">
              {favourites.map((row) => (
                <li key={`fav-${row.kind}-${row.key}`}>
                  <button
                    type="button"
                    onClick={() => onSelectConversation(row.conversation)}
                    className="flex w-16 flex-col items-center gap-1 rounded-lg p-1 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
                  >
                    <span className="relative">
                      {row.kind === 'channel' ? (
                        <span
                          className="flex h-12 w-12 items-center justify-center rounded-full bg-muted text-muted-foreground"
                          aria-hidden="true"
                        >
                          <Hash className="h-5 w-5" />
                        </span>
                      ) : (
                        <ContactAvatar
                          name={row.name}
                          publicKey={row.key}
                          size={48}
                          contactType={row.contact?.type}
                        />
                      )}
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
                  className="flex w-full items-center gap-3 px-4 py-2.5 text-left hover:bg-accent/40 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-ring"
                >
                  {row.kind === 'channel' ? (
                    <span
                      className="flex h-11 w-11 shrink-0 items-center justify-center rounded-full bg-muted text-muted-foreground"
                      aria-hidden="true"
                    >
                      <Hash className="h-5 w-5" />
                    </span>
                  ) : (
                    <ContactAvatar
                      name={row.name}
                      publicKey={row.key}
                      size={44}
                      contactType={row.contact?.type}
                    />
                  )}

                  <span className="flex min-w-0 flex-1 flex-col gap-0.5">
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
