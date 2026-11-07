import { useCallback, useEffect, useState } from 'react';
import { useTranslation } from 'react-i18next';

import { api, formatApiError } from '../../../api';
import type { NotificationChannel, NotificationRules } from '../../../notifications/types';
import { Button } from '../../ui/button';
import { toast } from '../../ui/sonner';
import { iconFor } from './notificationsShared';
import { eventRule, toggledChannels } from './rulesShared';

export const TELEMETRY_ALERT_EVENT = 'telemetry.alert';

/**
 * Where telemetry alerts are sent: a chip per notification channel, the `telemetry.alert`
 * row of the rules matrix (`PATCH /api/notifications/rules`). The matrix on the
 * Notifications page edits the same row.
 */
export function TelemetryAlertChannels() {
  const { t } = useTranslation();
  const [channels, setChannels] = useState<NotificationChannel[] | null>(null);
  const [rules, setRules] = useState<NotificationRules | null>(null);
  const [error, setError] = useState<unknown>(null);
  const [busy, setBusy] = useState(false);

  const load = useCallback(async () => {
    try {
      const [nextChannels, nextRules] = await Promise.all([
        api.getNotificationChannels(),
        api.getNotificationRules(),
      ]);
      setChannels(nextChannels);
      setRules(nextRules);
      setError(null);
    } catch (err) {
      setError(err);
    }
  }, []);

  useEffect(() => {
    // eslint-disable-next-line react-hooks/set-state-in-effect -- initial fetch
    void load();
  }, [load]);

  if (error || !channels || !rules) {
    if (!error) return null;
    return (
      <div className="space-y-2" role="alert">
        <p className="text-sm text-destructive">
          {t('settings.alerts.channelsLoadFailed')} {formatApiError(error, t)}
        </p>
        <Button variant="outline" size="sm" onClick={() => void load()}>
          {t('settings.notifications.rulesRetry')}
        </Button>
      </div>
    );
  }

  const selected = eventRule(rules, TELEMETRY_ALERT_EVENT).channels;
  const toggle = async (channelId: string, on: boolean) => {
    setBusy(true);
    try {
      setRules(
        await api.patchNotificationRules({
          events: {
            [TELEMETRY_ALERT_EVENT]: { channels: toggledChannels(selected, channelId, on) },
          },
        })
      );
    } catch (err) {
      toast.error(t('settings.alerts.channelsSaveFailed'), { description: formatApiError(err, t) });
    } finally {
      setBusy(false);
    }
  };

  if (channels.length === 0) {
    return (
      <p className="text-[0.8125rem] text-muted-foreground">{t('settings.alerts.channelsNone')}</p>
    );
  }

  return (
    <div
      role="group"
      aria-label={t('settings.alerts.channelsGroup')}
      className="flex flex-wrap gap-2"
      data-testid="telemetry-alert-channels"
    >
      {channels.map((channel) => {
        const on = selected.includes(channel.id);
        const Icon = iconFor(channel.type);
        return (
          <button
            key={channel.id}
            type="button"
            aria-pressed={on}
            disabled={busy}
            title={channel.enabled ? undefined : t('settings.notifications.matrix.channelOff')}
            onClick={() => void toggle(channel.id, !on)}
            className={`inline-flex max-w-full items-center gap-1.5 rounded-full border px-3 py-1 text-[0.8125rem] disabled:opacity-50 ${
              on
                ? 'border-primary bg-primary/10 text-foreground'
                : 'border-border text-muted-foreground'
            } ${channel.enabled ? '' : 'opacity-60'}`}
          >
            <Icon className="h-3.5 w-3.5 shrink-0" aria-hidden="true" />
            <span className="min-w-0 break-words text-left">{channel.name}</span>
            <span className="sr-only">
              {on ? t('settings.alerts.channelOn') : t('settings.alerts.channelOff')}
            </span>
          </button>
        );
      })}
    </div>
  );
}
