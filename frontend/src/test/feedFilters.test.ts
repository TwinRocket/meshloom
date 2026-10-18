import { afterEach, describe, expect, it, vi } from 'vitest';

import { buildFeedItems, createDayLabeler, localDayKey } from '../components/feed/feedDays';
import { createFeedNameResolver } from '../components/feed/feedNames';
import type { Channel, Contact, Message } from '../types';
import {
  countHiddenRows,
  defaultFeedFilters,
  FEED_FILTERS_STORAGE_KEY,
  filtersToQuery,
  hasEmptySelection,
  isChannelMuted,
  loadFeedFilters,
  normalizeFeedFilters,
  rowPassesFilters,
  saveFeedFilters,
  type FeedFilters,
} from '../utils/feedFilters';
import fixture from './fixtures/feedFilters.json';

type FixtureChannel = {
  key: string;
  muted: boolean;
  muted_until: number | null;
  membership: string;
};
interface FixtureCase {
  name: string;
  filters: Record<string, unknown>;
  row: { type: 'CHAN' | 'PRIV'; channel?: string; outgoing: boolean };
  expected: boolean;
  query: { type?: 'CHAN' | 'PRIV'; outgoing?: boolean } | null;
  server: boolean;
}

const fixtureChannels = fixture.channels as Record<string, FixtureChannel>;
const channels = Object.values(fixtureChannels) as unknown as Channel[];
const cases = fixture.cases as unknown as FixtureCase[];

function fixtureKey(row: FixtureCase['row']): string {
  if (row.type === 'PRIV') return fixture.dmKey;
  if (row.channel === 'deleted') return fixture.deletedChannelKey;
  return fixtureChannels[row.channel ?? 'public'].key;
}

function filtersOf(overrides: Record<string, unknown>): FeedFilters {
  const merged = {
    ...defaultFeedFilters,
    ...overrides,
    sources: { ...defaultFeedFilters.sources, ...(overrides.sources as object | undefined) },
  };
  return normalizeFeedFilters(merged);
}

afterEach(() => {
  vi.restoreAllMocks();
  localStorage.clear();
});

describe('rowPassesFilters (shared fixture)', () => {
  it.each(cases.map((c) => [c.name, c] as const))('%s', (_name, c) => {
    const row = { type: c.row.type, conversation_key: fixtureKey(c.row), outgoing: c.row.outgoing };
    const filters = filtersOf(c.filters);
    expect(rowPassesFilters(row, filters, channels, fixture.now)).toBe(c.expected);
    expect(filtersToQuery(filters)).toEqual(c.query ?? filtersToQuery(filtersOf({})));
    expect(hasEmptySelection(filters)).toBe(c.query === null);
  });
});

describe('isChannelMuted', () => {
  const channel = (muted: boolean, muted_until?: number | null) =>
    ({ key: 'K', muted, muted_until }) as unknown as Channel;

  it('mirrors effective_channel_muted', () => {
    expect(isChannelMuted(channel(false, 999), 100)).toBe(false);
    expect(isChannelMuted(channel(true), 100)).toBe(true);
    expect(isChannelMuted(channel(true, null), 100)).toBe(true);
    expect(isChannelMuted(channel(true, 101), 100)).toBe(true);
    expect(isChannelMuted(channel(true, 100), 100)).toBe(false);
    expect(isChannelMuted(channel(true, 50), 100)).toBe(false);
  });

  it('lets a timed mute expire as the clock moves, without any new channel frame', () => {
    const timed = fixtureChannels.mutedFuture as unknown as Channel;
    const row = { type: 'CHAN' as const, conversation_key: timed.key, outgoing: false };
    expect(rowPassesFilters(row, defaultFeedFilters, [timed], fixture.now)).toBe(false);
    expect(rowPassesFilters(row, defaultFeedFilters, [timed], fixture.now + 3601)).toBe(true);
  });

  it('matches the channel key case-insensitively', () => {
    const muted = fixtureChannels.mutedForever as unknown as Channel;
    const row = {
      type: 'CHAN' as const,
      conversation_key: muted.key.toLowerCase(),
      outgoing: false,
    };
    expect(rowPassesFilters(row, defaultFeedFilters, [muted], fixture.now)).toBe(false);
  });
});

describe('countHiddenRows', () => {
  it('counts what only the muted and pending chips hide', () => {
    const row = (name: string, outgoing = false) => ({
      type: 'CHAN' as const,
      conversation_key: fixtureChannels[name].key,
      outgoing,
    });
    const rows = [
      row('public'),
      row('mutedForever'),
      row('mutedForever'),
      row('mutedExpired'),
      row('pending'),
      row('pendingMuted'),
      { type: 'PRIV' as const, conversation_key: fixture.dmKey, outgoing: false },
    ];
    expect(countHiddenRows(rows, defaultFeedFilters, channels, fixture.now)).toEqual({
      muted: 3,
      pending: 2,
    });
    expect(
      countHiddenRows(rows, { ...defaultFeedFilters, includeMuted: true }, channels, fixture.now)
    ).toEqual({ muted: 0, pending: 2 });
    // A row another chip already hides is not counted twice.
    expect(
      countHiddenRows(rows, { ...defaultFeedFilters, chan: false }, channels, fixture.now)
    ).toEqual({ muted: 0, pending: 0 });
  });
});

describe('persistence', () => {
  it('round-trips through localStorage under the meshloom- key', () => {
    const filters = { ...defaultFeedFilters, includeMuted: true, outgoing: false };
    saveFeedFilters(filters);
    expect(localStorage.getItem(FEED_FILTERS_STORAGE_KEY)).not.toBeNull();
    expect(FEED_FILTERS_STORAGE_KEY).toBe('meshloom-feed-filters');
    expect(loadFeedFilters()).toEqual(filters);
  });

  it.each([
    ['absent', null],
    ['not JSON', '{oops'],
    ['not an object', '42'],
    ['null', 'null'],
  ])('gives the defaults when the stored value is %s', (_label, stored) => {
    if (stored !== null) localStorage.setItem(FEED_FILTERS_STORAGE_KEY, stored);
    expect(loadFeedFilters()).toEqual(defaultFeedFilters);
  });

  it('ignores a field of the wrong type and keeps the valid ones', () => {
    localStorage.setItem(
      FEED_FILTERS_STORAGE_KEY,
      JSON.stringify({ chan: 'yes', priv: false, sources: 3 })
    );
    expect(loadFeedFilters()).toEqual({ ...defaultFeedFilters, priv: false });
  });

  it('survives a storage that throws', () => {
    vi.spyOn(Storage.prototype, 'getItem').mockImplementation(() => {
      throw new Error('blocked');
    });
    vi.spyOn(Storage.prototype, 'setItem').mockImplementation(() => {
      throw new Error('blocked');
    });
    expect(loadFeedFilters()).toEqual(defaultFeedFilters);
    expect(() => saveFeedFilters(defaultFeedFilters)).not.toThrow();
  });
});

function message(extra: Partial<Message>): Message {
  return {
    id: 1,
    type: 'CHAN',
    conversation_key: 'AB'.repeat(16),
    text: 'Alice: hi',
    sender_timestamp: 1,
    received_at: 1,
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

describe('name resolution', () => {
  const channel = {
    key: 'AB'.repeat(16),
    name: 'Berlin',
    membership: 'adopted',
  } as unknown as Channel;
  const contact = (name: string | null) =>
    ({ public_key: 'cd'.repeat(32), name, last_advert: 1 }) as unknown as Contact;

  it('prefers the channel the client holds, then the WS name, then the abbreviated key', () => {
    const known = createFeedNameResolver([channel], [])(message({ channel_name: 'WS name' }));
    expect(known.conversation).toBe('Berlin');
    const fromFrame = createFeedNameResolver([], [])(message({ channel_name: 'WS name' }));
    expect(fromFrame.conversation).toBe('WS name');
    const deleted = createFeedNameResolver([], [])(message({}));
    expect(deleted.conversation).toBe('ABABABAB');
  });

  it('resolves a direct message by contact, then sender_name, then the key', () => {
    const dm = message({ type: 'PRIV', conversation_key: 'cd'.repeat(32), text: 'hello' });
    expect(createFeedNameResolver([], [contact('Bob')])(dm).conversation).toBe('Bob');
    expect(createFeedNameResolver([], [])({ ...dm, sender_name: 'Bobby' }).conversation).toBe(
      'Bobby'
    );
    expect(createFeedNameResolver([], [])(dm).conversation).toBe('cdcdcdcdcdcd');
    // Our own name on a sent DM is not the peer's.
    expect(
      createFeedNameResolver([], [])({ ...dm, outgoing: true, sender_name: 'Me' }).conversation
    ).toBe('cdcdcdcdcdcd');
  });

  it('splits the channel sender from the text and exposes the key for homonyms', () => {
    const labels = createFeedNameResolver([channel], [])(message({ sender_key: 'ef'.repeat(32) }));
    expect(labels).toMatchObject({ sender: 'Alice', content: 'hi', senderKey: 'efefefefefef' });
    const named = createFeedNameResolver([channel], [])(message({ sender_name: 'Alicia' }));
    expect(named.sender).toBe('Alicia');
  });

  it('flags a pending channel so its media is not loaded', () => {
    const pending = { ...channel, membership: 'pending' } as unknown as Channel;
    expect(createFeedNameResolver([pending], [])(message({})).pendingChannel).toBe(true);
    expect(createFeedNameResolver([channel], [])(message({})).pendingChannel).toBe(false);
  });
});

describe('day separators', () => {
  const at = (iso: string) => new Date(iso).getTime() / 1000;

  it('puts one separator before the first row of each local day', () => {
    const rows = [
      message({ id: 1, received_at: at('2026-03-01T09:00:00') }),
      message({ id: 2, received_at: at('2026-03-01T23:59:00') }),
      message({ id: 3, received_at: at('2026-03-02T00:01:00') }),
    ];
    expect(buildFeedItems(rows).map((i) => (i.kind === 'row' ? i.row.id : i.kind))).toEqual([
      'day',
      1,
      2,
      'day',
      3,
    ]);
    expect(buildFeedItems([])).toEqual([]);
  });

  it('labels today, yesterday and older days in the UI language', () => {
    const now = at('2026-03-10T12:00:00');
    const en = createDayLabeler('en');
    expect(en(at('2026-03-10T01:00:00'), now)).toBe('Today');
    expect(en(at('2026-03-09T23:00:00'), now)).toBe('Yesterday');
    expect(en(at('2026-03-01T10:00:00'), now)).toContain('2026');
    expect(createDayLabeler('fr')(at('2026-03-10T08:00:00'), now)).toMatch(/^Aujourd.hui$/);
    expect(localDayKey(now)).toBe(localDayKey(at('2026-03-10T00:00:01')));
  });
});
