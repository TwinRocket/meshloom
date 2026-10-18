import { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { useVirtualizer } from '@tanstack/react-virtual';
import { ArrowDown } from 'lucide-react';

import { formatApiError } from '../../api';
import {
  activateFeedStore,
  clearFeedRows,
  getFeedSnapshot,
  useFeedRows,
} from '../../stores/feedStore';
import type { Channel, Contact, Conversation, Message } from '../../types';
import { formatNumber } from '../../utils/formatNumber';
import {
  countHiddenRows,
  filtersToQuery,
  hasEmptySelection,
  loadFeedFilters,
  rowPassesFilters,
  saveFeedFilters,
  type FeedFilters,
} from '../../utils/feedFilters';
import type { SearchNavigateTarget } from '../SearchView';
import { Button } from '../ui/button';
import { ToolPaneHeader } from '../ToolPaneHeader';
import { FEED_DOM_MAX } from './feedConversation';
import { countArrivedSince, FOLLOW_THRESHOLD_PX, highestId } from './feedFollow';
import { buildFeedItems, createDayLabeler, type FeedItem } from './feedDays';
import {
  firstRowAfterMark,
  lastReachedMark,
  lastSeenOf,
  loadFeedLastSeen,
  saveFeedLastSeen,
} from './feedLastSeen';
import { pickAnchor, scrollShiftForAnchor, type ScrollAnchor } from './feedScroll';
import type { FeedRowActions } from './feedActions';
import { FeedFilterChips } from './FeedFilterChips';
import {
  fillHiddenPages,
  FEED_MAX_PAGES_PER_GESTURE,
  FEED_MIN_VISIBLE,
  loadFeedPage,
} from './feedHistory';
import { FeedMessageRow } from './FeedMessageRow';
import { createFeedNameResolver } from './feedNames';

interface FeedViewProps {
  contacts: Contact[];
  channels: Channel[];
  radioName?: string;
  onBackToTools?: () => void;
  /** Leaves the feed for the conversation, scrolled to (and highlighting) the message. */
  onNavigateToMessage?: (target: SearchNavigateTarget) => void;
  /** Leaves the feed for the conversation, at its bottom. */
  onSelectConversation?: (conversation: Conversation) => void;
  /** Opens the contact sheet beside the feed. */
  onOpenContactInfo?: (publicKey: string, fromChannel?: boolean) => void;
}

const ESTIMATED_ROW_HEIGHT = 56;
const ESTIMATED_DAY_HEIGHT = 32;
// A viewport that measures zero (hidden pane, jsdom) must still mount a screenful.
const FALLBACK_VIEWPORT_HEIGHT = 600;
/** Scrolling within this distance of the top asks for the previous page. */
const LOAD_OLDER_THRESHOLD_PX = 300;
/** A change of `type` / `outgoing` reloads after this pause, so rapid clicks send one request. */
const RELOAD_DEBOUNCE_MS = 250;
/** Timed mutes expire silently: rows are re-evaluated this often. */
const CLOCK_TICK_MS = 60_000;
/** Frames the pin is re-applied while row heights settle. */
const PIN_FRAMES = 12;

function useTimeFormatter(language: string) {
  return useMemo(() => {
    const time = new Intl.DateTimeFormat(language, {
      hour: '2-digit',
      minute: '2-digit',
      second: '2-digit',
    });
    const full = new Intl.DateTimeFormat(language, { dateStyle: 'medium', timeStyle: 'medium' });
    return (seconds: number) => ({
      short: time.format(seconds * 1000),
      full: full.format(seconds * 1000),
    });
  }, [language]);
}

/**
 * The global timeline: every channel and direct message, newest at the bottom.
 *
 * This is the only subscriber of `feedStore`. It fetches the newest page on mount,
 * pages backwards with the oldest held `(received_at, id)` as cursor, and mounts at
 * most `FEED_DOM_MAX` rows whatever the memory window holds. While the reader is
 * pinned to the bottom (follow mode) the view stays there; a scroll up turns follow
 * off and offers a button with the count of rows that arrived since.
 *
 * The filter chips are evaluated here by one function, `rowPassesFilters`, over the
 * rows the store holds. `type` and `outgoing` are also sent to the server so hidden
 * rows are not fetched; changing them empties the store and reloads the first page.
 */
export function FeedView({
  contacts,
  channels,
  radioName,
  onBackToTools,
  onNavigateToMessage,
  onSelectConversation,
  onOpenContactInfo,
}: FeedViewProps) {
  const { t, i18n } = useTranslation();
  const { rows, hasOlder, epoch } = useFeedRows();
  const formatRowTime = useTimeFormatter(i18n.language);
  const labelDay = useMemo(() => createDayLabeler(i18n.language), [i18n.language]);

  const [filters, setFilters] = useState<FeedFilters>(loadFeedFilters);
  const [nowSec, setNowSec] = useState(() => Math.floor(Date.now() / 1000));
  const [status, setStatus] = useState<'loading' | 'ready' | 'error'>('loading');
  const [loadError, setLoadError] = useState<string | null>(null);
  const [loadingOlder, setLoadingOlder] = useState(false);
  const [olderError, setOlderError] = useState<string | null>(null);
  const [following, setFollowing] = useState(true);
  const [baselineId, setBaselineId] = useState(0);
  const [scrollMargin, setScrollMargin] = useState(0);
  // The "where was I" mark is read once per visit: the separator then stays on the row it
  // named, whatever arrives and whatever the mark becomes when the view hides.
  const [lastSeenAtOpen] = useState(loadFeedLastSeen);
  const [newSinceId, setNewSinceId] = useState<number | null>(null);

  const scrollRef = useRef<HTMLDivElement | null>(null);
  const spacerRef = useRef<HTMLDivElement | null>(null);
  const followingRef = useRef(true);
  const loadingOlderRef = useRef(false);
  const hasOlderRef = useRef(hasOlder);
  const rowsRef = useRef<readonly Message[]>(rows);
  const visibleRowsRef = useRef<readonly Message[]>([]);
  const anchorRef = useRef<ScrollAnchor | null>(null);
  // Read on hide/unmount, when the scroller may already be detached.
  const viewportBottomRef = useRef(0);
  const itemsRef = useRef<FeedItem[]>([]);
  const measurementsRef = useRef<readonly { start: number }[]>([]);
  const knownMaxIdRef = useRef(0);
  const separatorDecidedRef = useRef(false);
  const epochRef = useRef(epoch);
  const tRef = useRef(t);
  const filtersRef = useRef(filters);
  const channelsRef = useRef(channels);
  const firstQueryRef = useRef(true);
  // True from a change of `type` / `outgoing` until the debounced reload empties the store:
  // the rows held still belong to the old query, so they are no cursor for the new one.
  const reloadPendingRef = useRef(false);
  const firstClientFilterRef = useRef(true);
  hasOlderRef.current = hasOlder;
  rowsRef.current = rows;
  tRef.current = t;
  filtersRef.current = filters;
  channelsRef.current = channels;

  // Preferences are per browser (`localStorage`); a second tab only sees them on reload.
  useEffect(() => saveFeedFilters(filters), [filters]);

  // Rows are re-evaluated once a minute so an expired timed mute reappears. A `channel`
  // frame reaches this view as a new `channels` prop, which the memo below depends on.
  useEffect(() => {
    const timer = setInterval(() => setNowSec(Math.floor(Date.now() / 1000)), CLOCK_TICK_MS);
    return () => clearInterval(timer);
  }, []);

  const emptySelection = hasEmptySelection(filters);
  const query = useMemo(() => filtersToQuery(filters), [filters]);
  const queryKey = emptySelection ? 'none' : JSON.stringify(query);

  const visibleRows = useMemo(
    () => rows.filter((row) => rowPassesFilters(row, filters, channels, nowSec)),
    [rows, filters, channels, nowSec]
  );
  visibleRowsRef.current = visibleRows;
  const hidden = useMemo(
    () => countHiddenRows(rows, filters, channels, nowSec),
    [rows, filters, channels, nowSec]
  );
  const items = useMemo(() => buildFeedItems(visibleRows, newSinceId), [visibleRows, newSinceId]);
  const resolveLabels = useMemo(
    () => createFeedNameResolver(channels, contacts),
    [channels, contacts]
  );

  /** Rows the current filters keep among those the store holds right now. */
  const countVisibleNow = useCallback(
    () =>
      getFeedSnapshot().rows.filter((row) =>
        rowPassesFilters(
          row,
          filtersRef.current,
          channelsRef.current,
          Math.floor(Date.now() / 1000)
        )
      ).length,
    []
  );

  const loadNewest = useCallback(
    async (serverQuery: ReturnType<typeof filtersToQuery>, signal?: AbortSignal) => {
      setStatus('loading');
      setLoadError(null);
      try {
        await loadFeedPage(null, signal, serverQuery);
        setStatus('ready');
        await fillHiddenPages(countVisibleNow, serverQuery, signal, FEED_MAX_PAGES_PER_GESTURE - 1);
      } catch (err) {
        if (signal?.aborted) return;
        setLoadError(formatApiError(err, tRef.current));
        setStatus('error');
      }
    },
    [countVisibleNow]
  );

  // First mount loads at once; a later change of `type` / `outgoing` waits out the
  // debounce, empties the store (its cursor belongs to the old query) and reloads.
  useEffect(() => {
    activateFeedStore();
    reloadPendingRef.current = false;
    if (queryKey === 'none') {
      setStatus('ready');
      return;
    }
    const controller = new AbortController();
    if (firstQueryRef.current) {
      firstQueryRef.current = false;
      void loadNewest(query, controller.signal);
      return () => controller.abort();
    }
    reloadPendingRef.current = true;
    const timer = setTimeout(() => {
      reloadPendingRef.current = false;
      clearFeedRows();
      void loadNewest(query, controller.signal);
    }, RELOAD_DEBOUNCE_MS);
    return () => {
      clearTimeout(timer);
      controller.abort();
    };
    // `query` is derived from `queryKey`.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [queryKey, loadNewest]);

  // The store was emptied by a reconciliation that could not catch up: fetch again.
  useEffect(() => {
    if (epochRef.current === epoch) return;
    epochRef.current = epoch;
    if (queryKey === 'none') return;
    void loadNewest(filtersToQuery(filtersRef.current));
    // `epoch` is the trigger; the rest is read through refs.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [epoch]);

  // Place the separator once, when the first page is in: later rows are live, not "since".
  useEffect(() => {
    if (separatorDecidedRef.current || status !== 'ready') return;
    separatorDecidedRef.current = true;
    setNewSinceId(firstRowAfterMark(visibleRowsRef.current, lastSeenAtOpen));
  }, [status, lastSeenAtOpen]);

  // Remember the newest row shown when the reader leaves, hides the tab or the page.
  useEffect(() => {
    const remember = () => {
      const mark = followingRef.current
        ? lastSeenOf(visibleRowsRef.current)
        : lastReachedMark(
            itemsRef.current,
            itemsRef.current.map((_, i) => measurementsRef.current[i]?.start ?? Infinity),
            viewportBottomRef.current
          );
      if (mark) saveFeedLastSeen(mark);
    };
    const onVisibility = () => {
      if (document.visibilityState === 'hidden') remember();
    };
    document.addEventListener('visibilitychange', onVisibility);
    return () => {
      document.removeEventListener('visibilitychange', onVisibility);
      remember();
    };
  }, []);

  const loadOlder = useCallback(async () => {
    if (loadingOlderRef.current || !hasOlderRef.current || reloadPendingRef.current) return;
    const oldest = rowsRef.current[0];
    if (!oldest) return;
    loadingOlderRef.current = true;
    setLoadingOlder(true);
    setOlderError(null);
    const serverQuery = filtersToQuery(filtersRef.current);
    try {
      await loadFeedPage(
        { received_at: oldest.received_at, id: oldest.id },
        undefined,
        serverQuery
      );
      await fillHiddenPages(
        countVisibleNow,
        serverQuery,
        undefined,
        FEED_MAX_PAGES_PER_GESTURE - 1
      );
    } catch (err) {
      setOlderError(formatApiError(err, tRef.current));
    } finally {
      loadingOlderRef.current = false;
      setLoadingOlder(false);
    }
  }, [countVisibleNow]);

  // Muted / pending are client-side: ticking them in place can leave a short list that
  // cannot be scrolled, so the same bounded fetch applies.
  const clientFilterKey = `${filters.includeMuted}|${filters.includePending}`;
  useEffect(() => {
    if (firstClientFilterRef.current) {
      firstClientFilterRef.current = false;
      return;
    }
    if (status === 'ready' && !hasEmptySelection(filtersRef.current)) void loadOlderIfHollow();
    // Only a change of these two chips is a gesture.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [clientFilterKey]);

  async function loadOlderIfHollow() {
    if (countVisibleNow() < FEED_MIN_VISIBLE) await loadOlder();
  }

  const virtualizer = useVirtualizer({
    count: items.length,
    getScrollElement: () => scrollRef.current,
    estimateSize: (index) =>
      items[index]?.kind === 'row' ? ESTIMATED_ROW_HEIGHT : ESTIMATED_DAY_HEIGHT,
    scrollMargin,
    getItemKey: (index) => items[index]?.key ?? `__idx:${index}`,
    overscan: 8,
    measureElement: (element) => element.getBoundingClientRect().height || ESTIMATED_ROW_HEIGHT,
    observeElementRect: (instance, cb) => {
      const element = instance.scrollElement;
      if (!element) return;
      const report = () => {
        const rect = element.getBoundingClientRect();
        cb({ width: rect.width, height: rect.height || FALLBACK_VIEWPORT_HEIGHT });
      };
      report();
      const observer = new ResizeObserver(report);
      observer.observe(element);
      return () => observer.disconnect();
    },
  });
  const totalSize = virtualizer.getTotalSize();
  itemsRef.current = items;
  measurementsRef.current = virtualizer.measurementsCache;
  const virtualItems = virtualizer.getVirtualItems();
  // Whatever the viewport size, never mount more than the DOM cap: keep the end the
  // reader is looking at (the newest while following, the oldest otherwise).
  const mounted =
    virtualItems.length > FEED_DOM_MAX
      ? following
        ? virtualItems.slice(-FEED_DOM_MAX)
        : virtualItems.slice(0, FEED_DOM_MAX)
      : virtualItems;

  // Rows start below the optional banner: measured from the spacer, not offsetTop.
  useLayoutEffect(() => {
    const spacer = spacerRef.current;
    const scroller = scrollRef.current;
    if (!spacer || !scroller) return;
    const next = Math.round(
      spacer.getBoundingClientRect().top - scroller.getBoundingClientRect().top + scroller.scrollTop
    );
    setScrollMargin((prev) => (prev === next ? prev : next));
  }, [loadingOlder, hasOlder, olderError, status]);

  const pinToBottom = useCallback(() => {
    const el = scrollRef.current;
    if (el) el.scrollTop = el.scrollHeight;
  }, []);

  // Rows landing above the reader (a prepended page, a historical decryption filling the
  // middle) must not move what they are looking at: hold the item at the top of the
  // viewport where it was. Following needs no hold, the pin takes over.
  useLayoutEffect(() => {
    const el = scrollRef.current;
    if (!el) return;
    if (!followingRef.current) {
      const shift = scrollShiftForAnchor(
        anchorRef.current,
        items.map((item, index) => ({
          key: item.key,
          start: virtualizer.measurementsCache[index]?.start ?? 0,
        }))
      );
      if (shift !== 0) el.scrollTop += shift;
    }
    anchorRef.current = pickAnchor(virtualizer.getVirtualItems(), el.scrollTop);
    viewportBottomRef.current = el.scrollTop + el.clientHeight;
  }, [items, totalSize, virtualizer]);

  // Follow mode: stay at the bottom as rows arrive and as their heights settle.
  useLayoutEffect(() => {
    if (!followingRef.current || status !== 'ready') return;
    pinToBottom();
    let frames = 0;
    let raf = 0;
    const tick = () => {
      if (!followingRef.current || frames++ >= PIN_FRAMES) return;
      pinToBottom();
      raf = requestAnimationFrame(tick);
    };
    raf = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(raf);
  }, [visibleRows.length, totalSize, status, pinToBottom]);

  const setFollow = useCallback((next: boolean) => {
    if (followingRef.current === next) return;
    followingRef.current = next;
    if (!next) setBaselineId(highestId(visibleRowsRef.current));
    setFollowing(next);
  }, []);

  const handleScroll = useCallback(() => {
    const el = scrollRef.current;
    if (!el) return;
    anchorRef.current = pickAnchor(virtualizer.getVirtualItems(), el.scrollTop);
    viewportBottomRef.current = el.scrollTop + el.clientHeight;
    const distance = el.scrollHeight - el.scrollTop - el.clientHeight;
    setFollow(distance <= FOLLOW_THRESHOLD_PX);
    if (el.scrollTop <= LOAD_OLDER_THRESHOLD_PX && status === 'ready') void loadOlder();
  }, [loadOlder, setFollow, status, virtualizer]);

  const handleFollowClick = useCallback(() => {
    setFollow(true);
    pinToBottom();
  }, [pinToBottom, setFollow]);

  // Like a conversation: what the reader sends brings them back to the bottom. Historical
  // rows (an old message with a new id) never do, whatever their direction.
  useEffect(() => {
    const previousMax = knownMaxIdRef.current;
    knownMaxIdRef.current = highestId(visibleRows);
    if (previousMax === 0 || followingRef.current) return;
    if (visibleRows.some((row) => row.id > previousMax && row.outgoing && !row.historical)) {
      handleFollowClick();
    }
  }, [visibleRows, handleFollowClick]);

  const arrived = useMemo(
    () => (following ? 0 : countArrivedSince(visibleRows, baselineId)),
    [following, visibleRows, baselineId]
  );

  // Without the three callbacks (a bare view) rows are read-only, no menu either.
  const actions = useMemo<FeedRowActions | undefined>(() => {
    if (!onNavigateToMessage || !onSelectConversation || !onOpenContactInfo) return undefined;
    return {
      onOpenMessage: (row, conversationName) =>
        onNavigateToMessage({
          id: row.id,
          type: row.type,
          conversation_key: row.conversation_key,
          conversation_name: conversationName,
        }),
      onOpenConversation: (row, conversationName) =>
        onSelectConversation({
          type: row.type === 'CHAN' ? 'channel' : 'contact',
          id: row.conversation_key,
          name: conversationName,
        }),
      onOpenContact: onOpenContactInfo,
    };
  }, [onNavigateToMessage, onSelectConversation, onOpenContactInfo]);

  const header = <ToolPaneHeader title={t('feed.title')} onBack={onBackToTools} />;
  const chips = <FeedFilterChips filters={filters} hidden={hidden} onChange={setFilters} />;

  if (status === 'loading' && rows.length === 0) {
    return (
      <>
        {header}
        {chips}
        <div
          role="status"
          className="flex flex-1 items-center justify-center text-muted-foreground"
        >
          {t('conversation.loadingFeed')}
        </div>
      </>
    );
  }

  if (status === 'error' && rows.length === 0) {
    return (
      <>
        {header}
        {chips}
        <div
          role="alert"
          className="flex flex-1 flex-col items-center justify-center gap-3 p-4 text-sm text-muted-foreground"
        >
          <p>{t('feed.loadError', { error: loadError })}</p>
          <Button size="sm" onClick={() => void loadNewest(query)}>
            {t('feed.retry')}
          </Button>
        </div>
      </>
    );
  }

  if (visibleRows.length === 0) {
    return (
      <>
        {header}
        {chips}
        {loadingOlder || status === 'loading' ? (
          <div
            role="status"
            className="flex flex-1 items-center justify-center text-muted-foreground"
          >
            {t('feed.loadingOlder')}
          </div>
        ) : (
          <div className="flex flex-1 items-center justify-center p-4 text-center text-muted-foreground">
            {rows.length === 0 && !emptySelection ? t('feed.empty') : t('feed.emptyFiltered')}
          </div>
        )}
      </>
    );
  }

  return (
    <>
      {header}
      {chips}
      <div className="relative flex min-h-0 flex-1 flex-col">
        <div
          ref={scrollRef}
          onScroll={handleScroll}
          className="min-h-0 flex-1 overflow-y-auto px-4 py-2"
        >
          {loadingOlder ? (
            <div role="status" className="py-2 text-center text-xs text-muted-foreground">
              {t('feed.loadingOlder')}
            </div>
          ) : olderError ? (
            <div
              role="alert"
              className="flex items-center justify-center gap-2 py-2 text-xs text-muted-foreground"
            >
              <span>{t('feed.loadOlderError', { error: olderError })}</span>
              <Button size="sm" variant="outline" onClick={() => void loadOlder()}>
                {t('feed.retry')}
              </Button>
            </div>
          ) : !hasOlder ? (
            <div className="py-2 text-center text-xs text-muted-foreground">
              {t('feed.beginning')}
            </div>
          ) : null}
          <div
            ref={spacerRef}
            role="list"
            aria-label={t('feed.listLabel')}
            style={{ height: totalSize, position: 'relative' }}
          >
            {mounted.map((item) => {
              const entry = items[item.index];
              if (!entry) return null;
              const position = {
                transform: `translateY(${item.start - scrollMargin}px)`,
              };
              if (entry.kind === 'new') {
                return (
                  <div
                    key={item.key}
                    ref={virtualizer.measureElement}
                    data-index={item.index}
                    role="separator"
                    aria-label={t('feed.newSinceLastVisit')}
                    className="absolute left-0 top-0 flex w-full items-center gap-2 py-1 text-xs font-medium text-primary"
                    style={position}
                  >
                    <span className="h-px flex-1 bg-primary/40" />
                    <span>{t('feed.newSinceLastVisit')}</span>
                    <span className="h-px flex-1 bg-primary/40" />
                  </div>
                );
              }
              if (entry.kind === 'day') {
                return (
                  <div
                    key={item.key}
                    ref={virtualizer.measureElement}
                    data-index={item.index}
                    role="presentation"
                    className="absolute left-0 top-0 w-full pt-3 pb-1"
                    style={position}
                  >
                    <h3 className="text-center text-xs font-medium text-muted-foreground">
                      {labelDay(entry.at, nowSec)}
                    </h3>
                  </div>
                );
              }
              return (
                <FeedMessageRow
                  key={item.key}
                  measureRef={virtualizer.measureElement}
                  index={item.index}
                  offset={item.start - scrollMargin}
                  row={entry.row}
                  labels={resolveLabels(entry.row)}
                  time={formatRowTime(entry.row.received_at)}
                  radioName={radioName}
                  actions={actions}
                />
              );
            })}
          </div>
        </div>
        {!following && (
          <Button
            type="button"
            size="sm"
            onClick={handleFollowClick}
            className="absolute bottom-3 right-4 shadow-md"
          >
            <ArrowDown className="h-4 w-4" aria-hidden="true" />
            {arrived > 0
              ? t('feed.followNew', { count: arrived, formatted: formatNumber(arrived) })
              : t('feed.follow')}
          </Button>
        )}
      </div>
    </>
  );
}
