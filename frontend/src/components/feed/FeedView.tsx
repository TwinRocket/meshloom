import {
  useCallback,
  useEffect,
  useLayoutEffect,
  useMemo,
  useRef,
  useState,
  type ReactNode,
} from 'react';
import { useTranslation } from 'react-i18next';
import { useVirtualizer } from '@tanstack/react-virtual';
import { ArrowDown, Monitor } from 'lucide-react';

import { ApiError, formatApiError } from '../../api';
import { useCapabilities } from '../../auth/SessionProvider';
import {
  activateFeedStore,
  clearFeedRows,
  getFeedSnapshot,
  setFeedAcceptsPrivate,
  useFeedRows,
} from '../../stores/feedStore';
import type { Channel, Contact, Conversation, Message, RawPacket } from '../../types';
import { buildJournalEntries, type JournalEntry } from '../../utils/controlJournal';
import { formatNumber } from '../../utils/formatNumber';
import {
  applyFeedQuery,
  countHiddenRows,
  controlPreset,
  feedQueryOf,
  parseFeedQuery,
  filtersToQuery,
  hasEmptySelection,
  hasNoMessages,
  loadFeedFilters,
  rowPassesFilters,
  saveFeedFilters,
  withoutPrivate,
  type FeedFilters,
} from '../../utils/feedFilters';
import type { SearchNavigateTarget } from '../SearchView';
import { RawPacketInspectorDialog } from '../RawPacketDetailModal';
import { Button } from '../ui/button';
import { ToolPaneHeader } from '../ToolPaneHeader';
import { replaceFeedHash } from '../../utils/urlHash';
import { saveLastViewedConversation } from '../../utils/lastViewedConversation';
import { FEED_DOM_MAX, makeFeedConversation } from './feedConversation';
import {
  countArrivedSince,
  countControlArrivedSince,
  FOLLOW_THRESHOLD_PX,
  highestId,
} from './feedFollow';
import { buildFeedNodes, visibleControlEntries } from './feedControl';
import { FeedControlRow, type FeedControlActions } from './FeedControlRow';
import { FeedNodesFacet } from './FeedNodesFacet';
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
import { FeedDisplayBar } from './FeedDisplayBar';
import { displayFilters, enterFullscreen, leaveFullscreen, useDisplayChrome } from './feedDisplay';
import {
  fillHiddenPages,
  FEED_MAX_PAGES_PER_GESTURE,
  FEED_MIN_VISIBLE,
  loadFeedPage,
} from './feedHistory';
import { FeedMessageRow } from './FeedMessageRow';
import { createFeedNameResolver } from './feedNames';

interface FeedViewProps {
  /** Opens with the Control preset's filters, which are not persisted. */
  preset?: 'control';
  /** The filters the URL hash pinned (`feedQuery`): laid over the stored ones when it opens. */
  initialQuery?: string;
  /** Reports the query of the filters in use (hash grammar, no `?`) after each change. */
  onQueryChange?: (feedQuery: string) => void;
  contacts: Contact[];
  channels: Channel[];
  radioName?: string;
  /** Our public key, to recognise "us" in control packets. */
  publicKey?: string;
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
/** How long the twin of a "Paired" badge stays highlighted after the scroll. */
const HIGHLIGHT_MS = 2000;

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
  preset,
  initialQuery,
  onQueryChange,
  contacts,
  channels,
  radioName,
  publicKey,
  onBackToTools,
  onNavigateToMessage,
  onSelectConversation,
  onOpenContactInfo,
}: FeedViewProps) {
  const { t, i18n } = useTranslation();
  const { rows, controlPackets, hasOlder, epoch } = useFeedRows();
  const formatRowTime = useTimeFormatter(i18n.language);
  const labelDay = useMemo(() => createDayLabeler(i18n.language), [i18n.language]);

  // The session's capability, never a role name: without `read_private` (the kiosk role) the
  // feed offers no Direct messages chip, asks the server for channels only and drops any DM
  // frame. The server refuses DMs anyway; this only stops the feed from offering what fails.
  const { canReadPrivate } = useCapabilities();
  const canReadPrivateRef = useRef(canReadPrivate);
  canReadPrivateRef.current = canReadPrivate;
  // An effect, declared before the one that attaches the store. It is not reset on unmount: the
  // store stays attached and keeps receiving frames, which must still obey this session.
  useEffect(() => {
    setFeedAcceptsPrivate(canReadPrivate);
  }, [canReadPrivate]);
  // What storage held for `chan` and `priv`: a session without the capability overrides them in
  // memory but never writes the override back, so the same browser keeps its choice for a fuller
  // session.
  const storedTypesRef = useRef<Pick<FeedFilters, 'chan' | 'priv'> | null>(null);
  // URL > localStorage > defaults, decided before the first request so one fetch is made.
  const startFilters = useCallback(
    (query: string | undefined): FeedFilters => {
      const base = preset === 'control' ? controlPreset : loadFeedFilters();
      if (storedTypesRef.current === null) {
        storedTypesRef.current = { chan: base.chan, priv: base.priv };
      }
      const pinned = applyFeedQuery(base, query);
      return canReadPrivateRef.current ? pinned : withoutPrivate(pinned);
    },
    [preset]
  );
  const [filters, setFilters] = useState<FeedFilters>(() => startFilters(initialQuery));
  // Display mode: a layer over `filters` (see `feedDisplay.ts`), so leaving it restores them.
  // It is channels only, whatever the URL says: nothing in the mode turns DMs or media back on.
  const [display, setDisplay] = useState(() =>
    initialQuery ? parseFeedQuery(initialQuery).display : false
  );
  useDisplayChrome(display);
  const [nowSec, setNowSec] = useState(() => Math.floor(Date.now() / 1000));
  const [status, setStatus] = useState<'loading' | 'ready' | 'error'>('loading');
  const [loadError, setLoadError] = useState<string | null>(null);
  const [forbidden, setForbidden] = useState(false);
  const [loadingOlder, setLoadingOlder] = useState(false);
  const [olderError, setOlderError] = useState<string | null>(null);
  const [following, setFollowing] = useState(true);
  const [baselineId, setBaselineId] = useState(0);
  const [baselineMs, setBaselineMs] = useState(0);
  const [inspected, setInspected] = useState<RawPacket | null>(null);
  const [highlightKey, setHighlightKey] = useState<string | null>(null);
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
  const visibleControlRef = useRef<readonly JournalEntry[]>([]);
  const highlightTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
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
  const displayRef = useRef(display);
  displayRef.current = display;
  const channelsRef = useRef(channels);
  const firstQueryRef = useRef(true);
  // True from a change of `type` / `outgoing` until the debounced reload empties the store:
  // the rows held still belong to the old query, so they are no cursor for the new one.
  const reloadPendingRef = useRef(false);
  const firstClientFilterRef = useRef(true);
  hasOlderRef.current = hasOlder;
  rowsRef.current = rows;
  tRef.current = t;
  channelsRef.current = channels;

  // The query this view last wrote or opened with. A different `initialQuery` is then a change
  // from outside (back/forward, a pasted bookmark in the same tab) and replaces the filters.
  const skipFirstSaveRef = useRef(Boolean(initialQuery) || !canReadPrivate);
  const firstUrlSyncRef = useRef(true);
  const ownQueryRef = useRef(initialQuery ?? '');
  const onQueryChangeRef = useRef(onQueryChange);
  onQueryChangeRef.current = onQueryChange;
  useEffect(() => {
    const incoming = initialQuery ?? '';
    if (incoming === ownQueryRef.current) return;
    ownQueryRef.current = incoming;
    // The URL already shows this state and the pinned filters are not the reader's choice.
    skipFirstSaveRef.current = true;
    firstUrlSyncRef.current = true;
    setDisplay(parseFeedQuery(incoming).display);
    setFilters(startFilters(incoming));
  }, [initialQuery, preset, startFilters]);

  // Preferences are per browser (`localStorage`); a second tab only sees them on reload.
  // A preset's filters are not the reader's feed filters: they are never written back. Filters
  // the URL pinned are not the reader's choice either: opening a bookmark must not replace what
  // they remembered, so the first write is skipped when the URL named any (URL > storage).
  useEffect(() => {
    if (skipFirstSaveRef.current) {
      skipFirstSaveRef.current = false;
      return;
    }
    if (preset) return;
    saveFeedFilters(canReadPrivate ? filters : { ...filters, ...storedTypesRef.current });
  }, [filters, preset, canReadPrivate]);

  // The URL follows the filters (no history entry), so any state is bookmarkable. The first
  // run only mirrors what the URL already pinned: a plain `#feed` is not rewritten.
  useEffect(() => {
    if (firstUrlSyncRef.current) {
      firstUrlSyncRef.current = false;
      return;
    }
    // In display mode the URL says what the screen shows (`types=chan`, never a DM type),
    // so a reload of the bookmark opens the same screen.
    const feedQuery = display
      ? feedQueryOf(displayFilters(filters), true)
      : feedQueryOf(filters, false);
    ownQueryRef.current = feedQuery;
    replaceFeedHash(preset, feedQuery);
    saveLastViewedConversation(
      makeFeedConversation(tRef.current('sidebar.feed'), preset, feedQuery)
    );
    onQueryChangeRef.current?.(feedQuery);
  }, [filters, display, preset]);

  // Rows are re-evaluated once a minute so an expired timed mute reappears. A `channel`
  // frame reaches this view as a new `channels` prop, which the memo below depends on.
  useEffect(() => {
    const timer = setInterval(() => setNowSec(Math.floor(Date.now() / 1000)), CLOCK_TICK_MS);
    return () => clearInterval(timer);
  }, []);

  // Control rows are built once per observation (the decode cache) and rebuilt, without
  // decoding, when the contacts or channels change.
  const entries = useMemo(
    () => buildJournalEntries([...controlPackets], contacts, channels, publicKey),
    [controlPackets, contacts, channels, publicKey]
  );
  const nodeSections = useMemo(() => buildFeedNodes(entries, t), [entries, t]);
  // What the timeline evaluates: the reader's filters, or the display layer over them.
  const shown = useMemo(() => (display ? displayFilters(filters) : filters), [display, filters]);
  // A stored node filters only while the facet that shows (and clears) it is on screen:
  // Control on and the node still among the threads. Otherwise it is ignored, never a
  // hidden filter the reader cannot see.
  // The match ignores case (a URL carries a lowercase key) and resolves to the thread's own id.
  const activeNode = useMemo(() => {
    if (!shown.sources.control || !shown.node) return null;
    const wanted = shown.node.toLowerCase();
    for (const section of nodeSections) {
      const found = section.nodes.find((n) => n.id.toLowerCase() === wanted);
      if (found) return found.id;
    }
    return null;
  }, [shown.sources.control, shown.node, nodeSections]);
  const effective = useMemo<FeedFilters>(
    () => (activeNode === shown.node ? shown : { ...shown, node: activeNode }),
    [shown, activeNode]
  );
  filtersRef.current = effective;

  const noMessages = hasNoMessages(shown);
  const emptySelection = hasEmptySelection(shown);
  const query = useMemo(() => filtersToQuery(shown), [shown]);
  const queryKey = noMessages ? 'none' : JSON.stringify(query);

  const visibleRows = useMemo(
    () => rows.filter((row) => rowPassesFilters(row, effective, channels, nowSec)),
    [rows, effective, channels, nowSec]
  );
  visibleRowsRef.current = visibleRows;
  const visibleControl = useMemo(
    () => visibleControlEntries(entries, shown.sources.control, activeNode),
    [entries, shown.sources.control, activeNode]
  );
  visibleControlRef.current = visibleControl;
  const hidden = useMemo(
    () => countHiddenRows(rows, effective, channels, nowSec),
    [rows, effective, channels, nowSec]
  );
  const items = useMemo(
    () => buildFeedItems(visibleRows, newSinceId, visibleControl),
    [visibleRows, newSinceId, visibleControl]
  );
  const itemCount = items.length;
  const resolveLabels = useMemo(
    () => createFeedNameResolver(channels, contacts),
    [channels, contacts]
  );

  /** Rows the current filters keep among those the store holds right now. */
  const countVisibleNow = useCallback(() => {
    const node = filtersRef.current.node;
    // A node that is not a channel hides every message: older pages cannot fill the list.
    if (node && !node.toUpperCase().startsWith('CHANNEL:')) return Number.MAX_SAFE_INTEGER;
    return (
      getFeedSnapshot().rows.filter((row) =>
        rowPassesFilters(
          row,
          filtersRef.current,
          channelsRef.current,
          Math.floor(Date.now() / 1000)
        )
      ).length + visibleControlRef.current.length
    );
  }, []);

  const loadNewest = useCallback(
    async (serverQuery: ReturnType<typeof filtersToQuery>, signal?: AbortSignal) => {
      setStatus('loading');
      setLoadError(null);
      setForbidden(false);
      try {
        await loadFeedPage(null, signal, serverQuery);
        setStatus('ready');
        await fillHiddenPages(countVisibleNow, serverQuery, signal, FEED_MAX_PAGES_PER_GESTURE - 1);
      } catch (err) {
        if (signal?.aborted) return;
        // A refused read (403) is the state of a surface this session does not have, not a failure.
        if (err instanceof ApiError && err.status === 403) {
          setForbidden(true);
          setStatus('ready');
          return;
        }
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
    if (hasNoMessages(filtersRef.current)) return;
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

  // Muted / pending and the node are client-side: ticking them in place can leave a short list that
  // cannot be scrolled, so the same bounded fetch applies.
  const clientFilterKey = `${shown.includeMuted}|${shown.includePending}|${activeNode}`;
  useEffect(() => {
    if (firstClientFilterRef.current) {
      firstClientFilterRef.current = false;
      return;
    }
    if (status === 'ready' && !hasNoMessages(filtersRef.current)) void loadOlderIfHollow();
    // Only a change of these is a gesture.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [clientFilterKey]);

  async function loadOlderIfHollow() {
    if (countVisibleNow() < FEED_MIN_VISIBLE) await loadOlder();
  }

  const virtualizer = useVirtualizer({
    count: items.length,
    getScrollElement: () => scrollRef.current,
    estimateSize: (index) =>
      items[index]?.kind === 'row' || items[index]?.kind === 'control'
        ? ESTIMATED_ROW_HEIGHT
        : ESTIMATED_DAY_HEIGHT,
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
  }, [itemCount, totalSize, status, pinToBottom]);

  const setFollow = useCallback((next: boolean) => {
    if (followingRef.current === next) return;
    followingRef.current = next;
    if (!next) {
      setBaselineId(highestId(visibleRowsRef.current));
      setBaselineMs(Date.now());
    }
    setFollowing(next);
  }, []);

  const handleScroll = useCallback(() => {
    const el = scrollRef.current;
    if (!el) return;
    anchorRef.current = pickAnchor(virtualizer.getVirtualItems(), el.scrollTop);
    viewportBottomRef.current = el.scrollTop + el.clientHeight;
    const distance = el.scrollHeight - el.scrollTop - el.clientHeight;
    // Display mode keeps following whatever touched the screen.
    setFollow(displayRef.current || distance <= FOLLOW_THRESHOLD_PX);
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
    () =>
      following
        ? 0
        : countArrivedSince(visibleRows, baselineId) +
          countControlArrivedSince(visibleControl, baselineMs),
    [following, visibleRows, visibleControl, baselineId, baselineMs]
  );

  // Without the three callbacks (a bare view) rows are read-only, no menu either.
  const actions = useMemo<FeedRowActions | undefined>(() => {
    // A wall screen has nobody to click: rows are plain text, no menu, no navigation.
    if (display) return undefined;
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
  }, [display, onNavigateToMessage, onSelectConversation, onOpenContactInfo]);

  useEffect(
    () => () => {
      if (highlightTimerRef.current) clearTimeout(highlightTimerRef.current);
    },
    []
  );
  const openPaired = useCallback(
    (pairedKey: string) => {
      const key = `control:${pairedKey}`;
      const index = items.findIndex((item) => item.key === key);
      if (index < 0) return;
      virtualizer.scrollToIndex(index, { align: 'center' });
      setHighlightKey(key);
      if (highlightTimerRef.current) clearTimeout(highlightTimerRef.current);
      highlightTimerRef.current = setTimeout(() => setHighlightKey(null), HIGHLIGHT_MS);
    },
    [items, virtualizer]
  );
  const controlActions = useMemo<FeedControlActions>(
    () =>
      display
        ? { onInspect: () => {} }
        : {
            onInspect: setInspected,
            onOpenContact: onOpenContactInfo,
            onOpenChannel: onSelectConversation
              ? (channel) =>
                  onSelectConversation({ type: 'channel', id: channel.key, name: channel.name })
              : undefined,
            onOpenPaired: openPaired,
          },
    [display, onOpenContactInfo, onSelectConversation, openPaired]
  );
  const itemKeys = useMemo(() => new Set(items.map((item) => item.key)), [items]);

  const enterDisplay = useCallback(() => {
    setFollow(true);
    setDisplay(true);
    enterFullscreen();
  }, [setFollow]);
  const exitDisplay = useCallback(() => {
    setDisplay(false);
    leaveFullscreen();
  }, []);
  // Leaving the view (or the tab closing) must not strand the browser in fullscreen.
  useEffect(() => leaveFullscreen, []);
  useEffect(() => {
    if (display) {
      followingRef.current = true;
      setFollowing(true);
    }
  }, [display]);

  const header = (
    <ToolPaneHeader
      title={t('feed.title')}
      onBack={onBackToTools}
      actions={
        <Button
          type="button"
          size="sm"
          variant="outline"
          onClick={enterDisplay}
          aria-label={t('feed.display.enter')}
          title={t('feed.display.enterHint')}
        >
          <Monitor className="h-4 w-4" aria-hidden="true" />
          <span className="hidden md:inline">{t('feed.display.enter')}</span>
        </Button>
      }
    />
  );
  const chips = (
    <FeedFilterChips
      filters={filters}
      hidden={hidden}
      onChange={setFilters}
      showDirect={canReadPrivate}
    />
  );
  const facet =
    !display && filters.sources.control && nodeSections.length > 0 ? (
      <FeedNodesFacet
        sections={nodeSections}
        selected={activeNode}
        defaultOpen={preset === 'control'}
        onSelect={(node) => setFilters((prev) => ({ ...prev, node }))}
      />
    ) : null;
  const inspector = (
    <RawPacketInspectorDialog
      open={inspected !== null}
      onOpenChange={(isOpen) => !isOpen && setInspected(null)}
      channels={channels}
      source={
        inspected
          ? { kind: 'packet', packet: inspected }
          : { kind: 'loading', message: t('rawPacket.loading') }
      }
      title={t('rawPacket.details')}
      description={t('rawPacket.detailsDescription')}
    />
  );
  // Header, chips, then the Nodes facet beside the pane the rest of the view fills.
  const shell = (content: ReactNode) => (
    <>
      {display ? (
        <FeedDisplayBar onExit={exitDisplay} />
      ) : (
        <>
          {header}
          {chips}
        </>
      )}
      <div className="flex min-h-0 flex-1 flex-col md:flex-row">
        {facet}
        <div className="flex min-h-0 min-w-0 flex-1 flex-col">{content}</div>
      </div>
      {inspector}
    </>
  );
  const hasNothing = rows.length === 0 && entries.length === 0;

  if (status === 'loading' && itemCount === 0) {
    return shell(
      <div role="status" className="flex flex-1 items-center justify-center text-muted-foreground">
        {t('conversation.loadingFeed')}
      </div>
    );
  }

  if (status === 'error' && itemCount === 0) {
    return shell(
      <div
        role="alert"
        className="flex flex-1 flex-col items-center justify-center gap-3 p-4 text-sm text-muted-foreground"
      >
        <p>{t('feed.loadError', { error: loadError })}</p>
        <Button size="sm" onClick={() => void loadNewest(query)}>
          {t('feed.retry')}
        </Button>
      </div>
    );
  }

  if (itemCount === 0) {
    return shell(
      loadingOlder || status === 'loading' ? (
        <div
          role="status"
          className="flex flex-1 items-center justify-center text-muted-foreground"
        >
          {t('feed.loadingOlder')}
        </div>
      ) : (
        <div className="flex flex-1 items-center justify-center p-4 text-center text-muted-foreground">
          {forbidden
            ? t('feed.forbidden')
            : hasNothing && !emptySelection
              ? t('feed.empty')
              : t('feed.emptyFiltered')}
        </div>
      )
    );
  }

  return shell(
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
                  style={{ transform: `translateY(${item.start - scrollMargin}px)` }}
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
            if (entry.kind === 'control') {
              return (
                <FeedControlRow
                  key={item.key}
                  measureRef={virtualizer.measureElement}
                  index={item.index}
                  offset={item.start - scrollMargin}
                  entry={entry.entry}
                  time={formatRowTime(entry.entry.packet.timestamp)}
                  publicKey={publicKey}
                  actions={controlActions}
                  pairedAvailable={
                    entry.entry.pairedKey !== null &&
                    itemKeys.has(`control:${entry.entry.pairedKey}`)
                  }
                  highlighted={highlightKey === item.key}
                  readOnly={display}
                />
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
                remoteMedia={!display}
                readOnly={display}
              />
            );
          })}
        </div>
      </div>
      {!following && !display && (
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
  );
}
