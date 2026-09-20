import { useEffect, useRef, useState, type ReactNode } from 'react';
import { useTranslation } from 'react-i18next';
import { Bell, ChevronDown, ChevronLeft, Globe2, Pin, Star, Trash2, VolumeX } from 'lucide-react';

import { Checkbox } from './ui/checkbox';
import { MUTE_DURATIONS } from './ChannelMuteMenu';
import { cn } from '../lib/utils';
import type { NotificationMediaChannel, NotificationMediaFlags } from '../types';

const MEDIA: { channel: NotificationMediaChannel; label: string }[] = [
  { channel: 'push', label: 'chatHeader.notifyPush' },
  { channel: 'email', label: 'chatHeader.notifyEmail' },
  { channel: 'webhook', label: 'chatHeader.notifyWebhook' },
];

type Panel = 'main' | 'notify' | 'mute';

interface ConversationOverflowMenuProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  pinned: boolean;
  favorite: boolean;
  canNotify: boolean;
  notifyMediaEnabled?: NotificationMediaFlags;
  emailReady?: boolean;
  webhookReady?: boolean;
  canMute: boolean;
  muted?: boolean;
  mutedUntil?: number | null;
  canRegion: boolean;
  canDelete: boolean;
  isChannel: boolean;
  onTogglePin: () => void;
  onToggleFavorite: () => void;
  onSetMedia?: (channel: NotificationMediaChannel, enabled: boolean) => void;
  onOpenNotifySettings?: () => void;
  onMute?: (durationSeconds: number) => void;
  onEditRegion?: () => void;
  onDelete: () => void;
}

function MenuRow({
  icon,
  children,
  onClick,
  destructive = false,
}: {
  icon: ReactNode;
  children: ReactNode;
  onClick: () => void;
  destructive?: boolean;
}) {
  return (
    <button
      type="button"
      role="menuitem"
      className={cn(
        'flex w-full items-center gap-3 px-3 py-2 text-left text-sm hover:bg-accent',
        destructive && 'text-destructive hover:bg-destructive/10'
      )}
      onClick={onClick}
    >
      <span className="inline-flex h-4 w-4 shrink-0 items-center justify-center">{icon}</span>
      <span className="min-w-0 flex-1">{children}</span>
    </button>
  );
}

export function ConversationOverflowMenu({
  open,
  onOpenChange,
  pinned,
  favorite,
  canNotify,
  notifyMediaEnabled,
  emailReady = false,
  webhookReady = false,
  canMute,
  muted = false,
  mutedUntil,
  canRegion,
  canDelete,
  isChannel,
  onTogglePin,
  onToggleFavorite,
  onSetMedia,
  onOpenNotifySettings,
  onMute,
  onEditRegion,
  onDelete,
}: ConversationOverflowMenuProps) {
  const { t, i18n } = useTranslation();
  const rootRef = useRef<HTMLDivElement>(null);
  const [panel, setPanel] = useState<Panel>('main');

  useEffect(() => {
    if (!open) {
      setPanel('main');
      return;
    }
    const onPointer = (event: PointerEvent) => {
      if (!rootRef.current?.contains(event.target as Node)) {
        onOpenChange(false);
      }
    };
    const onKey = (event: KeyboardEvent) => {
      if (event.key === 'Escape') onOpenChange(false);
    };
    document.addEventListener('pointerdown', onPointer);
    document.addEventListener('keydown', onKey);
    return () => {
      document.removeEventListener('pointerdown', onPointer);
      document.removeEventListener('keydown', onKey);
    };
  }, [open, onOpenChange]);

  const muteStatus =
    muted && mutedUntil && mutedUntil > Date.now() / 1000
      ? t('chatHeader.mutedUntil', {
          time: new Date(mutedUntil * 1000).toLocaleTimeString(i18n.language, {
            hour: '2-digit',
            minute: '2-digit',
          }),
        })
      : muted
        ? t('chatHeader.mutedIndefinite')
        : null;

  const ready = (channel: NotificationMediaChannel) =>
    channel === 'push' || (channel === 'email' ? emailReady : webhookReady);

  return (
    <div ref={rootRef} className="relative shrink-0">
      <button
        type="button"
        className={cn(
          'inline-flex h-8 w-8 items-center justify-center rounded-md text-muted-foreground transition-opacity hover:bg-accent hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring',
          'opacity-100 [@media(hover:hover)]:opacity-0 [@media(hover:hover)]:group-hover:opacity-100 [@media(hover:hover)]:group-focus-within:opacity-100',
          open && 'opacity-100'
        )}
        aria-label={t('conversationList.conversationOptions')}
        aria-haspopup="menu"
        aria-expanded={open}
        onClick={(event) => {
          event.stopPropagation();
          event.preventDefault();
          onOpenChange(!open);
        }}
      >
        <ChevronDown className="h-4 w-4" aria-hidden="true" />
      </button>
      {open && (
        <div
          role="menu"
          className="absolute right-0 z-50 mt-1 min-w-[14.5rem] rounded-md border border-border bg-background py-1 shadow-md"
        >
          {panel === 'main' && (
            <>
              <MenuRow
                icon={
                  <Pin className={cn('h-4 w-4', pinned && 'fill-current')} aria-hidden="true" />
                }
                onClick={() => {
                  onTogglePin();
                  onOpenChange(false);
                }}
              >
                {pinned ? t('chatHeader.unpin') : t('chatHeader.pin')}
              </MenuRow>
              {canNotify && notifyMediaEnabled && onSetMedia && (
                <MenuRow
                  icon={<Bell className="h-4 w-4" aria-hidden="true" />}
                  onClick={() => setPanel('notify')}
                >
                  {t('chatHeader.notifications')}
                </MenuRow>
              )}
              {canMute && onMute && (
                <MenuRow
                  icon={<VolumeX className="h-4 w-4" aria-hidden="true" />}
                  onClick={() => setPanel('mute')}
                >
                  {muted ? t('chatHeader.unmuteChannel') : t('chatHeader.muteChannel')}
                </MenuRow>
              )}
              <MenuRow
                icon={
                  <Star
                    className={cn('h-4 w-4', favorite && 'fill-current text-favorite')}
                    aria-hidden="true"
                  />
                }
                onClick={() => {
                  onToggleFavorite();
                  onOpenChange(false);
                }}
              >
                {favorite ? t('chatHeader.removeFavorite') : t('chatHeader.addFavorite')}
              </MenuRow>
              {canRegion && onEditRegion && (
                <MenuRow
                  icon={<Globe2 className="h-4 w-4" aria-hidden="true" />}
                  onClick={() => {
                    onEditRegion();
                    onOpenChange(false);
                  }}
                >
                  {t('chatHeader.regionalOverride')}
                </MenuRow>
              )}
              {canDelete && (
                <>
                  <div className="my-1 border-t border-border" />
                  <MenuRow
                    icon={<Trash2 className="h-4 w-4" aria-hidden="true" />}
                    destructive
                    onClick={() => {
                      onDelete();
                      onOpenChange(false);
                    }}
                  >
                    {isChannel ? t('chatHeader.leaveChannel') : t('chatHeader.deleteConversation')}
                  </MenuRow>
                </>
              )}
            </>
          )}
          {panel === 'notify' && notifyMediaEnabled && onSetMedia && (
            <>
              <button
                type="button"
                className="flex w-full items-center gap-2 px-3 py-2 text-left text-sm text-muted-foreground hover:bg-accent hover:text-foreground"
                onClick={() => setPanel('main')}
              >
                <ChevronLeft className="h-4 w-4" aria-hidden="true" />
                {t('chatHeader.notifyMenu')}
              </button>
              <div className="space-y-1 px-2 pb-1">
                {MEDIA.map(({ channel, label }) => {
                  const enabled = ready(channel);
                  const id = `overflow-notify-${channel}`;
                  return (
                    <label
                      key={channel}
                      htmlFor={id}
                      className={cn(
                        'flex items-center gap-2 rounded px-1 py-1 text-sm',
                        enabled ? 'cursor-pointer' : 'cursor-not-allowed opacity-60'
                      )}
                    >
                      <Checkbox
                        id={id}
                        checked={notifyMediaEnabled[channel]}
                        disabled={!enabled}
                        onCheckedChange={(checked) => onSetMedia(channel, checked === true)}
                      />
                      <span>{t(label)}</span>
                    </label>
                  );
                })}
              </div>
              {(!emailReady || !webhookReady) && onOpenNotifySettings && (
                <button
                  type="button"
                  className="w-full px-3 py-1.5 text-left text-[0.6875rem] text-muted-foreground hover:text-foreground"
                  onClick={() => {
                    onOpenChange(false);
                    onOpenNotifySettings();
                  }}
                >
                  {t('chatHeader.notifyConfigureDestinations')}
                </button>
              )}
            </>
          )}
          {panel === 'mute' && onMute && (
            <>
              <button
                type="button"
                className="flex w-full items-center gap-2 px-3 py-2 text-left text-sm text-muted-foreground hover:bg-accent hover:text-foreground"
                onClick={() => setPanel('main')}
              >
                <ChevronLeft className="h-4 w-4" aria-hidden="true" />
                {t('chatHeader.muteFor')}
              </button>
              {muteStatus && (
                <p className="px-3 py-1 text-[0.6875rem] text-muted-foreground">{muteStatus}</p>
              )}
              {muted && (
                <button
                  type="button"
                  role="menuitem"
                  className="flex w-full px-3 py-2 text-left text-sm hover:bg-accent"
                  onClick={() => {
                    onMute(0);
                    onOpenChange(false);
                  }}
                >
                  {t('chatHeader.unmuteChannel')}
                </button>
              )}
              {MUTE_DURATIONS.map(({ seconds, label }) => (
                <button
                  key={label}
                  type="button"
                  role="menuitem"
                  className="flex w-full px-3 py-2 text-left text-sm hover:bg-accent"
                  onClick={() => {
                    onMute(seconds);
                    onOpenChange(false);
                  }}
                >
                  {t(label)}
                </button>
              ))}
            </>
          )}
        </div>
      )}
    </div>
  );
}
