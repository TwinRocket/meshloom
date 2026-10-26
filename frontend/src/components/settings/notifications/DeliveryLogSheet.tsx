import { useCallback, useEffect, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { RefreshCw } from 'lucide-react';

import { api, formatApiError } from '../../../api';
import type { NotificationChannel, NotificationDeliveries } from '../../../notifications/types';
import { Button } from '../../ui/button';
import { Sheet, SheetContent, SheetDescription, SheetHeader, SheetTitle } from '../../ui/sheet';
import { DELIVERY_STATUS_CLASS, sentLast24h } from './deliveryStats';

const LOG_ROWS = 50;

/**
 * The last 50 journal rows of one channel (time, event, status badge, the scrubbed error code
 * the server stores: never a message text) and the sends of the last 24 hours.
 */
export function DeliveryLogSheet({
  channel,
  onClose,
}: {
  channel: NotificationChannel;
  onClose: () => void;
}) {
  const { t, i18n } = useTranslation();
  const [data, setData] = useState<NotificationDeliveries | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      setData(await api.getNotificationDeliveries({ channel: channel.id, limit: LOG_ROWS }));
      setError(null);
    } catch (err) {
      setError(formatApiError(err, t));
    } finally {
      setLoading(false);
    }
  }, [channel.id, t]);

  useEffect(() => {
    // eslint-disable-next-line react-hooks/set-state-in-effect -- initial fetch
    void load();
  }, [load]);

  const time = (epoch: number) =>
    new Intl.DateTimeFormat(i18n.language, { dateStyle: 'short', timeStyle: 'medium' }).format(
      new Date(epoch * 1000)
    );

  return (
    <Sheet open onOpenChange={(open) => !open && onClose()}>
      <SheetContent side="right" className="w-full overflow-y-auto sm:max-w-md">
        <SheetHeader>
          <SheetTitle>{t('settings.notifications.log.title', { name: channel.name })}</SheetTitle>
          <SheetDescription>{t('settings.notifications.log.help')}</SheetDescription>
        </SheetHeader>
        <div className="mt-4 space-y-3" data-testid="delivery-log">
          <div className="flex items-center justify-between gap-2">
            <p className="text-sm" data-testid="delivery-log-sent">
              {data
                ? t('settings.notifications.log.sent24h', { count: sentLast24h(data.counters) })
                : ''}
            </p>
            <Button variant="outline" size="sm" disabled={loading} onClick={() => void load()}>
              <RefreshCw className="mr-1 h-4 w-4" aria-hidden="true" />
              {t('settings.notifications.log.refresh')}
            </Button>
          </div>
          {error && (
            <p role="alert" className="text-sm text-destructive">
              {error}
            </p>
          )}
          {data && data.deliveries.length === 0 && !error && (
            <p className="text-[0.8125rem] text-muted-foreground">
              {t('settings.notifications.log.empty')}
            </p>
          )}
          <ul className="space-y-2">
            {data?.deliveries.map((row) => (
              <li
                key={row.id}
                className="rounded-md border border-border/60 p-2 text-sm"
                data-testid={`delivery-row-${row.id}`}
              >
                <div className="flex flex-wrap items-center justify-between gap-2">
                  <span className="break-all font-medium">
                    {t(`settings.notifications.log.events.${row.event}`, {
                      defaultValue: row.event,
                    })}
                  </span>
                  <span
                    className={`rounded px-1.5 py-0.5 text-[0.625rem] font-medium uppercase tracking-wider ${
                      DELIVERY_STATUS_CLASS[row.status] ?? 'bg-muted text-muted-foreground'
                    }`}
                    data-testid={`delivery-status-${row.id}`}
                  >
                    {t(`settings.notifications.log.status.${row.status}`, {
                      defaultValue: row.status,
                    })}
                  </span>
                </div>
                <p className="text-xs text-muted-foreground">{time(row.created_at)}</p>
                {row.error && (
                  <p className="break-words text-xs text-destructive">
                    {t(`errors.${row.error}`, { defaultValue: row.error })}
                  </p>
                )}
              </li>
            ))}
          </ul>
        </div>
      </SheetContent>
    </Sheet>
  );
}
