import type { ReactNode } from 'react';
import { MapPin, Smile, Sticker, X } from 'lucide-react';
import { useTranslation } from 'react-i18next';
import { Button } from './ui/button';
import { ComposerEmojiPicker } from './ComposerEmojiPicker';
import { GifPicker } from './GifPicker';
import { cn } from '@/lib/utils';

export type ComposerAttachTab = 'emoji' | 'gif' | 'location';

interface ComposerAttachPickerProps {
  tab: ComposerAttachTab;
  onTabChange: (tab: ComposerAttachTab) => void;
  onSelectEmoji: (emoji: string) => void;
  onSelectGif: (gifId: string) => void;
  onSendLocation: () => void;
  onClose: () => void;
  disabled?: boolean;
  hasShareableLocation: boolean;
  locationLabel?: string | null;
}

export function ComposerAttachPicker({
  tab,
  onTabChange,
  onSelectEmoji,
  onSelectGif,
  onSendLocation,
  onClose,
  disabled = false,
  hasShareableLocation,
  locationLabel,
}: ComposerAttachPickerProps) {
  const { t } = useTranslation();
  const title =
    tab === 'gif'
      ? t('chat.gifPicker')
      : tab === 'location'
        ? t('chat.locationPicker')
        : t('chat.emojiPicker');

  return (
    <div
      data-testid="composer-attach-picker"
      className="absolute inset-x-0 bottom-full z-40 mb-2 overflow-hidden rounded-xl border border-border bg-card shadow-lg"
    >
      <div className="flex items-center gap-2 border-b border-border px-3 py-2">
        <p className="min-w-0 flex-1 text-sm font-semibold">{title}</p>
        <Button
          type="button"
          variant="ghost"
          size="icon"
          className="h-8 w-8 flex-shrink-0"
          aria-label={t('chat.attachClose')}
          onClick={onClose}
        >
          <X className="h-4 w-4" />
        </Button>
      </div>
      {tab === 'emoji' && (
        <ComposerEmojiPicker onSelect={onSelectEmoji} disabled={disabled} embedded />
      )}
      {tab === 'gif' && <GifPicker onSelect={onSelectGif} disabled={disabled} embedded />}
      {tab === 'location' && (
        <div data-testid="location-picker" className="space-y-3 p-3">
          <p className="text-[0.8125rem] text-muted-foreground">
            {hasShareableLocation ? t('chat.locationReady') : t('share.noLocation')}
          </p>
          {hasShareableLocation && locationLabel && (
            <p className="truncate text-sm font-medium">{locationLabel}</p>
          )}
          <Button
            type="button"
            className="w-full"
            data-testid="share-location-trigger"
            disabled={disabled || !hasShareableLocation}
            onClick={onSendLocation}
          >
            <MapPin className="mr-2 h-4 w-4" />
            {t('share.shareLocation')}
          </Button>
        </div>
      )}
      <div className="flex items-center justify-around border-t border-border px-2 py-1">
        <AttachTab
          active={tab === 'emoji'}
          label={t('chat.emoji')}
          testId="composer-attach-tab-emoji"
          onSelect={() => onTabChange('emoji')}
        >
          <Smile className="h-5 w-5" />
        </AttachTab>
        <AttachTab
          active={tab === 'gif'}
          label={t('chat.gif')}
          testId="composer-attach-tab-gif"
          onSelect={() => onTabChange('gif')}
        >
          <Sticker className="h-5 w-5" />
          <span className="text-[0.625rem] font-semibold uppercase tracking-wider">
            {t('chat.gif')}
          </span>
        </AttachTab>
        <AttachTab
          active={tab === 'location'}
          label={t('share.shareLocation')}
          testId="composer-attach-tab-location"
          onSelect={() => onTabChange('location')}
        >
          <MapPin className="h-5 w-5" />
        </AttachTab>
      </div>
    </div>
  );
}

function AttachTab({
  active,
  label,
  testId,
  onSelect,
  children,
}: {
  active: boolean;
  label: string;
  testId: string;
  onSelect: () => void;
  children: ReactNode;
}) {
  return (
    <button
      type="button"
      data-testid={testId}
      aria-label={label}
      aria-current={active ? 'page' : undefined}
      className={cn(
        'flex h-10 min-w-10 items-center justify-center gap-1 rounded-full px-3 text-muted-foreground',
        active && 'bg-muted text-foreground'
      )}
      onClick={onSelect}
    >
      {children}
    </button>
  );
}
