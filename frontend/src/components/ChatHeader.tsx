import { useEffect, useState } from 'react';
import { useTranslation } from 'react-i18next';
import {
  Bell,
  BellOff,
  ChevronsLeftRight,
  Globe2,
  Info,
  Route,
  Star,
  Trash2,
  ChevronLeft,
  MoreVertical,
} from 'lucide-react';
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
  /** Leaves the conversation for the list. Narrow layouts only — where it is the way out. */
  onBack?: () => void;
}

export function ChatHeader({
  conversation,
  onBack,
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
  const [actionsOpen, setActionsOpen] = useState(false);
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
        // Opaque, and standing clear of the top edge on phones, for the same reason
        // the list screen does: installed, iOS blurs whatever reaches into the strip
        // under the status bar, and with no app header above it this row is what
        // lands there. Desktop keeps its original padding, since the app header is
        // still above it.
        // items-center, so the back arrow, the avatar and the name sit on one line
        // instead of being top-aligned against a title that may wrap.
        // The rule underneath is a hairline at low opacity rather than a grey bar:
        // the surfaces already differ, the line only has to hint at the boundary.
        'conversation-header grid items-center gap-x-2 gap-y-0.5 border-b border-border/30 bg-background px-3 pb-2.5 pt-8 md:px-4 md:pt-2.5',
        conversation.type === 'contact' && activeContact
          ? 'grid-cols-[minmax(0,1fr)_auto] min-[1100px]:grid-cols-[minmax(0,1fr)_auto_auto]'
          : 'grid-cols-[minmax(0,1fr)_auto]'
      )}
    >
      <span className="flex min-w-0 items-center gap-2.5 md:gap-2">
        {/* The way out of a conversation belongs to the conversation, next to whose
            conversation it is — not to a bar above it that says the app's name. */}
        {onBack && (
          <button
            type="button"
            onClick={onBack}
            aria-label={t('shell.backToConversations')}
            className="liquid-surface glass-back-button focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
          >
            <ChevronLeft className="h-[1.375rem] w-[1.375rem]" aria-hidden="true" />
          </button>
        )}
        {conversation.type === 'contact' && onOpenContactInfo && (
          <button
            type="button"
            className="avatar-action-button flex-shrink-0 cursor-pointer rounded-full border-none bg-transparent p-0 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
            onClick={() => onOpenContactInfo(conversation.id)}
            title={t('chatHeader.viewContactInfo')}
            aria-label={t('chatHeader.viewInfoFor', { name: conversation.name })}
          >
            {/* Two sizes rather than one compromise: on a phone the avatar sits
                beside a 40px back control and has to hold its own against it; on
                desktop the row is dense and 28 is right. */}
            <span className="md:hidden">
              <ContactAvatar
                name={conversation.name}
                publicKey={conversation.id}
                size={40}
                contactType={contacts.find((c) => c.public_key === conversation.id)?.type}
                clickable
              />
            </span>
            <span className="hidden md:block">
              <ContactAvatar
                name={conversation.name}
                publicKey={conversation.id}
                size={28}
                contactType={contacts.find((c) => c.public_key === conversation.id)?.type}
                clickable
              />
            </span>
          </button>
        )}
        <span className="flex min-w-0 flex-1 flex-col">
          <span className="flex min-w-0 flex-wrap items-baseline gap-x-2 gap-y-0.5">
            {/* Wraps below md. Held on one line it did not truncate, it overflowed —
                the key and its reveal button ran under the action icons in the next
                grid column, which is what the overlapping text was. */}
            <span className="flex min-w-0 flex-1 flex-wrap items-baseline gap-x-2 md:flex-nowrap md:whitespace-nowrap">
              {/* Spans the row so the whole strip opens the details, not just the
                  glyphs of the name — the empty space beside a title reads as part
                  of the title. */}
              <h2 className="min-w-0 flex-1 text-[1.0625rem] font-semibold md:text-base">
                {titleClickable ? (
                  <button
                    type="button"
                    className="flex w-full max-w-full min-w-0 items-center gap-1.5 overflow-hidden rounded-sm text-left transition-colors hover:text-primary focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
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
              {/* The key is reference material, and it already has a home in the info
                  pane the ⓘ opens. On a phone it cost the header two extra lines
                  above the conversation it is meant to be a header for. */}
              {isPrivateChannel && !showKey ? (
                <span className="hidden md:contents">{showKeyButton}</span>
              ) : isHiddenContactKey ? (
                // Their own row below sm: side by side they overlapped, the key
                // running under the button's label.
                <span className="hidden min-w-0 flex-wrap items-baseline gap-x-1 md:flex">
                  {renderCopyableKey(conversation.id.slice(0, 12), true)}
                  {showKeyButton}
                </span>
              ) : (
                <span className="hidden min-w-0 md:contents">
                  {renderCopyableKey(
                    conversation.type === 'channel'
                      ? conversation.id.toLowerCase()
                      : conversation.id
                  )}
                </span>
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
      {/* One line on a phone, truncated: a subtitle under a name is glanced at, and
          two wrapped lines of it push the conversation down every time. The detail is
          in the info pane the header opens. */}
      {conversation.type === 'contact' && activeContact && (
        <div className="col-span-2 row-start-2 min-w-0 truncate whitespace-nowrap text-[0.6875rem] text-muted-foreground md:whitespace-normal min-[1100px]:col-span-1 min-[1100px]:col-start-2 min-[1100px]:row-start-1">
          <ContactStatusInfo
            contact={activeContact}
            ourLat={config?.lat ?? null}
            ourLon={config?.lon ?? null}
          />
        </div>
      )}
      <div className="relative flex items-center justify-end gap-1.5">
        {/* Below md the row is folded behind one control. Simple is the point — the
            header is a back arrow, who you are talking to, and a way in — but folding
            is not hiding: every action is still here, one tap away, because several of
            them (trace, notifications, delete) have no other home in the app. */}
        <button
          type="button"
          onClick={() => setActionsOpen((open) => !open)}
          aria-expanded={actionsOpen}
          aria-label={t('chatHeader.moreActions')}
          className={cn(headerActionClass, 'md:hidden')}
        >
          <MoreVertical className="h-4 w-4" aria-hidden="true" />
        </button>
        <div
          className={cn(
            'items-center gap-1.5 md:flex',
            actionsOpen
              ? 'absolute right-0 top-full z-30 mt-1 flex rounded-full border border-border bg-popover px-1.5 py-1 shadow-lg md:static md:mt-0 md:border-0 md:bg-transparent md:p-0 md:shadow-none'
              : 'hidden'
          )}
        >
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
