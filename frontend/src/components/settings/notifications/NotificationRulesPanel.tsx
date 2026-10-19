import { useState } from 'react';
import { useTranslation } from 'react-i18next';

import { api, formatApiError } from '../../../api';
import type { NotificationRulesPatch } from '../../../notifications/types';
import type { Channel, Contact } from '../../../types';
import { Separator } from '../../ui/separator';
import { toast } from '../../ui/sonner';
import { SettingsGroupHeader } from '../settingsPrimitives';
import { ConversationExceptions } from './ConversationExceptions';
import { GlobalPauseControl } from './GlobalPauseControl';
import { RulesMatrix } from './RulesMatrix';
import type { NotificationChannelsState } from './useNotificationChannels';

/** Settings > Notifications, rules: the global pause, the events x channels matrix and the exceptions. */
export function NotificationRulesPanel({
  state,
  contacts,
  chatChannels,
}: {
  state: NotificationChannelsState;
  contacts: Contact[];
  chatChannels: Channel[];
}) {
  const { t } = useTranslation();
  const { channels, rules, events, loading, error, setRules } = state;
  const [busy, setBusy] = useState(false);

  const run = async (call: () => Promise<NonNullable<typeof rules>>, failure: string) => {
    setBusy(true);
    try {
      setRules(await call());
    } catch (err) {
      toast.error(failure, { description: formatApiError(err, t) });
    } finally {
      setBusy(false);
    }
  };

  const patch = (body: NotificationRulesPatch) =>
    void run(() => api.patchNotificationRules(body), t('settings.notifications.rulesSaveFailed'));

  if (error || loading || !rules) return null;

  return (
    <>
      <div className="space-y-3">
        <SettingsGroupHeader
          title={t('settings.notifications.pause.title')}
          storedOn="server"
          instant
        />
        <p className="text-[0.8125rem] text-muted-foreground">
          {t('settings.notifications.pause.help')}
        </p>
        <GlobalPauseControl
          pausedUntil={rules.paused_until}
          disabled={busy}
          onChange={(until) => patch({ paused_until: until })}
        />
      </div>

      <Separator />

      <div className="space-y-3">
        <SettingsGroupHeader
          title={t('settings.notifications.defaults')}
          storedOn="server"
          instant
        />
        <p className="text-[0.8125rem] text-muted-foreground">
          {t('settings.notifications.matrix.help')}
        </p>
        <RulesMatrix
          rules={rules}
          events={events}
          channels={channels}
          disabled={busy}
          onPatch={patch}
        />
      </div>

      <Separator />

      <div className="space-y-3">
        <SettingsGroupHeader
          title={t('settings.notifications.exceptions')}
          storedOn="server"
          instant
        />
        <p className="text-[0.8125rem] text-muted-foreground">
          {t('settings.notifications.exceptionsHelpN')}
        </p>
        <ConversationExceptions
          rules={rules}
          channels={channels}
          contacts={contacts}
          chatChannels={chatChannels}
          disabled={busy}
          onSet={(key, map) =>
            void run(
              () => api.setNotificationConversationRule(key, map),
              t('settings.notifications.rulesSaveFailed')
            )
          }
        />
      </div>
    </>
  );
}
