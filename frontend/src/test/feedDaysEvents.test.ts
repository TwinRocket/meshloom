import { describe, expect, it } from 'vitest';

import { buildFeedItems } from '../components/feed/feedDays';
import { countEventsArrivedSince } from '../components/feed/feedFollow';
import type { FeedEvent } from '../utils/feedEvents';
import type { Message } from '../types';

const T = 1_700_000_000;
const message = (id: number, at: number) => ({ id, received_at: at }) as Message;
const event = (key: string, at: number, atMs = at * 1000): FeedEvent => ({
  key,
  at,
  atMs,
  id: 'radio.offline',
});

describe('buildFeedItems with event rows', () => {
  it('merges messages and events by time, a message first on a tie', () => {
    const items = buildFeedItems(
      [message(1, T), message(2, T + 20)],
      null,
      [],
      [event('a', T), event('b', T + 10), event('c', T + 30)]
    );
    expect(items.filter((i) => i.kind !== 'day').map((i) => i.key)).toEqual([
      'row:1',
      'event:a',
      'event:b',
      'row:2',
      'event:c',
    ]);
  });

  it('puts the day separator before an event that opens a day', () => {
    const items = buildFeedItems([message(1, T)], null, [], [event('late', T + 3 * 86_400)]);
    expect(items.map((i) => i.kind)).toEqual(['day', 'row', 'day', 'event']);
  });

  it('shows events alone', () => {
    expect(buildFeedItems([], null, [], [event('a', T)]).map((i) => i.kind)).toEqual([
      'day',
      'event',
    ]);
  });

  it('counts the events produced after a browser-time baseline', () => {
    const events = [event('a', T, 1000), event('b', T + 1, 2000), event('c', T + 2, 3000)];
    expect(countEventsArrivedSince(events, 1500)).toBe(2);
  });
});
