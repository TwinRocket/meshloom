import { useMemo } from 'react';
import { useTranslation } from 'react-i18next';
import { cn } from '@/lib/utils';
import { RepeaterPane, NotFetched, KvRow, formatClockDrift } from './repeaterPaneShared';
import type { RepeaterNodeInfoResponse, PaneState } from '../../types';

export function NodeInfoPane({
  data,
  state,
  onRefresh,
  disabled,
}: {
  data: RepeaterNodeInfoResponse | null;
  state: PaneState;
  onRefresh: () => void;
  disabled?: boolean;
}) {
  const { t } = useTranslation();
  const clockUtc = data?.clock_utc;
  const fetchedAt = state.fetched_at;
  const clockDrift = useMemo(() => {
    if (!clockUtc) return null;
    return formatClockDrift(clockUtc, fetchedAt ?? undefined);
  }, [clockUtc, fetchedAt]);

  return (
    <RepeaterPane
      title={t('repeater.nodeInfo')}
      state={state}
      onRefresh={onRefresh}
      disabled={disabled}
    >
      {!data ? (
        <NotFetched />
      ) : (
        <div>
          <KvRow label={t('repeater.name')} value={data.name ?? '—'} />
          <KvRow
            label={t('repeater.latLon')}
            value={
              data.lat != null || data.lon != null ? `${data.lat ?? '—'}, ${data.lon ?? '—'}` : '—'
            }
          />
          <div className="flex justify-between text-sm py-0.5">
            <span className="text-muted-foreground">{t('repeater.clockUtc')}</span>
            <span>
              {data.clock_utc ?? '—'}
              {clockDrift && (
                <span
                  className={cn(
                    'ml-2 text-xs',
                    clockDrift.isLarge ? 'text-destructive' : 'text-muted-foreground'
                  )}
                >
                  {t('repeater.clockDrift', { text: clockDrift.text })}
                </span>
              )}
            </span>
          </div>
        </div>
      )}
    </RepeaterPane>
  );
}
