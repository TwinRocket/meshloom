import {
  act,
  cleanup,
  fireEvent,
  render,
  renderHook,
  screen,
  waitFor,
} from '@testing-library/react';
import { useRef } from 'react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import './qSlices';

const mocks = vi.hoisted(() => ({ getMessages: vi.fn() }));

vi.mock('../api', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../api')>();
  return { ...actual, api: { ...actual.api, getMessages: mocks.getMessages } };
});

import { useConversationRouter } from '../hooks/useConversationRouter';
import { FeedView } from '../components/feed/FeedView';
import { makeFeedConversation } from '../components/feed/feedConversation';
import i18n from '../i18n';
import { resetFeedStore } from '../stores/feedStore';
import {
  applyFeedQuery,
  canonicalFeedQuery,
  controlPreset,
  defaultFeedFilters,
  encodeFeedQuery,
  FEED_FILTERS_STORAGE_KEY,
  feedQueryOf,
  parseFeedQuery,
  type FeedFilters,
} from '../utils/feedFilters';
import {
  getLastViewedConversation,
  LAST_VIEWED_CONVERSATION_KEY,
  saveLastViewedConversation,
} from '../utils/lastViewedConversation';
import { getConversationHash, parseHashConversation } from '../utils/urlHash';

const PUBKEY = 'ab'.repeat(32);
const CHANNEL_KEY = '8B3387E9C5CDEA6AC9E5EDBAA115CD72';
const HEX32 = /[0-9a-f]{32}/i;

describe('feed hash grammar', () => {
  it('parses every key of the grammar', () => {
    const state = parseFeedQuery(
      `src=messages,events&types=chan&dir=out&muted=1&pending=1&node=${PUBKEY}&display=1`
    );
    expect(state).toEqual({
      display: true,
      filters: {
        sources: { messages: true, control: false, events: true },
        chan: true,
        priv: false,
        incoming: false,
        outgoing: true,
        includeMuted: true,
        includePending: true,
        node: `contact:${PUBKEY}`,
      },
    });
  });

  it('round-trips a full filter state through the query', () => {
    const filters: FeedFilters = {
      sources: { messages: false, control: true, events: true },
      chan: false,
      priv: true,
      incoming: true,
      outgoing: false,
      includeMuted: true,
      includePending: false,
      node: `contact:${PUBKEY}`,
    };
    const query = feedQueryOf(filters, true);
    expect(applyFeedQuery(defaultFeedFilters, query)).toEqual(filters);
    expect(parseFeedQuery(query).display).toBe(true);
    expect(canonicalFeedQuery(query)).toBe(query);
  });

  it('round-trips an empty selection (all sources off)', () => {
    const filters: FeedFilters = {
      ...defaultFeedFilters,
      sources: { messages: false, control: false, events: false },
    };
    expect(applyFeedQuery(controlPreset, feedQueryOf(filters))).toEqual(filters);
  });

  it('ignores unknown keys, unknown tokens and malformed values one by one', () => {
    const state = parseFeedQuery(
      `bogus=1&src=control,nope&types=zzz&dir=&muted=yes&pending=2&node=${'a'.repeat(31)}&display=true`
    );
    expect(state.filters).toEqual({
      sources: { messages: false, control: true, events: false },
      incoming: false,
      outgoing: false,
    });
    expect(state.display).toBe(false);
    expect(canonicalFeedQuery('bogus=1&x=y')).toBe('');
  });

  it('is case-insensitive on tokens and keeps the first of a repeated key', () => {
    expect(parseFeedQuery('src=Control&src=messages').filters.sources).toEqual({
      messages: false,
      control: true,
      events: false,
    });
  });

  it('never accepts nor emits a channel key', () => {
    expect(parseFeedQuery(`node=${CHANNEL_KEY}`).filters.node).toBeUndefined();
    expect(parseFeedQuery(`channel=${CHANNEL_KEY}`).filters).toEqual({});
    const filters: FeedFilters = { ...defaultFeedFilters, node: `channel:${CHANNEL_KEY}` };
    expect(feedQueryOf(filters, true)).not.toMatch(HEX32);
    expect(feedQueryOf(filters)).not.toContain('node');
    expect(
      encodeFeedQuery({ filters: { node: `contact:${CHANNEL_KEY}` }, display: false })
    ).not.toMatch(HEX32);
    for (const node of [null, 'us', 'unresolved', `channel:${CHANNEL_KEY}`]) {
      expect(feedQueryOf({ ...defaultFeedFilters, node })).not.toMatch(HEX32);
    }
  });
});

describe('feed hash routing', () => {
  it('parses #feed?... and #control?... into a canonical feedQuery', () => {
    expect(parseHashConversation('feed?src=control&bogus=1')).toEqual({
      type: 'feed',
      name: 'feed',
      feedQuery: 'src=control',
    });
    expect(parseHashConversation('control?display=1')).toEqual({
      type: 'feed',
      name: 'feed',
      feedPreset: 'control',
      feedQuery: 'display=1',
    });
    expect(parseHashConversation('feed?nothing=1')).toEqual({ type: 'feed', name: 'feed' });
  });

  it('encodes the hash back, keeping #control a preset', () => {
    expect(getConversationHash(makeFeedConversation('Feed', undefined, 'src=control'))).toBe(
      '#feed?src=control'
    );
    expect(getConversationHash(makeFeedConversation('Feed', 'control', 'display=1'))).toBe(
      '#control?display=1'
    );
    const hash = '#feed?src=messages,events&types=chan&dir=in&muted=1&pending=0&display=1';
    const parsed = parseHashConversation(hash.slice(1))!;
    expect(
      getConversationHash(makeFeedConversation('Feed', parsed.feedPreset, parsed.feedQuery))
    ).toBe(hash);
  });
});

describe('last viewed feed query', () => {
  beforeEach(() => localStorage.clear());

  it('stores and restores the query, sanitised on the way back', () => {
    saveLastViewedConversation(makeFeedConversation('Feed', undefined, 'src=control&types=chan'));
    expect(getLastViewedConversation()).toMatchObject({
      type: 'feed',
      feedQuery: 'src=control&types=chan',
    });
    localStorage.setItem(
      LAST_VIEWED_CONVERSATION_KEY,
      JSON.stringify({ type: 'feed', id: 'feed', name: 'Feed', feedQuery: 'bogus=1&dir=in' })
    );
    expect(getLastViewedConversation()?.feedQuery).toBe('dir=in');
  });
});

describe('FeedView with a pinned query', () => {
  beforeEach(() => {
    localStorage.clear();
    mocks.getMessages.mockReset();
    mocks.getMessages.mockResolvedValue([]);
    window.history.replaceState(null, '', '#feed');
  });
  afterEach(() => {
    cleanup();
    resetFeedStore();
  });

  it('applies the URL filters before the first history request: one fetch', async () => {
    // Stored filters say both directions; the URL wins and asks for received messages only.
    localStorage.setItem(FEED_FILTERS_STORAGE_KEY, JSON.stringify({ outgoing: true }));
    render(<FeedView initialQuery="types=chan&dir=in" contacts={[]} channels={[]} />);
    await waitFor(() => expect(mocks.getMessages).toHaveBeenCalled());
    await new Promise((resolve) => setTimeout(resolve, 400));
    expect(mocks.getMessages).toHaveBeenCalledTimes(1);
    expect(mocks.getMessages.mock.calls[0]).toEqual(
      expect.arrayContaining([expect.objectContaining({ type: 'CHAN', outgoing: false })])
    );
  });

  it('leaves a plain #feed URL alone', async () => {
    const replace = vi.spyOn(window.history, 'replaceState');
    render(<FeedView contacts={[]} channels={[]} />);
    await waitFor(() => expect(mocks.getMessages).toHaveBeenCalled());
    expect(replace).not.toHaveBeenCalled();
    replace.mockRestore();
  });

  it('mirrors a filter change in the URL (replaceState) and in the last viewed feed', async () => {
    const push = vi.spyOn(window.history, 'pushState');
    render(<FeedView initialQuery="display=1" contacts={[]} channels={[]} />);
    await waitFor(() => expect(mocks.getMessages).toHaveBeenCalled());
    const control = await screen.findByRole('button', { name: /control|contrôle/i });
    fireEvent.click(control);
    await waitFor(() => expect(window.location.hash).toMatch(/^#feed\?src=messages&/));
    expect(window.location.hash).toContain('display=1');
    expect(window.location.hash).not.toMatch(HEX32);
    expect(getLastViewedConversation()?.feedQuery).toBe(window.location.hash.split('?')[1]);
    expect(push).not.toHaveBeenCalled();
    push.mockRestore();
  });
});

describe('feed query kept in sync with the filters', () => {
  beforeEach(() => {
    localStorage.clear();
    mocks.getMessages.mockReset();
    mocks.getMessages.mockResolvedValue([]);
    window.history.replaceState(null, '', '#feed?src=messages,control');
  });
  afterEach(() => {
    cleanup();
    resetFeedStore();
  });

  const controlChip = () => screen.findByRole('button', { name: /control|contrôle/i });

  it('reports the new query upward after a chip change', async () => {
    const onQueryChange = vi.fn();
    render(
      <FeedView
        initialQuery="src=messages,control"
        onQueryChange={onQueryChange}
        contacts={[]}
        channels={[]}
      />
    );
    fireEvent.click(await controlChip());
    await waitFor(() => expect(onQueryChange).toHaveBeenCalled());
    expect(onQueryChange.mock.calls[onQueryChange.mock.calls.length - 1]?.[0]).toMatch(
      /^src=messages&/
    );
  });

  it('keeps the conversation query current, so a hash sync does not restore stale filters', async () => {
    const hook = renderHook(
      ({ suspend }: { suspend: boolean }) => {
        const pendingDeleteFallbackRef = useRef(false);
        return useConversationRouter({
          channels: [],
          contacts: [],
          contactsLoaded: true,
          suspendHashSync: suspend,
          setSidebarOpen: vi.fn(),
          pendingDeleteFallbackRef,
          settingsLoaded: true,
          directoryEnabled: false,
        });
      },
      { initialProps: { suspend: false } }
    );
    await waitFor(() => expect(hook.result.current.activeConversation?.type).toBe('feed'));
    expect(hook.result.current.activeConversation?.feedQuery).toBe('src=messages,control');

    // The view changes a chip: URL by replaceState, the conversation through syncFeedQuery.
    const next = feedQueryOf({
      ...defaultFeedFilters,
      sources: { messages: true, control: false, events: false },
    });
    window.history.replaceState(null, '', `#feed?${next}`);
    act(() => hook.result.current.syncFeedQuery(next));
    expect(hook.result.current.activeConversation?.feedQuery).toBe(next);

    // Settings open and close: the hash sync must write the current query, not the old one.
    hook.rerender({ suspend: true });
    window.history.replaceState(null, '', '#settings/local');
    hook.rerender({ suspend: false });
    act(() => hook.result.current.syncFeedQuery(next));
    window.history.replaceState(null, '', '#feed');
    hook.rerender({ suspend: true });
    hook.rerender({ suspend: false });
    expect(getConversationHash(hook.result.current.activeConversation)).toBe(`#feed?${next}`);
  });

  it('follows a new query from outside (popstate) without touching the remembered filters', async () => {
    localStorage.setItem(FEED_FILTERS_STORAGE_KEY, JSON.stringify({ includePending: true }));
    const view = render(
      <FeedView initialQuery="src=messages,control" contacts={[]} channels={[]} />
    );
    const chip = await controlChip();
    expect(chip).toHaveAttribute('aria-pressed', 'true');
    view.rerender(<FeedView initialQuery="src=messages" contacts={[]} channels={[]} />);
    await waitFor(() => expect(chip).toHaveAttribute('aria-pressed', 'false'));
    expect(JSON.parse(localStorage.getItem(FEED_FILTERS_STORAGE_KEY) ?? '{}')).toEqual({
      includePending: true,
    });
    // Back to a plain #feed: the remembered filters apply again.
    view.rerender(<FeedView contacts={[]} channels={[]} />);
    await waitFor(() => expect(chip).toHaveAttribute('aria-pressed', 'true'));
  });

  it('does not overwrite the remembered filters when opened with a query', async () => {
    localStorage.setItem(FEED_FILTERS_STORAGE_KEY, JSON.stringify({ includePending: true }));
    render(<FeedView initialQuery="src=control" contacts={[]} channels={[]} />);
    await controlChip();
    expect(JSON.parse(localStorage.getItem(FEED_FILTERS_STORAGE_KEY) ?? '{}')).toEqual({
      includePending: true,
    });
    // A change by the reader is theirs again and is remembered.
    fireEvent.click(await screen.findByRole('button', { name: i18n.t('feed.filters.messages') }));
    await waitFor(() =>
      expect(JSON.parse(localStorage.getItem(FEED_FILTERS_STORAGE_KEY) ?? '{}').sources).toEqual({
        messages: true,
        control: true,
        events: false,
      })
    );
  });
});
