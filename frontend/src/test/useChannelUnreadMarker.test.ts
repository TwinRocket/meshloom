/**
 * The unread divider anchor must be taken from the unread count of the render
 * that opens a channel, even though useUnreadCounts clears that count for the
 * viewed conversation in the same render pass.
 */
import { act, renderHook, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import { useChannelUnreadMarker } from '../hooks/useChannelUnreadMarker';
import { useUnreadCounts } from '../hooks/useUnreadCounts';
import type { Channel, Conversation } from '../types';
import { getStateKey } from '../utils/conversationState';

vi.mock('../api', () => ({
  api: {
    getUnreads: vi.fn(),
    markChannelRead: vi.fn().mockResolvedValue({ status: 'ok', key: '' }),
    markContactRead: vi.fn().mockResolvedValue({ status: 'ok', public_key: '' }),
    markAllRead: vi.fn().mockResolvedValue({ status: 'ok' }),
  },
}));

vi.mock('../prefetch', () => ({
  takePrefetchOrFetch: vi.fn((_key: string, fetcher: () => Promise<unknown>) => fetcher()),
}));

const CHANNEL_A = 'AA000000000000000000000000000000';
const CHANNEL_B = 'BB000000000000000000000000000000';

function channel(key: string): Channel {
  return {
    key,
    name: key.slice(0, 2),
    is_hashtag: false,
    on_radio: false,
    last_read_at: null,
    favorite: false,
    muted: false,
  };
}

function conv(key: string): Conversation {
  return { type: 'channel', id: key, name: key.slice(0, 2) };
}

const channels = [channel(CHANNEL_A), channel(CHANNEL_B)];

function renderApp(initial: Conversation | null) {
  return renderHook(
    ({ active }: { active: Conversation | null }) => {
      const unread = useUnreadCounts(channels, [], active);
      const marker = useChannelUnreadMarker(active, unread.unreadCounts, unread.firstUnreadIds);
      return { ...unread, ...marker };
    },
    { initialProps: { active: initial } }
  );
}

describe('useChannelUnreadMarker with useUnreadCounts', () => {
  beforeEach(async () => {
    vi.clearAllMocks();
    const { api } = await import('../api');
    vi.mocked(api.getUnreads).mockResolvedValue({
      counts: { [getStateKey('channel', CHANNEL_B)]: 3 },
      mentions: {},
      last_message_times: {},
      last_message_previews: {},
      first_unread_ids: { [getStateKey('channel', CHANNEL_B)]: 42 },
      last_read_ats: {},
    });
  });

  it('anchors the divider when opening a channel with unreads, then clears its count', async () => {
    const { result, rerender } = renderApp(conv(CHANNEL_A));
    await waitFor(() => {
      expect(result.current.unreadCounts[getStateKey('channel', CHANNEL_B)]).toBe(3);
    });
    expect(result.current.channelUnreadMarker).toBeNull();

    rerender({ active: conv(CHANNEL_B) });

    expect(result.current.channelUnreadMarker).toEqual({ channelId: CHANNEL_B, messageId: 42 });
    expect(result.current.unreadCounts[getStateKey('channel', CHANNEL_B)]).toBeUndefined();
  });

  it('stays dismissed while the channel stays open, and resets on leaving', async () => {
    const { result, rerender } = renderApp(conv(CHANNEL_A));
    await waitFor(() => {
      expect(result.current.unreadCounts[getStateKey('channel', CHANNEL_B)]).toBe(3);
    });
    rerender({ active: conv(CHANNEL_B) });
    expect(result.current.channelUnreadMarker).not.toBeNull();

    act(() => result.current.dismissChannelUnreadMarker());
    expect(result.current.channelUnreadMarker).toBeNull();
    rerender({ active: conv(CHANNEL_B) });
    expect(result.current.channelUnreadMarker).toBeNull();

    rerender({ active: null });
    expect(result.current.channelUnreadMarker).toBeNull();
    rerender({ active: conv(CHANNEL_A) });
    expect(result.current.channelUnreadMarker).toBeNull();
  });
});
