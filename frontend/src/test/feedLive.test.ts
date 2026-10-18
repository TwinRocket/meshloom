import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

const mocks = vi.hoisted(() => ({ getMessages: vi.fn() }));

vi.mock('../api', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../api')>();
  return { ...actual, api: { ...actual.api, getMessages: mocks.getMessages } };
});

import { buildFeedItems } from '../components/feed/feedDays';
import {
  FEED_RECONCILE_MAX_PAGES,
  FEED_RECONCILE_PAGE_SIZE,
  loadFeedPage,
  reconcileFeed,
} from '../components/feed/feedHistory';
import {
  FEED_LAST_SEEN_STORAGE_KEY,
  firstRowAfterMark,
  lastSeenOf,
  loadFeedLastSeen,
  saveFeedLastSeen,
} from '../components/feed/feedLastSeen';
import { pickAnchor, scrollShiftForAnchor } from '../components/feed/feedScroll';
import { activateFeedStore, getFeedSnapshot, resetFeedStore } from '../stores/feedStore';
import type { Message } from '../types';

function msg(id: number, received_at: number, extra: Partial<Message> = {}): Message {
  return {
    id,
    type: 'CHAN',
    conversation_key: 'AA'.repeat(16),
    text: `m${id}`,
    sender_timestamp: received_at,
    received_at,
    paths: null,
    txt_type: 0,
    signature: null,
    sender_key: null,
    outgoing: false,
    acked: 0,
    sender_name: null,
    ...extra,
  };
}

const flush = () => Promise.resolve();
const ids = () => getFeedSnapshot().rows.map((m) => m.id);

describe('scrollShiftForAnchor', () => {
  it('moves by what landed above the anchor', () => {
    const anchor = { key: 'row:5', start: 400 };
    expect(
      scrollShiftForAnchor(anchor, [
        { key: 'row:9', start: 400 },
        { key: 'row:5', start: 520 },
      ])
    ).toBe(120);
  });

  it('does not move for rows inserted below, or when the anchor is gone or absent', () => {
    expect(scrollShiftForAnchor({ key: 'a', start: 10 }, [{ key: 'a', start: 10 }])).toBe(0);
    expect(scrollShiftForAnchor({ key: 'gone', start: 10 }, [{ key: 'a', start: 10 }])).toBe(0);
    expect(scrollShiftForAnchor(null, [{ key: 'a', start: 10 }])).toBe(0);
  });

  it('picks the first item whose bottom edge is below the scroll offset', () => {
    const items = [
      { key: 'a', start: 0, end: 50 },
      { key: 'b', start: 50, end: 100 },
    ];
    expect(pickAnchor(items, 60)).toEqual({ key: 'b', start: 50 });
    expect(pickAnchor(items, 500)).toBeNull();
  });
});

describe('last-visit mark', () => {
  beforeEach(() => localStorage.clear());
  afterEach(() => vi.restoreAllMocks());

  const rows = [msg(1, 100), msg(2, 200), msg(3, 300)];

  it('places the separator before the first row strictly after the mark', () => {
    expect(firstRowAfterMark(rows, { at: 200, id: 2 })).toBe(3);
    expect(firstRowAfterMark(rows, { at: 200, id: 1 })).toBe(2);
    const items = buildFeedItems(rows, 3);
    expect(items.map((i) => i.kind)).toEqual(['day', 'row', 'row', 'new', 'row']);
  });

  it('is absent without a mark or when nothing is newer', () => {
    expect(firstRowAfterMark(rows, null)).toBeNull();
    expect(firstRowAfterMark(rows, { at: 300, id: 3 })).toBeNull();
    expect(buildFeedItems(rows, null).some((i) => i.kind === 'new')).toBe(false);
  });

  it('ignores historical rows: they never mark news', () => {
    const mixed = [msg(1, 100), msg(9, 250, { historical: true }), msg(4, 400)];
    expect(firstRowAfterMark(mixed, { at: 200, id: 1 })).toBe(4);
  });

  it('round-trips and takes the newest row as the mark', () => {
    expect(lastSeenOf(rows)).toEqual({ at: 300, id: 3 });
    expect(lastSeenOf([])).toBeNull();
    saveFeedLastSeen({ at: 300, id: 3 });
    expect(loadFeedLastSeen()).toEqual({ at: 300, id: 3 });
  });

  it('tolerates corrupted and blocked storage', () => {
    localStorage.setItem(FEED_LAST_SEEN_STORAGE_KEY, '{"at":"x"');
    expect(loadFeedLastSeen()).toBeNull();
    localStorage.setItem(FEED_LAST_SEEN_STORAGE_KEY, '{"at":1}');
    expect(loadFeedLastSeen()).toBeNull();
    vi.spyOn(Storage.prototype, 'getItem').mockImplementation(() => {
      throw new Error('blocked');
    });
    vi.spyOn(Storage.prototype, 'setItem').mockImplementation(() => {
      throw new Error('blocked');
    });
    expect(loadFeedLastSeen()).toBeNull();
    expect(() => saveFeedLastSeen({ at: 1, id: 1 })).not.toThrow();
  });
});

describe('reconcileFeed', () => {
  beforeEach(() => {
    mocks.getMessages.mockReset();
    resetFeedStore();
  });

  async function seed() {
    activateFeedStore();
    mocks.getMessages.mockResolvedValueOnce([msg(10, 1000), msg(9, 900)]);
    await loadFeedPage(null, undefined, { type: 'CHAN' });
    await flush();
  }

  it('does nothing while the feed was never opened or holds nothing', async () => {
    await reconcileFeed();
    activateFeedStore();
    await reconcileFeed();
    expect(mocks.getMessages).not.toHaveBeenCalled();
  });

  it('asks since_id with the highest id and the current query, and merges a historical row', async () => {
    await seed();
    mocks.getMessages.mockResolvedValueOnce([msg(11, 50, { historical: true }), msg(12, 1200)]);
    await reconcileFeed();
    await flush();
    expect(mocks.getMessages).toHaveBeenLastCalledWith({
      since_id: 10,
      limit: FEED_RECONCILE_PAGE_SIZE,
      type: 'CHAN',
    });
    // The short first page reached the beginning, so the old row has its place at the top.
    expect(ids()).toEqual([11, 9, 10, 12]);
  });

  it('inserts a historical row with an old received_at in place when the window reaches back', async () => {
    activateFeedStore();
    mocks.getMessages.mockResolvedValueOnce([msg(10, 1000), msg(9, 900)]);
    await loadFeedPage(null);
    mocks.getMessages.mockResolvedValueOnce([msg(11, 950, { historical: true })]);
    await reconcileFeed();
    await flush();
    expect(ids()).toEqual([9, 11, 10]);
  });

  it('pages while a page is full, from the last id of each page', async () => {
    await seed();
    const full = (from: number) =>
      Array.from({ length: FEED_RECONCILE_PAGE_SIZE }, (_, i) => msg(from + i, 2000 + from + i));
    mocks.getMessages.mockResolvedValueOnce(full(100));
    mocks.getMessages.mockResolvedValueOnce([msg(5000, 9000)]);
    await reconcileFeed();
    const sinceIds = mocks.getMessages.mock.calls.slice(1).map(([params]) => params.since_id);
    expect(sinceIds).toEqual([10, 100 + FEED_RECONCILE_PAGE_SIZE - 1]);
  });

  it('empties the store and bumps the epoch after five full pages', async () => {
    await seed();
    const epoch = getFeedSnapshot().epoch;
    let next = 100;
    mocks.getMessages.mockImplementation(async () => {
      const page = Array.from({ length: FEED_RECONCILE_PAGE_SIZE }, (_, i) =>
        msg(next + i, 2000 + next + i)
      );
      next += FEED_RECONCILE_PAGE_SIZE;
      return page;
    });
    await reconcileFeed();
    await flush();
    expect(mocks.getMessages).toHaveBeenCalledTimes(1 + FEED_RECONCILE_MAX_PAGES);
    expect(ids()).toEqual([]);
    expect(getFeedSnapshot().epoch).toBe(epoch + 1);
  });

  it('survives a failed request and runs overlapping calls once more', async () => {
    await seed();
    vi.spyOn(console, 'error').mockImplementation(() => {});
    mocks.getMessages.mockRejectedValueOnce(new Error('offline'));
    await expect(reconcileFeed()).resolves.toBeUndefined();

    let release: (rows: Message[]) => void = () => {};
    mocks.getMessages.mockReturnValueOnce(new Promise<Message[]>((r) => (release = r)));
    mocks.getMessages.mockResolvedValueOnce([]);
    const first = reconcileFeed();
    const second = reconcileFeed();
    release([msg(11, 1100)]);
    await Promise.all([first, second]);
    expect(mocks.getMessages).toHaveBeenCalledTimes(4);
  });
});
