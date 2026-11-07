import type { TFunction } from 'i18next';
import { useTranslation } from 'react-i18next';

import { Label } from '../../ui/label';
import type { ChannelDraft } from './notificationsShared';

/** The intervals offered (seconds); 0 is off. The server accepts 0 or 300..86400. */
export const DIGEST_INTERVALS = [0, 300, 900, 1800, 3600, 21600, 86400] as const;

const SELECT_CLASS =
  'h-9 w-full px-3 rounded-md border border-input bg-background text-sm ring-offset-background focus:outline-hidden focus:ring-2 focus:ring-ring focus:ring-offset-2';

/**
 * The channel's digest mode (MSHL-136): one summary per interval instead of one notification
 * per message. Offered on every channel type.
 */
export function DigestField({
  idPrefix,
  draft,
  disabled,
  onChange,
}: {
  idPrefix: string;
  draft: Pick<ChannelDraft, 'digest_interval_s'>;
  disabled?: boolean;
  onChange: (next: Partial<Pick<ChannelDraft, 'digest_interval_s'>>) => void;
}) {
  const { t } = useTranslation();
  const intervals: number[] = DIGEST_INTERVALS.includes(
    draft.digest_interval_s as (typeof DIGEST_INTERVALS)[number]
  )
    ? [...DIGEST_INTERVALS]
    : [...DIGEST_INTERVALS, draft.digest_interval_s];

  return (
    <div className="space-y-1.5" data-testid="channel-digest">
      <Label htmlFor={`${idPrefix}-digest`}>{t('settings.notifications.digest.title')}</Label>
      <select
        id={`${idPrefix}-digest`}
        value={draft.digest_interval_s}
        disabled={disabled}
        onChange={(event) => onChange({ digest_interval_s: Number(event.target.value) })}
        className={SELECT_CLASS}
      >
        {intervals.map((seconds) => (
          <option key={seconds} value={seconds}>
            {intervalLabel(seconds, t)}
          </option>
        ))}
      </select>
      <p className="text-[0.8125rem] text-muted-foreground">
        {t('settings.notifications.digest.help')}
      </p>
    </div>
  );
}

function intervalLabel(seconds: number, t: TFunction): string {
  if (seconds === 0) return t('settings.notifications.digest.off');
  if (seconds % 3600 === 0) {
    return t('settings.notifications.digest.everyHours', { count: seconds / 3600 });
  }
  return t('settings.notifications.digest.everyMinutes', { count: Math.round(seconds / 60) });
}
