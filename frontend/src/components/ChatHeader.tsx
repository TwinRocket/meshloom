import { useEffect, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { Bell, BellOff, ChevronsLeftRight, Globe2, Info, Route, Star, Trash2 } from 'lucide-react';
import { toast } from './ui/sonner';
import { DirectTraceIcon } from './DirectTraceIcon';
import { ContactPathDiscoveryModal } from './ContactPathDiscoveryModal';
import { ChannelFloodScopeOverrideModal } from './ChannelFloodScopeOverrideModal';
import { ChannelPathHashModeOverrideModal } from './ChannelPathHashModeOverrideModal';
import { handleKeyboardActivate } from '../utils/a11y';
import { isPublicChannelKey } from '../utils/publicChannel';
import { stripRegionScopePrefix, floodScopeOverrideLabel } from '../utils/regionScope';
import { isPrefixOnlyContact } from '../utils/pubkey';
import { cn } from '../lib/utils';
import { ContactAvatar } from './ContactAvatar';
import { ContactStatusInfo } from './ContactStatusInfo';
import type { Channel, Contact, Conversation, PathDiscoveryResponse, RadioConfig } from '../types';
import { CONTACT_TYPE_ROOM } from '../types';

interface ChatHeaderProps {
  conversation: Conversation;
  contacts: Contact[];
  channels: Channel[];
  config: RadioConfig | null;
  onTrace: () => void;
  onPathDiscovery: (publicKey: string) => Promise<PathDiscoveryResponse>;
  pushSupported?: boolean;
  pushEnabledForConversation?: boolean;
  onTogglePush?: () => void;
  onToggleFavorite: (type: 'channel' | 'contact', id: string) => void;
  onToggleMute?: (key: string) => void;
  onSetChannelFloodScopeOverride?: (key: string, floodScopeOverride: string) => void;
  onSetChannelPathHashModeOverride?: (key: string, pathHashModeOverride: number | null) => void;
  onDeleteChannel: (key: string) => void;
  onDeleteContact: (publicKey: string) => void;
  onOpenContactInfo?: (publicKey: string) => void;
  onOpenChannelInfo?: (channelKey: string) => void;
}

export function ChatHeader({
  conversation,
  contacts,
  channels,
  config,
  onTrace,
  onPathDiscovery,
  pushSupported,
  pushEnabledForConversation,
  onTogglePush,
  onToggleFavorite,
  onToggleMute,
  onSetChannelFloodScopeOverride,
  onSetChannelPathHashModeOverride,
  onDeleteChannel,
  onDeleteContact,
  onOpenContactInfo,
  onOpenChannelInfo,
}: ChatHeaderProps) {
  const { t } = useTranslation();
  const [showKey, setShowKey] = useState(false);
  const [pathDiscoveryOpen, setPathDiscoveryOpen] = useState(false);
  const [channelOverrideOpen, setChannelOverrideOpen] = useState(false);
  const [pathHashModeOverrideOpen, setPathHashModeOverrideOpen] = useState(false);

  useEffect(() => {
    setShowKey(false);
    setPathDiscoveryOpen(false);
    setChannelOverrideOpen(false);
    setPathHashModeOverrideOpen(false);
  }, [conversation.id]);

  const activeChannel =
    conversation.type === 'channel'
      ? channels.find((channel) => channel.key === conversation.id)
      : undefined;
  const activeFloodScopeOverride =
    conversation.type === 'channel' ? (activeChannel?.flood_scope_override ?? null) : null;
  const activeFloodScopeLabel = activeFloodScopeOverride
    ? stripRegionScopePrefix(activeFloodScopeOverride)
    : null;
  // Badge text: maps the raw override ("*", "#Region", null) to a friendly label
  // so the unscoped marker renders as "unscoped" instead of a bare "*".
  const activeFloodScopeBadge = floodScopeOverrideLabel(activeFloodScopeOverride);
  const activePathHashModeOverride =
    conversation.type === 'channel' ? (activeChannel?.path_hash_mode_override ?? null) : null;
  const showPathHashModeOverride =
    conversation.type === 'channel' &&
    onSetChannelPathHashModeOverride &&
    config?.path_hash_mode_supported;
  const isPrivateChannel = conversation.type === 'channel' && !activeChannel?.is_hashtag;
  const activeContact =
    conversation.type === 'contact'
      ? contacts.find((contact) => contact.public_key === conversation.id)
      : null;
  const activeContactIsRoomServer = activeContact?.type === CONTACT_TYPE_ROOM;
  const activeContactIsPrefixOnly = activeContact
    ? isPrefixOnlyContact(activeContact.public_key)
    : false;

  const titleClickable =
    (conversation.type === 'contact' && onOpenContactInfo) ||
    (conversation.type === 'channel' && onOpenChannelInfo);
  const isFav =
    conversation.type === 'contact'
      ? (activeContact?.favorite ?? false)
      : conversation.type === 'channel'
        ? (activeChannel?.favorite ?? false)
        : false;
  const favoriteTitle =
    conversation.type === 'contact'
      ? isFav
        ? t('chatHeader.removeFavoriteContact')
        : t('chatHeader.addFavoriteContact')
      : isFav
        ? t('chatHeader.removeFavorite')
        : t('chatHeader.addFavorite');

  const handleEditFloodScopeOverride = () => {
    if (conversation.type !== 'channel' || !onSetChannelFloodScopeOverride) return;
    setChannelOverrideOpen(true);
  };

  const handleEditPathHashModeOverride = () => {
    if (conversation.type !== 'channel' || !onSetChannelPathHashModeOverride) return;
    setPathHashModeOverrideOpen(true);
  };

  const handleOpenConversationInfo = () => {
    if (conversation.type === 'contact' && onOpenContactInfo) {
      onOpenContactInfo(conversation.id);
      return;
    }
    if (conversation.type === 'channel' && onOpenChannelInfo) {
      onOpenChannelInfo(conversation.id);
    }
  };

  const isHiddenContactKey =
    conversation.type === 'contact' && conversation.id.length >= 64 && !showKey;

  const copyConversationKey = (event: { stopPropagation: () => void }) => {
    event.stopPropagation();
    void navigator.clipboard.writeText(conversation.id);
    toast.success(
      conversation.type === 'channel'
        ? t('chatHeader.channelKeyCopied')
        : t('chatHeader.contactKeyCopied')
    );
  };

  const headerActionClass =
    'inline-flex h-9 w-9 items-center justify-center rounded p-2 text-lg leading-none transition-colors hover:bg-accent focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring';
  const headerActionDisabledClass = `${headerActionClass} disabled:cursor-not-allowed disabled:opacity-50`;

  const showKeyButton = (
    <button
      type="button"
      className="inline-flex min-h-6 min-w-0 flex-shrink items-center rounded px-1.5 font-mono text-[0.6875rem] text-muted-foreground transition-colors hover:bg-accent hover:text-primary focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
      onClick={(e) => {
        e.stopPropagation();
        setShowKey(true);
      }}
      title={t('chatHeader.revealKey')}
    >
      {t('chatHeader.showKey')}
    </button>
  );

  const renderCopyableKey = (display: string, compact = false) => (
    <span
      className={cn(
        'min-w-0 font-mono text-[0.6875rem] text-muted-foreground transition-colors hover:text-primary',
        compact ? 'flex-shrink' : 'flex-1 truncate'
      )}
      role="button"
      tabIndex={0}
      onKeyDown={handleKeyboardActivate}
      onClick={copyConversationKey}
      title={t('chatHeader.clickToCopy')}
      aria-label={
        conversation.type === 'channel'
          ? t('chatHeader.copyChannelKey')
          : t('chatHeader.copyContactKey')
      }
    >
      {display}
    </span>
  );

  return (
    <header
      className={cn(
        'conversation-header grid items-start gap-x-2 gap-y-0.5 border-b border-border px-4 py-2.5',
        conversation.type === 'contact' && activeContact
          ? 'grid-cols-[minmax(0,1fr)_auto] min-[1100px]:grid-cols-[minmax(0,1fr)_auto_auto]'
          : 'grid-cols-[minmax(0,1fr)_auto]'
      )}
    >
      <span className="flex min-w-0 items-start gap-2">
        {conversation.type === 'contact' && onOpenContactInfo && (
          <button
            type="button"
            className="avatar-action-button flex-shrink-0 cursor-pointer rounded-full border-none bg-transparent p-0 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
            onClick={() => onOpenContactInfo(conversation.id)}
            title={t('chatHeader.viewContactInfo')}
            aria-label={t('chatHeader.viewInfoFor', { name: conversation.name })}
          >
            <ContactAvatar
              name={conversation.name}
              publicKey={conversation.id}
              size={28}
              contactType={contacts.find((c) => c.public_key === conversation.id)?.type}
              clickable
            />
          </button>
        )}
        <span className="flex min-w-0 flex-1 flex-col">
          <span className="flex min-w-0 flex-wrap items-baseline gap-x-2 gap-y-0.5">
            <span className="flex min-w-0 flex-1 items-baseline gap-2 whitespace-nowrap">
              <h2 className="min-w-0 flex-shrink font-semibold text-base">
                {titleClickable ? (
                  <button
                    type="button"
                    className="flex max-w-full min-w-0 items-center gap-1.5 overflow-hidden rounded-sm text-left transition-colors hover:text-primary focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
                    aria-label={t('chatHeader.viewInfoFor', { name: conversation.name })}
                    onClick={handleOpenConversationInfo}
                  >
                    <span className="truncate">
                      {conversation.type === 'channel' &&
                      !conversation.name.startsWith('#') &&
                      activeChannel?.is_hashtag
                        ? '#'
                        : ''}
                      {conversation.name}
                    </span>
                    <Info
                      className="h-3.5 w-3.5 flex-shrink-0 text-muted-foreground/80"
                      aria-hidden="true"
                    />
                  </button>
                ) : (
                  <span className="truncate">
                    {conversation.type === 'channel' &&
                    !conversation.name.startsWith('#') &&
                    activeChannel?.is_hashtag
                      ? '#'
                      : ''}
                    {conversation.name}
                  </span>
                )}
              </h2>
              {isPrivateChannel && !showKey ? (
                showKeyButton
              ) : isHiddenContactKey ? (
                <>
                  {renderCopyableKey(conversation.id.slice(0, 12), true)}
                  {showKeyButton}
                </>
              ) : (
                renderCopyableKey(
                  conversation.type === 'channel' ? conversation.id.toLowerCase() : conversation.id
                )
              )}
            </span>
            {conversation.type === 'channel' && activeFloodScopeBadge && (
              <button
                className="mt-0.5 flex basis-full items-center gap-1 text-left sm:hidden"
                onClick={handleEditFloodScopeOverride}
                title={t('chatHeader.regionalOverride')}
                aria-label={t('chatHeader.regionalOverride')}
              >
                <Globe2
                  className="h-3.5 w-3.5 flex-shrink-0 text-[hsl(var(--region-override))]"
                  aria-hidden="true"
                />
                <span className="min-w-0 truncate text-[0.6875rem] font-medium text-[hsl(var(--region-override))]">
                  {activeFloodScopeBadge}
                </span>
              </button>
            )}
          </span>
        </span>
      </span>
      {conversation.type === 'contact' && activeContact && (
        <div className="col-span-2 row-start-2 min-w-0 text-[0.6875rem] text-muted-foreground min-[1100px]:col-span-1 min-[1100px]:col-start-2 min-[1100px]:row-start-1">
          <ContactStatusInfo
            contact={activeContact}
            ourLat={config?.lat ?? null}
            ourLon={config?.lon ?? null}
          />
        </div>
      )}
      <div className="flex items-center justify-end gap-1.5">
        {conversation.type === 'contact' && !activeContactIsRoomServer && (
          <button
            className={headerActionDisabledClass}
            onClick={() => setPathDiscoveryOpen(true)}
            title={
              activeContactIsPrefixOnly
                ? t('chatHeader.pathDiscoveryUnavailable')
                : t('chatHeader.pathDiscoveryTitle')
            }
            aria-label={t('chatHeader.pathDiscovery')}
            disabled={activeContactIsPrefixOnly}
          >
            <Route className="h-4 w-4 text-muted-foreground" aria-hidden="true" />
          </button>
        )}
        {conversation.type === 'contact' && !activeContactIsRoomServer && (
          <button
            className={headerActionDisabledClass}
            onClick={onTrace}
            title={
              activeContactIsPrefixOnly
                ? t('chatHeader.directTraceUnavailable')
                : t('chatHeader.directTraceTitle')
            }
            aria-label={t('chatHeader.directTrace')}
            disabled={activeContactIsPrefixOnly}
          >
            <DirectTraceIcon className="h-4 w-4 text-muted-foreground" />
          </button>
        )}
        {pushSupported && onTogglePush && (
          <button
            className={headerActionClass}
            onClick={() => void onTogglePush()}
            title={t('chatHeader.notifications')}
            aria-label={t('chatHeader.notifications')}
            aria-pressed={!!pushEnabledForConversation}
          >
            <Bell
              className={cn(
                'h-4 w-4',
                pushEnabledForConversation ? 'text-primary' : 'text-muted-foreground'
              )}
              fill={pushEnabledForConversation ? 'currentColor' : 'none'}
              aria-hidden="true"
            />
          </button>
        )}
        {conversation.type === 'channel' && onToggleMute && (
          <button
            className={headerActionClass}
            onClick={() => onToggleMute(conversation.id)}
            title={
              activeChannel?.muted ? t('chatHeader.unmuteChannel') : t('chatHeader.muteChannel')
            }
            aria-label={
              activeChannel?.muted ? t('chatHeader.unmuteChannel') : t('chatHeader.muteChannel')
            }
            aria-pressed={!!activeChannel?.muted}
          >
            <BellOff
              className={cn(
                'h-4 w-4',
                activeChannel?.muted ? 'text-primary' : 'text-muted-foreground'
              )}
              aria-hidden="true"
            />
          </button>
        )}
        {conversation.type === 'channel' && onSetChannelFloodScopeOverride && (
          <button
            className="inline-flex shrink-0 items-center gap-1 rounded p-2 text-lg leading-none transition-colors hover:bg-accent focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
            onClick={handleEditFloodScopeOverride}
            title={t('chatHeader.regionalOverride')}
            aria-label={t('chatHeader.regionalOverride')}
          >
            <Globe2
              className={`h-4 w-4 ${activeFloodScopeLabel ? 'text-[hsl(var(--region-override))]' : 'text-muted-foreground'}`}
              aria-hidden="true"
            />
            {activeFloodScopeBadge && (
              <span className="hidden text-[0.6875rem] font-medium text-[hsl(var(--region-override))] sm:inline">
                {activeFloodScopeBadge}
              </span>
            )}
          </button>
        )}
        {showPathHashModeOverride && (
          <button
            className="inline-flex h-9 w-9 shrink-0 items-center justify-center gap-1 rounded p-2 text-lg leading-none transition-colors hover:bg-accent focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
            onClick={handleEditPathHashModeOverride}
            title={t('chatHeader.pathHashOverride')}
            aria-label={t('chatHeader.pathHashOverride')}
          >
            <ChevronsLeftRight
              className={`h-4 w-4 ${activePathHashModeOverride != null ? 'text-status-connected' : 'text-muted-foreground'}`}
              aria-hidden="true"
            />
          </button>
        )}
        {(conversation.type === 'channel' || conversation.type === 'contact') && (
          <button
            className={headerActionClass}
            onClick={() =>
              onToggleFavorite(conversation.type as 'channel' | 'contact', conversation.id)
            }
            title={favoriteTitle}
            aria-label={isFav ? t('chatHeader.removeFavorite') : t('chatHeader.addFavorite')}
          >
            {isFav ? (
              <Star className="h-4 w-4 fill-current text-favorite" aria-hidden="true" />
            ) : (
              <Star className="h-4 w-4 text-muted-foreground" aria-hidden="true" />
            )}
          </button>
        )}
        {!(conversation.type === 'channel' && isPublicChannelKey(conversation.id)) && (
          <button
            className={cn(
              headerActionClass,
              'ml-1.5 hover:bg-destructive/10 text-muted-foreground hover:text-destructive'
            )}
            onClick={() => {
              if (conversation.type === 'channel') {
                onDeleteChannel(conversation.id);
              } else {
                onDeleteContact(conversation.id);
              }
            }}
            title={t('chatHeader.delete')}
            aria-label={t('chatHeader.delete')}
          >
            <Trash2 className="h-4 w-4" aria-hidden="true" />
          </button>
        )}
      </div>
      {conversation.type === 'contact' && activeContact && (
        <ContactPathDiscoveryModal
          open={pathDiscoveryOpen}
          onClose={() => setPathDiscoveryOpen(false)}
          contact={activeContact}
          contacts={contacts}
          radioName={config?.name ?? null}
          onDiscover={onPathDiscovery}
        />
      )}
      {conversation.type === 'channel' && onSetChannelFloodScopeOverride && (
        <ChannelFloodScopeOverrideModal
          open={channelOverrideOpen}
          onClose={() => setChannelOverrideOpen(false)}
          roomName={conversation.name}
          currentOverride={activeFloodScopeOverride}
          onSetOverride={(value) => onSetChannelFloodScopeOverride(conversation.id, value)}
        />
      )}
      {showPathHashModeOverride && (
        <ChannelPathHashModeOverrideModal
          open={pathHashModeOverrideOpen}
          onClose={() => setPathHashModeOverrideOpen(false)}
          channelName={conversation.name}
          currentOverride={activePathHashModeOverride}
          radioDefault={config?.path_hash_mode ?? 0}
          onSetOverride={(value) => onSetChannelPathHashModeOverride(conversation.id, value)}
        />
      )}
    </header>
  );
}
