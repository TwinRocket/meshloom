import { useEffect } from 'react';
import { useTranslation } from 'react-i18next';

import { usePush } from '../contexts/PushSubscriptionContext';
import { useNotificationStatuses } from '../contexts/NotificationStatusContext';
import { formatApiError } from '../api';
import {
  useConversationNotifications,
  type ConversationNotificationsState,
} from '../hooks/useConversationNotifications';
import type { NotificationRules } from '../notifications/types';
import { BUILTIN_PUSH_ID } from '../notifications/ids';
import { conversationChannelEnabled } from '../utils/notificationPolicy';
import { Checkbox } from './ui/checkbox';
import { GlobalPauseControl } from './settings/notifications/GlobalPauseControl';
import { STATUS_DOT_CLASS, iconFor } from './settings/notifications/notificationsShared';
import { exceptionState, type ExceptionState } from './settings/notifications/rulesShared';

export interface ConversationNotifyTarget {
  /** `contact-<pubkey>` or `channel-<key>` (`getStateKey`). */
  stateKey: string;
  messageType: 'PRIV' | 'CHAN';
  isHashtag?: boolean;
  isPublic?: boolean;
}

/** Whether `channelId` notifies this conversation, with or without its own exception. */
export function channelOn(
  rules: NotificationRules | null,
  target: ConversationNotifyTarget,
  channelId: string,
  withoutException = false
): boolean {
  if (!rules) return false;
  const conversations = { ...rules.conversations };
  if (withoutException) delete conversations[target.stateKey];
  return conversationChannelEnabled({
    stateKey: target.stateKey,
    messageType: target.messageType,
    rules: { ...rules, conversations },
    channelId,
    isHashtag: target.isHashtag,
    isPublic: target.isPublic,
  });
}

/** The panel with its own state: loads when mounted, so mount it when the menu opens. */
export function ConversationNotifyPanel(props: {
  target: ConversationNotifyTarget;
  idPrefix: string;
  onOpenSettings?: () => void;
}) {
  const state = useConversationNotifications();
  const { load } = state;
  useEffect(() => {
    void load();
  }, [load]);
  return <ConversationNotifyPanelView {...props} state={state} />;
}

/**
 * The body of the conversation bell, shared by the chat header menu and the list row menu:
 * a checkbox per enabled channel (type icon, name, status dot), "Do not disturb" and a link
 * to the channels. A box shows what the conversation resolves to; the caption says whether
 * that is the default or this conversation's own exception (inherits / on / off). */
export function ConversationNotifyPanelView({
  target,
  idPrefix,
  onOpenSettings,
  state,
}: {
  target: ConversationNotifyTarget;
  idPrefix: string;
  onOpenSettings?: () => void;
  state: ConversationNotificationsState;
}) {
  const { t } = useTranslation();
  const push = usePush();
  const statuses = useNotificationStatuses();
  const { channels, rules, loading, error, setException, setPause } = state;
  const { stateKey } = target;
  const resolve = (channelId: string, withoutException: boolean) =>
    channelOn(rules, target, channelId, withoutException);

  const toggle = async (channelId: string, checked: boolean) => {
    const inherited = resolve(channelId, true);
    const next: ExceptionState = checked === inherited ? 'inherit' : checked ? 'on' : 'off';
    if (checked && channelId === BUILTIN_PUSH_ID && push.isSupported && !push.isSubscribed) {
      // The browser subscribes first, as before; a refusal toasts and the exception is still
      // written (it also applies to the other devices).
      await push.subscribe();
    }
    await setException(stateKey, channelId, next);
  };

  if (error && !rules) {
    return (
      <p role="alert" className="px-1 py-1 text-[0.8125rem] text-destructive">
        {t('chatHeader.notifyLoadFailed')} {formatApiError(error, t)}
      </p>
    );
  }
  if (loading && !rules) {
    return (
      <p role="status" className="px-1 py-1 text-[0.8125rem] text-muted-foreground">
        {t('chatHeader.notifyLoading')}
      </p>
    );
  }

  return (
    <div className="space-y-2">
      {channels.length === 0 && rules && (
        <p className="px-1 text-[0.8125rem] text-muted-foreground">{t('chatHeader.notifyNone')}</p>
      )}
      <ul className="space-y-1">
        {channels.map((channel) => {
          const Icon = iconFor(channel.type);
          const id = `${idPrefix}-${channel.id}`;
          const stored = exceptionState(rules?.conversations[stateKey] ?? {}, channel.id);
          const status = statuses[channel.id]?.status;
          const statusLabel = status
            ? t(`settings.notifications.channels.status.${status}`, { defaultValue: status })
            : null;
          return (
            <li key={channel.id}>
              <label
                htmlFor={id}
                className="flex cursor-pointer items-center gap-2 rounded px-1 py-1 text-sm"
              >
                <Checkbox
                  id={id}
                  checked={resolve(channel.id, false)}
                  onCheckedChange={(checked) => void toggle(channel.id, checked === true)}
                />
                <Icon className="h-4 w-4 shrink-0 text-muted-foreground" aria-hidden="true" />
                <span className="min-w-0 flex-1 break-words">{channel.name}</span>
                {status && (
                  <span
                    role="img"
                    aria-label={statusLabel ?? status}
                    title={statusLabel ?? status}
                    data-testid={`notify-status-${channel.id}`}
                    className={`inline-block h-2 w-2 shrink-0 rounded-full ${
                      STATUS_DOT_CLASS[status] ?? STATUS_DOT_CLASS.disconnected
                    }`}
                  />
                )}
                <span
                  className="shrink-0 text-[0.6875rem] text-muted-foreground"
                  data-testid={`notify-state-${channel.id}`}
                >
                  {t(`chatHeader.notifyState.${stored}`)}
                </span>
              </label>
            </li>
          );
        })}
      </ul>
      {rules && (
        <div className="space-y-1 border-t border-border pt-2">
          <p className="px-1 text-[0.6875rem] font-medium uppercase tracking-wider text-muted-foreground">
            {t('settings.notifications.pause.title')}
          </p>
          <div className="px-1">
            <GlobalPauseControl
              pausedUntil={rules.paused_until}
              onChange={(until) => void setPause(until)}
            />
          </div>
        </div>
      )}
      {onOpenSettings && (
        <button
          type="button"
          className="w-full rounded px-1 py-1 text-left text-[0.75rem] text-muted-foreground underline hover:text-foreground"
          onClick={onOpenSettings}
        >
          {t('chatHeader.notifyConfigureChannels')}
        </button>
      )}
    </div>
  );
}
