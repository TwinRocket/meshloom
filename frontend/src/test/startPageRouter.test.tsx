import { renderHook, waitFor } from '@testing-library/react';
import { useRef } from 'react';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import { useConversationRouter } from '../hooks/useConversationRouter';
import {
  REOPEN_LAST_CONVERSATION_KEY,
  LAST_VIEWED_CONVERSATION_KEY,
} from '../utils/lastViewedConversation';
import { PUBLIC_CHANNEL_KEY } from '../utils/publicChannel';
import type { Channel } from '../types';

vi.mock('../components/ui/sonner', () => ({
  toast: { error: vi.fn(), success: vi.fn() },
}));

const publicChannel: Channel = {
  key: PUBLIC_CHANNEL_KEY,
  name: 'Public',
  is_hashtag: false,
  on_radio: true,
  last_read_at: null,
  favorite: false,
  muted: false,
};

function renderRouter(
  startView: string,
  options: { settingsLoaded?: boolean; allowed?: (id: string) => boolean } = {}
) {
  return renderHook(
    ({ loaded }: { loaded: boolean }) => {
      const pendingDeleteFallbackRef = useRef(false);
      return useConversationRouter({
        channels: [publicChannel],
        contacts: [],
        contactsLoaded: true,
        suspendHashSync: true,
        setSidebarOpen: vi.fn(),
        pendingDeleteFallbackRef,
        settingsLoaded: loaded,
        directoryEnabled: false,
        startView,
        toolAllowed: options.allowed,
      });
    },
    { initialProps: { loaded: options.settingsLoaded ?? true } }
  );
}

describe('useConversationRouter start page', () => {
  beforeEach(() => {
    window.history.replaceState(null, '', '/');
    localStorage.clear();
  });

  it('opens the feed when the URL has no hash', async () => {
    const { result } = renderRouter('feed');
    await waitFor(() => expect(result.current.activeConversation?.type).toBe('feed'));
  });

  it('opens the Control preset of the feed', async () => {
    const { result } = renderRouter('control');
    await waitFor(() => {
      expect(result.current.activeConversation).toMatchObject({
        type: 'feed',
        feedPreset: 'control',
      });
    });
  });

  it('opens the feed with the filters of a start page query', async () => {
    const { result } = renderRouter('control?display=1&bogus=1');
    await waitFor(() => {
      expect(result.current.activeConversation).toMatchObject({
        type: 'feed',
        feedPreset: 'control',
        feedQuery: 'display=1',
      });
    });
  });

  it('waits for settings before choosing', async () => {
    const { result, rerender } = renderRouter('feed', { settingsLoaded: false });
    expect(result.current.activeConversation).toBeNull();
    rerender({ loaded: true });
    await waitFor(() => expect(result.current.activeConversation?.type).toBe('feed'));
  });

  it('lets the hash win', async () => {
    window.location.hash = '#map';
    const { result } = renderRouter('feed');
    await waitFor(() => expect(result.current.activeConversation?.type).toBe('map'));
  });

  it('lets reopen-last win when enabled and something is stored', async () => {
    localStorage.setItem(REOPEN_LAST_CONVERSATION_KEY, '1');
    localStorage.setItem(
      LAST_VIEWED_CONVERSATION_KEY,
      JSON.stringify({ type: 'live', id: 'live', name: 'Live' })
    );
    const { result } = renderRouter('feed');
    await waitFor(() => expect(result.current.activeConversation?.type).toBe('live'));
  });

  it('uses the start page when reopen-last is on but nothing is stored', async () => {
    localStorage.setItem(REOPEN_LAST_CONVERSATION_KEY, '1');
    const { result } = renderRouter('feed');
    await waitFor(() => expect(result.current.activeConversation?.type).toBe('feed'));
  });

  it('keeps today’s default (Public) when the start page is empty', async () => {
    const { result } = renderRouter('');
    await waitFor(() => expect(result.current.activeConversation?.type).toBe('channel'));
  });

  it.each(['nope', 'channel/ABC', 'test'])('ignores %s and falls back to Public', async (value) => {
    const { result } = renderRouter(value);
    await waitFor(() => expect(result.current.activeConversation?.type).toBe('channel'));
  });

  it('ignores a tool the role may not use', async () => {
    const { result } = renderRouter('trace', { allowed: (id) => id !== 'trace' });
    await waitFor(() => expect(result.current.activeConversation?.type).toBe('channel'));
  });
});
