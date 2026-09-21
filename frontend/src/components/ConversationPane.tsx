import { lazy, Suspense, useEffect, useMemo, useState, type Ref } from 'react';
import { useTranslation } from 'react-i18next';

import { Radio } from 'lucide-react';
import { toast } from 'sonner';

import { api, formatApiError } from '../api';
import { cn } from '../lib/utils';
import { Button } from './ui/button';
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
  NotificationMediaChannel,
  NotificationMediaFlags,
  PathDiscoveryResponse,
  RadioAdvertMode,
  RadioConfig,
  RadioTraceHopRequest,
  RadioTraceResponse,
} from '../types';
import { CONTACT_TYPE_REPEATER, CONTACT_TYPE_ROOM, CONTACT_TYPE_SENSOR } from '../types';
import { isPendingChannel } from '../utils/channelMembership';
import { DiscoveredChannelsView } from './DiscoveredChannelsView';
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
  onTogglePin?: (type: 'channel' | 'contact', id: string) => Promise<void>;
  onMuteChannel: (key: string, durationSeconds: number) => Promise<void>;
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
  notifyMediaEnabled?: NotificationMediaFlags;
  emailReady?: boolean;
  webhookReady?: boolean;
  onSetConversationMedia?: (channel: NotificationMediaChannel, enabled: boolean) => void;
  onOpenNotifySettings?: () => void;
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
  onAdvertise?: (mode: RadioAdvertMode) => Promise<void>;
  unreadCounts?: Record<string, number>;
  lastMessageTimes?: Record<string, number>;
  lastMessagePreviews?: Record<string, string>;
  onToggleCracker?: () => void;
  crackerVisible?: boolean;
  crackerQueueCount?: number;
  onAdoptChannel?: (key: string) => Promise<void>;
  onRefuseChannel?: (key: string) => Promise<void>;
}

function LoadingPane({ label }: { label: string }) {
  return (
    <div className="flex-1 flex items-center justify-center text-muted-foreground">{label}</div>
  );
}

/** J replaces this with ControlJournalView, keeping the same props. */
function ControlJournalPlaceholder({
  contacts: _contacts,
  channels: _channels,
  onOpenContactInfo: _onOpenContactInfo,
  onSelectConversation: _onSelectConversation,
  publicKey: _publicKey,
}: {
  contacts: Contact[];
  channels: Channel[];
  onOpenContactInfo: ConversationPaneProps['onOpenContactInfo'];
  onSelectConversation: ConversationPaneProps['onSelectConversation'];
  publicKey?: string;
}) {
  return <div data-testid="control-journal-placeholder" />;
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
  onTogglePin,
  onMuteChannel,
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
  notifyMediaEnabled,
  emailReady,
  webhookReady,
  onSetConversationMedia,
  onOpenNotifySettings,
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
  onAdvertise,
  unreadCounts = {},
  lastMessageTimes = {},
  lastMessagePreviews = {},
  onToggleCracker,
  crackerVisible = false,
  crackerQueueCount = 0,
  onAdoptChannel,
  onRefuseChannel,
}: ConversationPaneProps) {
  const { t } = useTranslation();
  const [advertisingMode, setAdvertisingMode] = useState<RadioAdvertMode | null>(null);

  const handleLiveAdvertise = async (mode: RadioAdvertMode) => {
    if (advertisingMode !== null) return;
    setAdvertisingMode(mode);
    try {
      if (onAdvertise) {
        await onAdvertise(mode);
      } else {
        await api.sendAdvertisement(mode);
        toast.success(mode === 'zero_hop' ? t('toast.advertZeroHopSent') : t('toast.advertSent'));
      }
    } catch (err) {
      const label = mode === 'zero_hop' ? t('toast.advertZeroHopLabel') : t('toast.advertLabel');
      console.error(`Failed to send ${label}:`, err);
      toast.error(t('toast.advertFailed', { label }), {
        description: formatApiError(err, t) || t('chat.checkRadio'),
      });
    } finally {
      setAdvertisingMode(null);
    }
  };

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
    const connected = health?.radio_connected === true;
    return (
      <>
        <ToolPaneHeader
          title={t('conversation.live')}
          onBack={onBackToTools}
          actions={
            <div className="flex items-center gap-1.5 sm:gap-2">
              <Button
                type="button"
                variant="outline"
                size="sm"
                disabled={advertisingMode !== null || !connected}
                onClick={() => void handleLiveAdvertise('zero_hop')}
                title={connected ? t('radioStatus.advertHelp') : t('radioStatus.disconnectedHelp')}
                className="h-8 gap-1.5 px-2.5 text-xs"
              >
                <Radio
                  className={cn(
                    'h-3.5 w-3.5 shrink-0',
                    advertisingMode === 'zero_hop' && 'animate-pulse'
                  )}
                  aria-hidden="true"
                />
                <span>
                  {advertisingMode === 'zero_hop'
                    ? t('radioStatus.sending')
                    : t('radioStatus.advertZeroHop')}
                </span>
              </Button>
              <Button
                type="button"
                variant="outline"
                size="sm"
                disabled={advertisingMode !== null || !connected}
                onClick={() => void handleLiveAdvertise('flood')}
                title={connected ? t('radioStatus.advertHelp') : t('radioStatus.disconnectedHelp')}
                className="h-8 gap-1.5 px-2.5 text-xs"
              >
                <Radio
                  className={cn(
                    'h-3.5 w-3.5 shrink-0',
                    advertisingMode === 'flood' && 'animate-pulse'
                  )}
                  aria-hidden="true"
                />
                <span>
                  {advertisingMode === 'flood'
                    ? t('radioStatus.sending')
                    : t('radioStatus.advertFlood')}
                </span>
              </Button>
            </div>
          }
        />
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
          directoryEnabled={directoryEnabled}
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
        onOpenContactInfo={onOpenContactInfo}
        onSelectConversation={onSelectConversation}
      />
    );
  }

  if (activeConversation.type === 'control') {
    return (
      <ControlJournalPlaceholder
        contacts={contacts}
        channels={channels}
        onOpenContactInfo={onOpenContactInfo}
        onSelectConversation={onSelectConversation}
        publicKey={config?.public_key}
      />
    );
  }

  if (activeConversation.type === 'search') {
    return null;
  }

  if (activeConversation.type === 'discovered') {
    return (
      <DiscoveredChannelsView
        channels={channels}
        unreadCounts={unreadCounts}
        lastMessageTimes={lastMessageTimes}
        lastMessagePreviews={lastMessagePreviews}
        onSelectConversation={onSelectConversation}
        onBackToTools={onBackToTools}
        onToggleCracker={onToggleCracker ?? (() => undefined)}
        crackerVisible={crackerVisible}
        crackerQueueCount={crackerQueueCount}
        onAdoptedRejected={(key) => {
          void onAdoptChannel?.(key);
        }}
      />
    );
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
        notifyMediaEnabled={notifyMediaEnabled}
        emailReady={emailReady}
        webhookReady={webhookReady}
        onSetConversationMedia={onSetConversationMedia}
        onOpenNotifySettings={onOpenNotifySettings}
        onTrace={onTrace}
        onPathDiscovery={onPathDiscovery}
        onToggleFavorite={onToggleFavorite}
        onTogglePin={onTogglePin}
        onMuteChannel={onMuteChannel}
        onSetChannelFloodScopeOverride={onSetChannelFloodScopeOverride}
        onSetChannelPathHashModeOverride={onSetChannelPathHashModeOverride}
        onDeleteChannel={onDeleteChannel}
        onDeleteContact={onDeleteContact}
        onOpenContactInfo={onOpenContactInfo}
        onOpenChannelInfo={onOpenChannelInfo}
        onBack={onBack}
        pending={isPendingChannel(
          channels.find((channel) => channel.key === activeConversation.id)
        )}
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
          {activeConversation.type === 'channel' &&
          isPendingChannel(channels.find((channel) => channel.key === activeConversation.id)) ? (
            <div className="flex shrink-0 gap-2 border-t border-border p-3">
              <Button
                type="button"
                variant="outline"
                className="h-11 flex-1 border-green-600/50 text-green-700 hover:bg-green-600/10 dark:text-green-400"
                onClick={() => void onAdoptChannel?.(activeConversation.id)}
              >
                {t('discovered.adopt')}
              </Button>
              <Button
                type="button"
                variant="outline"
                className="h-11 flex-1 border-destructive/50 text-destructive hover:bg-destructive/10"
                onClick={() => void onRefuseChannel?.(activeConversation.id)}
              >
                {t('discovered.refuse')}
              </Button>
            </div>
          ) : !(activeConversation.type === 'contact' && isPrefixOnlyActiveContact) ? (
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
                  : t('chat.messagePlaceholder')
              }
            />
          ) : null}
        </div>
      )}
    </>
  );
}
