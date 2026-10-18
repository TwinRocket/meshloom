/**
 * The feed store must stay off the chat render path: nothing between `App` and
 * `MessageList` may subscribe to it (same invariant as `appPacketIsolation.test.tsx`,
 * which explains why the real ancestor chain has to be mounted).
 */
import React from 'react';
import { act, render, screen, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import './eSlices';

const mocks = vi.hoisted(() => ({
  messageList: vi.fn(() => <div data-testid="message-list" />),
  api: {
    getRadioConfig: vi.fn(),
    getSettings: vi.fn(),
    getUndecryptedPacketCount: vi.fn(),
    getChannels: vi.fn(),
    getContacts: vi.fn(),
    getHealth: vi.fn(),
    getCommunity: vi.fn(),
    getUpdates: vi.fn(),
    getCommunityHashtags: vi.fn(),
    putCommunityHashtags: vi.fn(),
  },
  ws: { options: null as null | { onMessage?: (m: unknown) => void } },
}));

vi.mock('../api', () => ({ api: mocks.api }));
vi.mock('../useWebSocket', () => ({
  useWebSocket: (options: { onMessage?: (m: unknown) => void }) => {
    mocks.ws.options = options;
  },
}));

vi.mock('../contexts/PushSubscriptionContext', () => ({
  usePush: () => ({
    isSupported: false,
    isSubscribed: false,
    currentSubscriptionId: null,
    allSubscriptions: [],
    pushConversations: [],
    loading: false,
    subscribe: vi.fn(async () => null),
    unsubscribe: vi.fn(async () => {}),
    toggleConversation: vi.fn(async () => {}),
    isConversationPushEnabled: () => false,
    isConversationMediaEnabled: () => false,
    deleteSubscription: vi.fn(async () => {}),
    testPush: vi.fn(async () => {}),
    refreshSubscriptions: vi.fn(async () => []),
    refreshConversations: vi.fn(async () => {}),
  }),
}));

vi.mock('../hooks', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../hooks')>();
  return {
    ...actual,
    useConversationMessages: () => ({
      messages: [],
      messagesLoading: false,
      loadingOlder: false,
      hasOlderMessages: false,
      hasNewerMessages: false,
      loadingNewer: false,
      fetchOlderMessages: vi.fn(async () => {}),
      fetchNewerMessages: vi.fn(async () => {}),
      jumpToBottom: vi.fn(),
      reloadCurrentConversation: vi.fn(),
      observeMessage: vi.fn(() => ({ added: false, activeConversation: false })),
      receiveMessageAck: vi.fn(),
      reconcileOnReconnect: vi.fn(),
      renameConversationMessages: vi.fn(),
      removeConversationMessages: vi.fn(),
      removeMessage: vi.fn(),
      clearConversationMessages: vi.fn(),
    }),
    useUnreadCounts: () => ({
      unreadCounts: {},
      mentions: {},
      lastMessageTimes: {},
      lastMessagePreviews: {},
      unreadLastReadAts: {},
      firstUnreadIds: {},
      recordMessageEvent: vi.fn(),
      renameConversationState: vi.fn(),
      removeConversationState: vi.fn(),
      markAllRead: vi.fn(),
      refreshUnreads: vi.fn(async () => {}),
    }),
  };
});

vi.mock('../components/MessageList', () => ({ MessageList: mocks.messageList }));
vi.mock('../components/MessageInput', () => ({
  MessageInput: React.forwardRef((_props, ref) => {
    React.useImperativeHandle(ref, () => ({ appendText: vi.fn(), focus: vi.fn() }));
    return <div data-testid="message-input" />;
  }),
}));
vi.mock('../components/NewMessageModal', () => ({ NewMessageModal: () => null }));
vi.mock('../components/SettingsModal', () => ({
  SettingsModal: () => null,
  SETTINGS_SECTION_ORDER: ['radio'],
  SETTINGS_SECTION_LABELS: { radio: 'Radio' },
}));
vi.mock('../components/MapView', () => ({ MapView: () => null }));
vi.mock('../components/VisualizerView', () => ({ VisualizerView: () => null }));
vi.mock('../components/LiveView', () => ({ LiveView: () => null }));
vi.mock('../components/CrackerPanel', () => ({ CrackerPanel: () => null }));
vi.mock('../components/ui/sonner', () => ({
  Toaster: () => null,
  toast: { success: vi.fn(), error: vi.fn() },
}));
vi.mock('../utils/urlHash', () => ({
  parseHashConversation: () => null,
  parseHashSettingsSection: () => null,
  updateUrlHash: vi.fn(),
  pushUrlHash: vi.fn(),
  updateSettingsHash: vi.fn(),
  pushSettingsHash: vi.fn(),
  getSettingsHash: (section: string) => `#settings/${section}`,
  getMapFocusHash: () => '#map',
}));

import { App } from '../App';
import {
  activateFeedStore,
  getFeedSnapshot,
  resetFeedStore,
  useFeedRows,
} from '../stores/feedStore';
import type { Message } from '../types';

function frame(id: number): Message {
  return {
    id,
    type: 'CHAN',
    conversation_key: '8B3387E9C5CDEA6AC9E5EDBAA115CD72',
    text: `m${id}`,
    sender_timestamp: 1700000000 + id,
    received_at: 1700000000 + id,
    paths: null,
    txt_type: 0,
    signature: null,
    sender_key: null,
    outgoing: false,
    acked: 0,
    sender_name: null,
  };
}

const publicChannel = {
  key: '8B3387E9C5CDEA6AC9E5EDBAA115CD72',
  name: 'Public',
  is_hashtag: false,
  on_radio: false,
  last_read_at: null,
  favorite: false,
  muted: false,
};

async function pushFrames(count: number) {
  await act(async () => {
    for (let i = 1; i <= count; i += 1) mocks.ws.options?.onMessage?.(frame(i));
    await Promise.resolve();
  });
}

describe('feed messages and the chat render path', () => {
  beforeEach(() => {
    window.location.hash = '';
    vi.clearAllMocks();
    resetFeedStore();
    mocks.api.getRadioConfig.mockResolvedValue({
      public_key: 'aa'.repeat(32),
      name: 'TestNode',
      lat: 0,
      lon: 0,
      tx_power: 17,
      max_tx_power: 22,
      radio: { freq: 910.525, bw: 62.5, sf: 7, cr: 5 },
      path_hash_mode: 0,
      path_hash_mode_supported: false,
    });
    mocks.api.getSettings.mockResolvedValue({
      ui_preferences: { nav_rail: [], theme: '' },
      max_radio_contacts: 200,
      auto_decrypt_dm_on_advert: false,
      last_message_times: {},
      advert_interval: 0,
      last_advert_time: 0,
      flood_scope: '',
      known_regions: [],
      blocked_keys: [],
      blocked_names: [],
    });
    mocks.api.getUndecryptedPacketCount.mockResolvedValue({ count: 0 });
    mocks.api.getChannels.mockResolvedValue([publicChannel]);
    mocks.api.getContacts.mockResolvedValue([]);
    mocks.api.getHealth.mockResolvedValue(null);
    mocks.api.getUpdates.mockResolvedValue({
      current: '1.0.0',
      latest: null,
      update_available: false,
      html_url: null,
      install_kind: 'source',
      apply_supported: false,
      auto_update: false,
      job: { state: 'idle', phase: null, percent: null, error: null, started_at: null },
    });
    mocks.api.getCommunity.mockResolvedValue({
      enabled: true,
      iata: 'LYS',
      publisher_configured: true,
      publisher_connected: false,
    });
    mocks.api.getCommunityHashtags.mockResolvedValue({ hashtags: [] });
    mocks.api.putCommunityHashtags.mockResolvedValue({ hashtags: [] });
  });

  it('does not re-render the message list when 200 message frames arrive', async () => {
    render(<App />);
    await waitFor(() => expect(screen.getByTestId('message-list')).toBeInTheDocument());
    activateFeedStore();

    const rendersBefore = mocks.messageList.mock.calls.length;
    await pushFrames(200);

    // Guards against passing for the wrong reason: the frames really reached the store.
    expect(getFeedSnapshot().rows).toHaveLength(200);
    expect(mocks.messageList.mock.calls.length).toBe(rendersBefore);
  });

  it('re-renders the message list if an ancestor subscribes (negative control)', async () => {
    // The ancestor must build <App /> in its own render, or React bails out on identity.
    function SubscribingAncestor() {
      useFeedRows();
      return <App />;
    }

    render(<SubscribingAncestor />);
    await waitFor(() => expect(screen.getByTestId('message-list')).toBeInTheDocument());
    activateFeedStore();

    const rendersBefore = mocks.messageList.mock.calls.length;
    await pushFrames(200);

    expect(mocks.messageList.mock.calls.length).toBeGreaterThan(rendersBefore);
  });
});
