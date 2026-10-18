import { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { useVirtualizer } from '@tanstack/react-virtual';
import { ArrowDown } from 'lucide-react';

import { formatApiError } from '../../api';
import { activateFeedStore, useFeedRows } from '../../stores/feedStore';
import type { Channel, Contact, Message } from '../../types';
import { formatNumber } from '../../utils/formatNumber';
import { getContactDisplayName } from '../../utils/pubkey';
import { Button } from '../ui/button';
import { MessageBody } from '../message/MessageBody';
import { ToolPaneHeader } from '../ToolPaneHeader';
import { FEED_DOM_MAX } from './feedConversation';
import { countArrivedSince, FOLLOW_THRESHOLD_PX, highestId } from './feedFollow';
import { loadFeedPage } from './feedHistory';

interface FeedViewProps {
  contacts: Contact[];
  channels: Channel[];
  radioName?: string;
  onBackToTools?: () => void;
}

const ESTIMATED_ROW_HEIGHT = 56;
// A viewport that measures zero (hidden pane, jsdom) must still mount a screenful.
const FALLBACK_VIEWPORT_HEIGHT = 600;
/** Scrolling within this distance of the top asks for the previous page. */
const LOAD_OLDER_THRESHOLD_PX = 300;
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
 */
export function FeedView({ contacts, channels, radioName, onBackToTools }: FeedViewProps) {
  const { t, i18n } = useTranslation();
  const { rows, hasOlder } = useFeedRows();
  const formatRowTime = useTimeFormatter(i18n.language);

  const [status, setStatus] = useState<'loading' | 'ready' | 'error'>('loading');
  const [loadError, setLoadError] = useState<string | null>(null);
  const [loadingOlder, setLoadingOlder] = useState(false);
  const [olderError, setOlderError] = useState<string | null>(null);
  const [following, setFollowing] = useState(true);
  const [baselineId, setBaselineId] = useState(0);
  const [scrollMargin, setScrollMargin] = useState(0);

  const scrollRef = useRef<HTMLDivElement | null>(null);
  const spacerRef = useRef<HTMLDivElement | null>(null);
  const followingRef = useRef(true);
  const loadingOlderRef = useRef(false);
  const hasOlderRef = useRef(hasOlder);
  const rowsRef = useRef<readonly Message[]>(rows);
  const previousRef = useRef<{ firstId: number | null; scrollHeight: number }>({
    firstId: null,
    scrollHeight: 0,
  });
  hasOlderRef.current = hasOlder;
  rowsRef.current = rows;

  const channelNames = useMemo(() => new Map(channels.map((c) => [c.key, c.name])), [channels]);
  const contactNames = useMemo(
    () =>
      new Map(
        contacts.map((c) => [
          c.public_key,
          getContactDisplayName(c.name, c.public_key, c.last_advert),
        ])
      ),
    [contacts]
  );

  const conversationLabel = useCallback(
    (row: Message): string => {
      if (row.type === 'CHAN') {
        return (
          channelNames.get(row.conversation_key) ??
          row.channel_name ??
          row.conversation_key.slice(0, 8)
        );
      }
      return contactNames.get(row.conversation_key) ?? row.conversation_key.slice(0, 12);
    },
    [channelNames, contactNames]
  );

  const loadNewest = useCallback(
    async (signal?: AbortSignal) => {
      setStatus('loading');
      setLoadError(null);
      try {
        await loadFeedPage(null, signal);
        setStatus('ready');
      } catch (err) {
        if (signal?.aborted) return;
        setLoadError(formatApiError(err, t));
        setStatus('error');
      }
    },
    [t]
  );

  useEffect(() => {
    activateFeedStore();
    const controller = new AbortController();
    void loadNewest(controller.signal);
    return () => controller.abort();
  }, [loadNewest]);

  const loadOlder = useCallback(async () => {
    if (loadingOlderRef.current || !hasOlderRef.current) return;
    const oldest = rowsRef.current[0];
    if (!oldest) return;
    loadingOlderRef.current = true;
    setLoadingOlder(true);
    setOlderError(null);
    try {
      await loadFeedPage({ received_at: oldest.received_at, id: oldest.id });
    } catch (err) {
      setOlderError(formatApiError(err, t));
    } finally {
      loadingOlderRef.current = false;
      setLoadingOlder(false);
    }
  }, [t]);

  const virtualizer = useVirtualizer({
    count: rows.length,
    getScrollElement: () => scrollRef.current,
    estimateSize: () => ESTIMATED_ROW_HEIGHT,
    scrollMargin,
    getItemKey: (index) => rows[index]?.id ?? `__idx:${index}`,
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

  // A page prepended above the reader must not move what they are looking at.
  const firstId = rows[0]?.id ?? null;
  useLayoutEffect(() => {
    const el = scrollRef.current;
    const previous = previousRef.current;
    if (el && !followingRef.current && previous.firstId !== null && firstId !== previous.firstId) {
      const grew = el.scrollHeight - previous.scrollHeight;
      if (grew > 0) el.scrollTop += grew;
    }
    previousRef.current = { firstId, scrollHeight: el?.scrollHeight ?? 0 };
  }, [firstId, rows.length, totalSize]);

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
  }, [rows.length, totalSize, status, pinToBottom]);

  const setFollow = useCallback((next: boolean) => {
    if (followingRef.current === next) return;
    followingRef.current = next;
    if (!next) setBaselineId(highestId(rowsRef.current));
    setFollowing(next);
  }, []);

  const handleScroll = useCallback(() => {
    const el = scrollRef.current;
    if (!el) return;
    const distance = el.scrollHeight - el.scrollTop - el.clientHeight;
    setFollow(distance <= FOLLOW_THRESHOLD_PX);
    if (el.scrollTop <= LOAD_OLDER_THRESHOLD_PX && status === 'ready') void loadOlder();
  }, [loadOlder, setFollow, status]);

  const handleFollowClick = useCallback(() => {
    setFollow(true);
    pinToBottom();
  }, [pinToBottom, setFollow]);

  const arrived = useMemo(
    () => (following ? 0 : countArrivedSince(rows, baselineId)),
    [following, rows, baselineId]
  );

  const header = <ToolPaneHeader title={t('feed.title')} onBack={onBackToTools} />;

  if (status === 'loading' && rows.length === 0) {
    return (
      <>
        {header}
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
        <div
          role="alert"
          className="flex flex-1 flex-col items-center justify-center gap-3 p-4 text-sm text-muted-foreground"
        >
          <p>{t('feed.loadError', { error: loadError })}</p>
          <Button size="sm" onClick={() => void loadNewest()}>
            {t('feed.retry')}
          </Button>
        </div>
      </>
    );
  }

  if (status === 'ready' && rows.length === 0) {
    return (
      <>
        {header}
        <div className="flex flex-1 items-center justify-center p-4 text-center text-muted-foreground">
          {t('feed.empty')}
        </div>
      </>
    );
  }

  return (
    <>
      {header}
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
              const row = rows[item.index];
              if (!row) return null;
              const time = formatRowTime(row.received_at);
              return (
                <div
                  key={item.key}
                  ref={virtualizer.measureElement}
                  data-index={item.index}
                  role="listitem"
                  tabIndex={0}
                  className="absolute left-0 top-0 w-full border-b border-border/50 py-2 text-sm focus-visible:outline-hidden focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-ring"
                  style={{ transform: `translateY(${item.start - scrollMargin}px)` }}
                >
                  <div className="flex items-baseline gap-2 text-xs text-muted-foreground">
                    <time
                      dateTime={new Date(row.received_at * 1000).toISOString()}
                      title={time.full}
                    >
                      {time.short}
                    </time>
                    <span className="min-w-0 truncate font-medium text-foreground/80">
                      {conversationLabel(row)}
                    </span>
                  </div>
                  <MessageBody content={row.text} radioName={radioName} />
                </div>
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
