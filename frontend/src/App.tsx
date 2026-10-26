import { useCapabilities } from './auth/SessionProvider';
import {
  useEffect,
  useCallback,
  useLayoutEffect,
  useRef,
  useState,
  useMemo,
  type MouseEvent,
} from 'react';
import { normalizeServerLabel, serverLabelToAdopt } from './utils/serverLabel';
import { useTranslation } from 'react-i18next';
import { useChannelUnreadMarker } from './hooks/useChannelUnreadMarker';
import { api } from './api';
import { updateUrlHash } from './utils/urlHash';
import { takePrefetchOrFetch } from './prefetch';
import { useWebSocket } from './useWebSocket';
import {
  useAppShell,
  useUnreadCounts,
  useConversationMessages,
  useRadioControl,
  useAppSettings,
  useConversationRouter,
  useContactsAndChannels,
  useConversationActions,
  useConversationNavigation,
  useRealtimeAppState,
  useFaviconBadge,
  useUnreadTitle,
  useCommunityHashtagNames,
} from './hooks';
import { toast } from './components/ui/sonner';
import { AppShell } from './components/AppShell';
import { toolAllowed } from './components/navDestinations';
import type { MessageInputHandle } from './components/MessageInput';
import { DistanceUnitProvider } from './contexts/DistanceUnitContext';
import { PathHopWidthProvider } from './contexts/PathHopWidthContext';
import { RichPayloadProvider } from './contexts/RichPayloadContext';
import { BlockedSendersProvider } from './contexts/BlockedSendersContext';
import { NotificationStatusProvider } from './contexts/NotificationStatusContext';
import { messageContainsMention } from './utils/messageParser';
import { getStateKey } from './utils/conversationState';
import { seedCommunityEnabled, useCommunityEnabled } from './stores/communityStore';
import type {
  BulkCreateHashtagChannelsResult,
  Channel,
  Contact,
  Conversation,
  Message,
} from './types';
import { CONTACT_TYPE_REPEATER, CONTACT_TYPE_ROOM } from './types';
import { shouldAutoFocusInput } from './utils/autoFocusInput';
import { resetClientStateAfterIdentityAdopt } from './utils/identityAdoptReset';

interface NewMessagePrefillRequest {
  tab: 'hashtag';
  hashtagName: string;
  nonce: number;
}

/**
 * Which message the unread divider should sit on.
 *
 * Normally the server's first-unread id. The exception is a channel that has
 * never been read: its true boundary is the first message ever sent there, so
 * offering to jump would haul the reader to the start of history for no gain.
 * Everything loaded is unread in that case, so the divider belongs at the top of
 * the window — which is what the pre-id behaviour did, and it is genuinely the
 * more useful answer.
 */
export function resolveUnreadMarkerId(
  boundaryId: number | null,
  lastReadAt: number | null,
  messages: Message[]
): number | null {
  if (boundaryId === null) return null;
  if (lastReadAt !== null) return boundaryId;
  if (messages.length === 0) return boundaryId;
  if (messages.some((msg) => msg.id === boundaryId)) return boundaryId;

  const oldestLoaded = messages.reduce((oldest, msg) => {
    if (msg.received_at < oldest.received_at) return msg;
    if (msg.received_at === oldest.received_at && msg.id < oldest.id) return msg;
    return oldest;
  }, messages[0]);
  return oldestLoaded.id;
}

/** Stable empty block list, so the provider value does not change on every render. */
const NO_BLOCKED: readonly string[] = [];
export function App() {
  const { t } = useTranslation();
  const quoteSearchOperatorValue = useCallback((value: string) => {
    return `"${value.replace(/\\/g, '\\\\').replace(/"/g, '\\"')}"`;
  }, []);

  const messageInputRef = useRef<MessageInputHandle>(null);
  const [newMessagePrefillRequest, setNewMessagePrefillRequest] =
    useState<NewMessagePrefillRequest | null>(null);
  const [showBulkAddChannelTab, setShowBulkAddChannelTab] = useState(false);
  const [bulkAddResult, setBulkAddResult] = useState<BulkCreateHashtagChannelsResult | null>(null);
  const [repeaterAutoLoginKey, setRepeaterAutoLoginKey] = useState<string | null>(null);
  const [visibilityVersion, setVisibilityVersion] = useState(0);
  const capabilities = useCapabilities();
  const {
    showNewMessage,
    showSettings,
    settingsSection,
    sidebarOpen,
    desktopSidebarCollapsed,
    showCracker,
    crackerRunning,
    distanceUnit,
    renderRichPayloads,
    showPathHopWidth,
    setSettingsSection,
    setSidebarOpen,
    setCrackerRunning,
    setDistanceUnit,
    setRenderRichPayloads,
    setShowPathHopWidth,
    handleCloseSettingsView,
    handleToggleSettingsView,
    handleOpenNewMessage: openNewMessageModal,
    handleCloseNewMessage: closeNewMessageModal,
    handleToggleCracker,
    handleToggleDesktopSidebar,
    handleExpandDesktopSidebar,
  } = useAppShell();

  // Shared ref between useConversationRouter and useContactsAndChannels
  const pendingDeleteFallbackRef = useRef(false);

  // Stable ref bridge: useContactsAndChannels needs setActiveConversation and
  // markDefaultConversationSet from useConversationRouter, but useConversationRouter
  // needs channels/contacts from useContactsAndChannels. We break the cycle with a
  // ref-based indirection, filled in a layout effect below.
  const setActiveConversationRef = useRef<(conv: Conversation | null) => void>(() => {});
  const markDefaultConversationSetRef = useRef<() => void>(() => {});
  const removeConversationMessagesRef = useRef<(conversationId: string) => void>(() => {});

  // --- Extracted hooks ---

  const {
    health,
    setHealth,
    config,
    prevHealthRef,
    fetchConfig,
    handleSaveConfig,
    handleSetPrivateKey,
    handleReboot,
    handleDisconnect,
    handleReconnect,
    handleAdvertise,
    meshDiscovery,
    meshDiscoveryLoadingTarget,
    handleDiscoverMesh,
    regionDiscovery,
    regionDiscoveryLoading,
    handleDiscoverRegions,
    handleHealthRefresh,
  } = useRadioControl();

  const {
    appSettings,
    fetchAppSettings,
    handleSaveAppSettings,
    handleToggleBlockedKey,
    handleToggleBlockedName,
    handleToggleTrackedTelemetry,
    handleToggleTrackedTelemetryContact,
  } = useAppSettings();

  // Community's directory is what the reach lookups and the radio test run on.
  // One store (stores/communityStore) so the router, the navigation surfaces and
  // the panes all agree, and all follow a change made in the settings at once.
  // App settings only seed it on startup; GET /api/community is the answer.
  const communityEnabled = useCommunityEnabled();
  useEffect(() => {
    if (appSettings) seedCommunityEnabled(appSettings.directory_available ?? false);
  }, [appSettings]);
  const directoryEnabled = communityEnabled === true;

  // Keep user's name in ref for mention detection in WebSocket callback
  const myNameRef = useRef<string | null>(null);
  useEffect(() => {
    myNameRef.current = config?.name ?? null;
  }, [config?.name]);

  // Keep block lists in refs for WS callback filtering
  const blockedKeysRef = useRef<string[]>([]);
  const blockedNamesRef = useRef<string[]>([]);
  useEffect(() => {
    blockedKeysRef.current = appSettings?.blocked_keys ?? [];
    blockedNamesRef.current = appSettings?.blocked_names ?? [];
  }, [appSettings?.blocked_keys, appSettings?.blocked_names]);

  // Check if a message mentions the user
  const checkMention = useCallback(
    (text: string): boolean => messageContainsMention(text, myNameRef.current),
    []
  );

  // useContactsAndChannels is called first — it uses the ref bridge for setActiveConversation
  const {
    contacts,
    contactsLoaded,
    channels,
    undecryptedCount,
    setContacts,
    setContactsLoaded,
    setChannels,
    fetchAllContacts,
    fetchUndecryptedCount,
    handleCreateContact,
    handleCreateChannel,
    handleCreateHashtagChannel,
    handleBulkCreateHashtagChannels,
    handleDeleteChannel,
    handleDeleteContact,
    handleAdoptChannel,
    handleRefuseChannel,
  } = useContactsAndChannels({
    setActiveConversation: (conv) => setActiveConversationRef.current(conv),
    pendingDeleteFallbackRef,
    markDefaultConversationSet: () => markDefaultConversationSetRef.current(),
    removeConversationMessages: (conversationId) =>
      removeConversationMessagesRef.current(conversationId),
  });

  // Keep channels in a ref for WS callback mute filtering
  const channelsRef = useRef<Channel[]>([]);
  useEffect(() => {
    channelsRef.current = channels;
  }, [channels]);
  // Same for the contacts, null until the first load: the feed's event rows tell a new contact
  // from a snapshot frame by what the client already held.
  const contactsRef = useRef<Contact[] | null>(null);
  useEffect(() => {
    contactsRef.current = contactsLoaded ? contacts : null;
  }, [contacts, contactsLoaded]);

  const handleToggleFavorite = useCallback(
    async (type: 'channel' | 'contact', id: string) => {
      // Optimistically toggle the favorite flag
      if (type === 'contact') {
        setContacts((prev) =>
          prev.map((c) => (c.public_key === id ? { ...c, favorite: !c.favorite } : c))
        );
      } else {
        setChannels((prev) =>
          prev.map((c) => (c.key === id ? { ...c, favorite: !c.favorite } : c))
        );
      }

      try {
        await api.toggleFavorite(type, id);
      } catch {
        // Revert on failure
        if (type === 'contact') {
          setContacts((prev) =>
            prev.map((c) => (c.public_key === id ? { ...c, favorite: !c.favorite } : c))
          );
        } else {
          setChannels((prev) =>
            prev.map((c) => (c.key === id ? { ...c, favorite: !c.favorite } : c))
          );
        }
        toast.error(t('toast.favoriteFailed'));
      }
    },
    [setContacts, setChannels, t]
  );

  const handleTogglePin = useCallback(
    async (type: 'channel' | 'contact', id: string) => {
      if (type === 'contact') {
        setContacts((prev) =>
          prev.map((c) => (c.public_key === id ? { ...c, pinned: !c.pinned } : c))
        );
      } else {
        setChannels((prev) => prev.map((c) => (c.key === id ? { ...c, pinned: !c.pinned } : c)));
      }

      try {
        await api.togglePin(type, id);
      } catch {
        if (type === 'contact') {
          setContacts((prev) =>
            prev.map((c) => (c.public_key === id ? { ...c, pinned: !c.pinned } : c))
          );
        } else {
          setChannels((prev) => prev.map((c) => (c.key === id ? { ...c, pinned: !c.pinned } : c)));
        }
        toast.error(t('toast.pinFailed'));
      }
    },
    [setContacts, setChannels, t]
  );

  // useConversationRouter is called second — it receives channels/contacts as inputs
  const {
    activeConversation,
    setActiveConversation,
    activeConversationRef,
    handleSelectConversation,
    markDefaultConversationSet,
    syncFeedQuery,
  } = useConversationRouter({
    channels,
    contacts,
    contactsLoaded,
    suspendHashSync: showSettings,
    setSidebarOpen,
    pendingDeleteFallbackRef,
    settingsLoaded: appSettings !== null && communityEnabled !== null,
    directoryEnabled,
    startView: appSettings?.ui_preferences?.start_view ?? '',
    toolAllowed: (id) => toolAllowed(id, capabilities),
  });

  const {
    targetMessageId,
    setTargetMessageId,
    infoPaneContactKey,
    infoPaneFromChannel,
    infoPaneChannelKey,
    searchPrefillRequest,
    handleOpenContactInfo,
    handleCloseContactInfo,
    handleOpenChannelInfo,
    handleCloseChannelInfo,
    handleSelectConversationWithTargetReset,
    handleNavigateToChannel,
    handleNavigateToMessage,
    handleOpenSearchWithQuery,
  } = useConversationNavigation({
    channels,
    handleSelectConversation,
  });

  // Custom hooks for conversation-specific functionality
  const {
    messages,
    messagesLoading,
    loadingOlder,
    hasOlderMessages,
    hasNewerMessages,
    loadingNewer,
    fetchOlderMessages,
    fetchNewerMessages,
    jumpToBottom,
    reloadCurrentConversation,
    observeMessage,
    receiveMessageAck,
    reconcileOnReconnect,
    renameConversationMessages,
    removeConversationMessages,
    removeMessage,
    clearConversationMessages,
  } = useConversationMessages(activeConversation, targetMessageId);

  // Wire up the ref bridge so useContactsAndChannels handlers reach the real
  // functions. A layout effect (refs must not be written during render) runs
  // before any passive effect or user event can call through the bridge.
  useLayoutEffect(() => {
    setActiveConversationRef.current = setActiveConversation;
    markDefaultConversationSetRef.current = markDefaultConversationSet;
    removeConversationMessagesRef.current = removeConversationMessages;
  });

  // Auto-focus the message input on conversation change (desktop only by default)
  useEffect(() => {
    if (!activeConversation) return;
    if (activeConversation.type !== 'channel' && activeConversation.type !== 'contact') return;
    // Repeaters show a login form, not a message input
    if (activeConversation.type === 'contact') {
      const contact = contacts.find((c) => c.public_key === activeConversation.id);
      if (contact?.type === CONTACT_TYPE_REPEATER) return;
    }
    if (!shouldAutoFocusInput()) return;
    // Defer to let the input mount/render first
    const raf = requestAnimationFrame(() => messageInputRef.current?.focus?.());
    return () => cancelAnimationFrame(raf);
  }, [activeConversation?.id]); // eslint-disable-line react-hooks/exhaustive-deps

  // Room servers replay stored history as a burst of DMs, all arriving with similar received_at
  // but spanning a wide range of sender_timestamps. Sort by sender_timestamp for room contacts
  // so the display reflects the original send order rather than our radio's receipt order.
  const activeContactIsRoom =
    activeConversation?.type === 'contact' &&
    contacts.find((c) => c.public_key === activeConversation.id)?.type === CONTACT_TYPE_ROOM;
  const sortedMessages = useMemo(() => {
    if (!activeContactIsRoom || messages.length === 0) return messages;
    return [...messages].sort((a, b) => {
      const aTs = a.sender_timestamp ?? a.received_at;
      const bTs = b.sender_timestamp ?? b.received_at;
      return aTs !== bTs ? aTs - bTs : a.id - b.id;
    });
  }, [activeContactIsRoom, messages]);

  const {
    unreadCounts,
    mentions,
    lastMessageTimes,
    lastMessagePreviews,
    lastMessageOutgoing,
    unreadLastReadAts,
    firstUnreadIds,
    recordMessageEvent,
    renameConversationState,
    removeConversationState,
    markAllRead,
    refreshUnreads,
  } = useUnreadCounts(channels, contacts, activeConversation);
  useFaviconBadge(unreadCounts, mentions, channels);
  useUnreadTitle(unreadCounts, contacts, channels);

  const handleMuteChannel = useCallback(
    async (key: string, durationSeconds: number) => {
      const previous = channels.find((channel) => channel.key === key);
      const muted = durationSeconds !== 0;
      const mutedUntil =
        durationSeconds > 0 ? Math.floor(Date.now() / 1000) + durationSeconds : null;
      setChannels((prev) =>
        prev.map((channel) =>
          channel.key === key ? { ...channel, muted, muted_until: mutedUntil } : channel
        )
      );
      try {
        const result = await api.toggleChannelMute(key, durationSeconds);
        setChannels((prev) =>
          prev.map((channel) =>
            channel.key === key
              ? { ...channel, muted: result.muted, muted_until: result.muted_until }
              : channel
          )
        );
        await refreshUnreads();
      } catch {
        if (previous) {
          setChannels((prev) => prev.map((channel) => (channel.key === key ? previous : channel)));
        }
        toast.error(t('toast.muteFailed'));
      }
    },
    [channels, setChannels, refreshUnreads, t]
  );

  const { channelUnreadMarker, dismissChannelUnreadMarker } = useChannelUnreadMarker(
    activeConversation,
    unreadCounts,
    firstUnreadIds
  );

  const wsHandlers = useRealtimeAppState({
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
    contactsRef,
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
  });
  const reloadServerData = useCallback(async () => {
    await Promise.all([
      api.getChannels().then(setChannels).catch(console.error),
      fetchAppSettings(),
      fetchAllContacts().then(setContacts).catch(console.error),
      handleHealthRefresh(),
      fetchConfig(),
      refreshUnreads(),
    ]);
  }, [
    fetchAllContacts,
    fetchAppSettings,
    fetchConfig,
    handleHealthRefresh,
    refreshUnreads,
    setChannels,
    setContacts,
  ]);
  const handleIdentityAdopted = useCallback(async () => {
    resetClientStateAfterIdentityAdopt();
    setContacts([]);
    handleSelectConversation({
      type: 'map',
      id: 'map',
      name: 'Node Map',
    });
    window.history.replaceState(null, '', '#map');
    await reloadServerData();
  }, [handleSelectConversation, reloadServerData, setContacts]);
  // A restore from Settings > Database: the user stays in settings, but the
  // conversations, contacts and channels behind it may have changed or been wiped.
  const handleBackupRestored = useCallback(
    async (identityChanged: boolean) => {
      if (identityChanged) {
        resetClientStateAfterIdentityAdopt();
        setContacts([]);
      }
      clearConversationMessages();
      reloadCurrentConversation();
      await reloadServerData();
    },
    [clearConversationMessages, reloadCurrentConversation, reloadServerData, setContacts]
  );

  const handleVisibilityPolicyChanged = useCallback(() => {
    clearConversationMessages();
    reloadCurrentConversation();
    void refreshUnreads();
    setVisibilityVersion((current) => current + 1);
  }, [clearConversationMessages, refreshUnreads, reloadCurrentConversation]);

  const handleBlockKey = useCallback(
    async (key: string) => {
      await handleToggleBlockedKey(key);
      handleVisibilityPolicyChanged();
    },
    [handleToggleBlockedKey, handleVisibilityPolicyChanged]
  );

  const handleBlockName = useCallback(
    async (name: string) => {
      await handleToggleBlockedName(name);
      handleVisibilityPolicyChanged();
    },
    [handleToggleBlockedName, handleVisibilityPolicyChanged]
  );
  const {
    handleSendMessage,
    handleResendChannelMessage,
    handleSetChannelFloodScopeOverride,
    handleSetChannelPathHashModeOverride,
    handleSenderClick,
    handleTrace,
    handlePathDiscovery,
  } = useConversationActions({
    activeConversation,
    activeConversationRef,
    setContacts,
    setChannels,
    observeMessage,
    messageInputRef,
  });
  const handleCreateCrackedChannel = useCallback(
    async (name: string, key: string, tryHistorical: boolean) => {
      const created = await api.createChannel(name, key);
      const updatedChannels = await api.getChannels();
      setChannels(updatedChannels);
      try {
        if (tryHistorical) {
          await api.decryptHistoricalPackets({
            key_type: 'channel',
            channel_key: created.key,
          });
          toast.success(t('cracker.decryptStarted'));
        }
      } finally {
        void fetchUndecryptedCount().catch((error) => {
          console.error('Failed to refresh undecrypted count after cracked channel create:', error);
        });
      }
    },
    [fetchUndecryptedCount, setChannels, t]
  );

  const [discoveredHashtagNames, setDiscoveredHashtagNames] = useState<string[]>([]);
  const communityHashtagNames = useCommunityHashtagNames(directoryEnabled, discoveredHashtagNames);

  const handleHashtagDiscovered = useCallback((name: string) => {
    // Local finder wordlist only. Backend owns Community publish
    // (create / bulk / radio_sync / catalogue resolve).
    setDiscoveredHashtagNames((previous) =>
      previous.includes(name) ? previous : [...previous, name]
    );
  }, []);

  const handleRepeaterAutoLogin = useCallback(
    (publicKey: string, displayName: string) => {
      handleSelectConversationWithTargetReset({
        type: 'contact',
        id: publicKey,
        name: displayName,
      });
      setRepeaterAutoLoginKey(publicKey);
    },
    [handleSelectConversationWithTargetReset]
  );

  const handleOpenNewMessage = useCallback(
    (event?: MouseEvent<HTMLButtonElement>) => {
      setNewMessagePrefillRequest(null);
      setShowBulkAddChannelTab(event?.altKey === true);
      openNewMessageModal();
    },
    [openNewMessageModal]
  );

  const handleCloseNewMessage = useCallback(() => {
    setNewMessagePrefillRequest(null);
    setShowBulkAddChannelTab(false);
    closeNewMessageModal();
  }, [closeNewMessageModal]);

  const handleCloseBulkAddResults = useCallback(() => {
    setBulkAddResult(null);
  }, []);

  const handleChannelReferenceClick = useCallback(
    (channelName: string) => {
      const existingChannel = channels.find((channel) => channel.name === channelName);
      if (existingChannel) {
        handleNavigateToChannel(existingChannel.key);
        return;
      }

      setNewMessagePrefillRequest((previous) => ({
        tab: 'hashtag',
        hashtagName: channelName.slice(1),
        nonce: (previous?.nonce ?? 0) + 1,
      }));
      setShowBulkAddChannelTab(false);
      openNewMessageModal();
    },
    [channels, handleNavigateToChannel, openNewMessageModal]
  );

  const handleBulkAddChannels = useCallback(
    async (channelNames: string[], tryHistorical: boolean) => {
      const result = await handleBulkCreateHashtagChannels(channelNames, tryHistorical);
      setBulkAddResult(result);
    },
    [handleBulkCreateHashtagChannels]
  );

  const statusProps = {
    health,
    config,
  };
  const sidebarProps = {
    contacts,
    channels,
    activeConversation,
    onSelectConversation: handleSelectConversationWithTargetReset,
    onNewMessage: handleOpenNewMessage,
    lastMessageTimes,
    lastMessagePreviews,
    lastMessageOutgoing,
    radioName: config?.name ?? null,
    activeMessages: sortedMessages,
    unreadCounts,
    mentions,
    showCracker,
    crackerRunning,
    onToggleCracker: handleToggleCracker,
    onMarkAllRead: () => {
      void markAllRead();
    },
    blockedKeys: appSettings?.blocked_keys ?? [],
    blockedNames: appSettings?.blocked_names ?? [],
    onTogglePin: handleTogglePin,
    onToggleFavorite: handleToggleFavorite,
    onMuteChannel: handleMuteChannel,
    onDeleteChannel: handleDeleteChannel,
    onDeleteContact: handleDeleteContact,
    onSetChannelFloodScopeOverride: handleSetChannelFloodScopeOverride,
    onOpenNotifySettings: () => {
      setSettingsSection('notifications');
      if (!showSettings) handleToggleSettingsView();
    },
  };
  const bulkAddChannelResultModalProps = {
    result: bulkAddResult,
  };
  const conversationPaneProps = {
    activeConversation,
    contacts,
    channels,
    config,
    health,
    messages: sortedMessages,
    preSorted: activeContactIsRoom,
    messagesLoading,
    loadingOlder,
    hasOlderMessages,
    unreadMarkerMessageId:
      activeConversation?.type === 'channel' &&
      channelUnreadMarker?.channelId === activeConversation.id
        ? resolveUnreadMarkerId(
            channelUnreadMarker.messageId,
            unreadLastReadAts[getStateKey('channel', activeConversation.id)] ?? null,
            messages
          )
        : undefined,
    onNavigateToUnread: (messageId: number) => setTargetMessageId(messageId),
    targetMessageId,
    hasNewerMessages,
    loadingNewer,
    messageInputRef,
    onTrace: handleTrace,
    onRunTracePath: api.requestRadioTrace,
    onPathDiscovery: handlePathDiscovery,
    onToggleFavorite: handleToggleFavorite,
    onTogglePin: handleTogglePin,
    onMuteChannel: handleMuteChannel,
    onDeleteContact: handleDeleteContact,
    onDeleteChannel: handleDeleteChannel,
    onAdoptChannel: handleAdoptChannel,
    onRefuseChannel: handleRefuseChannel,
    onSetChannelFloodScopeOverride: handleSetChannelFloodScopeOverride,
    onSetChannelPathHashModeOverride: handleSetChannelPathHashModeOverride,
    onSelectConversation: handleSelectConversationWithTargetReset,
    onNavigateToMessage: handleNavigateToMessage,
    onOpenContactInfo: handleOpenContactInfo,
    onFeedQueryChange: syncFeedQuery,
    onSearchHistory: handleOpenSearchWithQuery,
    onOpenChannelInfo: handleOpenChannelInfo,
    onSenderClick: handleSenderClick,
    onChannelReferenceClick: handleChannelReferenceClick,
    onLoadOlder: fetchOlderMessages,
    onResendChannelMessage: handleResendChannelMessage,
    onTargetReached: () => setTargetMessageId(null),
    onLoadNewer: fetchNewerMessages,
    onJumpToBottom: jumpToBottom,
    onSendMessage: handleSendMessage,
    onMessageDeleted: removeMessage,
    onDismissUnreadMarker: dismissChannelUnreadMarker,
    onOpenNotifySettings: () => {
      setSettingsSection('notifications');
      if (!showSettings) handleToggleSettingsView();
    },
    trackedTelemetryRepeaters: appSettings?.tracked_telemetry_repeaters ?? [],
    onToggleTrackedTelemetry: handleToggleTrackedTelemetry,
    trackedTelemetryContacts: appSettings?.tracked_telemetry_contacts ?? [],
    onToggleTrackedTelemetryContact: handleToggleTrackedTelemetryContact,
    repeaterAutoLoginKey,
    onClearRepeaterAutoLogin: () => setRepeaterAutoLoginKey(null),
    blockedKeys: appSettings?.blocked_keys,
    blockedNames: appSettings?.blocked_names,
    directoryEnabled,
    knownRegions: appSettings?.known_regions ?? [],
    floodScope: appSettings?.flood_scope,
    onOpenDirectorySettings: () => {
      setSettingsSection('community');
      if (!showSettings) handleToggleSettingsView();
    },
    onAdvertise: handleAdvertise,
  };
  const searchProps = {
    contacts,
    channels,
    visibilityVersion,
    onNavigateToMessage: handleNavigateToMessage,
    prefillRequest: searchPrefillRequest,
  };
  const settingsProps = {
    config,
    health,
    appSettings,
    onSave: handleSaveConfig,
    onSaveAppSettings: handleSaveAppSettings,
    onSetPrivateKey: handleSetPrivateKey,
    onReboot: handleReboot,
    onDisconnect: handleDisconnect,
    onReconnect: handleReconnect,
    onAdvertise: handleAdvertise,
    meshDiscovery,
    meshDiscoveryLoadingTarget,
    onDiscoverMesh: handleDiscoverMesh,
    regionDiscovery,
    regionDiscoveryLoading,
    onDiscoverRegions: handleDiscoverRegions,
    onHealthRefresh: handleHealthRefresh,
    onBackupRestored: handleBackupRestored,
    onRefreshAppSettings: fetchAppSettings,
    blockedKeys: appSettings?.blocked_keys,
    blockedNames: appSettings?.blocked_names,
    onToggleBlockedKey: handleBlockKey,
    onToggleBlockedName: handleBlockName,
    contacts,
    channels,
    onBulkDeleteContacts: (deletedKeys: string[]) => {
      const keySet = new Set(deletedKeys.map((k) => k.toLowerCase()));
      setContacts((prev) => prev.filter((c) => !keySet.has(c.public_key.toLowerCase())));
    },
    trackedTelemetryRepeaters: appSettings?.tracked_telemetry_repeaters ?? [],
    onToggleTrackedTelemetry: handleToggleTrackedTelemetry,
    trackedTelemetryContacts: appSettings?.tracked_telemetry_contacts ?? [],
    onToggleTrackedTelemetryContact: handleToggleTrackedTelemetryContact,
  };
  const crackerProps = {
    channels,
    onChannelCreate: handleCreateCrackedChannel,
    priorityNames: communityHashtagNames,
    onHashtagDiscovered: handleHashtagDiscovered,
  };
  const newMessageModalProps = {
    undecryptedCount,
    showBulkAddChannelTab,
    prefillRequest: newMessagePrefillRequest,
    onCreateContact: handleCreateContact,
    onCreateChannel: handleCreateChannel,
    onCreateHashtagChannel: handleCreateHashtagChannel,
    onBulkAddHashtagChannels: handleBulkAddChannels,
  };
  const contactInfoPaneProps = {
    contactKey: infoPaneContactKey,
    fromChannel: infoPaneFromChannel,
    onClose: handleCloseContactInfo,
    contacts,
    config,
    onToggleFavorite: handleToggleFavorite,
    onNavigateToChannel: handleNavigateToChannel,
    onSearchMessagesByKey: (publicKey: string) => {
      handleOpenSearchWithQuery(`user:${publicKey}`);
    },
    onSearchMessagesByName: (name: string) => {
      handleOpenSearchWithQuery(`user:${quoteSearchOperatorValue(name)}`);
    },
    onLocateContact: (publicKey: string) => {
      handleCloseContactInfo();
      handleSelectConversationWithTargetReset({
        type: 'locate',
        id: 'locate',
        name: 'RF Locate',
        locateKey: publicKey,
      });
    },
    // Blocking and telemetry tracking are written with the app settings (administrators).
    onToggleBlockedKey: capabilities.isAdmin ? handleBlockKey : undefined,
    onToggleBlockedName: capabilities.isAdmin ? handleBlockName : undefined,
    blockedKeys: appSettings?.blocked_keys ?? [],
    blockedNames: appSettings?.blocked_names ?? [],
    trackedTelemetryContacts: appSettings?.tracked_telemetry_contacts ?? [],
    onToggleTrackedTelemetryContact: capabilities.isAdmin
      ? handleToggleTrackedTelemetryContact
      : undefined,
  };
  const channelInfoPaneProps = {
    channelKey: infoPaneChannelKey,
    onClose: handleCloseChannelInfo,
    channels,
    onToggleFavorite: handleToggleFavorite,
  };

  // Connect to WebSocket
  useWebSocket(wsHandlers);

  // Initial fetch for config, settings, and data
  useEffect(() => {
    fetchConfig();
    fetchAppSettings();
    fetchUndecryptedCount();

    // Fetch contacts and channels via REST (parallel, faster than WS serial push)
    takePrefetchOrFetch('channels', api.getChannels).then(setChannels).catch(console.error);
    fetchAllContacts()
      .then((data) => {
        setContacts(data);
        setContactsLoaded(true);
      })
      .catch((err) => {
        console.error(err);
        setContactsLoaded(true);
      });
  }, [
    fetchConfig,
    fetchAppSettings,
    fetchUndecryptedCount,
    fetchAllContacts,
    setChannels,
    setContacts,
    setContactsLoaded,
  ]);
  const serverLabel = normalizeServerLabel(appSettings?.ui_preferences?.server_label);

  // The label used to live in this browser. The first device to open an instance
  // that already had one hands it over, so nobody watches their label vanish.
  const adoptedLabelRef = useRef(false);
  useEffect(() => {
    if (adoptedLabelRef.current || !appSettings) return;
    const adopted = serverLabelToAdopt(serverLabel);
    if (!adopted) return;
    adoptedLabelRef.current = true;
    void handleSaveAppSettings({
      ui_preferences: {
        ...appSettings.ui_preferences,
        nav_rail: appSettings.ui_preferences?.nav_rail ?? [],
        theme: appSettings.ui_preferences?.theme ?? '',
        server_label: adopted,
      },
    });
  }, [appSettings, serverLabel, handleSaveAppSettings]);

  return (
    <DistanceUnitProvider distanceUnit={distanceUnit} setDistanceUnit={setDistanceUnit}>
      <RichPayloadProvider
        renderRichPayloads={renderRichPayloads}
        setRenderRichPayloads={setRenderRichPayloads}
      >
        <BlockedSendersProvider
          blockedKeys={appSettings?.blocked_keys ?? NO_BLOCKED}
          blockedNames={appSettings?.blocked_names ?? NO_BLOCKED}
          loaded={appSettings !== null}
        >
          <PathHopWidthProvider
            showPathHopWidth={showPathHopWidth}
            setShowPathHopWidth={setShowPathHopWidth}
          >
            <NotificationStatusProvider
              statuses={health?.notification_statuses}
              capped={health?.notifications_capped === true}
              failing={health?.notifications_failing === true}
            >
              <AppShell
                navRailOrder={appSettings?.ui_preferences?.nav_rail}
                serverLabel={serverLabel}
                showNewMessage={showNewMessage}
                showBulkAddResults={bulkAddResult !== null}
                showSettings={showSettings}
                settingsSection={settingsSection}
                sidebarOpen={sidebarOpen}
                showCracker={showCracker}
                onSettingsSectionChange={setSettingsSection}
                onSidebarOpenChange={setSidebarOpen}
                onCrackerRunningChange={setCrackerRunning}
                onToggleSettingsView={handleToggleSettingsView}
                onClearActiveConversation={() => {
                  // The hash has to go with it: left behind, the router reads it on the
                  // next pass and reopens the conversation that was just closed.
                  updateUrlHash(null);
                  setActiveConversation(null);
                }}
                onCloseSettingsView={handleCloseSettingsView}
                onCloseNewMessage={handleCloseNewMessage}
                onCloseBulkAddResults={handleCloseBulkAddResults}
                statusProps={statusProps}
                sidebarProps={sidebarProps}
                conversationPaneProps={conversationPaneProps}
                searchProps={searchProps}
                settingsProps={settingsProps}
                crackerProps={crackerProps}
                newMessageModalProps={newMessageModalProps}
                bulkAddChannelResultModalProps={bulkAddChannelResultModalProps}
                contactInfoPaneProps={contactInfoPaneProps}
                channelInfoPaneProps={channelInfoPaneProps}
                onRepeaterAutoLogin={handleRepeaterAutoLogin}
                onIdentityAdopted={handleIdentityAdopted}
                conversationListCollapsed={desktopSidebarCollapsed}
                onToggleConversationList={handleToggleDesktopSidebar}
                onExpandConversationList={handleExpandDesktopSidebar}
              />
            </NotificationStatusProvider>
          </PathHopWidthProvider>
        </BlockedSendersProvider>
      </RichPayloadProvider>
    </DistanceUnitProvider>
  );
}
