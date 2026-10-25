import { act, cleanup, render, screen } from '@testing-library/react';
import { memo } from 'react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import './qSlices';

const mocks = vi.hoisted(() => ({
  getMessages: vi.fn(),
  getPacketsHistory: vi.fn(),
  rowRenders: { count: 0 },
}));

vi.mock('../api', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../api')>();
  return {
    ...actual,
    api: {
      ...actual.api,
      getMessages: mocks.getMessages,
      getPacketsHistory: mocks.getPacketsHistory,
    },
  };
});

// Counts how often a row is handed new props: an outer memo with the same shallow comparison
// the real row has, so it renders exactly when the real one would.
vi.mock('../components/feed/FeedMessageRow', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../components/feed/FeedMessageRow')>();
  const Inner = actual.FeedMessageRow;
  const Counted = memo(function Counted(props: React.ComponentProps<typeof Inner>) {
    mocks.rowRenders.count += 1;
    return <Inner {...props} />;
  });
  return { ...actual, FeedMessageRow: Counted };
});

import { FeedView } from '../components/feed/FeedView';
import { createFeedNameResolver } from '../components/feed/feedNames';
import { recordFeedMessage, resetFeedStore } from '../stores/feedStore';
import type { Channel, Message } from '../types';

const KEY = '8B3387E9C5CDEA6AC9E5EDBAA115CD72';
const channels = [{ key: KEY, name: 'Public', muted: false }] as unknown as Channel[];

function msg(id: number): Message {
  return {
    id,
    type: 'CHAN',
    conversation_key: KEY,
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
  };
}

beforeEach(() => {
  mocks.rowRenders.count = 0;
  mocks.getMessages.mockReset();
  mocks.getPacketsHistory.mockReset();
  mocks.getPacketsHistory.mockResolvedValue({ items: [], total: 0, truncated: false, scanned: 0 });
  resetFeedStore();
  localStorage.clear();
});

afterEach(cleanup);

describe('feed row memoisation', () => {
  it('hands a row the same labels object until the row or the names change', () => {
    const resolve = createFeedNameResolver(channels, []);
    const row = msg(1);
    expect(resolve(row)).toBe(resolve(row));
    expect(resolve({ ...row })).not.toBe(resolve(row));
    expect(createFeedNameResolver(channels, [])(row)).not.toBe(resolve(row));
  });

  it('does not re-render the rows already shown when a new message arrives', async () => {
    mocks.getMessages.mockResolvedValue(Array.from({ length: 40 }, (_, i) => msg(40 - i)));
    render(<FeedView contacts={[]} channels={channels} onBackToTools={vi.fn()} />);
    await screen.findAllByRole('listitem');
    const mounted = screen.getAllByRole('listitem').length;
    expect(mounted).toBeGreaterThan(10);

    mocks.rowRenders.count = 0;
    await act(async () => {
      recordFeedMessage(msg(41));
      await Promise.resolve();
    });
    const afterOne = mocks.rowRenders.count;
    console.log(`row renders for one new message with ${mounted} rows shown: ${afterOne}`);
    // Before the labels and time objects were cached this was every mounted row (18 here, 300 at most).
    expect(afterOne).toBeLessThanOrEqual(1);
  });
});
