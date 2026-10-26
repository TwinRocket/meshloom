import type { TFunction } from 'i18next';
import { useTranslation } from 'react-i18next';

import { Checkbox } from '../../ui/checkbox';
import { Input } from '../../ui/input';
import { Label } from '../../ui/label';
import type { ChannelDraft } from './notificationsShared';

/** The intervals offered (seconds); 0 is off. The server accepts 0 or 300..86400. */
export const HEARTBEAT_INTERVALS = [0, 300, 900, 3600, 21600, 86400] as const;

const SELECT_CLASS =
  'h-9 w-full px-3 rounded-md border border-input bg-background text-sm ring-offset-background focus:outline-hidden focus:ring-2 focus:ring-ring focus:ring-offset-2';

type HeartbeatDraft = Pick<
  ChannelDraft,
  'heartbeat_interval_s' | 'heartbeat_method' | 'heartbeat_url' | 'heartbeat_url_clear'
>;

/**
 * The channel's heartbeat (MSHL-131): a `notify.heartbeat` message every interval, or a
 * GET/POST to a healthchecks-style URL instead. The URL is write-only, like the other
 * secrets: a saved one is never shown, an empty field keeps it.
 */
export function HeartbeatFields({
  idPrefix,
  draft,
  urlSet,
  disabled,
  onChange,
}: {
  idPrefix: string;
  draft: HeartbeatDraft;
  /** A ping URL is saved on the channel. */
  urlSet: boolean;
  disabled?: boolean;
  onChange: (next: Partial<HeartbeatDraft>) => void;
}) {
  const { t } = useTranslation();
  const intervals: number[] = HEARTBEAT_INTERVALS.includes(
    draft.heartbeat_interval_s as (typeof HEARTBEAT_INTERVALS)[number]
  )
    ? [...HEARTBEAT_INTERVALS]
    : [...HEARTBEAT_INTERVALS, draft.heartbeat_interval_s];
  const on = draft.heartbeat_interval_s > 0;

  return (
    <div className="space-y-3" data-testid="channel-heartbeat">
      <div className="space-y-1.5">
        <Label htmlFor={`${idPrefix}-heartbeat`}>
          {t('settings.notifications.heartbeat.title')}
        </Label>
        <select
          id={`${idPrefix}-heartbeat`}
          value={draft.heartbeat_interval_s}
          disabled={disabled}
          onChange={(event) => onChange({ heartbeat_interval_s: Number(event.target.value) })}
          className={SELECT_CLASS}
        >
          {intervals.map((seconds) => (
            <option key={seconds} value={seconds}>
              {intervalLabel(seconds, t)}
            </option>
          ))}
        </select>
        <p className="text-[0.8125rem] text-muted-foreground">
          {t('settings.notifications.heartbeat.help')}
        </p>
      </div>
      {on && (
        <div className="grid gap-3 sm:grid-cols-[1fr_8rem]">
          <div className="space-y-1.5">
            <Label htmlFor={`${idPrefix}-heartbeat-url`}>
              {t('settings.notifications.heartbeat.url')}
            </Label>
            <Input
              id={`${idPrefix}-heartbeat-url`}
              type="url"
              value={draft.heartbeat_url}
              maxLength={2048}
              autoComplete="off"
              disabled={disabled || draft.heartbeat_url_clear}
              placeholder={
                urlSet ? t('settings.notifications.heartbeat.urlKept') : 'https://hc-ping.com/…'
              }
              onChange={(event) => onChange({ heartbeat_url: event.target.value })}
            />
          </div>
          <div className="space-y-1.5">
            <Label htmlFor={`${idPrefix}-heartbeat-method`}>
              {t('settings.notifications.heartbeat.method')}
            </Label>
            <select
              id={`${idPrefix}-heartbeat-method`}
              value={draft.heartbeat_method}
              disabled={disabled}
              onChange={(event) =>
                onChange({ heartbeat_method: event.target.value as 'GET' | 'POST' })
              }
              className={SELECT_CLASS}
            >
              <option value="GET">GET</option>
              <option value="POST">POST</option>
            </select>
          </div>
          <p className="text-[0.8125rem] text-muted-foreground sm:col-span-2">
            {t('settings.notifications.heartbeat.urlHelp')}
          </p>
          {urlSet && (
            <div className="flex items-center gap-2 sm:col-span-2">
              <Checkbox
                id={`${idPrefix}-heartbeat-clear`}
                checked={draft.heartbeat_url_clear}
                disabled={disabled}
                onCheckedChange={(checked) =>
                  onChange({ heartbeat_url_clear: checked === true, heartbeat_url: '' })
                }
              />
              <Label htmlFor={`${idPrefix}-heartbeat-clear`}>
                {t('settings.notifications.heartbeat.urlClear')}
              </Label>
            </div>
          )}
        </div>
      )}
    </div>
  );
}

function intervalLabel(seconds: number, t: TFunction): string {
  if (seconds === 0) return t('settings.notifications.heartbeat.off');
  if (seconds % 3600 === 0) {
    return t('settings.notifications.heartbeat.everyHours', { count: seconds / 3600 });
  }
  return t('settings.notifications.heartbeat.everyMinutes', { count: Math.round(seconds / 60) });
}
