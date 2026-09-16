import { lazy, Suspense, useEffect, useMemo, useState, type Ref } from 'react';
import { useTranslation } from 'react-i18next';

import { ChatHeader } from './ChatHeader';
import { MessageInput, type MessageInputHandle } from './MessageInput';
import { MessageList } from './MessageList';
import { RawPacketFeedView } from './RawPacketFeedView';
import { RoomServerPanel } from './RoomServerPanel';
import { SensorTelemetryPanel } from './SensorTelemetryPanel';
import { LocatePane, locateConversation } from './LocatePane';
import { TracePane } from './TracePane';
import { ToolPaneHeader } from './ToolPaneHeader';
import type {
  Channel,
  Contact,
  Conversation,
  HealthStatus,
  Message,
  PathDiscoveryResponse,
  RadioConfig,
  RadioTraceHopRequest,
  RadioTraceResponse,
} from '../types';
import { CONTACT_TYPE_REPEATER, CONTACT_TYPE_ROOM, CONTACT_TYPE_SENSOR } from '../types';
import {
  getContactDisplayName,
  isPrefixOnlyContact,
  isUnknownFullKeyContact,
} from '../utils/pubkey';

const RepeaterDashboard = lazy(() =>
  import('./RepeaterDashboard').then((m) => ({ default: m.RepeaterDashboard }))
);
const MapView = lazy(() => import('./MapView').then((m) => ({ default: m.MapView })));
const LiveView = lazy(() => import('./LiveView').then((m) => ({ default: m.LiveView })));
const VisualizerView = lazy(() =>
  import('./VisualizerView').then((m) => ({ default: m.VisualizerView }))
);

interface ConversationPaneProps {
  activeConversation: Conversation | null;
  /** Leaves a tool sub-screen for the Tools screen it was opened from. Phones only. */
  onBackToTools?: () => void;
  contacts: Contact[];
  channels: Channel[];
  config: RadioConfig | null;
  health: HealthStatus | null;
  messages: Message[];
  preSorted?: boolean;
  messagesLoading: boolean;
  loadingOlder: boolean;
  hasOlderMessages: boolean;
  unreadMarkerMessageId?: number | null;
  onNavigateToUnread?: (messageId: number) => void;
  targetMessageId: number | null;
  hasNewerMessages: boolean;
  loadingNewer: boolean;
  messageInputRef: Ref<MessageInputHandle>;
  /** Leaves the conversation for the list, on narrow layouts. */
  onBack?: () => void;
  onTrace: () => Promise<void>;
  onRunTracePath: (
    hopHashBytes: 1 | 2 | 4,
    hops: RadioTraceHopRequest[]
  ) => Promise<RadioTraceResponse>;
  onPathDiscovery: (publicKey: string) => Promise<PathDiscoveryResponse>;
  onToggleFavorite: (type: 'channel' | 'contact', id: string) => Promise<void>;
  onToggleMute: (key: string) => Promise<void>;
  onDeleteContact: (publicKey: string) => Promise<void>;
  onDeleteChannel: (key: string) => Promise<void>;
  onSetChannelFloodScopeOverride: (channelKey: string, floodScopeOverride: string) => Promise<void>;
  onSetChannelPathHashModeOverride?: (
    channelKey: string,
    pathHashModeOverride: number | null
  ) => Promise<void>;
  onSelectConversation: (conversation: Conversation) => void;
  onOpenContactInfo: (publicKey: string, fromChannel?: boolean) => void;
  onOpenChannelInfo: (channelKey: string) => void;
  onSenderClick: (sender: string, quote?: string) => void;
  onChannelReferenceClick?: (channelName: string) => void;
  onLoadOlder: () => Promise<void>;
  onResendChannelMessage: (messageId: number, newTimestamp?: boolean) => Promise<void>;
  onTargetReached: () => void;
  onLoadNewer: () => Promise<void>;
  onJumpToBottom: () => void;
  onDismissUnreadMarker: () => void;
  onSendMessage: (text: string) => Promise<void>;
  onMessageDeleted?: (messageId: number) => void;
  pushSupported?: boolean;
  pushSubscribed?: boolean;
  pushEnabledForConversation?: boolean;
  onTogglePush?: () => void;
  onOpenPushSettings?: () => void;
  trackedTelemetryRepeaters: string[];
  onToggleTrackedTelemetry: (publicKey: string) => Promise<void>;
  trackedTelemetryContacts?: string[];
  onToggleTrackedTelemetryContact?: (publicKey: string) => Promise<void>;
  repeaterAutoLoginKey: string | null;
  onClearRepeaterAutoLogin: () => void;
  blockedKeys?: string[];
  blockedNames?: string[];
  directoryEnabled?: boolean;
  communityEnabled?: boolean;
  communityIata?: string;
  onOpenDirectorySettings?: () => void;
  onOpenCommunitySettings?: () => void;
}

function LoadingPane({ label }: { label: string }) {
  return (
    <div className="flex-1 flex items-center justify-center text-muted-foreground">{label}</div>
  );
}

function ContactResolutionBanner({ variant }: { variant: 'unknown-full-key' | 'prefix-only' }) {
  const { t } = useTranslation();
  if (variant === 'prefix-only') {
    return (
      <div className="mx-4 mt-3 rounded-md border border-destructive/30 bg-destructive/10 px-3 py-2 text-sm text-destructive">
        {t('conversation.prefixOnlyBanner')}
      </div>
    );
  }

  return (
    <div className="mx-4 mt-3 rounded-md border border-warning/30 bg-warning/10 px-3 py-2 text-sm text-warning">
      {t('conversation.unknownFullKeyBanner')}
    </div>
  );
}

export function ConversationPane({
  activeConversation,
  onBackToTools,
  contacts,
  channels,
  config,
  health,
  messages,
  preSorted,
  messagesLoading,
  loadingOlder,
  hasOlderMessages,
  unreadMarkerMessageId,
  onNavigateToUnread,
  targetMessageId,
  hasNewerMessages,
  loadingNewer,
  messageInputRef,
  onBack,
  onTrace,
  onRunTracePath,
  onPathDiscovery,
  onToggleFavorite,
  onToggleMute,
  onDeleteContact,
  onDeleteChannel,
  onSetChannelFloodScopeOverride,
  onSetChannelPathHashModeOverride,
  onSelectConversation,
  onOpenContactInfo,
  onOpenChannelInfo,
  onSenderClick,
  onChannelReferenceClick,
  onLoadOlder,
  onResendChannelMessage,
  onTargetReached,
  onLoadNewer,
  onJumpToBottom,
  onDismissUnreadMarker,
  onSendMessage,
  onMessageDeleted,
  pushSupported,
  pushEnabledForConversation,
  onTogglePush,
  trackedTelemetryRepeaters,
  onToggleTrackedTelemetry,
  trackedTelemetryContacts = [],
  onToggleTrackedTelemetryContact,
  repeaterAutoLoginKey,
  onClearRepeaterAutoLogin,
  blockedKeys,
  blockedNames,
  directoryEnabled,
  communityEnabled = true,
  communityIata,
  onOpenDirectorySettings,
  onOpenCommunitySettings,
}: ConversationPaneProps) {
  const { t } = useTranslation();
  const [roomAuthenticated, setRoomAuthenticated] = useState(false);
  const activeContactIsRepeater = useMemo(() => {
    if (!activeConversation || activeConversation.type !== 'contact') return false;
    const contact = contacts.find((candidate) => candidate.public_key === activeConversation.id);
    return contact?.type === CONTACT_TYPE_REPEATER;
  }, [activeConversation, contacts]);
  const activeContact = useMemo(() => {
    if (!activeConversation || activeConversation.type !== 'contact') return null;
    return contacts.find((candidate) => candidate.public_key === activeConversation.id) ?? null;
  }, [activeConversation, contacts]);
  const activeContactIsRoom = activeContact?.type === CONTACT_TYPE_ROOM;
  const activeContactIsSensor = activeContact?.type === CONTACT_TYPE_SENSOR;
  useEffect(() => {
    setRoomAuthenticated(false);
  }, [activeConversation?.id]);
  const isPrefixOnlyActiveContact = activeContact
    ? isPrefixOnlyContact(activeContact.public_key)
    : false;
  const isUnknownFullKeyActiveContact =
    activeContact !== null &&
    !isPrefixOnlyActiveContact &&
    isUnknownFullKeyContact(activeContact.public_key, activeContact.last_advert);

  if (!activeConversation) {
    return (
      <div className="flex-1 flex items-center justify-center text-muted-foreground">
        {t('conversation.selectPrompt')}
      </div>
    );
  }

  if (activeConversation.type === 'map') {
    return (
      <>
        <ToolPaneHeader title={t('conversation.nodeMap')} />
        <div className="flex-1 overflow-hidden">
          <Suspense fallback={<LoadingPane label={t('conversation.loadingMap')} />}>
            <MapView
              contacts={contacts}
              focusedKey={activeConversation.mapFocusKey}
              config={config}
              blockedKeys={blockedKeys}
              blockedNames={blockedNames}
              directoryEnabled={directoryEnabled}
              onSelectContact={(contact) =>
                onSelectConversation({
                  type: 'contact',
                  id: contact.public_key,
                  name: getContactDisplayName(
                    contact.name,
                    contact.public_key,
                    contact.last_advert
                  ),
                })
              }
            />
          </Suspense>
        </div>
      </>
    );
  }

  if (activeConversation.type === 'live') {
    return (
      <>
        <ToolPaneHeader title={t('conversation.live')} onBack={onBackToTools} />
        <div className="flex-1 overflow-hidden">
          <Suspense fallback={<LoadingPane label={t('conversation.loadingLive')} />}>
            <LiveView
              contacts={contacts}
              config={config}
              communityEnabled={communityEnabled}
              communityIata={communityIata}
              blockedKeys={blockedKeys}
              blockedNames={blockedNames}
              onOpenContactInfo={onOpenContactInfo}
              onSelectConversation={onSelectConversation}
              onOpenCommunitySettings={onOpenCommunitySettings}
            />
          </Suspense>
        </div>
      </>
    );
  }

  if (activeConversation.type === 'visualizer') {
    return (
      <Suspense fallback={<LoadingPane label={t('conversation.loadingVisualizer')} />}>
        <VisualizerView
          onBackToTools={onBackToTools}
          contacts={contacts}
          channels={channels}
          config={config}
          radioOffline={!health?.radio_connected}
        />
      </Suspense>
    );
  }

  if (activeConversation.type === 'raw') {
    return (
      <RawPacketFeedView
        onBackToTools={onBackToTools}
        contacts={contacts}
        channels={channels}
        radioOffline={!health?.radio_connected}
      />
    );
  }

  if (activeConversation.type === 'search') {
    return null;
  }

  if (activeConversation.type === 'trace') {
    return (
      <TracePane
        contacts={contacts}
        config={config}
        onRunTracePath={onRunTracePath}
        onBackToTools={onBackToTools}
      />
    );
  }

  if (activeConversation.type === 'locate') {
    return (
      <LocatePane
        onBackToTools={onBackToTools}
        contacts={contacts}
        locateKey={activeConversation.locateKey}
        directoryEnabled={directoryEnabled}
        onSelectLocate={(query) => onSelectConversation(locateConversation(query))}
        onOpenDirectorySettings={onOpenDirectorySettings}
      />
    );
  }

  if (activeContactIsRepeater) {
    return (
      <Suspense fallback={<LoadingPane label={t('conversation.loadingDashboard')} />}>
        <RepeaterDashboard
          key={activeConversation.id}
          onBack={onBack}
          conversation={activeConversation}
          contacts={contacts}
          radioLat={config?.lat ?? null}
          radioLon={config?.lon ?? null}
          radioName={config?.name ?? null}
          onTrace={onTrace}
          onPathDiscovery={onPathDiscovery}
          onToggleFavorite={onToggleFavorite}
          onDeleteContact={onDeleteContact}
          onOpenContactInfo={onOpenContactInfo}
          trackedTelemetryRepeaters={trackedTelemetryRepeaters}
          onToggleTrackedTelemetry={onToggleTrackedTelemetry}
          autoLoginAndLoadAll={repeaterAutoLoginKey === activeConversation.id}
          onAutoLoginConsumed={onClearRepeaterAutoLogin}
        />
      </Suspense>
    );
  }

  const showRoomChat = !activeContactIsRoom || roomAuthenticated;

  return (
    <>
      <ChatHeader
        conversation={activeConversation}
        contacts={contacts}
        channels={channels}
        config={config}
        pushSupported={pushSupported}
        pushEnabledForConversation={pushEnabledForConversation}
        onTogglePush={onTogglePush}
        onTrace={onTrace}
        onPathDiscovery={onPathDiscovery}
        onToggleFavorite={onToggleFavorite}
        onToggleMute={onToggleMute}
        onSetChannelFloodScopeOverride={onSetChannelFloodScopeOverride}
        onSetChannelPathHashModeOverride={onSetChannelPathHashModeOverride}
        onDeleteChannel={onDeleteChannel}
        onDeleteContact={onDeleteContact}
        onOpenContactInfo={onOpenContactInfo}
        onOpenChannelInfo={onOpenChannelInfo}
        onBack={onBack}
      />
      {activeConversation.type === 'contact' && isPrefixOnlyActiveContact && (
        <ContactResolutionBanner variant="prefix-only" />
      )}
      {activeConversation.type === 'contact' && isUnknownFullKeyActiveContact && (
        <ContactResolutionBanner variant="unknown-full-key" />
      )}
      {activeContactIsSensor && activeContact && (
        <SensorTelemetryPanel
          key={activeContact.public_key}
          contact={activeContact}
          contacts={contacts}
          trackedTelemetryContacts={trackedTelemetryContacts}
          onToggleTrackedTelemetryContact={onToggleTrackedTelemetryContact}
        />
      )}
      {activeContactIsRoom && activeContact && (
        <RoomServerPanel
          key={activeContact.public_key}
          contact={activeContact}
          onAuthenticatedChange={setRoomAuthenticated}
        />
      )}
      {showRoomChat && <div data-toast-anchor="conversation" aria-hidden="true" />}
      {showRoomChat && (
        <div className="mx-auto flex w-full max-w-none flex-1 min-h-0 flex-col lg:max-w-[52rem] 2xl:max-w-[64rem]">
          <MessageList
            key={activeConversation.id}
            messages={messages}
            preSorted={preSorted}
            contacts={contacts}
            channels={channels}
            loading={messagesLoading}
            loadingOlder={loadingOlder}
            hasOlderMessages={hasOlderMessages}
            unreadMarkerMessageId={
              activeConversation.type === 'channel' ? unreadMarkerMessageId : undefined
            }
            onNavigateToUnread={
              activeConversation.type === 'channel' ? onNavigateToUnread : undefined
            }
            onDismissUnreadMarker={
              activeConversation.type === 'channel' ? onDismissUnreadMarker : undefined
            }
            onSenderClick={onSenderClick}
            onSendMessage={onSendMessage}
            onChannelReferenceClick={onChannelReferenceClick}
            onLoadOlder={onLoadOlder}
            onResendChannelMessage={
              activeConversation.type === 'channel' ? onResendChannelMessage : undefined
            }
            radioName={config?.name}
            config={config}
            onOpenContactInfo={onOpenContactInfo}
            targetMessageId={targetMessageId}
            onTargetReached={onTargetReached}
            hasNewerMessages={hasNewerMessages}
            loadingNewer={loadingNewer}
            onLoadNewer={onLoadNewer}
            onJumpToBottom={onJumpToBottom}
            onMessageDeleted={onMessageDeleted}
            directoryEnabled={directoryEnabled}
            conversationKey={activeConversation.id}
          />
          {!(activeConversation.type === 'contact' && isPrefixOnlyActiveContact) ? (
            <MessageInput
              ref={messageInputRef}
              onSend={onSendMessage}
              disabled={!health?.radio_connected}
              conversationType={activeConversation.type}
              conversationId={
                activeConversation.type === 'contact' || activeConversation.type === 'channel'
                  ? activeConversation.id
                  : undefined
              }
              senderName={config?.name}
              radioLat={config?.lat}
              radioLon={config?.lon}
              placeholder={
                !health?.radio_connected
                  ? t('chat.radioNotConnected')
                  : t('chat.messageTo', { name: activeConversation.name })
              }
            />
          ) : null}
        </div>
      )}
    </>
  );
}
