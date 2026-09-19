import { useEffect, useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { Bell } from 'lucide-react';

import { Checkbox } from './ui/checkbox';
import { cn } from '../lib/utils';
import type { NotificationMediaChannel, NotificationMediaFlags } from '../types';

const MEDIA: { channel: NotificationMediaChannel; label: string }[] = [
  { channel: 'push', label: 'chatHeader.notifyPush' },
  { channel: 'email', label: 'chatHeader.notifyEmail' },
  { channel: 'webhook', label: 'chatHeader.notifyWebhook' },
];

interface ConversationNotifyMenuProps {
  mediaEnabled: NotificationMediaFlags;
  emailReady: boolean;
  webhookReady: boolean;
  onSetMedia: (channel: NotificationMediaChannel, enabled: boolean) => void;
  onOpenSettings?: () => void;
}

export function ConversationNotifyMenu({
  mediaEnabled,
  emailReady,
  webhookReady,
  onSetMedia,
  onOpenSettings,
}: ConversationNotifyMenuProps) {
  const { t } = useTranslation();
  const [open, setOpen] = useState(false);
  const rootRef = useRef<HTMLDivElement>(null);
  const anyOn = mediaEnabled.push || mediaEnabled.email || mediaEnabled.webhook;

  useEffect(() => {
    if (!open) return;
    const onPointer = (event: PointerEvent) => {
      if (!rootRef.current?.contains(event.target as Node)) {
        setOpen(false);
      }
    };
    const onKey = (event: KeyboardEvent) => {
      if (event.key === 'Escape') setOpen(false);
    };
    document.addEventListener('pointerdown', onPointer);
    document.addEventListener('keydown', onKey);
    return () => {
      document.removeEventListener('pointerdown', onPointer);
      document.removeEventListener('keydown', onKey);
    };
  }, [open]);

  const ready = (channel: NotificationMediaChannel) =>
    channel === 'push' || (channel === 'email' ? emailReady : webhookReady);

  return (
    <div ref={rootRef} className="relative">
      <button
        className="inline-flex h-9 w-9 shrink-0 items-center justify-center rounded p-2 transition-colors hover:bg-accent focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
        onClick={() => setOpen((value) => !value)}
        title={t('chatHeader.notifications')}
        aria-label={t('chatHeader.notifications')}
        aria-expanded={open}
        aria-haspopup="menu"
        aria-pressed={anyOn}
      >
        <Bell
          className={cn('h-4 w-4', anyOn ? 'text-primary' : 'text-muted-foreground')}
          fill={anyOn ? 'currentColor' : 'none'}
          aria-hidden="true"
        />
      </button>
      {open && (
        <div
          role="menu"
          className="absolute right-0 z-50 mt-1 min-w-[13.5rem] rounded-md border border-border bg-background p-2 shadow-md"
        >
          <p className="px-1 pb-2 text-[0.6875rem] font-medium uppercase tracking-wider text-muted-foreground">
            {t('chatHeader.notifyMenu')}
          </p>
          <div className="space-y-1.5">
            {MEDIA.map(({ channel, label }) => {
              const enabled = ready(channel);
              const id = `notify-menu-${channel}`;
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
                    checked={mediaEnabled[channel]}
                    disabled={!enabled}
                    onCheckedChange={(checked) => onSetMedia(channel, checked === true)}
                  />
                  <span>{t(label)}</span>
                </label>
              );
            })}
          </div>
          {(!emailReady || !webhookReady) && onOpenSettings && (
            <button
              type="button"
              className="mt-2 w-full rounded px-1 py-1 text-left text-[0.6875rem] text-muted-foreground hover:text-foreground"
              onClick={() => {
                setOpen(false);
                onOpenSettings();
              }}
            >
              {t('chatHeader.notifyConfigureDestinations')}
            </button>
          )}
        </div>
      )}
    </div>
  );
}
