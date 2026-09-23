import { renderHook, waitFor } from '@testing-library/react';
import { useRef } from 'react';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import { useConversationRouter } from '../hooks/useConversationRouter';
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

function renderRouter(settingsLoaded: boolean, directoryEnabled: boolean) {
  return renderHook(
    ({ loaded, enabled }: { loaded: boolean; enabled: boolean }) => {
      const hasSetDefaultConversation = useRef(false);
      const pendingDeleteFallbackRef = useRef(false);
      const router = useConversationRouter({
        channels: [publicChannel],
        contacts: [],
        contactsLoaded: true,
        suspendHashSync: true,
        setSidebarOpen: vi.fn(),
        pendingDeleteFallbackRef,
        hasSetDefaultConversation,
        settingsLoaded: loaded,
        directoryEnabled: enabled,
      });
      return { ...router, hasSetDefaultConversation };
    },
    { initialProps: { loaded: settingsLoaded, enabled: directoryEnabled } }
  );
}

describe('useConversationRouter #test', () => {
  beforeEach(() => {
    window.location.hash = '#test';
    localStorage.clear();
  });

  it('waits for settings before opening or discarding #test', async () => {
    const { result, rerender } = renderRouter(false, false);

    expect(result.current.activeConversation).toBeNull();
    expect(result.current.hasSetDefaultConversation.current).toBe(false);

    rerender({ loaded: true, enabled: true });
    await waitFor(() => {
      expect(result.current.activeConversation?.type).toBe('test');
    });
  });

  it('does not open #test when Community is off', async () => {
    const { result } = renderRouter(true, false);

    await waitFor(() => {
      expect(result.current.activeConversation?.type).toBe('channel');
    });
    expect(result.current.activeConversation?.id).toBe(PUBLIC_CHANNEL_KEY);
  });

  it('leaves #test when Community turns off', async () => {
    const { result, rerender } = renderRouter(true, true);

    await waitFor(() => {
      expect(result.current.activeConversation?.type).toBe('test');
    });

    rerender({ loaded: true, enabled: false });
    await waitFor(() => {
      expect(result.current.activeConversation?.type).toBe('channel');
    });
  });
});
