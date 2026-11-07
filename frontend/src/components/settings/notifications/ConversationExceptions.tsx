import { useTranslation } from 'react-i18next';
import { X } from 'lucide-react';

import type { NotificationChannel, NotificationRules } from '../../../notifications/types';
import type { Channel, Contact } from '../../../types';
import { getContactDisplayName } from '../../../utils/pubkey';
import {
  channelLabel,
  exceptionState,
  nextExceptionState,
  withExceptionState,
  type ExceptionState,
} from './rulesShared';

/** Name of a conversation key (`contact-<pubkey>`, `channel-<key>`) from contacts and channels. */
export function conversationName(key: string, contacts: Contact[], channels: Channel[]): string {
  if (key.startsWith('contact-')) {
    const pubkey = key.slice('contact-'.length);
    const contact = contacts.find((c) => c.public_key === pubkey);
    return contact ? getContactDisplayName(contact.name, contact.public_key) : pubkey.slice(0, 12);
  }
  if (key.startsWith('channel-')) {
    const k = key.slice('channel-'.length);
    const channel = channels.find((c) => c.key === k);
    if (channel?.name) return channel.name.startsWith('#') ? channel.name : `#${channel.name}`;
    return `#${k.slice(0, 12)}`;
  }
  return key;
}

const STATE_CLASS: Record<ExceptionState, string> = {
  inherit: 'border-border text-muted-foreground',
  on: 'border-primary bg-primary/10 text-foreground',
  off: 'border-destructive/60 bg-destructive/10 text-foreground line-through',
};

/**
 * One line per conversation with an exception: a chip per channel cycling inherit, on, off
 * (a stored value wins over the event rule), and a remove button.
 */
export function ConversationExceptions({
  rules,
  channels,
  contacts,
  chatChannels,
  disabled,
  onSet,
}: {
  rules: NotificationRules;
  channels: NotificationChannel[];
  contacts: Contact[];
  chatChannels: Channel[];
  disabled?: boolean;
  /** `null` removes the exception. */
  onSet: (key: string, map: Record<string, boolean> | null) => void;
}) {
  const { t } = useTranslation();
  const keys = Object.keys(rules.conversations);
  if (keys.length === 0) {
    return (
      <p className="text-[0.8125rem] text-muted-foreground">
        {t('settings.notifications.exceptionsEmpty')}
      </p>
    );
  }
  return (
    <ul className="space-y-2" data-testid="notification-exceptions">
      {keys.map((key) => {
        const map = rules.conversations[key];
        const known = new Set(channels.map((c) => c.id));
        const orphans = Object.keys(map).filter((id) => !known.has(id));
        // The server refuses unknown channel ids, so every write drops the orphans.
        const live = Object.fromEntries(Object.entries(map).filter(([id]) => known.has(id)));
        const name = conversationName(key, contacts, chatChannels);
        return (
          <li
            key={key}
            className="flex flex-wrap items-center gap-x-3 gap-y-1.5 rounded-md border border-border/60 px-3 py-2"
          >
            <span className="min-w-0 max-w-full break-words text-sm font-medium">{name}</span>
            <div className="flex flex-wrap gap-1.5">
              {channels.map((channel) => {
                const state = exceptionState(map, channel.id);
                const stateLabel = t(`settings.notifications.exceptionState.${state}`);
                return (
                  <button
                    key={channel.id}
                    type="button"
                    disabled={disabled}
                    onClick={() =>
                      onSet(key, withExceptionState(live, channel.id, nextExceptionState(state)))
                    }
                    aria-label={t('settings.notifications.exceptionChip', {
                      name,
                      channel: channel.name,
                      state: stateLabel,
                    })}
                    className={`rounded-full border px-2.5 py-0.5 text-[0.75rem] disabled:opacity-50 ${STATE_CLASS[state]}`}
                  >
                    {channel.name} · {stateLabel}
                  </button>
                );
              })}
              {orphans.map((id) => (
                <button
                  key={id}
                  type="button"
                  disabled={disabled}
                  onClick={() => onSet(key, Object.keys(live).length ? live : null)}
                  aria-label={t('settings.notifications.exceptionOrphan', {
                    name,
                    channel: channelLabel(channels, id),
                  })}
                  className="rounded-full border border-dashed border-border px-2.5 py-0.5 text-[0.75rem] text-muted-foreground disabled:opacity-50"
                >
                  {channelLabel(channels, id)} · {t('settings.notifications.exceptionOrphanState')}{' '}
                  <X className="inline h-3 w-3" aria-hidden="true" />
                </button>
              ))}
            </div>
            <button
              type="button"
              disabled={disabled}
              onClick={() => onSet(key, null)}
              className="ml-auto rounded-full p-1 transition-colors hover:bg-accent"
              title={t('settings.remove')}
              aria-label={t('settings.notifications.removeOverride', { name })}
            >
              <X className="h-3.5 w-3.5" />
            </button>
          </li>
        );
      })}
    </ul>
  );
}
