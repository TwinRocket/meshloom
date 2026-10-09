import {
  useCallback,
  useMemo,
  useRef,
  type Dispatch,
  type MutableRefObject,
  type SetStateAction,
} from 'react';
import { api } from '../api';
import type { UseWebSocketOptions } from '../useWebSocket';
import { toast } from '../components/ui/sonner';
import i18n from '../i18n';
import { getStateKey } from '../utils/conversationState';
import { mergeContactIntoList } from '../utils/contactMerge';
import { getContactDisplayName } from '../utils/pubkey';
import { clearRawPackets, MAX_RAW_PACKETS, recordRawPacket } from '../stores/rawPacketStore';
import { applyLiveStatus, recordCommunityPacket } from '../stores/livePacketStore';
import { applyCommunityPacketObserverTick } from './useVisibleObserverReach';
import { emitStatusDotPulse } from '../utils/statusDotPulse';
import type {
  Channel,
  Contact,
  Conversation,
  HealthStatus,
  Message,
  CommunityLiveStatus,
  CommunityPacket,
  MessagePath,
  RawPacket,
} from '../types';

interface UseRealtimeAppStateArgs {
  prevHealthRef: MutableRefObject<HealthStatus | null>;
  setHealth: Dispatch<SetStateAction<HealthStatus | null>>;
  fetchConfig: () => void | Promise<void>;
  reconcileOnReconnect: () => void;
  refreshUnreads: () => Promise<void>;
  setChannels: Dispatch<SetStateAction<Channel[]>>;
  fetchAllContacts: () => Promise<Contact[]>;
  setContacts: Dispatch<SetStateAction<Contact[]>>;
  blockedKeysRef: MutableRefObject<string[]>;
  blockedNamesRef: MutableRefObject<string[]>;
  channelsRef: MutableRefObject<Channel[]>;
  activeConversationRef: MutableRefObject<Conversation | null>;
  observeMessage: (msg: Message) => { added: boolean; activeConversation: boolean };
  recordMessageEvent: (args: {
    msg: Message;
    activeConversation: boolean;
    isNewMessage: boolean;
    hasMention?: boolean;
  }) => void;
  renameConversationState: (oldStateKey: string, newStateKey: string) => void;
  removeConversationState: (stateKey: string) => void;
  checkMention: (text: string) => boolean;
  pendingDeleteFallbackRef: MutableRefObject<boolean>;
  setActiveConversation: (conv: Conversation | null) => void;
  renameConversationMessages: (oldId: string, newId: string) => void;
  removeConversationMessages: (conversationId: string) => void;
  receiveMessageAck: (
    messageId: number,
    ackCount: number,
    paths?: MessagePath[],
    packetId?: number | null,
    extras?: { packet_hash?: string | null; observer_reach_eligible?: boolean | null }
  ) => void;
  removeMessage: (messageId: number) => void;
  /** Buffer cap override. Defaults to the store's own cap; tests use it to force eviction. */
  maxRawPackets?: number;
}

function isMessageBlocked(msg: Message, blockedKeys: string[], blockedNames: string[]): boolean {
  if (msg.outgoing) {
    return false;
  }

  if (blockedKeys.length > 0) {
    if (msg.type === 'PRIV' && blockedKeys.includes(msg.conversation_key.toLowerCase())) {
      return true;
    }
    if (
      msg.type === 'CHAN' &&
      msg.sender_key &&
      blockedKeys.includes(msg.sender_key.toLowerCase())
    ) {
      return true;
    }
  }

  return blockedNames.length > 0 && !!msg.sender_name && blockedNames.includes(msg.sender_name);
}

/** Keys changed by WebSocket deltas while a reconnect REST snapshot is in flight. */
interface SnapshotTracker {
  contacts: Set<string>;
  channels: Set<string>;
}

/** Snapshot rows win, except for keys a newer WS delta touched: those keep the live state
 *  (present in `live` = upserted by a delta, absent = deleted by a delta). */
function mergeSnapshot<T>(
  snapshot: T[],
  live: T[],
  touched: Set<string>,
  keyOf: (item: T) => string
): T[] {
  if (touched.size === 0) return snapshot;
  const liveByKey = new Map(live.map((item) => [keyOf(item), item]));
  const merged = snapshot.filter((item) => !touched.has(keyOf(item)));
  for (const key of touched) {
    const item = liveByKey.get(key);
    if (item) merged.push(item);
  }
  return merged;
}

export function useRealtimeAppState({
  prevHealthRef,
  setHealth,
  fetchConfig,
  reconcileOnReconnect,
  refreshUnreads,
  setChannels,
  fetchAllContacts,
  setContacts,
  blockedKeysRef,
  blockedNamesRef,
  channelsRef,
  activeConversationRef,
  observeMessage,
  recordMessageEvent,
  renameConversationState,
  removeConversationState,
  checkMention,
  pendingDeleteFallbackRef,
  setActiveConversation,
  renameConversationMessages,
  removeConversationMessages,
  receiveMessageAck,
  removeMessage,
  maxRawPackets = MAX_RAW_PACKETS,
}: UseRealtimeAppStateArgs): UseWebSocketOptions {
  // Reconnect recovery: bumped per reconnect so only the newest snapshot is applied, and
  // each in-flight snapshot records which keys WS deltas touched meanwhile so a slower
  // REST response cannot overwrite them with older data.
  const reconnectGenerationRef = useRef(0);
  const activeSnapshotsRef = useRef<Set<SnapshotTracker>>(new Set());
  const noteDelta = useCallback((kind: keyof SnapshotTracker, ...keys: string[]) => {
    for (const tracker of activeSnapshotsRef.current) {
      for (const key of keys) tracker[kind].add(key);
    }
  }, []);

  const mergeChannelIntoList = useCallback(
    (updated: Channel) => {
      setChannels((prev) => {
        const existingIndex = prev.findIndex((channel) => channel.key === updated.key);
        if (existingIndex === -1) {
          return [...prev, updated].sort((a, b) => a.name.localeCompare(b.name));
        }
        const next = [...prev];
        next[existingIndex] = updated;
        return next;
      });
    },
    [setChannels]
  );

  return useMemo(
    () => ({
      onHealth: (data: HealthStatus) => {
        const prev = prevHealthRef.current;
        prevHealthRef.current = data;
        setHealth(data);
        const nextRadioState =
          data.radio_state ??
          (data.radio_initializing
            ? 'initializing'
            : data.radio_connected
              ? 'connected'
              : 'disconnected');
        const initializationCompleted =
          prev !== null &&
          prev.radio_connected &&
          prev.radio_initializing &&
          data.radio_connected &&
          !data.radio_initializing;

        if (prev !== null && prev.radio_connected !== data.radio_connected) {
          if (data.radio_connected) {
            toast.success(i18n.t('toast.radioConnected'), {
              description: data.connection_info
                ? i18n.t('toast.radioConnectedVia', { info: data.connection_info })
                : undefined,
            });
            fetchConfig();
          } else {
            if (nextRadioState === 'paused') {
              toast.success(i18n.t('toast.radioPaused'));
            } else if (
              nextRadioState !== 'identity_mismatch' &&
              nextRadioState !== 'identity_unbound_legacy'
            ) {
              toast.error(i18n.t('toast.radioDisconnected'), {
                description: i18n.t('toast.radioDisconnectedDetail'),
              });
            }
          }
        }

        if (initializationCompleted) {
          fetchConfig();
        }
      },
      onError: (error: {
        message: string;
        details?: string;
        code?: string;
        params?: Record<string, unknown>;
      }) => {
        const key = error.code ? `errors.${error.code}` : '';
        const translated = key ? i18n.t(key, error.params) : error.message;
        toast.error(key && translated !== key ? translated : error.message, {
          description: error.details,
        });
      },
      onSuccess: (success: {
        message: string;
        details?: string;
        code?: string;
        params?: Record<string, unknown>;
      }) => {
        const key = success.code ? `errors.${success.code}` : '';
        const translated = key ? i18n.t(key, success.params) : success.message;
        toast.success(key && translated !== key ? translated : success.message, {
          description: success.details,
        });
      },
      onReconnect: () => {
        clearRawPackets();
        reconcileOnReconnect();
        refreshUnreads();
        const generation = ++reconnectGenerationRef.current;
        const tracker: SnapshotTracker = { contacts: new Set(), channels: new Set() };
        activeSnapshotsRef.current.add(tracker);
        const isLatest = () => generation === reconnectGenerationRef.current;
        const channelsDone = api
          .getChannels()
          .then((data) => {
            if (!isLatest()) return;
            const touched = tracker.channels;
            setChannels(
              touched.size === 0 ? data : (prev) => mergeSnapshot(data, prev, touched, (c) => c.key)
            );
          })
          .catch(console.error);
        const contactsDone = fetchAllContacts()
          .then((data) => {
            if (!isLatest()) return;
            const touched = tracker.contacts;
            setContacts(
              touched.size === 0
                ? data
                : (prev) => mergeSnapshot(data, prev, touched, (c) => c.public_key)
            );
          })
          .catch(console.error);
        void Promise.all([channelsDone, contactsDone]).finally(() => {
          activeSnapshotsRef.current.delete(tracker);
        });
      },
      onMessage: (msg: Message) => {
        if (isMessageBlocked(msg, blockedKeysRef.current, blockedNamesRef.current)) {
          return;
        }

        const isMutedChannel =
          msg.type === 'CHAN' &&
          !!msg.conversation_key &&
          channelsRef.current.some((c) => c.key === msg.conversation_key && c.muted);

        const { added: isNewMessage, activeConversation: isForActiveConversation } =
          observeMessage(msg);

        if (!isMutedChannel) {
          recordMessageEvent({
            msg,
            activeConversation: isForActiveConversation,
            isNewMessage,
            hasMention: checkMention(msg.text),
          });
        }
      },
      onContact: (contact: Contact) => {
        noteDelta('contacts', contact.public_key);
        setContacts((prev) => mergeContactIntoList(prev, contact));
      },
      onContactResolved: (previousPublicKey: string, contact: Contact) => {
        noteDelta('contacts', previousPublicKey, contact.public_key);
        setContacts((prev) =>
          mergeContactIntoList(
            prev.filter((candidate) => candidate.public_key !== previousPublicKey),
            contact
          )
        );
        renameConversationMessages(previousPublicKey, contact.public_key);
        renameConversationState(
          getStateKey('contact', previousPublicKey),
          getStateKey('contact', contact.public_key)
        );

        const active = activeConversationRef.current;
        if (active?.type === 'contact' && active.id === previousPublicKey) {
          setActiveConversation({
            type: 'contact',
            id: contact.public_key,
            name: getContactDisplayName(contact.name, contact.public_key, contact.last_advert),
          });
        }
      },
      onChannel: (channel: Channel) => {
        noteDelta('channels', channel.key);
        const existed = channelsRef.current.some((item) => item.key === channel.key);
        mergeChannelIntoList(channel);
        if (!existed && channel.membership === 'pending') {
          toast(i18n.t('discovered.toast', { name: channel.name }), {
            action: {
              label: i18n.t('discovered.toastAction'),
              onClick: () =>
                setActiveConversation({
                  type: 'channel',
                  id: channel.key,
                  name: channel.name,
                }),
            },
          });
        }
      },
      onContactDeleted: (publicKey: string) => {
        noteDelta('contacts', publicKey);
        setContacts((prev) => prev.filter((c) => c.public_key !== publicKey));
        removeConversationMessages(publicKey);
        removeConversationState(getStateKey('contact', publicKey));
        const active = activeConversationRef.current;
        if (active?.type === 'contact' && active.id === publicKey) {
          pendingDeleteFallbackRef.current = true;
          setActiveConversation(null);
        }
      },
      onChannelDeleted: (key: string) => {
        noteDelta('channels', key);
        setChannels((prev) => prev.filter((c) => c.key !== key));
        removeConversationMessages(key);
        removeConversationState(getStateKey('channel', key));
        const active = activeConversationRef.current;
        if (active?.type === 'channel' && active.id === key) {
          pendingDeleteFallbackRef.current = true;
          setActiveConversation(null);
        }
      },
      onRawPacket: (packet: RawPacket) => {
        emitStatusDotPulse(packet.payload_type);
        recordRawPacket(packet, maxRawPackets);
      },
      onCommunityPacket: (packet: CommunityPacket) => {
        recordCommunityPacket(packet);
        applyCommunityPacketObserverTick(packet);
      },
      onCommunityLive: (status: CommunityLiveStatus) => {
        applyLiveStatus(status);
      },
      onMessageAcked: (
        messageId: number,
        ackCount: number,
        paths?: MessagePath[],
        packetId?: number | null,
        extras?: { packet_hash?: string | null; observer_reach_eligible?: boolean | null }
      ) => {
        receiveMessageAck(messageId, ackCount, paths, packetId, extras);
      },
      onMessageDeleted: (messageId: number) => {
        removeMessage(messageId);
      },
    }),
    [
      activeConversationRef,
      blockedKeysRef,
      blockedNamesRef,
      checkMention,
      fetchAllContacts,
      fetchConfig,
      removeConversationState,
      renameConversationState,
      renameConversationMessages,
      maxRawPackets,
      mergeChannelIntoList,
      noteDelta,
      pendingDeleteFallbackRef,
      prevHealthRef,
      recordMessageEvent,
      receiveMessageAck,
      removeMessage,
      observeMessage,
      refreshUnreads,
      reconcileOnReconnect,
      removeConversationMessages,
      setActiveConversation,
      setChannels,
      setContacts,
      setHealth,
    ]
  );
}
