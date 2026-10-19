import { act, renderHook, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import { useRealtimeAppState } from '../hooks/useRealtimeAppState';
import i18n from '../i18n';
import { RADIO_REBOOTING_TOAST_ID } from '../types';
import {
  getRawPacketStatsSession,
  getRawPackets,
  resetRawPacketStore,
  seedRawPacketStore,
} from '../stores/rawPacketStore';
import {
  activateFeedStore,
  getFeedSnapshot,
  mergeHistoryPage,
  recordFeedMessage,
  resetFeedStore,
} from '../stores/feedStore';
import type { Channel, Contact, Conversation, HealthStatus, Message, RawPacket } from '../types';

const mocks = vi.hoisted(() => ({
  api: {
    getChannels: vi.fn(),
    getMessages: vi.fn(),
  },
  toast: Object.assign(vi.fn(), {
    success: vi.fn(),
    error: vi.fn(),
    info: vi.fn(),
  }),
}));

vi.mock('../api', () => ({
  api: mocks.api,
}));

vi.mock('../components/ui/sonner', () => ({
  toast: mocks.toast,
}));

const publicChannel: Channel = {
  key: '8B3387E9C5CDEA6AC9E5EDBAA115CD72',
  name: 'Public',
  is_hashtag: false,
  on_radio: false,
  last_read_at: null,
  favorite: false,
  muted: false,
};

const rawPacketFixture: RawPacket = {
  id: 1,
  observation_id: 2,
  timestamp: 1700000000,
  data: 'aabb',
  payload_type: 'GROUP_TEXT',
  snr: 7.5,
  rssi: -80,
  decrypted: false,
  decrypted_info: null,
};

const incomingDm: Message = {
  id: 7,
  type: 'PRIV',
  conversation_key: 'aa'.repeat(32),
  text: 'hello',
  sender_timestamp: 1700000000,
  received_at: 1700000001,
  paths: null,
  txt_type: 0,
  signature: null,
  sender_key: 'aa'.repeat(32),
  outgoing: false,
  acked: 0,
  sender_name: 'Alice',
};

function createRealtimeArgs(overrides: Partial<Parameters<typeof useRealtimeAppState>[0]> = {}) {
  const setHealth = vi.fn();
  const setChannels = vi.fn();
  const setContacts = vi.fn();

  return {
    args: {
      prevHealthRef: { current: null as HealthStatus | null },
      setHealth,
      fetchConfig: vi.fn(),
      reconcileOnReconnect: vi.fn(),
      refreshUnreads: vi.fn(async () => {}),
      setChannels,
      fetchAllContacts: vi.fn(async () => [] as Contact[]),
      setContacts,
      blockedKeysRef: { current: [] as string[] },
      channelsRef: { current: [publicChannel] },
      contactsRef: { current: [] as Contact[] | null },
      blockedNamesRef: { current: [] as string[] },
      activeConversationRef: { current: null as Conversation | null },
      observeMessage: vi.fn(() => ({ added: false, activeConversation: false })),
      recordMessageEvent: vi.fn(),
      renameConversationState: vi.fn(),
      removeConversationState: vi.fn(),
      checkMention: vi.fn(() => false),
      pendingDeleteFallbackRef: { current: false },
      setActiveConversation: vi.fn(),
      renameConversationMessages: vi.fn(),
      removeConversationMessages: vi.fn(),
      receiveMessageAck: vi.fn(),
      removeMessage: vi.fn(),
      ...overrides,
    },
    fns: {
      setHealth,
      setChannels,
      setContacts,
    },
  };
}

const controlPacketFixture: RawPacket = {
  ...rawPacketFixture,
  id: 5,
  observation_id: 5,
  // Request, flood route: header 0x01, then a path length and an encrypted-looking body.
  data: `0100bbaaaabb${'11'.repeat(16)}`,
  payload_type: 'Request',
};

describe('useRealtimeAppState', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    resetRawPacketStore();
    resetFeedStore();
    mocks.api.getChannels.mockResolvedValue([publicChannel]);
    mocks.api.getMessages.mockResolvedValue([]);
  });

  it('reconnect clears raw packets and refetches channels/contacts/unreads', async () => {
    const contacts: Contact[] = [
      {
        public_key: 'bb'.repeat(32),
        name: 'Bob',
        type: 1,
        flags: 0,
        direct_path: null,
        direct_path_len: 0,
        direct_path_hash_mode: 0,
        last_advert: null,
        lat: null,
        lon: null,
        last_seen: null,
        on_radio: false,
        favorite: false,
        last_contacted: null,
        last_read_at: null,
        first_seen: null,
      },
    ];

    const { args, fns } = createRealtimeArgs({
      fetchAllContacts: vi.fn(async () => contacts),
    });

    const { result } = renderHook(() => useRealtimeAppState(args));

    seedRawPacketStore({ packets: [rawPacketFixture] });

    act(() => {
      result.current.onReconnect?.();
    });

    await waitFor(() => {
      expect(args.reconcileOnReconnect).toHaveBeenCalledTimes(1);
      expect(args.refreshUnreads).toHaveBeenCalledTimes(1);
      expect(mocks.api.getChannels).toHaveBeenCalledTimes(1);
      expect(args.fetchAllContacts).toHaveBeenCalledTimes(1);
      expect(getRawPackets()).toEqual([]);
      expect(fns.setChannels).toHaveBeenCalledWith([publicChannel]);
      expect(fns.setContacts).toHaveBeenCalledWith(contacts);
    });
  });

  it('reconnect snapshot does not overwrite contacts/channels changed by newer WS deltas', async () => {
    const mk = (key: string, name: string): Contact => ({
      public_key: key,
      name,
      type: 1,
      flags: 0,
      direct_path: null,
      direct_path_len: 0,
      direct_path_hash_mode: 0,
      last_advert: null,
      lat: null,
      lon: null,
      last_seen: null,
      on_radio: false,
      favorite: false,
      last_contacted: null,
      last_read_at: null,
      first_seen: null,
    });
    const a = 'aa'.repeat(32);
    const b = 'bb'.repeat(32);
    const c = 'cc'.repeat(32);

    let resolveContacts!: (v: Contact[]) => void;
    const slowContacts = new Promise<Contact[]>((r) => (resolveContacts = r));
    // Live state as the app holds it: A (stale name), B (about to be deleted).
    let liveContacts: Contact[] = [mk(a, 'Old A'), mk(b, 'Bob')];
    const setContacts = vi.fn((u: Contact[] | ((p: Contact[]) => Contact[])) => {
      liveContacts = typeof u === 'function' ? u(liveContacts) : u;
    });
    const { args } = createRealtimeArgs({
      fetchAllContacts: vi.fn(() => slowContacts),
      setContacts: setContacts as never,
    });
    const { result } = renderHook(() => useRealtimeAppState(args));

    act(() => {
      result.current.onReconnect?.();
    });
    // Deltas arrive while the REST snapshot is still in flight.
    act(() => {
      result.current.onContact?.(mk(a, 'New A'));
      result.current.onContactDeleted?.(b);
    });
    // The older snapshot still lists B and the old name of A, and adds C.
    await act(async () => {
      resolveContacts([mk(a, 'Old A'), mk(b, 'Bob'), mk(c, 'Carol')]);
      await slowContacts;
    });

    await waitFor(() => {
      // Server order is preserved: A keeps its slot (with the live name), B is gone.
      expect(liveContacts.map((x) => x.name)).toEqual(['New A', 'Carol']);
    });
  });

  it('only the newest reconnect snapshot is applied', async () => {
    let resolveFirst!: (v: Contact[]) => void;
    const first = new Promise<Contact[]>((r) => (resolveFirst = r));
    const fetchAllContacts = vi
      .fn<() => Promise<Contact[]>>()
      .mockReturnValueOnce(first)
      .mockResolvedValueOnce([]);
    const { args, fns } = createRealtimeArgs({ fetchAllContacts });
    const { result } = renderHook(() => useRealtimeAppState(args));

    act(() => {
      result.current.onReconnect?.();
      result.current.onReconnect?.();
    });
    await waitFor(() => expect(fns.setContacts).toHaveBeenCalledTimes(1));
    await act(async () => {
      resolveFirst([]);
      await first;
    });
    expect(fns.setContacts).toHaveBeenCalledTimes(1);
  });

  it('reconnect skips active-conversation reconcile while browsing mid-history', async () => {
    const contacts: Contact[] = [
      {
        public_key: 'bb'.repeat(32),
        name: 'Bob',
        type: 1,
        flags: 0,
        direct_path: null,
        direct_path_len: 0,
        direct_path_hash_mode: 0,
        last_advert: null,
        lat: null,
        lon: null,
        last_seen: null,
        on_radio: false,
        favorite: false,
        last_contacted: null,
        last_read_at: null,
        first_seen: null,
      },
    ];

    const { args, fns } = createRealtimeArgs({
      fetchAllContacts: vi.fn(async () => contacts),
    });

    const { result } = renderHook(() => useRealtimeAppState(args));

    seedRawPacketStore({ packets: [rawPacketFixture] });

    act(() => {
      result.current.onReconnect?.();
    });

    await waitFor(() => {
      expect(args.reconcileOnReconnect).toHaveBeenCalledTimes(1);
      expect(args.refreshUnreads).toHaveBeenCalledTimes(1);
      expect(mocks.api.getChannels).toHaveBeenCalledTimes(1);
      expect(args.fetchAllContacts).toHaveBeenCalledTimes(1);
      expect(getRawPackets()).toEqual([]);
      expect(fns.setChannels).toHaveBeenCalledWith([publicChannel]);
      expect(fns.setContacts).toHaveBeenCalledWith(contacts);
    });
  });

  it('tracks unread state for a new non-active incoming message', () => {
    const { args } = createRealtimeArgs({
      checkMention: vi.fn(() => true),
      observeMessage: vi.fn(() => ({ added: true, activeConversation: false })),
    });

    const { result } = renderHook(() => useRealtimeAppState(args));

    act(() => {
      result.current.onMessage?.(incomingDm);
    });

    expect(args.observeMessage).toHaveBeenCalledWith(incomingDm);
    expect(args.recordMessageEvent).toHaveBeenCalledWith({
      msg: incomingDm,
      activeConversation: false,
      isNewMessage: true,
      hasMention: true,
    });
  });

  it('deleting the active contact clears it and marks fallback recovery pending', () => {
    const pendingDeleteFallbackRef = { current: false };
    const activeConversationRef = {
      current: {
        type: 'contact',
        id: incomingDm.conversation_key,
        name: 'Alice',
      } satisfies Conversation,
    };
    const { args, fns } = createRealtimeArgs({
      activeConversationRef,
      pendingDeleteFallbackRef,
    });

    const { result } = renderHook(() => useRealtimeAppState(args));

    act(() => {
      result.current.onContactDeleted?.(incomingDm.conversation_key);
    });

    expect(fns.setContacts).toHaveBeenCalledWith(expect.any(Function));
    expect(args.removeConversationMessages).toHaveBeenCalledWith(incomingDm.conversation_key);
    expect(args.removeConversationState).toHaveBeenCalledWith(
      `contact-${incomingDm.conversation_key}`
    );
    expect(args.setActiveConversation).toHaveBeenCalledWith(null);
    expect(pendingDeleteFallbackRef.current).toBe(true);
  });

  it('deleting a channel drops its unread/preview conversation state', () => {
    const { args, fns } = createRealtimeArgs();
    const { result } = renderHook(() => useRealtimeAppState(args));

    act(() => {
      result.current.onChannelDeleted?.(publicChannel.key);
    });

    expect(fns.setChannels).toHaveBeenCalledWith(expect.any(Function));
    expect(args.removeConversationMessages).toHaveBeenCalledWith(publicChannel.key);
    expect(args.removeConversationState).toHaveBeenCalledWith(`channel-${publicChannel.key}`);
  });

  it('resolves a prefix-only contact into a full key and updates active conversation state', () => {
    const previousPublicKey = 'abc123def456';
    const resolvedContact: Contact = {
      public_key: 'aa'.repeat(32),
      name: null,
      type: 0,
      flags: 0,
      direct_path: null,
      direct_path_len: -1,
      direct_path_hash_mode: -1,
      last_advert: null,
      lat: null,
      lon: null,
      last_seen: null,
      on_radio: false,
      favorite: false,
      last_contacted: 1700000000,
      last_read_at: null,
      first_seen: 1700000000,
    };
    const activeConversationRef = {
      current: {
        type: 'contact',
        id: previousPublicKey,
        name: 'abc123def456',
      } satisfies Conversation,
    };
    const { args, fns } = createRealtimeArgs({
      activeConversationRef,
    });

    const { result } = renderHook(() => useRealtimeAppState(args));

    act(() => {
      result.current.onContactResolved?.(previousPublicKey, resolvedContact);
    });

    expect(fns.setContacts).toHaveBeenCalledWith(expect.any(Function));
    expect(args.renameConversationMessages).toHaveBeenCalledWith(
      previousPublicKey,
      resolvedContact.public_key
    );
    expect(args.renameConversationState).toHaveBeenCalledWith(
      `contact-${previousPublicKey}`,
      `contact-${resolvedContact.public_key}`
    );
    expect(args.setActiveConversation).toHaveBeenCalledWith({
      type: 'contact',
      id: resolvedContact.public_key,
      name: '[unknown sender]',
    });
  });

  it('appends raw packets using observation identity dedup', () => {
    const { args } = createRealtimeArgs();
    const packet = rawPacketFixture;

    const { result } = renderHook(() => useRealtimeAppState(args));

    act(() => {
      result.current.onRawPacket?.(packet);
    });

    // Live frames are stamped with the browser receipt time when recorded.
    expect(getRawPackets()).toEqual([{ ...packet, received_at_ms: expect.any(Number) }]);
    expect(getRawPacketStatsSession().totalObservedPackets).toBe(1);
  });

  it('toasts a newly discovered pending channel', () => {
    const { args, fns } = createRealtimeArgs();
    const pending: Channel = {
      ...publicChannel,
      key: '11'.repeat(16),
      name: '#mesh',
      is_hashtag: true,
      membership: 'pending',
    };
    const { result } = renderHook(() => useRealtimeAppState(args));

    act(() => {
      result.current.onChannel?.(pending);
    });

    expect(fns.setChannels).toHaveBeenCalledWith(expect.any(Function));
    expect(mocks.toast).toHaveBeenCalledWith(
      expect.stringContaining('#mesh'),
      expect.objectContaining({
        action: expect.objectContaining({ label: expect.any(String) }),
      })
    );
  });

  it('does not toast when an existing pending channel is updated', () => {
    const pending: Channel = {
      ...publicChannel,
      key: '11'.repeat(16),
      name: '#mesh',
      is_hashtag: true,
      membership: 'pending',
    };
    const { args } = createRealtimeArgs({
      channelsRef: { current: [publicChannel, pending] },
    });
    const { result } = renderHook(() => useRealtimeAppState(args));

    act(() => {
      result.current.onChannel?.(pending);
    });

    expect(mocks.toast).not.toHaveBeenCalled();
  });

  it('announces the clock-fix reboot as information, not as a lost radio', () => {
    const { args } = createRealtimeArgs();
    const { result } = renderHook(() => useRealtimeAppState(args));
    const connected = {
      status: 'ok',
      radio_connected: true,
      radio_initializing: false,
      radio_state: 'connected',
      connection_info: 'BLE: AA:BB:CC:DD:EE:FF',
      database_size_mb: 1,
      oldest_undecrypted_timestamp: null,
      fanout_statuses: {},
      bots_disabled: false,
    } as HealthStatus;

    act(() => {
      result.current.onHealth?.(connected);
      result.current.onHealth?.({
        ...connected,
        status: 'degraded',
        radio_connected: false,
        radio_state: 'rebooting',
      });
    });

    expect(mocks.toast.info).toHaveBeenCalledWith(i18n.t('toast.radioRebooting'), {
      id: RADIO_REBOOTING_TOAST_ID,
      description: i18n.t('toast.radioRebootingDetail'),
    });
    expect(mocks.toast.error).not.toHaveBeenCalled();
  });

  it('announces a Wi-Fi reboot with the Wi-Fi copy', () => {
    const { args } = createRealtimeArgs();
    const { result } = renderHook(() => useRealtimeAppState(args));
    const connected = {
      status: 'ok',
      radio_connected: true,
      radio_initializing: false,
      radio_state: 'connected',
      connection_info: 'Serial: /dev/ttyUSB0',
      database_size_mb: 1,
      oldest_undecrypted_timestamp: null,
      fanout_statuses: {},
      bots_disabled: false,
    } as HealthStatus;

    act(() => {
      result.current.onHealth?.(connected);
      result.current.onHealth?.({
        ...connected,
        status: 'degraded',
        radio_connected: false,
        radio_state: 'rebooting',
        reboot_reason: 'wifi_config',
      });
    });

    expect(mocks.toast.info).toHaveBeenCalledWith(i18n.t('toast.radioRebootingWifiConfig'), {
      id: RADIO_REBOOTING_TOAST_ID,
      description: i18n.t('toast.radioRebootingDetail'),
    });
    expect(mocks.toast.error).not.toHaveBeenCalled();
  });

  it('translates the details of a coded error when a Detail key exists', () => {
    const { args } = createRealtimeArgs();
    const { result } = renderHook(() => useRealtimeAppState(args));

    act(() => {
      result.current.onError?.({
        message: 'Radio did not come back after its clock reboot',
        details: "Check the radio's power and connection.",
        code: 'radio_reboot_timeout',
      });
      result.current.onError?.({
        message: 'Reconnection failed',
        details: 'Could not connect to serial port /dev/ttyUSB0.',
        code: 'reconnection_failed',
      });
    });

    expect(mocks.toast.error).toHaveBeenNthCalledWith(1, i18n.t('errors.radio_reboot_timeout'), {
      description: i18n.t('errors.radio_reboot_timeoutDetail'),
    });
    // No Detail key: the backend's own details (here the real cause) stay.
    expect(mocks.toast.error).toHaveBeenNthCalledWith(2, i18n.t('errors.reconnection_failed'), {
      description: 'Could not connect to serial port /dev/ttyUSB0.',
    });
  });

  it('routes message_deleted to removeMessage', () => {
    const { args } = createRealtimeArgs();
    const { result } = renderHook(() => useRealtimeAppState(args));

    act(() => {
      result.current.onMessageDeleted?.(42);
    });

    expect(args.removeMessage).toHaveBeenCalledWith(42);
  });

  describe('feed store wiring', () => {
    const flush = () => Promise.resolve();

    it('records nothing while the feed has never been opened', async () => {
      const { args } = createRealtimeArgs();
      const { result } = renderHook(() => useRealtimeAppState(args));

      act(() => {
        result.current.onMessage?.(incomingDm);
      });
      await flush();

      expect(getFeedSnapshot().rows).toHaveLength(0);
    });

    it('records a live message once active, but not a blocked one', async () => {
      activateFeedStore();
      const { args } = createRealtimeArgs({
        blockedKeysRef: { current: ['bb'.repeat(32)] },
      });
      const { result } = renderHook(() => useRealtimeAppState(args));

      act(() => {
        result.current.onMessage?.(incomingDm);
        result.current.onMessage?.({ ...incomingDm, id: 8, conversation_key: 'bb'.repeat(32) });
      });
      await flush();

      expect(getFeedSnapshot().rows.map((m) => m.id)).toEqual([7]);
    });

    it('reconciles the feed with since_id on reconnect and after a historical decryption', async () => {
      activateFeedStore();
      mergeHistoryPage([incomingDm], { hasOlder: false });
      const { args } = createRealtimeArgs();
      const { result } = renderHook(() => useRealtimeAppState(args));

      act(() => {
        result.current.onReconnect?.();
      });
      await waitFor(() =>
        expect(mocks.api.getMessages).toHaveBeenCalledWith({ since_id: 7, limit: 1000 })
      );

      mocks.api.getMessages.mockClear();
      act(() => {
        result.current.onSuccess?.({ message: 'other', code: 'something_else' });
      });
      await flush();
      expect(mocks.api.getMessages).not.toHaveBeenCalled();

      mocks.api.getMessages.mockResolvedValueOnce([
        { ...incomingDm, id: 8, received_at: 1600000000, historical: true },
      ]);
      act(() => {
        result.current.onSuccess?.({
          message: 'done',
          code: 'historical_decrypt_complete',
          params: { name: 'x' },
        });
      });
      await waitFor(() =>
        expect(mocks.api.getMessages).toHaveBeenCalledWith({ since_id: 7, limit: 1000 })
      );
      await waitFor(() => expect(getFeedSnapshot().rows.map((m) => m.id)).toEqual([8, 7]));
    });

    it('does not fetch for the feed while it has never been opened', async () => {
      const { args } = createRealtimeArgs();
      const { result } = renderHook(() => useRealtimeAppState(args));
      act(() => {
        result.current.onReconnect?.();
      });
      await waitFor(() => expect(mocks.api.getChannels).toHaveBeenCalled());
      expect(mocks.api.getMessages).not.toHaveBeenCalled();
    });

    it('applies acks, deletions and contact resolution to the feed', async () => {
      activateFeedStore();
      recordFeedMessage(incomingDm);
      const { args } = createRealtimeArgs();
      const { result } = renderHook(() => useRealtimeAppState(args));

      act(() => {
        result.current.onMessageAcked?.(7, 2);
      });
      await flush();
      expect(getFeedSnapshot().rows[0].acked).toBe(2);

      const resolvedKey = 'cc'.repeat(32);
      act(() => {
        result.current.onContactResolved?.(incomingDm.conversation_key, {
          public_key: resolvedKey,
          name: null,
          type: 0,
          flags: 0,
          direct_path: null,
          direct_path_len: -1,
          direct_path_hash_mode: -1,
          last_advert: null,
          lat: null,
          lon: null,
          last_seen: null,
          on_radio: false,
          favorite: false,
          last_contacted: null,
          last_read_at: null,
          first_seen: null,
        });
      });
      await flush();
      expect(getFeedSnapshot().rows[0].conversation_key).toBe(resolvedKey);

      act(() => {
        result.current.onMessageDeleted?.(7);
      });
      await flush();
      expect(getFeedSnapshot().rows).toHaveLength(0);
    });
  });

  describe('feed control rows', () => {
    it('records nothing while the feed store is inactive', () => {
      const { args } = createRealtimeArgs();
      const { result } = renderHook(() => useRealtimeAppState(args));
      act(() => {
        result.current.onRawPacket?.(controlPacketFixture);
      });
      expect(getRawPackets()).toHaveLength(1);
      expect(getFeedSnapshot().controlPackets).toEqual([]);
    });

    it('feeds the same packet to the raw buffer and the feed, and ignores other types', async () => {
      activateFeedStore();
      const { args } = createRealtimeArgs();
      const { result } = renderHook(() => useRealtimeAppState(args));
      act(() => {
        result.current.onRawPacket?.(controlPacketFixture);
        result.current.onRawPacket?.(rawPacketFixture);
      });
      await Promise.resolve();
      expect(getRawPackets()).toHaveLength(2);
      expect(getFeedSnapshot().controlPackets.map((p) => p.id)).toEqual([5]);
    });

    it('clears the raw buffer on reconnect but keeps the control rows, without duplicating', async () => {
      activateFeedStore();
      const { args } = createRealtimeArgs();
      const { result } = renderHook(() => useRealtimeAppState(args));
      act(() => {
        result.current.onRawPacket?.(controlPacketFixture);
      });
      await Promise.resolve();
      expect(getFeedSnapshot().controlPackets).toHaveLength(1);

      act(() => {
        result.current.onReconnect?.();
      });
      await waitFor(() => expect(getRawPackets()).toEqual([]));
      expect(getFeedSnapshot().controlPackets).toHaveLength(1);

      // The same observation replayed after the reconnect is not a second row.
      act(() => {
        result.current.onRawPacket?.(controlPacketFixture);
      });
      await Promise.resolve();
      expect(getRawPackets()).toHaveLength(1);
      expect(getFeedSnapshot().controlPackets).toHaveLength(1);
    });
  });
});

describe('useRealtimeAppState feed event rows', () => {
  const KEY = 'cd'.repeat(32);
  const known = (overrides: Partial<Contact> = {}): Contact => ({
    public_key: KEY,
    name: 'Tower',
    type: 2,
    flags: 0,
    direct_path: null,
    direct_path_len: 0,
    direct_path_hash_mode: 0,
    last_advert: null,
    lat: null,
    lon: null,
    last_seen: null,
    on_radio: false,
    favorite: false,
    last_contacted: null,
    last_read_at: null,
    first_seen: 1_700_000_000,
    ...overrides,
  });
  const eventIds = () => getFeedSnapshot().events.map((e) => e.id);

  beforeEach(() => {
    vi.clearAllMocks();
    resetFeedStore();
    activateFeedStore();
    mocks.api.getChannels.mockResolvedValue([publicChannel]);
    mocks.api.getMessages.mockResolvedValue([]);
  });

  it('reports a contact the client never held, not a known one', async () => {
    const { args } = createRealtimeArgs({ contactsRef: { current: [known()] } });
    const { result } = renderHook(() => useRealtimeAppState(args));
    act(() => {
      result.current.onContact?.(known({ last_seen: 5 }));
      result.current.onContact?.(known({ public_key: 'ee'.repeat(32), name: 'Newcomer' }));
    });
    await Promise.resolve();
    expect(eventIds()).toEqual(['contact.first_seen']);
  });

  it('reports nothing before the contact list is loaded', async () => {
    const { args } = createRealtimeArgs({ contactsRef: { current: null } });
    const { result } = renderHook(() => useRealtimeAppState(args));
    act(() => {
      result.current.onContact?.(known());
    });
    await Promise.resolve();
    expect(eventIds()).toEqual([]);
  });

  it('ignores contact frames while a post-reconnect resync is in flight', async () => {
    let resolveContacts: (value: Contact[]) => void = () => {};
    const slow = new Promise<Contact[]>((resolve) => {
      resolveContacts = resolve;
    });
    const { args } = createRealtimeArgs({
      contactsRef: { current: [] },
      fetchAllContacts: vi.fn(() => slow),
    });
    const { result } = renderHook(() => useRealtimeAppState(args));
    act(() => {
      result.current.onReconnect?.();
    });
    // The burst of snapshots a reconnect brings: no row, whoever the contact is.
    act(() => {
      result.current.onContact?.(known());
    });
    await act(async () => {
      resolveContacts([known()]);
      await slow;
    });
    await Promise.resolve();
    expect(eventIds()).toEqual([]);
    // Once the window is closed a frame for a contact seen during it is still not new.
    act(() => {
      result.current.onContact?.(known({ last_seen: 9 }));
    });
    await Promise.resolve();
    expect(eventIds()).toEqual([]);
  });

  it('reports a newly discovered pending channel once', async () => {
    const pending: Channel = {
      ...publicChannel,
      key: '11'.repeat(16),
      name: '#mesh',
      is_hashtag: true,
      membership: 'pending',
    };
    const { args } = createRealtimeArgs();
    const { result } = renderHook(() => useRealtimeAppState(args));
    act(() => {
      result.current.onChannel?.(pending);
      result.current.onChannel?.(pending);
    });
    await Promise.resolve();
    expect(eventIds()).toEqual(['channel.found']);
  });

  it('feeds health frames to the radio events', async () => {
    vi.useFakeTimers();
    try {
      const { args } = createRealtimeArgs();
      const { result } = renderHook(() => useRealtimeAppState(args));
      const frame = (radio_state: HealthStatus['radio_state']) =>
        ({
          status: 'ok',
          radio_connected: radio_state === 'connected',
          radio_initializing: false,
          radio_state,
        }) as HealthStatus;
      act(() => {
        result.current.onHealth?.(frame('connected'));
        result.current.onHealth?.(frame('disconnected'));
      });
      act(() => {
        vi.advanceTimersByTime(60_000);
      });
      await Promise.resolve();
      expect(eventIds()).toEqual(['radio.offline']);
    } finally {
      vi.useRealTimers();
    }
  });
});
