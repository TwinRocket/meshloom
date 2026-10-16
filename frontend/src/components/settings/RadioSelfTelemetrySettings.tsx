import { type ReactNode, useCallback, useEffect, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { api, formatApiError } from '../../api';
import { useDistanceUnit } from '../../contexts/DistanceUnitContext';
import type { RadioSelfTelemetry } from '../../types';
import { formatNumber } from '../../utils/formatNumber';
import { KvRow, LppSensorRow, formatLppLabel } from '../repeater/repeaterPaneShared';
import { Button } from '../ui/button';
import { SettingsGroup, SettingsGroupHeader } from './settingsPrimitives';

/** The radio's own sensors (battery, MCU temperature, GPS), read from the radio on demand. */
export function RadioSelfTelemetrySettings() {
  const { t } = useTranslation();
  const { distanceUnit } = useDistanceUnit();
  const [data, setData] = useState<RadioSelfTelemetry | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      setData(await api.getRadioSelfTelemetry());
    } catch (err) {
      setError(formatApiError(err, t) || t('settings.radio.selfTelemetry.loadFailed'));
    } finally {
      setLoading(false);
    }
  }, [t]);

  useEffect(() => {
    void load();
  }, [load]);

  const hasStorage =
    data !== null &&
    data.storage_used_kb !== null &&
    data.storage_total_kb !== null &&
    data.storage_total_kb > 0;
  const hasBattery = data !== null && data.battery_mv !== null;
  const sensors = data?.sensors ?? [];
  const isEmpty = data !== null && sensors.length === 0 && !hasBattery && !hasStorage;

  let body: ReactNode;
  if (data === null) {
    body = loading ? (
      <p className="text-[0.8125rem] text-muted-foreground" role="status">
        {t('settings.radio.selfTelemetry.loading')}
      </p>
    ) : null;
  } else if (isEmpty) {
    body = (
      <p className="max-w-prose text-[0.8125rem] text-muted-foreground">
        {t('settings.radio.selfTelemetry.empty')}
      </p>
    );
  } else {
    body = (
      <div className="space-y-0.5 rounded-md border border-border/60 p-3">
        {hasBattery && (
          <KvRow
            label={t('settings.radio.selfTelemetry.battery')}
            value={t('settings.radio.selfTelemetry.batteryValue', {
              value: ((data.battery_mv as number) / 1000).toFixed(2),
            })}
          />
        )}
        {hasStorage && (
          <KvRow
            label={t('settings.radio.selfTelemetry.storage')}
            value={t('settings.radio.selfTelemetry.storageValue', {
              used: formatNumber(data.storage_used_kb as number),
              total: formatNumber(data.storage_total_kb as number),
              percent: Math.round(
                ((data.storage_used_kb as number) / (data.storage_total_kb as number)) * 100
              ),
            })}
          />
        )}
        {sensors.map((sensor, i) => (
          <LppSensorRow
            key={`${sensor.type_name}-${sensor.channel}-${i}`}
            sensor={sensor}
            unitPref={distanceUnit}
            label={t('repeater.lppChannel', {
              label: formatLppLabel(sensor.type_name),
              channel: sensor.channel,
            })}
          />
        ))}
      </div>
    );
  }

  return (
    <SettingsGroup id="radio-self-telemetry">
      <SettingsGroupHeader
        id="radio-self-telemetry"
        title={t('settings.radio.selfTelemetry.title')}
        storedOn="radio"
      />
      <p className="max-w-prose text-[0.8125rem] text-muted-foreground">
        {t('settings.radio.selfTelemetry.intro')}
      </p>
      {body}
      {error && (
        <div className="text-sm text-destructive" role="alert">
          {error}
        </div>
      )}
      <Button type="button" variant="outline" disabled={loading} onClick={() => void load()}>
        {loading
          ? t('settings.radio.selfTelemetry.refreshing')
          : t('settings.radio.selfTelemetry.refresh')}
      </Button>
    </SettingsGroup>
  );
}
