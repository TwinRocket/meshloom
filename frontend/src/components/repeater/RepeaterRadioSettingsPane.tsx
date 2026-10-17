import { useTranslation } from 'react-i18next';
import { cn } from '@/lib/utils';
import { Separator } from '../ui/separator';
import {
  RepeaterPane,
  RefreshIcon,
  NotFetched,
  KvRow,
  formatAdvertInterval,
} from './repeaterPaneShared';
import type {
  RepeaterRadioSettingsResponse,
  RepeaterAdvertIntervalsResponse,
  PaneState,
} from '../../types';

function formatRadioTuple(radio: string | null): { display: string; raw: string | null } {
  if (radio == null) {
    return { display: '—', raw: null };
  }

  const trimmed = radio.trim();
  const parts = trimmed.split(',').map((part) => part.trim());
  if (parts.length !== 4) {
    return { display: trimmed || '—', raw: trimmed || null };
  }

  const [freqRaw, bwRaw, sfRaw, crRaw] = parts;
  const freq = Number.parseFloat(freqRaw);
  const bw = Number.parseFloat(bwRaw);
  const sf = Number.parseInt(sfRaw, 10);
  const cr = Number.parseInt(crRaw, 10);

  if (![freq, bw, sf, cr].every(Number.isFinite)) {
    return { display: trimmed || '—', raw: trimmed || null };
  }

  const formattedFreq = Number(freq.toFixed(3)).toString();
  const formattedBw = Number(bw.toFixed(3)).toString();
  return {
    display: `${formattedFreq} MHz, BW ${formattedBw} kHz, SF${sf}, CR${cr}`,
    raw: trimmed,
  };
}

export function RadioSettingsPane({
  data,
  state,
  onRefresh,
  disabled,
  advertData,
  advertState,
  onRefreshAdvert,
}: {
  data: RepeaterRadioSettingsResponse | null;
  state: PaneState;
  onRefresh: () => void;
  disabled?: boolean;
  advertData: RepeaterAdvertIntervalsResponse | null;
  advertState: PaneState;
  onRefreshAdvert: () => void;
}) {
  const { t } = useTranslation();
  const formattedRadio = formatRadioTuple(data?.radio ?? null);

  return (
    <RepeaterPane
      title={t('repeater.radioSettings')}
      state={state}
      onRefresh={onRefresh}
      disabled={disabled}
    >
      {!data ? (
        <NotFetched />
      ) : (
        <div>
          <KvRow label={t('repeater.firmware')} value={data.firmware_version ?? '—'} />
          <KvRow
            label={t('repeater.radio')}
            value={<span title={formattedRadio.raw ?? undefined}>{formattedRadio.display}</span>}
          />
          <KvRow
            label={t('repeater.txPower')}
            value={
              data.tx_power != null ? t('repeater.txPowerValue', { value: data.tx_power }) : '—'
            }
          />
          <KvRow label={t('repeater.airtimeFactor')} value={data.airtime_factor ?? '—'} />
          {/* Duty cycle limit is firmware >= 1.15 only; omit the row entirely on
              older nodes rather than showing an empty placeholder. */}
          {data.duty_cycle_limit != null && (
            <KvRow label={t('repeater.dutyCycleLimit')} value={data.duty_cycle_limit} />
          )}
          <KvRow label={t('repeater.repeatMode')} value={data.repeat_enabled ?? '—'} />
          <KvRow label={t('repeater.maxFloodHops')} value={data.flood_max ?? '—'} />
        </div>
      )}
      {/* Advert Intervals sub-section */}
      <Separator className="my-2" />
      <div className="flex items-center justify-between mb-1">
        <span className="text-xs font-medium text-muted-foreground">
          {t('repeater.advertIntervals')}
        </span>
        <button
          type="button"
          onClick={onRefreshAdvert}
          disabled={disabled || advertState.loading}
          className={cn(
            'p-1 rounded transition-colors disabled:opacity-50',
            disabled || advertState.loading
              ? 'text-muted-foreground'
              : 'text-success hover:bg-accent hover:text-success'
          )}
          title={t('repeater.refreshAdvertIntervals')}
          aria-label={t('repeater.refreshAdvertIntervals')}
        >
          <RefreshIcon
            className={cn('w-3 h-3', advertState.loading && 'animate-spin direction-reverse')}
          />
        </button>
      </div>
      {advertState.error && <p className="text-xs text-destructive mb-1">{advertState.error}</p>}
      {advertState.loading ? (
        <p className="text-sm text-muted-foreground italic">
          {advertState.attempt > 1
            ? t('repeater.fetchingAttempt', { attempt: advertState.attempt, max: 3 })
            : t('repeater.fetching')}
        </p>
      ) : !advertData ? (
        <NotFetched />
      ) : (
        <div>
          <KvRow
            label={t('repeater.localAdvert')}
            value={formatAdvertInterval(advertData.advert_interval, 'minutes')}
          />
          <KvRow
            label={t('repeater.floodAdvert')}
            value={formatAdvertInterval(advertData.flood_advert_interval)}
          />
        </div>
      )}
    </RepeaterPane>
  );
}
