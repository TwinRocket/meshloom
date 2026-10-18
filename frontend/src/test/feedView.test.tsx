import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
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
import { getFeedSnapshot, recordFeedMessage, resetFeedStore } from '../stores/feedStore';
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

function renderFeed() {
  return render(<FeedView contacts={[]} channels={channels} onBackToTools={vi.fn()} />);
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
