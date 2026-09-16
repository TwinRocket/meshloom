import React from 'react';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import i18n from '../i18n';

const mocks = vi.hoisted(() => ({
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
  hookFns: {
    observeMessage: vi.fn(() => ({ added: false, activeConversation: false })),
    refreshUnreads: vi.fn(async () => {}),
  },
}));

vi.mock('../api', () => ({ api: mocks.api }));
vi.mock('../useWebSocket', () => ({ useWebSocket: vi.fn() }));
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
      observeMessage: mocks.hookFns.observeMessage,
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
      refreshUnreads: mocks.hookFns.refreshUnreads,
    }),
  };
});
vi.mock('../components/MessageList', () => ({ MessageList: () => <div data-testid="message-list" /> }));
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
vi.mock('../components/CrackerPanel', () => ({
  CrackerPanel: ({ visible }: { visible?: boolean }) =>
    visible ? <div data-testid="cracker-panel" /> : null,
}));
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

const publicChannel = {
  key: '8B3387E9C5CDEA6AC9E5EDBAA115CD72',
  name: 'Public',
  is_hashtag: false,
  on_radio: false,
  last_read_at: null,
  favorite: false,
  muted: false,
};

const hashtagChannel = {
  key: 'AA'.repeat(16),
  name: '#fr',
  is_hashtag: true,
  on_radio: false,
  last_read_at: null,
  favorite: false,
  muted: false,
};

describe('App hashtag publish ownership', () => {
  beforeEach(() => {
    vi.clearAllMocks();
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
    mocks.api.getChannels.mockResolvedValue([publicChannel, hashtagChannel]);
    mocks.api.getContacts.mockResolvedValue([]);
    mocks.api.getHealth.mockResolvedValue(null);
    mocks.api.getUpdates.mockResolvedValue({
      current: '1.0.0',
      latest: null,
      update_available: false,
      html_url: null,
    });
    mocks.api.getCommunity.mockResolvedValue({
      enabled: true,
      locked: false,
      iata: 'LYS',
      broker_host: '',
      api_base: '',
      publisher_configured: true,
      publisher_connected: false,
      env_seeded: false,
    });
    mocks.api.getCommunityHashtags.mockResolvedValue({
      hashtags: [{ name: 'mesh', hash_byte: 'ab' }],
    });
    mocks.api.putCommunityHashtags.mockResolvedValue({ hashtags: [] });
  });

  it('does not PUT hashtag names when showCracker opens', async () => {
    render(<App />);
    await waitFor(() => {
      expect(screen.getByTestId('message-list')).toBeInTheDocument();
    });
    expect(mocks.api.putCommunityHashtags).not.toHaveBeenCalled();

    fireEvent.click(
      screen.getByRole('button', {
        name: i18n.t('sidebar.showChannelFinder'),
        hidden: true,
      })
    );

    await waitFor(() => {
      expect(mocks.api.getCommunityHashtags).toHaveBeenCalled();
    });
    expect(mocks.api.putCommunityHashtags).not.toHaveBeenCalled();
  });
});
