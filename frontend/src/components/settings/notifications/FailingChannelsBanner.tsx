import { useTranslation } from 'react-i18next';

import { BUILTIN_PUSH_ID } from '../../../notifications/ids';
import type { NotificationChannel } from '../../../notifications/types';
import { Button } from '../../ui/button';
import { channelAttention } from './notificationsShared';

/**
 * The channels that need a look (MSHL-130): failing, auto-paused, or with a stored secret
 * the key cannot open. Each row offers a test send and, unless the secret is the issue, Resume
 * (clears the pause and the failure count).
 */
export function FailingChannelsBanner({
  channels,
  busyId,
  onTest,
  onResume,
}: {
  channels: NotificationChannel[];
  busyId: string | null;
  onTest: (channel: NotificationChannel) => void;
  onResume: (channel: NotificationChannel) => void;
}) {
  const { t, i18n } = useTranslation();
  const rows = channels
    .map((channel) => ({ channel, kind: channelAttention(channel) }))
    .filter((row) => row.kind !== null);
  if (rows.length === 0) return null;

  return (
    <div
      role="status"
      className="space-y-2 rounded-md border border-warning/40 bg-warning/10 p-2 text-[0.8125rem]"
      data-testid="notifications-failing-banner"
    >
      <p className="font-medium">{t('settings.notifications.attention.title')}</p>
      <p className="text-xs text-muted-foreground">{t('settings.notifications.attention.help')}</p>
      <ul className="space-y-1.5">
        {rows.map(({ channel, kind }) => {
          const name =
            channel.id === BUILTIN_PUSH_ID
              ? t('settings.notifications.channels.builtinPushName')
              : channel.name;
          const time = channel.paused_until
            ? new Date(channel.paused_until * 1000).toLocaleTimeString(i18n.language, {
                hour: '2-digit',
                minute: '2-digit',
              })
            : '';
          const busy = busyId === channel.id;
          return (
            <li
              key={channel.id}
              className="flex flex-wrap items-center gap-2"
              data-testid={`failing-channel-${channel.id}`}
            >
              <span className="min-w-0 flex-1 break-words">
                {t(`settings.notifications.attention.${kind}`, {
                  name,
                  count: channel.failure_count,
                  time,
                })}
              </span>
              <Button
                variant="outline"
                size="sm"
                disabled={busy}
                onClick={() => onTest(channel)}
                data-testid={`failing-test-${channel.id}`}
              >
                {t('settings.notifications.attention.test')}
              </Button>
              {kind !== 'secret_unreadable' && (
                <Button
                  variant="outline"
                  size="sm"
                  disabled={busy}
                  onClick={() => onResume(channel)}
                  data-testid={`failing-resume-${channel.id}`}
                >
                  {t('settings.notifications.attention.resume')}
                </Button>
              )}
            </li>
          );
        })}
      </ul>
    </div>
  );
}
