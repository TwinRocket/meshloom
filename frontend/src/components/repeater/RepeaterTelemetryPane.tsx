import type { ReactNode } from 'react';
import { useTranslation } from 'react-i18next';
import { Separator } from '../ui/separator';
import { RepeaterPane, NotFetched, KvRow, formatDuration } from './repeaterPaneShared';
import type { RepeaterStatusResponse, PaneState } from '../../types';
import { formatNumber } from '../../utils/formatNumber';

function Secondary({ children }: { children: ReactNode }) {
  return <span className="ml-1.5 font-normal text-muted-foreground">{children}</span>;
}

function formatAirtimePercent(airtimeSec: number, uptimeSec: number): string | null {
  if (uptimeSec <= 0) return null;
  return `${((airtimeSec / uptimeSec) * 100).toFixed(2)}%`;
}

function formatPerMinute(count: number, uptimeSec: number): string | null {
  if (uptimeSec <= 0) return null;
  const rate = (count * 60) / uptimeSec;
  return rate >= 10 ? rate.toFixed(0) : rate.toFixed(1);
}

export function TelemetryPane({
  data,
  state,
  onRefresh,
  disabled,
}: {
  data: RepeaterStatusResponse | null;
  state: PaneState;
  onRefresh: () => void;
  disabled?: boolean;
}) {
  const { t } = useTranslation();
  const txPct = data ? formatAirtimePercent(data.airtime_seconds, data.uptime_seconds) : null;
  const rxPct = data ? formatAirtimePercent(data.rx_airtime_seconds, data.uptime_seconds) : null;
  const rxPerMin = data ? formatPerMinute(data.packets_received, data.uptime_seconds) : null;
  const txPerMin = data ? formatPerMinute(data.packets_sent, data.uptime_seconds) : null;

  return (
    <RepeaterPane
      title={t('repeater.telemetry')}
      state={state}
      onRefresh={onRefresh}
      disabled={disabled}
    >
      {!data ? (
        <NotFetched />
      ) : (
        <div className="space-y-2">
          <KvRow label={t('repeater.battery')} value={`${data.battery_volts.toFixed(3)}V`} />
          <KvRow label={t('repeater.uptime')} value={formatDuration(data.uptime_seconds)} />
          <KvRow
            label={t('repeater.txAirtime')}
            value={
              <>
                {formatDuration(data.airtime_seconds)}
                {txPct && <Secondary>({txPct})</Secondary>}
              </>
            }
          />
          <KvRow
            label={t('repeater.rxAirtime')}
            value={
              <>
                {formatDuration(data.rx_airtime_seconds)}
                {rxPct && <Secondary>({rxPct})</Secondary>}
              </>
            }
          />
          <Separator className="my-1" />
          <KvRow label={t('repeater.noiseFloor')} value={`${data.noise_floor_dbm} dBm`} />
          <KvRow label={t('repeater.lastRssi')} value={`${data.last_rssi_dbm} dBm`} />
          <KvRow label={t('repeater.lastSnr')} value={`${data.last_snr_db.toFixed(1)} dB`} />
          <Separator className="my-1" />
          <KvRow
            label={t('repeater.packets')}
            value={
              <>
                {t('repeater.packetsRxTx', {
                  rx: formatNumber(data.packets_received),
                  tx: formatNumber(data.packets_sent),
                })}
                {rxPerMin && txPerMin && (
                  <Secondary>{t('repeater.packetsAvg', { rx: rxPerMin, tx: txPerMin })}</Secondary>
                )}
              </>
            }
          />
          <KvRow
            label={t('repeater.flood')}
            value={t('repeater.packetsRxTx', {
              rx: formatNumber(data.recv_flood),
              tx: formatNumber(data.sent_flood),
            })}
          />
          <KvRow
            label={t('repeater.direct')}
            value={t('repeater.packetsRxTx', {
              rx: formatNumber(data.recv_direct),
              tx: formatNumber(data.sent_direct),
            })}
          />
          <KvRow
            label={t('repeater.duplicates')}
            value={t('repeater.duplicatesValue', {
              flood: formatNumber(data.flood_dups),
              direct: formatNumber(data.direct_dups),
            })}
          />
          {data.recv_errors != null && (
            <KvRow
              label={t('repeater.rxErrors')}
              value={
                <>
                  {formatNumber(data.recv_errors)}
                  {data.packets_received > 0 && (
                    <Secondary>
                      (
                      {(
                        (data.recv_errors / (data.packets_received + data.recv_errors)) *
                        100
                      ).toFixed(2)}
                      %)
                    </Secondary>
                  )}
                </>
              }
            />
          )}
          <Separator className="my-1" />
          <KvRow label={t('repeater.txQueue')} value={data.tx_queue_len} />
          <KvRow label={t('repeater.debugFlags')} value={data.full_events} />
        </div>
      )}
    </RepeaterPane>
  );
}
