import { useCallback, useEffect, useMemo, useState } from 'react';
import { Trans, useTranslation } from 'react-i18next';
import { Activity } from 'lucide-react';

import { api, isAbortError } from '../api';
import { useDistanceUnit } from '../contexts/DistanceUnitContext';
import { formatTime } from '../utils/messageParser';
import type { Contact, LppSensor, TelemetryHistoryEntry } from '../types';
import { Button } from './ui/button';
import { toast } from './ui/sonner';
import { LppSensorRow, formatLppLabel } from './repeater/repeaterPaneShared';
import { MAX_TRACKED, TelemetryHistoryChart } from './repeater/RepeaterTelemetryHistoryPane';

interface SensorTelemetryPanelProps {
  contact: Contact;
  contacts: Contact[];
  trackedTelemetryContacts: string[];
  onToggleTrackedTelemetryContact?: (publicKey: string) => Promise<void>;
}

export function SensorTelemetryPanel({
  contact,
  contacts,
  trackedTelemetryContacts,
  onToggleTrackedTelemetryContact,
}: SensorTelemetryPanelProps) {
  const { t } = useTranslation();
  const { distanceUnit } = useDistanceUnit();
  const [history, setHistory] = useState<TelemetryHistoryEntry[]>([]);
  const [sensors, setSensors] = useState<LppSensor[]>([]);
  const [fetchedAt, setFetchedAt] = useState<number | null>(null);
  const [loading, setLoading] = useState(false);
  const [toggling, setToggling] = useState(false);

  const publicKey = contact.public_key;
  const isTracked = trackedTelemetryContacts.includes(publicKey);
  const slotsFull = trackedTelemetryContacts.length >= MAX_TRACKED && !isTracked;

  useEffect(() => {
    let cancelled = false;
    api
      .contactTelemetryHistory(publicKey)
      .then((data) => {
        if (cancelled) return;
        setHistory(data);
        const latest = data.length > 0 ? data[data.length - 1] : null;
        if (latest?.data?.lpp_sensors) {
          setSensors(latest.data.lpp_sensors as LppSensor[]);
          setFetchedAt(latest.timestamp);
        } else {
          setSensors([]);
          setFetchedAt(null);
        }
      })
      .catch(() => {
        if (!cancelled) {
          setHistory([]);
          setSensors([]);
          setFetchedAt(null);
        }
      });
    return () => {
      cancelled = true;
    };
  }, [publicKey]);

  const handleRequest = useCallback(async () => {
    setLoading(true);
    try {
      const result = await api.requestContactTelemetry(publicKey);
      setSensors(result.sensors);
      setFetchedAt(result.fetched_at);
      setHistory(result.telemetry_history);
    } catch (err) {
      if (!isAbortError(err)) {
        toast.error(err instanceof Error ? err.message : t('contactInfo.telemetryFailed'));
      }
    } finally {
      setLoading(false);
    }
  }, [publicKey, t]);

  const handleToggle = useCallback(async () => {
    if (!onToggleTrackedTelemetryContact) return;
    setToggling(true);
    try {
      await onToggleTrackedTelemetryContact(publicKey);
    } finally {
      setToggling(false);
    }
  }, [onToggleTrackedTelemetryContact, publicKey]);

  const labels = useMemo(() => {
    const counts = new Map<string, number>();
    return sensors.map((s) => {
      const base = `${s.type_name}_${s.channel}`;
      const n = (counts.get(base) ?? 0) + 1;
      counts.set(base, n);
      return n > 1
        ? t('repeater.lppChannelN', {
            label: formatLppLabel(s.type_name),
            channel: s.channel,
            n,
          })
        : t('repeater.lppChannel', {
            label: formatLppLabel(s.type_name),
            channel: s.channel,
          });
    });
  }, [sensors, t]);

  const trackedNames = slotsFull
    ? trackedTelemetryContacts.map((key) => {
        const tracked = contacts.find((c) => c.public_key === key);
        return tracked?.name ?? key.slice(0, 12);
      })
    : [];

  const csvName = contact.name ?? publicKey.slice(0, 12);

  return (
    <section
      data-testid="sensor-telemetry-panel"
      className="border-b border-border bg-muted/20 px-4 py-3"
    >
      <div className="space-y-3">
        <div className="flex items-center justify-between gap-2">
          <h3 className="text-sm font-medium">{t('sensor.title')}</h3>
          <Button
            type="button"
            variant="outline"
            size="sm"
            onClick={handleRequest}
            disabled={loading}
          >
            <Activity className="h-3.5 w-3.5 mr-1.5" aria-hidden="true" />
            {loading ? t('contactInfo.fetching') : t('contactInfo.request')}
          </Button>
        </div>

        {sensors.length === 0 ? (
          <p className="text-sm text-muted-foreground italic">
            {fetchedAt ? t('contactInfo.noSensorData') : t('contactInfo.notFetched')}
          </p>
        ) : (
          <div className="space-y-0.5">
            {sensors.map((sensor, i) => (
              <LppSensorRow
                key={`${sensor.type_name}-${sensor.channel}-${i}`}
                sensor={sensor}
                unitPref={distanceUnit}
                label={labels[i]}
              />
            ))}
            {fetchedAt ? (
              <p className="text-[0.6875rem] text-muted-foreground mt-1.5">
                {t('contactInfo.fetchedAt', { time: formatTime(fetchedAt) })}
              </p>
            ) : null}
          </div>
        )}

        {onToggleTrackedTelemetryContact ? (
          isTracked ? (
            <Button
              variant="outline"
              onClick={handleToggle}
              disabled={toggling}
              className="border-destructive/50 text-destructive hover:bg-destructive/10"
            >
              {toggling ? t('contactInfo.updating') : t('contactInfo.stopTracking')}
            </Button>
          ) : slotsFull ? (
            <div className="space-y-2">
              <Button variant="outline" disabled>
                {t('sensor.trackingFull', {
                  used: trackedTelemetryContacts.length,
                  max: MAX_TRACKED,
                })}
              </Button>
              <p className="text-xs text-muted-foreground">
                {t('sensor.trackingFullHelp', { names: trackedNames.join(', ') })}
              </p>
            </div>
          ) : (
            <Button
              variant="outline"
              onClick={handleToggle}
              disabled={toggling}
              className="border-green-600/50 text-green-600 hover:bg-green-600/10"
            >
              {toggling ? t('contactInfo.updating') : t('contactInfo.startTracking')}
            </Button>
          )
        ) : null}

        <TelemetryHistoryChart
          entries={history}
          publicKey={publicKey}
          csvName={csvName}
          title={t('contactInfo.historySamples', { count: history.length })}
          includeBuiltin={false}
          emptyLabel={t('sensor.noHistory')}
          toolbar={
            <p className="text-xs text-muted-foreground leading-relaxed">
              <Trans
                i18nKey="sensor.historyHelp"
                values={{
                  endpoint: 'POST /api/contacts/<key>/telemetry',
                  max: MAX_TRACKED,
                }}
                components={{
                  settings: (
                    <a
                      href="#settings/radio-app"
                      className="underline text-primary hover:text-primary/80 transition-colors"
                    />
                  ),
                }}
              />
            </p>
          }
        />
      </div>
    </section>
  );
}
