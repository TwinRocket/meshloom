import { useCallback, useEffect, useState } from 'react';
import { useTranslation } from 'react-i18next';

import { api, formatApiError } from '../../../api';
import { useCapabilities } from '../../../auth/SessionProvider';
import type { MyPushRuleState, MyPushRules, MyPushRulesPatch } from '../../../notifications/types';
import { Button } from '../../ui/button';
import { Separator } from '../../ui/separator';
import { toast } from '../../ui/sonner';
import { SettingsGroupHeader } from '../settingsPrimitives';
import { CONTACT_KINDS, GROUP_ORDER, eventKey, groupOf } from './rulesShared';

const SELECT_CLASS =
  'h-8 rounded-md border border-input bg-background px-2 text-sm ring-offset-background focus:outline-hidden focus:ring-2 focus:ring-ring focus:ring-offset-2';

type Choice = 'default' | 'on' | 'off';

function choiceOf(state: MyPushRuleState): Choice {
  return state.choice === null ? 'default' : state.choice ? 'on' : 'off';
}

function valueOf(choice: Choice): boolean | null {
  return choice === 'default' ? null : choice === 'on';
}

function ChoiceSelect({
  label,
  state,
  disabled,
  onChange,
}: {
  label: string;
  state: MyPushRuleState;
  disabled: boolean;
  onChange: (value: boolean | null) => void;
}) {
  const { t } = useTranslation();
  return (
    <select
      aria-label={label}
      className={SELECT_CLASS}
      value={choiceOf(state)}
      disabled={disabled}
      onChange={(e) => onChange(valueOf(e.target.value as Choice))}
    >
      <option value="default">
        {state.default
          ? t('settings.notifications.mine.followDefaultOn')
          : t('settings.notifications.mine.followDefaultOff')}
      </option>
      <option value="on">{t('settings.notifications.mine.on')}</option>
      <option value="off">{t('settings.notifications.mine.off')}</option>
    </select>
  );
}

/**
 * The account's own notification rules (MSHL-321): for each event, follow the administrator's
 * default or force it on or off for this account's devices. Instance destinations (e-mail,
 * webhooks...) stay with the administrator, whose matrix below is the default for everyone.
 * Renders nothing for a role that owns no devices.
 */
export function MyNotificationRules() {
  const { t } = useTranslation();
  const { canSend } = useCapabilities();
  const [rules, setRules] = useState<MyPushRules | null>(null);
  const [failed, setFailed] = useState(false);
  const [busy, setBusy] = useState(false);

  const load = useCallback(async () => {
    try {
      setRules(await api.getMyPushRules());
      setFailed(false);
    } catch {
      setFailed(true);
    }
  }, []);

  useEffect(() => {
    // eslint-disable-next-line react-hooks/set-state-in-effect -- initial fetch
    if (canSend) void load();
  }, [canSend, load]);

  if (!canSend || failed || !rules) return null;

  const run = async (call: () => Promise<MyPushRules>) => {
    setBusy(true);
    try {
      setRules(await call());
    } catch (err) {
      toast.error(t('settings.notifications.mine.saveFailed'), {
        description: formatApiError(err, t),
      });
    } finally {
      setBusy(false);
    }
  };
  const patch = (body: MyPushRulesPatch) => void run(() => api.patchMyPushRules(body));

  const customised =
    Object.values(rules.events).some((s) => s.choice !== null) ||
    Object.values(rules.contact_types).some((s) => s.choice !== null);
  const eventLabel = (id: string) =>
    t(`settings.notifications.matrix.events.${eventKey(id)}.label`);

  return (
    <>
      <div className="space-y-3" data-testid="my-notification-rules">
        <SettingsGroupHeader
          title={t('settings.notifications.mine.title')}
          storedOn="server"
          instant
        />
        <p className="text-[0.8125rem] text-muted-foreground">
          {t('settings.notifications.mine.help')}
        </p>
        {GROUP_ORDER.map((group) => {
          const ids = Object.keys(rules.events).filter((id) => groupOf(id) === group);
          if (ids.length === 0) return null;
          return (
            <div key={group} className="space-y-1">
              <span className="text-[0.625rem] font-medium uppercase tracking-wider text-muted-foreground">
                {t(`settings.notifications.matrix.groups.${group}`)}
              </span>
              {ids.map((id) => (
                <div key={id}>
                  <div className="flex items-center justify-between gap-3 py-1">
                    <span className="min-w-0 text-sm">{eventLabel(id)}</span>
                    {id === 'contact.first_seen' ? null : (
                      <ChoiceSelect
                        label={eventLabel(id)}
                        state={rules.events[id]}
                        disabled={busy}
                        onChange={(value) => patch({ events: { [id]: value } })}
                      />
                    )}
                  </div>
                  {id === 'contact.first_seen' &&
                    CONTACT_KINDS.map((kind) => {
                      const state = rules.contact_types[kind];
                      if (!state) return null;
                      const kindLabel = t(`settings.notifications.matrix.contactTypes.${kind}`);
                      return (
                        <div
                          key={kind}
                          className="flex items-center justify-between gap-3 py-1 pl-4"
                        >
                          <span className="min-w-0 text-sm text-muted-foreground">{kindLabel}</span>
                          <ChoiceSelect
                            label={`${eventLabel(id)}: ${kindLabel}`}
                            state={state}
                            disabled={busy}
                            onChange={(value) => patch({ contact_types: { [kind]: value } })}
                          />
                        </div>
                      );
                    })}
                </div>
              ))}
            </div>
          );
        })}
        <Button
          variant="outline"
          size="sm"
          disabled={busy || !customised}
          onClick={() => void run(() => api.resetMyPushRules())}
        >
          {t('settings.notifications.mine.reset')}
        </Button>
      </div>
      <Separator />
    </>
  );
}
