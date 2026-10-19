import { useEffect, useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { Bell } from 'lucide-react';

import { useConversationNotifications } from '../hooks/useConversationNotifications';
import { cn } from '../lib/utils';
import {
  ConversationNotifyPanelView,
  channelOn,
  type ConversationNotifyTarget,
} from './ConversationNotifyPanel';

interface ConversationNotifyMenuProps {
  target: ConversationNotifyTarget;
  onOpenSettings?: () => void;
}

/**
 * The conversation bell: a button that opens the channel checkboxes and "Do not disturb"
 * (`ConversationNotifyPanelView`). The bell is filled while any enabled channel notifies
 * this conversation. The caller shows it to administrators only (the API is admin-only).
 */
export function ConversationNotifyMenu({ target, onOpenSettings }: ConversationNotifyMenuProps) {
  const { t } = useTranslation();
  const [open, setOpen] = useState(false);
  const rootRef = useRef<HTMLDivElement>(null);
  const state = useConversationNotifications();
  const { load, channels, rules } = state;
  const anyOn = channels.some((channel) => channelOn(rules, target, channel.id));

  useEffect(() => {
    void load();
  }, [load]);

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

  return (
    <div ref={rootRef} className="relative">
      <button
        className="inline-flex h-9 w-9 shrink-0 items-center justify-center rounded p-2 transition-colors hover:bg-accent focus-visible:outline-hidden focus-visible:ring-2 focus-visible:ring-ring"
        onClick={() => {
          if (!open) void load(true);
          setOpen((value) => !value);
        }}
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
          className="absolute right-0 z-50 mt-1 w-72 max-w-[calc(100vw-1.5rem)] rounded-md border border-border bg-background p-2 shadow-md"
        >
          <p className="px-1 pb-2 text-[0.6875rem] font-medium uppercase tracking-wider text-muted-foreground">
            {t('chatHeader.notifyMenu')}
          </p>
          <ConversationNotifyPanelView
            target={target}
            idPrefix="notify-menu"
            state={state}
            onOpenSettings={
              onOpenSettings
                ? () => {
                    setOpen(false);
                    onOpenSettings();
                  }
                : undefined
            }
          />
        </div>
      )}
    </div>
  );
}
