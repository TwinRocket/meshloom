import { useMemo, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { X } from 'lucide-react';

import type { Channel, Contact } from '../../../types';
import { getContactDisplayName } from '../../../utils/pubkey';
import { Button } from '../../ui/button';
import { conversationName } from './ConversationExceptions';

const SELECT_CLASS =
  'h-8 rounded-md border border-input bg-background px-2 text-sm ring-offset-background focus:outline-hidden focus:ring-2 focus:ring-ring focus:ring-offset-2';

/**
 * The account's own conversation exceptions (MSHL-362): mute or force one channel or contact for
 * this account's devices, over its event choices. A muted conversation never notifies; a forced
 * one notifies unless the conversation is muted or the sender blocked.
 */
export function MyConversationRules({
  conversations,
  contacts,
  chatChannels,
  disabled,
  onSet,
}: {
  conversations: Record<string, boolean>;
  contacts: Contact[];
  chatChannels: Channel[];
  disabled: boolean;
  /** `null` removes the exception. */
  onSet: (key: string, value: boolean | null) => void;
}) {
  const { t } = useTranslation();
  const [picked, setPicked] = useState('');

  const options = useMemo(() => {
    const channelOptions = chatChannels
      .filter((c) => c.membership !== 'pending')
      .map((c) => ({
        key: `channel-${c.key}`,
        label: conversationName(`channel-${c.key}`, [], [c]),
      }));
    const contactOptions = contacts.map((c) => ({
      key: `contact-${c.public_key}`,
      label: getContactDisplayName(c.name, c.public_key),
    }));
    return [...channelOptions, ...contactOptions]
      .filter((o) => !(o.key in conversations))
      .sort((a, b) => a.label.localeCompare(b.label));
  }, [chatChannels, contacts, conversations]);

  const keys = Object.keys(conversations);
  return (
    <div className="space-y-2" data-testid="my-conversation-rules">
      <span className="text-[0.625rem] font-medium uppercase tracking-wider text-muted-foreground">
        {t('settings.notifications.mine.conversations')}
      </span>
      <p className="text-[0.8125rem] text-muted-foreground">
        {t('settings.notifications.mine.conversationsHelp')}
      </p>
      {keys.length === 0 ? (
        <p className="text-[0.8125rem] text-muted-foreground">
          {t('settings.notifications.mine.conversationsEmpty')}
        </p>
      ) : (
        <ul className="space-y-1">
          {keys.map((key) => {
            const name = conversationName(key, contacts, chatChannels);
            return (
              <li key={key} className="flex items-center justify-between gap-3 py-1">
                <span className="min-w-0 break-words text-sm">{name}</span>
                <span className="flex items-center gap-1.5">
                  <select
                    aria-label={name}
                    className={SELECT_CLASS}
                    value={conversations[key] ? 'on' : 'off'}
                    disabled={disabled}
                    onChange={(e) => onSet(key, e.target.value === 'on')}
                  >
                    <option value="on">{t('settings.notifications.mine.on')}</option>
                    <option value="off">{t('settings.notifications.mine.off')}</option>
                  </select>
                  <button
                    type="button"
                    disabled={disabled}
                    onClick={() => onSet(key, null)}
                    className="rounded-full p-1 transition-colors hover:bg-accent disabled:opacity-50"
                    title={t('settings.remove')}
                    aria-label={t('settings.notifications.mine.conversationRemove', { name })}
                  >
                    <X className="h-3.5 w-3.5" />
                  </button>
                </span>
              </li>
            );
          })}
        </ul>
      )}
      <div className="flex flex-wrap items-center gap-2">
        <select
          aria-label={t('settings.notifications.mine.conversationPick')}
          className={`${SELECT_CLASS} max-w-full`}
          value={picked}
          disabled={disabled || options.length === 0}
          onChange={(e) => setPicked(e.target.value)}
        >
          <option value="">{t('settings.notifications.mine.conversationPick')}</option>
          {options.map((o) => (
            <option key={o.key} value={o.key}>
              {o.label}
            </option>
          ))}
        </select>
        <Button
          type="button"
          variant="outline"
          size="sm"
          disabled={disabled || !picked}
          onClick={() => {
            onSet(picked, false);
            setPicked('');
          }}
        >
          {t('settings.notifications.mine.conversationMute')}
        </Button>
        <Button
          type="button"
          variant="outline"
          size="sm"
          disabled={disabled || !picked}
          onClick={() => {
            onSet(picked, true);
            setPicked('');
          }}
        >
          {t('settings.notifications.mine.conversationForce')}
        </Button>
      </div>
    </div>
  );
}
