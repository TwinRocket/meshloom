import { act, cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import './qSlices';

const mocks = vi.hoisted(() => ({ getMessages: vi.fn() }));

vi.mock('../api', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../api')>();
  return { ...actual, api: { ...actual.api, getMessages: mocks.getMessages } };
});

import { FeedView } from '../components/feed/FeedView';
import { FEED_DOM_MAX, FEED_PAGE_SIZE } from '../components/feed/feedConversation';
import { countArrivedSince } from '../components/feed/feedFollow';
import i18n from '../i18n';
import { FEED_LAST_SEEN_STORAGE_KEY } from '../components/feed/feedLastSeen';
import {
  getFeedSnapshot,
  recordFeedMessage,
  resetFeedForRefetch,
  resetFeedStore,
} from '../stores/feedStore';
import { FEED_FILTERS_STORAGE_KEY } from '../utils/feedFilters';
import type { Channel, Message } from '../types';

const CHANNEL_KEY = '8B3387E9C5CDEA6AC9E5EDBAA115CD72';
const channels = [
  {
    key: CHANNEL_KEY,
    name: 'Public',
    is_hashtag: false,
    on_radio: false,
    last_read_at: null,
    favorite: false,
  },
] as unknown as Channel[];

function msg(id: number, extra: Partial<Message> = {}): Message {
  return {
    id,
    type: 'CHAN',
    conversation_key: CHANNEL_KEY,
    text: `Alice: hello ${id}`,
    sender_timestamp: 1700000000 + id,
    received_at: 1700000000 + id * 10,
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

/** What the server returns: newest first, `before`/`before_id` cut strictly older. */
function serverPage(ids: number[]) {
  return ids.map((id) => msg(id)).sort((a, b) => b.received_at - a.received_at || b.id - a.id);
}

function range(from: number, to: number): number[] {
  const out: number[] = [];
  for (let id = from; id <= to; id++) out.push(id);
  return out;
}

function renderFeed(withChannels: Channel[] = channels) {
  return render(<FeedView contacts={[]} channels={withChannels} onBackToTools={vi.fn()} />);
}

/** jsdom lays nothing out: give the scroller the geometry the follow logic reads. */
function mockGeometry(
  scroller: HTMLElement,
  geometry: { scrollHeight: number; clientHeight: number }
) {
  let top = 0;
  Object.defineProperty(scroller, 'scrollHeight', {
    configurable: true,
    get: () => geometry.scrollHeight,
  });
  Object.defineProperty(scroller, 'clientHeight', {
    configurable: true,
    get: () => geometry.clientHeight,
  });
  Object.defineProperty(scroller, 'scrollTop', {
    configurable: true,
    get: () => top,
    set: (value: number) => {
      top = value;
    },
  });
}

async function findScroller(): Promise<HTMLElement> {
  const list = await screen.findByRole('list');
  return list.parentElement as HTMLElement;
}

beforeEach(() => {
  mocks.getMessages.mockReset();
  resetFeedStore();
  localStorage.clear();
});

describe('FeedView pagination', () => {
  it('asks for the newest page, then pages back from the oldest row; three pages merge cleanly', async () => {
    const pages = [range(801, 1000), range(601, 800), range(551, 600)];
    mocks.getMessages.mockImplementation(async () => serverPage(pages.shift() ?? []));
    renderFeed();

    const scroller = await findScroller();
    expect(mocks.getMessages).toHaveBeenNthCalledWith(
      1,
      { limit: FEED_PAGE_SIZE },
      expect.anything()
    );

    fireEvent.scroll(scroller);
    await waitFor(() => expect(mocks.getMessages).toHaveBeenCalledTimes(2));
    const oldestOfFirst = msg(801);
    expect(mocks.getMessages).toHaveBeenNthCalledWith(
      2,
      { limit: FEED_PAGE_SIZE, before: oldestOfFirst.received_at, before_id: 801 },
      undefined
    );

    await waitFor(() => expect(getFeedSnapshot().rows).toHaveLength(400));
    fireEvent.scroll(scroller);
    await waitFor(() => expect(mocks.getMessages).toHaveBeenCalledTimes(3));
    expect(mocks.getMessages).toHaveBeenNthCalledWith(
      3,
      { limit: FEED_PAGE_SIZE, before: msg(601).received_at, before_id: 601 },
      undefined
    );

    await waitFor(() => expect(getFeedSnapshot().rows).toHaveLength(450));
    const { rows, hasOlder } = getFeedSnapshot();
    expect(new Set(rows.map((r) => r.id)).size).toBe(rows.length);
    expect(rows.map((r) => r.id)).toEqual(range(551, 1000));
    // A short last page is the beginning of history.
    expect(hasOlder).toBe(false);
    expect(await screen.findByText(i18n.t('feed.beginning'))).toBeInTheDocument();

    fireEvent.scroll(scroller);
    expect(mocks.getMessages).toHaveBeenCalledTimes(3);
  });

  it('knows there is no older history when the first page is short', async () => {
    mocks.getMessages.mockResolvedValue(serverPage(range(1, 3)));
    renderFeed();

    expect(await screen.findByText(i18n.t('feed.beginning'))).toBeInTheDocument();
    expect(getFeedSnapshot().hasOlder).toBe(false);
    fireEvent.scroll(await findScroller());
    expect(mocks.getMessages).toHaveBeenCalledTimes(1);
  });

  it('shows a loading-older banner while the previous page is in flight', async () => {
    let release: (rows: Message[]) => void = () => {};
    mocks.getMessages
      .mockResolvedValueOnce(serverPage(range(801, 1000)))
      .mockImplementationOnce(() => new Promise<Message[]>((resolve) => (release = resolve)));
    renderFeed();

    fireEvent.scroll(await findScroller());
    expect(await screen.findByText(i18n.t('feed.loadingOlder'))).toBeInTheDocument();
    await act(async () => release(serverPage(range(601, 800))));
    await waitFor(() => expect(screen.queryByText(i18n.t('feed.loadingOlder'))).toBeNull());
  });
});

describe('FeedView states', () => {
  it('shows the loading state, then the empty state', async () => {
    let release: (rows: Message[]) => void = () => {};
    mocks.getMessages.mockImplementation(
      () => new Promise<Message[]>((resolve) => (release = resolve))
    );
    renderFeed();

    expect(screen.getByText(i18n.t('conversation.loadingFeed'))).toBeInTheDocument();
    await act(async () => release([]));
    expect(await screen.findByText(i18n.t('feed.empty'))).toBeInTheDocument();
  });

  it('shows the error and retries', async () => {
    mocks.getMessages
      .mockRejectedValueOnce(new Error('boom'))
      .mockResolvedValueOnce(serverPage(range(1, 2)));
    renderFeed();

    const alert = await screen.findByRole('alert');
    expect(alert).toHaveTextContent('boom');
    fireEvent.click(within(alert).getByRole('button', { name: i18n.t('feed.retry') }));
    expect(await screen.findByRole('list')).toBeInTheDocument();
    expect(mocks.getMessages).toHaveBeenCalledTimes(2);
  });

  it('reports a failed older page inline and keeps the rows', async () => {
    mocks.getMessages
      .mockResolvedValueOnce(serverPage(range(801, 1000)))
      .mockRejectedValueOnce(new Error('later'));
    renderFeed();

    fireEvent.scroll(await findScroller());
    expect(await screen.findByRole('alert')).toHaveTextContent('later');
    expect(getFeedSnapshot().rows).toHaveLength(200);
  });
});

describe('FeedView rows and keyboard', () => {
  it('exposes a list of focusable items, named by channel, with the time in an element', async () => {
    mocks.getMessages.mockResolvedValue(serverPage(range(1, 3)));
    renderFeed();

    const list = await screen.findByRole('list', { name: i18n.t('feed.listLabel') });
    const items = within(list).getAllByRole('listitem');
    expect(items).toHaveLength(3);
    for (const item of items) {
      expect(item).toHaveAttribute('tabindex', '0');
      expect(within(item).getByText('Public')).toBeInTheDocument();
      expect(item.querySelector('time')).not.toBeNull();
    }
    expect(within(items[0]).getByText(/hello 1/)).toBeInTheDocument();
  });

  it('never mounts more than FEED_DOM_MAX rows', async () => {
    mocks.getMessages.mockResolvedValue(serverPage(range(1, 200)));
    renderFeed();

    const list = await screen.findByRole('list');
    expect(within(list).getAllByRole('listitem').length).toBeLessThanOrEqual(FEED_DOM_MAX);
  });
});

describe('FeedView follow mode', () => {
  async function renderWithGeometry() {
    mocks.getMessages.mockResolvedValue(serverPage(range(1, 3)));
    renderFeed();
    const scroller = await findScroller();
    mockGeometry(scroller, { scrollHeight: 1000, clientHeight: 400 });
    return scroller;
  }

  const followButton = () =>
    screen.queryByRole('button', { name: new RegExp(`^${i18n.t('feed.follow')}`) });

  it('stays at the bottom when a row arrives while pinned', async () => {
    const scroller = await renderWithGeometry();
    scroller.scrollTop = 600;
    fireEvent.scroll(scroller);
    expect(followButton()).toBeNull();

    scroller.scrollTop = 0;
    await act(async () => {
      recordFeedMessage(msg(10));
      await Promise.resolve();
    });

    await waitFor(() => expect(scroller.scrollTop).toBe(1000));
    expect(followButton()).toBeNull();
  });

  it('counts rows that arrive after a scroll up, ignores historical ones, and re-pins on click', async () => {
    const scroller = await renderWithGeometry();

    scroller.scrollTop = 100;
    fireEvent.scroll(scroller);
    const button = await screen.findByRole('button', { name: i18n.t('feed.follow') });
    expect(button).toBeInTheDocument();

    await act(async () => {
      recordFeedMessage(msg(10));
      recordFeedMessage(msg(11));
      recordFeedMessage(msg(12, { historical: true }));
      await Promise.resolve();
    });

    const withCount = await screen.findByRole('button', {
      name: i18n.t('feed.followNew', { count: 2, formatted: '2' }),
    });
    expect(scroller.scrollTop).toBe(100);

    fireEvent.click(withCount);
    expect(followButton()).toBeNull();
    expect(scroller.scrollTop).toBe(1000);
  });

  it('turns follow back on by itself when the reader scrolls to the bottom', async () => {
    const scroller = await renderWithGeometry();
    scroller.scrollTop = 100;
    fireEvent.scroll(scroller);
    expect(await screen.findByRole('button', { name: i18n.t('feed.follow') })).toBeInTheDocument();

    scroller.scrollTop = 580;
    fireEvent.scroll(scroller);
    await waitFor(() => expect(followButton()).toBeNull());
  });
});

describe('countArrivedSince', () => {
  it('counts newer, non-historical rows only', () => {
    const rows = [msg(1), msg(5), msg(6, { historical: true }), msg(7)];
    expect(countArrivedSince(rows, 5)).toBe(1);
    expect(countArrivedSince(rows, 0)).toBe(3);
  });
});

const MUTED_KEY = '11111111111111111111111111111111';
const PENDING_KEY = '44444444444444444444444444444444';

function channel(key: string, name: string, extra: Partial<Channel> = {}): Channel {
  return { ...channels[0], key, name, muted: false, ...extra } as Channel;
}

const chip = (name: string) => {
  const found = screen
    .getAllByRole('button')
    .find((button) => button.firstChild?.textContent === name);
  if (!found) throw new Error(`No chip named ${name}`);
  return found;
};
const chipLabel = (key: string) => i18n.t(`feed.filters.${key}`);
const hidden = (key: 'hiddenMuted' | 'hiddenPending', count: number) =>
  i18n.t(`feed.${key}`, { count, formatted: String(count) });

describe('FeedView filter chips', () => {
  const mixedChannels = [
    channels[0],
    channel(MUTED_KEY, 'Quiet', { muted: true }),
    channel(PENDING_KEY, 'Newcomer', { membership: 'pending' }),
  ];
  const mixedRows = () => [
    msg(1),
    msg(2, { conversation_key: MUTED_KEY }),
    msg(3, { conversation_key: MUTED_KEY }),
    msg(4, { conversation_key: PENDING_KEY }),
    msg(5, { type: 'PRIV', conversation_key: 'aa'.repeat(32), text: 'psst' }),
  ];

  it('hides muted and pending channels by default and counts what is hidden', async () => {
    mocks.getMessages.mockResolvedValue(serverPage([]).concat(mixedRows()));
    renderFeed(mixedChannels);

    const list = await screen.findByRole('list');
    expect(within(list).getAllByRole('listitem')).toHaveLength(2);
    expect(within(chip(chipLabel('muted'))).getByText(hidden('hiddenMuted', 2))).toBeTruthy();
    expect(within(chip(chipLabel('pending'))).getByText(hidden('hiddenPending', 1))).toBeTruthy();

    fireEvent.click(chip(chipLabel('muted')));
    expect(within(list).getAllByRole('listitem')).toHaveLength(4);
    // Shown rows are no longer "hidden": the count goes away.
    expect(within(chip(chipLabel('muted'))).queryByText(/hidden|masqu/)).toBeNull();
    // Toggling a client-side chip never refetches.
    expect(mocks.getMessages).toHaveBeenCalledTimes(1);
  });

  it('re-evaluates rows when a channel frame flips muted, without reload', async () => {
    mocks.getMessages.mockResolvedValue(mixedRows());
    const view = renderFeed(mixedChannels);
    const list = await screen.findByRole('list');
    expect(within(list).getAllByRole('listitem')).toHaveLength(2);

    const unmuted = mixedChannels.map((c) => (c.key === MUTED_KEY ? { ...c, muted: false } : c));
    view.rerender(<FeedView contacts={[]} channels={unmuted} onBackToTools={vi.fn()} />);
    expect(within(await screen.findByRole('list')).getAllByRole('listitem')).toHaveLength(4);
    expect(mocks.getMessages).toHaveBeenCalledTimes(1);
  });

  it('lets an expired timed mute reappear on the minute tick', async () => {
    vi.useFakeTimers({ toFake: ['setInterval', 'clearInterval', 'Date'] });
    try {
      const nowSec = Math.floor(Date.now() / 1000);
      const timed = channel(MUTED_KEY, 'Quiet', { muted: true, muted_until: nowSec + 90 });
      mocks.getMessages.mockResolvedValue([msg(1), msg(2, { conversation_key: MUTED_KEY })]);
      renderFeed([channels[0], timed]);
      const list = await screen.findByRole('list');
      expect(within(list).getAllByRole('listitem')).toHaveLength(1);

      await act(async () => {
        vi.advanceTimersByTime(60_000);
      });
      expect(within(list).getAllByRole('listitem')).toHaveLength(1);
      await act(async () => {
        vi.advanceTimersByTime(60_000);
      });
      expect(within(list).getAllByRole('listitem')).toHaveLength(2);
    } finally {
      vi.useRealTimers();
    }
  });

  it('asks the server for the type and direction chips, debounced, from an emptied store', async () => {
    mocks.getMessages.mockResolvedValue(mixedRows());
    renderFeed(mixedChannels);
    await screen.findByRole('list');
    expect(mocks.getMessages).toHaveBeenLastCalledWith(
      { limit: FEED_PAGE_SIZE },
      expect.anything()
    );

    mocks.getMessages.mockResolvedValue([msg(1)]);
    fireEvent.click(chip(chipLabel('direct')));
    fireEvent.click(chip(chipLabel('sent')));
    await waitFor(() => expect(mocks.getMessages).toHaveBeenCalledTimes(2));
    // Two clicks inside the debounce: one request carrying both.
    expect(mocks.getMessages).toHaveBeenLastCalledWith(
      { limit: FEED_PAGE_SIZE, type: 'CHAN', outgoing: false },
      expect.anything()
    );
    await waitFor(() => expect(getFeedSnapshot().rows.map((r) => r.id)).toEqual([1]));
  });

  it('does not page with the new query from an old cursor while the reload is pending', async () => {
    mocks.getMessages.mockResolvedValueOnce(serverPage(range(801, 1000)));
    renderFeed();
    const scroller = await findScroller();

    mocks.getMessages.mockResolvedValue(serverPage(range(1, 3)));
    fireEvent.click(chip(chipLabel('direct')));
    // Inside the debounce the store still holds rows of the old query.
    fireEvent.scroll(scroller);
    expect(mocks.getMessages).toHaveBeenCalledTimes(1);

    await waitFor(() => expect(mocks.getMessages).toHaveBeenCalledTimes(2));
    expect(mocks.getMessages).toHaveBeenLastCalledWith(
      { limit: FEED_PAGE_SIZE, type: 'CHAN' },
      expect.anything()
    );
  });

  it('sends no request and shows the empty state when both chips of a pair are off', async () => {
    mocks.getMessages.mockResolvedValue(mixedRows());
    renderFeed(mixedChannels);
    await screen.findByRole('list');

    fireEvent.click(chip(chipLabel('channels')));
    fireEvent.click(chip(chipLabel('direct')));
    expect(await screen.findByText(i18n.t('feed.emptyFiltered'))).toBeInTheDocument();
    await new Promise((resolve) => setTimeout(resolve, 400));
    expect(mocks.getMessages).toHaveBeenCalledTimes(1);
    // The chips stay reachable to leave the empty state.
    expect(chip(chipLabel('channels'))).toHaveAttribute('aria-pressed', 'false');
  });

  it('keeps the filters across a reload, and starts from defaults on corrupted storage', async () => {
    mocks.getMessages.mockResolvedValue(mixedRows());
    const first = renderFeed(mixedChannels);
    await screen.findByRole('list');
    fireEvent.click(chip(chipLabel('muted')));
    expect(JSON.parse(localStorage.getItem(FEED_FILTERS_STORAGE_KEY) ?? '{}')).toMatchObject({
      includeMuted: true,
    });
    first.unmount();

    renderFeed(mixedChannels);
    await screen.findByRole('list');
    expect(chip(chipLabel('muted'))).toHaveAttribute('aria-pressed', 'true');
    cleanup();

    localStorage.setItem(FEED_FILTERS_STORAGE_KEY, '{not json');
    renderFeed(mixedChannels);
    await screen.findByRole('list');
    expect(chip(chipLabel('muted'))).toHaveAttribute('aria-pressed', 'false');
    expect(chip(chipLabel('channels'))).toHaveAttribute('aria-pressed', 'true');
  });

  it('enables Control (on by default) and keeps Events disabled until its source exists', async () => {
    mocks.getMessages.mockResolvedValue(mixedRows());
    renderFeed(mixedChannels);
    await screen.findByRole('list');
    expect(chip(chipLabel('control'))).toBeEnabled();
    expect(chip(chipLabel('control'))).toHaveAttribute('aria-pressed', 'true');
    expect(chip(chipLabel('events'))).toBeDisabled();
  });
});

describe('FeedView hollow pages', () => {
  const hollowChannels = [channel(MUTED_KEY, 'Quiet', { muted: true })];
  const fullPage = (page: number) =>
    range(page * 200 + 1, page * 200 + 200)
      .reverse()
      .map((id) => msg(id, { conversation_key: MUTED_KEY }));

  it('fetches the next page by itself, at most five pages per gesture', async () => {
    let page = 4;
    mocks.getMessages.mockImplementation(async () => fullPage(page--));
    renderFeed(hollowChannels);

    await waitFor(() => expect(mocks.getMessages).toHaveBeenCalledTimes(5));
    await new Promise((resolve) => setTimeout(resolve, 100));
    expect(mocks.getMessages).toHaveBeenCalledTimes(5);
    expect(screen.getByText(i18n.t('feed.emptyFiltered'))).toBeInTheDocument();
    expect(mocks.getMessages).toHaveBeenNthCalledWith(
      2,
      { limit: FEED_PAGE_SIZE, before: expect.any(Number), before_id: 801 },
      expect.anything()
    );
  });

  it('stops as soon as enough rows are visible', async () => {
    // A full page of which fewer than 20 rows are visible, then a page of visible rows.
    const first = range(100, 299).map((id) =>
      msg(id, id % 13 === 0 ? {} : { conversation_key: MUTED_KEY })
    );
    const visibleInFirst = first.filter((m) => m.conversation_key !== MUTED_KEY).length;
    expect(visibleInFirst).toBeLessThan(20);
    const second = range(1, 99).map((id) => msg(id));
    mocks.getMessages
      .mockResolvedValueOnce([...first].reverse())
      .mockResolvedValueOnce([...second].reverse());
    renderFeed(hollowChannels);

    await waitFor(() => expect(mocks.getMessages).toHaveBeenCalledTimes(2));
    await new Promise((resolve) => setTimeout(resolve, 100));
    expect(mocks.getMessages).toHaveBeenCalledTimes(2);
  });
});

describe('FeedView message rows', () => {
  const dayOf = (iso: string) => Math.floor(new Date(iso).getTime() / 1000);

  it('puts a day separator before the first row of each day', async () => {
    mocks.getMessages.mockResolvedValue([
      msg(3, { received_at: dayOf('2026-03-02T00:30:00') }),
      msg(2, { received_at: dayOf('2026-03-01T22:00:00') }),
      msg(1, { received_at: dayOf('2026-03-01T08:00:00') }),
    ]);
    renderFeed();

    const list = await screen.findByRole('list');
    expect(within(list).getAllByRole('heading', { level: 3 })).toHaveLength(2);
    expect(within(list).getAllByRole('listitem')).toHaveLength(3);
  });

  it('shows the sender with its key on hover, the sent marker and the region', async () => {
    mocks.getMessages.mockResolvedValue([
      msg(2, { outgoing: true, text: 'Me: out', region: 'EU' }),
      msg(1, { text: 'Alice: hi', sender_key: 'ef'.repeat(32) }),
    ]);
    renderFeed();

    const list = await screen.findByRole('list');
    const [incoming, outgoing] = within(list).getAllByRole('listitem');
    expect(within(incoming).getByText('Alice')).toHaveAttribute('title', 'efefefefefef');
    expect(within(incoming).getByText('hi')).toBeInTheDocument();
    expect(within(incoming).queryByText(i18n.t('feed.sent'))).toBeNull();
    expect(within(outgoing).getByText(i18n.t('feed.sent'))).toBeInTheDocument();
    expect(within(outgoing).getByText('EU')).toBeInTheDocument();
  });

  it('names a deleted channel by the WS name, then by its abbreviated key', async () => {
    const gone = '77777777777777777777777777777777';
    mocks.getMessages.mockResolvedValue([
      msg(2, { conversation_key: gone, channel_name: 'Old name' }),
      msg(1, { conversation_key: gone }),
    ]);
    renderFeed();

    const list = await screen.findByRole('list');
    expect(within(list).getByText('Old name')).toBeInTheDocument();
    expect(within(list).getByText('77777777')).toBeInTheDocument();
  });

  it('renders a MeshCore Open reaction whose target is not loaded', async () => {
    mocks.getMessages.mockResolvedValue([msg(1, { text: 'Alice: r:abcd:00' })]);
    renderFeed();

    const item = within(await screen.findByRole('list')).getByRole('listitem');
    expect(item).not.toHaveTextContent('r:abcd:00');
    expect(item).toHaveTextContent(i18n.t('messageList.reacted'));
  });

  it('does not load remote media for a pending channel', async () => {
    const pending = channel(PENDING_KEY, 'Newcomer', { membership: 'pending' });
    mocks.getMessages.mockResolvedValue([
      msg(2, { conversation_key: PENDING_KEY, text: 'Bob: g:abc123' }),
      msg(1, { text: 'Alice: g:abc123' }),
    ]);
    localStorage.setItem(FEED_FILTERS_STORAGE_KEY, JSON.stringify({ includePending: true }));
    renderFeed([channels[0], pending]);

    const list = await screen.findByRole('list');
    const [adopted, held] = within(list).getAllByRole('listitem');
    expect(within(adopted).queryByTestId('message-gif')).not.toBeNull();
    expect(within(held).queryByTestId('message-gif')).toBeNull();
    expect(held).toHaveTextContent('g:abc123');
  });
});

describe('FeedView live merge', () => {
  const separator = () =>
    screen.queryByRole('separator', { name: i18n.t('feed.newSinceLastVisit') });

  beforeEach(() => {
    i18n.addResourceBundle(
      'en',
      'translation',
      { feed: { newSinceLastVisit: 'New since your last visit' } },
      true,
      true
    );
  });

  it('puts the separator before the first row after the mark, and nowhere without one', async () => {
    mocks.getMessages.mockResolvedValue(serverPage(range(1, 5)));
    localStorage.setItem(
      FEED_LAST_SEEN_STORAGE_KEY,
      JSON.stringify({ at: msg(3).received_at, id: 3 })
    );
    const first = renderFeed();
    await screen.findByRole('list');
    await waitFor(() => expect(separator()).toBeInTheDocument());
    const items = screen.getAllByRole('listitem').map((el) => el.textContent ?? '');
    const before = items.findIndex((text) => text.includes('hello 3'));
    const after = items.findIndex((text) => text.includes('hello 4'));
    const sep = separator() as HTMLElement;
    const order = Array.from(sep.parentElement?.children ?? []);
    expect(order.indexOf(sep)).toBeGreaterThan(
      order.findIndex((el) => el.textContent?.includes('hello 3'))
    );
    expect(order.indexOf(sep)).toBeLessThan(
      order.findIndex((el) => el.textContent?.includes('hello 4'))
    );
    expect(before).toBeGreaterThanOrEqual(0);
    expect(after).toBeGreaterThan(before);
    first.unmount();

    localStorage.clear();
    resetFeedStore();
    mocks.getMessages.mockResolvedValue(serverPage(range(1, 5)));
    renderFeed();
    await screen.findByRole('list');
    expect(separator()).toBeNull();
  });

  it('hides the separator when nothing is newer than the mark', async () => {
    mocks.getMessages.mockResolvedValue(serverPage(range(1, 3)));
    localStorage.setItem(
      FEED_LAST_SEEN_STORAGE_KEY,
      JSON.stringify({ at: msg(3).received_at, id: 3 })
    );
    renderFeed();
    await screen.findByRole('list');
    expect(separator()).toBeNull();
  });

  it('does not move the separator for live rows, and writes the mark on unmount and on hide', async () => {
    mocks.getMessages.mockResolvedValue(serverPage(range(1, 3)));
    const view = renderFeed();
    await screen.findByRole('list');
    await act(async () => {
      recordFeedMessage(msg(4));
      await Promise.resolve();
    });
    expect(separator()).toBeNull();

    Object.defineProperty(document, 'visibilityState', { configurable: true, value: 'hidden' });
    document.dispatchEvent(new Event('visibilitychange'));
    Object.defineProperty(document, 'visibilityState', { configurable: true, value: 'visible' });
    expect(JSON.parse(localStorage.getItem(FEED_LAST_SEEN_STORAGE_KEY) ?? 'null')).toEqual({
      at: msg(4).received_at,
      id: 4,
    });

    await act(async () => {
      recordFeedMessage(msg(5));
      await Promise.resolve();
    });
    view.unmount();
    expect(JSON.parse(localStorage.getItem(FEED_LAST_SEEN_STORAGE_KEY) ?? 'null')).toEqual({
      at: msg(5).received_at,
      id: 5,
    });
  });

  it('does not mark rows the reader never scrolled to', async () => {
    mocks.getMessages.mockResolvedValue(serverPage(range(1, 3)));
    const view = renderFeed();
    const scroller = await findScroller();
    mockGeometry(scroller, { scrollHeight: 1000, clientHeight: 100 });
    scroller.scrollTop = 0;
    fireEvent.scroll(scroller);
    await screen.findByRole('button', { name: i18n.t('feed.follow') });
    await act(async () => {
      recordFeedMessage(msg(4));
      recordFeedMessage(msg(5));
      await Promise.resolve();
    });
    view.unmount();
    const mark = JSON.parse(localStorage.getItem(FEED_LAST_SEEN_STORAGE_KEY) ?? 'null');
    expect(mark.id).toBeLessThan(4);
  });

  it('keeps working when localStorage throws', async () => {
    mocks.getMessages.mockResolvedValue(serverPage(range(1, 3)));
    const get = vi.spyOn(Storage.prototype, 'getItem').mockImplementation(() => {
      throw new Error('blocked');
    });
    const set = vi.spyOn(Storage.prototype, 'setItem').mockImplementation(() => {
      throw new Error('blocked');
    });
    const view = renderFeed();
    await screen.findByRole('list');
    expect(separator()).toBeNull();
    expect(() => view.unmount()).not.toThrow();
    get.mockRestore();
    set.mockRestore();
  });

  it('inserts a historical row in the middle silently: no follow count', async () => {
    mocks.getMessages.mockResolvedValue(serverPage([1, 2, 4]));
    renderFeed();
    const scroller = await findScroller();
    mockGeometry(scroller, { scrollHeight: 1000, clientHeight: 400 });
    scroller.scrollTop = 100;
    fireEvent.scroll(scroller);
    await screen.findByRole('button', { name: i18n.t('feed.follow') });

    await act(async () => {
      recordFeedMessage(msg(9, { received_at: msg(3).received_at, historical: true }));
      await Promise.resolve();
    });
    const texts = screen.getAllByRole('listitem').map((el) => el.textContent ?? '');
    expect(texts.findIndex((t) => t.includes('hello 9'))).toBe(
      texts.findIndex((t) => t.includes('hello 2')) + 1
    );
    expect(screen.getByRole('button', { name: i18n.t('feed.follow') })).toBeInTheDocument();
    expect(scroller.scrollTop).toBe(100);
  });

  it('re-pins when the reader sends a message, but not for a historical outgoing one', async () => {
    mocks.getMessages.mockResolvedValue(serverPage(range(1, 3)));
    renderFeed();
    const scroller = await findScroller();
    mockGeometry(scroller, { scrollHeight: 1000, clientHeight: 400 });
    scroller.scrollTop = 100;
    fireEvent.scroll(scroller);
    await screen.findByRole('button', { name: i18n.t('feed.follow') });

    await act(async () => {
      recordFeedMessage(msg(8, { outgoing: true, historical: true }));
      await Promise.resolve();
    });
    expect(scroller.scrollTop).toBe(100);

    await act(async () => {
      recordFeedMessage(msg(10, { outgoing: true }));
      await Promise.resolve();
    });
    await waitFor(() => expect(scroller.scrollTop).toBe(1000));
    expect(
      screen.queryByRole('button', { name: new RegExp(`^${i18n.t('feed.follow')}`) })
    ).toBeNull();
  });

  it('fetches the newest page again after a reconciliation reset', async () => {
    mocks.getMessages.mockResolvedValueOnce(serverPage(range(1, 3)));
    renderFeed();
    await screen.findByRole('list');
    mocks.getMessages.mockResolvedValueOnce(serverPage(range(50, 52)));
    await act(async () => {
      resetFeedForRefetch();
      await Promise.resolve();
    });
    await waitFor(() => expect(getFeedSnapshot().rows.map((m) => m.id)).toEqual([50, 51, 52]));
    expect(mocks.getMessages).toHaveBeenCalledTimes(2);
  });
});
