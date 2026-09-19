import { useState, useEffect, useRef } from 'react';
import { Trans, useTranslation } from 'react-i18next';
import { Label } from '../ui/label';
import { Button } from '../ui/button';
import { Input } from '../ui/input';
import { Separator } from '../ui/separator';
import { toast } from '../ui/sonner';
import { api } from '../../api';
import { formatTime } from '../../utils/messageParser';
import { lppDisplayUnit } from '../repeater/repeaterPaneShared';
import { useDistanceUnit } from '../../contexts/DistanceUnitContext';
import { BulkDeleteContactsModal } from './BulkDeleteContactsModal';
import { ContactGroupsEditor } from './ContactGroupsEditor';
import { SettingsGroupHeader } from './settingsPrimitives';
import {
  type AppSettings,
  type AppSettingsUpdate,
  type Contact,
  type TelemetryHistoryEntry,
  type TelemetrySchedule,
} from '../../types';

export function SettingsRadioAppSection({
  appSettings,
  onSaveAppSettings,
  blockedKeys = [],
  blockedNames = [],
  onToggleBlockedKey,
  onToggleBlockedName,
  contacts = [],
  onBulkDeleteContacts,
  trackedTelemetryRepeaters = [],
  onToggleTrackedTelemetry,
  trackedTelemetryContacts = [],
  onToggleTrackedTelemetryContact,
  className,
}: {
  appSettings: AppSettings;
  onSaveAppSettings: (update: AppSettingsUpdate) => Promise<void>;
  blockedKeys?: string[];
  blockedNames?: string[];
  onToggleBlockedKey?: (key: string) => void;
  onToggleBlockedName?: (name: string) => void;
  contacts?: Contact[];
  onBulkDeleteContacts?: (deletedKeys: string[]) => void;
  trackedTelemetryRepeaters?: string[];
  onToggleTrackedTelemetry?: (publicKey: string) => Promise<void>;
  trackedTelemetryContacts?: string[];
  onToggleTrackedTelemetryContact?: (publicKey: string) => Promise<void>;
  className?: string;
}) {
  const { t } = useTranslation();
  const { distanceUnit } = useDistanceUnit();
  const [discoveryBlockedTypes, setDiscoveryBlockedTypes] = useState<number[]>([]);
  const [bulkDeleteOpen, setBulkDeleteOpen] = useState(false);
  const [staleDays, setStaleDays] = useState(String(appSettings.stale_contact_days ?? 0));
  const [communityOn, setCommunityOn] = useState(false);
  const [cacheResetting, setCacheResetting] = useState(false);

  const [latestTelemetry, setLatestTelemetry] = useState<
    Record<string, TelemetryHistoryEntry | null>
  >({});
  const telemetryFetchedRef = useRef(false);

  const [latestContactTelemetry, setLatestContactTelemetry] = useState<
    Record<string, TelemetryHistoryEntry | null>
  >({});
  const contactTelemetryFetchedRef = useRef(false);

  const [schedule, setSchedule] = useState<TelemetrySchedule | null>(null);
  const [intervalDraft, setIntervalDraft] = useState<number>(appSettings.telemetry_interval_hours);

  const saveChainRef = useRef<Promise<void>>(Promise.resolve());
  const directoryViaStats = communityOn || Boolean(appSettings.directory_available);

  useEffect(() => {
    setDiscoveryBlockedTypes(appSettings.discovery_blocked_types ?? []);
    setIntervalDraft(appSettings.telemetry_interval_hours);
    setStaleDays(String(appSettings.stale_contact_days ?? 0));
  }, [appSettings]);

  useEffect(() => {
    let cancelled = false;
    void api.getCommunity().then(
      (status) => {
        if (!cancelled) setCommunityOn(status.enabled);
      },
      () => {
        if (!cancelled) setCommunityOn(false);
      }
    );
    return () => {
      cancelled = true;
    };
  }, []);

  useEffect(() => {
    let cancelled = false;
    api
      .getTelemetrySchedule()
      .then((s) => {
        if (!cancelled) setSchedule(s);
      })
      .catch(() => {});
    return () => {
      cancelled = true;
    };
  }, [
    trackedTelemetryRepeaters.length,
    trackedTelemetryContacts.length,
    appSettings.telemetry_interval_hours,
    appSettings.telemetry_routed_hourly,
  ]);

  useEffect(() => {
    if (trackedTelemetryRepeaters.length === 0 || telemetryFetchedRef.current) return;
    telemetryFetchedRef.current = true;
    let cancelled = false;
    const fetches = trackedTelemetryRepeaters.map((key) =>
      api.repeaterTelemetryHistory(key).then(
        (history) => [key, history.length > 0 ? history[history.length - 1] : null] as const,
        () => [key, null] as const
      )
    );
    Promise.all(fetches).then((entries) => {
      if (cancelled) return;
      setLatestTelemetry(Object.fromEntries(entries));
    });
    return () => {
      cancelled = true;
    };
  }, [trackedTelemetryRepeaters]);

  useEffect(() => {
    if (trackedTelemetryContacts.length === 0 || contactTelemetryFetchedRef.current) return;
    contactTelemetryFetchedRef.current = true;
    let cancelled = false;
    const fetches = trackedTelemetryContacts.map((key) =>
      api.contactTelemetryHistory(key).then(
        (history) => [key, history.length > 0 ? history[history.length - 1] : null] as const,
        () => [key, null] as const
      )
    );
    Promise.all(fetches).then((entries) => {
      if (cancelled) return;
      setLatestContactTelemetry(Object.fromEntries(entries));
    });
    return () => {
      cancelled = true;
    };
  }, [trackedTelemetryContacts]);

  const persistAppSettings = (update: AppSettingsUpdate, revert: () => void): Promise<void> => {
    const chained = saveChainRef.current.then(async () => {
      try {
        await onSaveAppSettings(update);
      } catch (err) {
        console.error('Failed to save radio-app settings:', err);
        revert();
        toast.error(t('settings.radioApp.saveFailed'), {
          description: err instanceof Error ? err.message : t('settings.radioApp.unknownError'),
        });
      }
    });
    saveChainRef.current = chained;
    return chained;
  };

  return (
    <div className={className}>
      {/* ── Tracked Repeater Telemetry ── */}
      <div className="space-y-3">
        <SettingsGroupHeader
          title={t('settings.radioApp.trackedRepeaters')}
          storedOn="server"
          instant
        />
        <p className="text-[0.8125rem] text-muted-foreground">
          {t('settings.radioApp.trackedRepeatersHelp', {
            max: schedule?.max_tracked ?? 8,
            used: trackedTelemetryRepeaters.length,
          })}
        </p>

        <div className="space-y-1.5">
          <Label htmlFor="telemetry-interval" className="text-sm">
            {t('settings.radioApp.collectionInterval')}
          </Label>
          <div className="flex items-center gap-2">
            <select
              id="telemetry-interval"
              value={intervalDraft}
              onChange={(e) => {
                const nextValue = Number(e.target.value);
                if (!Number.isFinite(nextValue) || nextValue === intervalDraft) return;
                const prevValue = intervalDraft;
                setIntervalDraft(nextValue);
                void persistAppSettings({ telemetry_interval_hours: nextValue }, () =>
                  setIntervalDraft(prevValue)
                );
              }}
              className="h-9 px-3 rounded-md border border-input bg-background text-sm ring-offset-background focus:outline-none focus:ring-2 focus:ring-ring focus:ring-offset-2"
            >
              {(schedule?.options ?? [1, 2, 3, 4, 6, 8, 12, 24]).map((hrs) => (
                <option key={hrs} value={hrs}>
                  {t('settings.radioApp.everyHour', {
                    count: hrs,
                    checks: Math.floor(24 / hrs),
                  })}
                </option>
              ))}
            </select>
          </div>
          {schedule && schedule.effective_hours !== schedule.preferred_hours && (
            <p className="text-xs text-warning">
              {t('settings.radioApp.intervalClamped', {
                count: schedule.tracked_count,
                preferred: schedule.preferred_hours,
                effective: schedule.effective_hours,
                tracked: schedule.tracked_count,
              })}
            </p>
          )}
        </div>

        <div className="space-y-3">
          <h4 className="text-sm font-semibold">{t('settings.radioApp.alertRules')}</h4>
          <p className="text-[0.8125rem] text-muted-foreground">
            <Trans
              i18nKey="settings.radioApp.alertsMoved"
              components={{
                alerts: (
                  <a
                    href="#settings/alerts"
                    className="underline text-primary hover:text-primary/80 transition-colors"
                  />
                ),
              }}
            />
          </p>
        </div>

        <label className="flex items-start gap-2 cursor-pointer">
          <input
            type="checkbox"
            checked={appSettings.telemetry_routed_hourly}
            onChange={() => {
              const next = !appSettings.telemetry_routed_hourly;
              void persistAppSettings({ telemetry_routed_hourly: next }, () => {});
            }}
            className="w-4 h-4 rounded border-input accent-primary mt-0.5"
          />
          <div>
            <span className="text-sm">{t('settings.radioApp.pollRoutedHourly')}</span>
            <p className="text-[0.8125rem] text-muted-foreground">
              {t('settings.radioApp.pollRoutedHourlyHelp')}
            </p>
          </div>
        </label>

        {schedule?.next_run_at != null && (
          <p className="text-xs text-muted-foreground">
            {schedule.routed_hourly
              ? t('settings.radioApp.nextFloodRun', { time: formatTime(schedule.next_run_at) })
              : t('settings.radioApp.nextRun', { time: formatTime(schedule.next_run_at) })}
          </p>
        )}
        {schedule?.next_routed_run_at != null && (
          <p className="text-xs text-muted-foreground">
            {t('settings.radioApp.nextRoutedRun', {
              time: formatTime(schedule.next_routed_run_at),
            })}
          </p>
        )}

        {trackedTelemetryRepeaters.length === 0 ? (
          <p className="text-sm text-muted-foreground italic">
            {t('settings.radioApp.noRepeatersTracked')}
          </p>
        ) : (
          <div className="space-y-2">
            {trackedTelemetryRepeaters.map((key) => {
              const contact = contacts.find((c) => c.public_key === key);
              const displayName = contact?.name ?? key.slice(0, 12);
              const routeSource = contact?.effective_route_source ?? 'flood';
              const hasRealPath =
                contact?.effective_route != null && contact.effective_route.path_len >= 0;
              const routeLabel = !hasRealPath
                ? t('settings.radioApp.routeFlood')
                : routeSource === 'override'
                  ? t('settings.radioApp.routeRouted')
                  : routeSource === 'direct'
                    ? t('settings.radioApp.routeDirect')
                    : t('settings.radioApp.routeFlood');
              const routeColor = hasRealPath
                ? 'text-primary bg-primary/10'
                : 'text-muted-foreground bg-muted';
              const snap = latestTelemetry[key];
              const d = snap?.data;
              return (
                <div key={key} className="rounded-md border border-border px-3 py-2">
                  <div className="flex items-center justify-between gap-2">
                    <div className="flex-1 min-w-0">
                      <span className="text-sm truncate block">{displayName}</span>
                      <div className="flex items-center gap-1.5">
                        <span className="text-[0.625rem] text-muted-foreground font-mono">
                          {key.slice(0, 12)}
                        </span>
                        <span
                          className={`text-[0.625rem] uppercase tracking-wider px-1.5 py-0.5 rounded font-medium ${routeColor}`}
                        >
                          {routeLabel}
                        </span>
                      </div>
                    </div>
                    {onToggleTrackedTelemetry && (
                      <Button
                        variant="ghost"
                        size="sm"
                        onClick={() => onToggleTrackedTelemetry(key)}
                        className="h-7 text-xs flex-shrink-0 text-destructive hover:text-destructive"
                      >
                        {t('settings.remove')}
                      </Button>
                    )}
                  </div>
                  {d ? (
                    <div className="mt-1.5 flex flex-wrap gap-x-3 gap-y-0.5 text-[0.625rem] text-muted-foreground">
                      <span>{d.battery_volts?.toFixed(2)}V</span>
                      <span>{t('settings.radioApp.noise', { value: d.noise_floor_dbm })}</span>
                      <span>
                        {t('settings.radioApp.rx', {
                          value:
                            d.packets_received != null ? d.packets_received.toLocaleString() : '?',
                        })}
                      </span>
                      <span>
                        {t('settings.radioApp.tx', {
                          value: d.packets_sent != null ? d.packets_sent.toLocaleString() : '?',
                        })}
                      </span>
                      {d.lpp_sensors?.map((s) => {
                        if (typeof s.value !== 'number') return null;
                        const display = lppDisplayUnit(s.type_name, s.value, distanceUnit);
                        const val =
                          typeof display.value === 'number'
                            ? display.value % 1 === 0
                              ? display.value
                              : display.value.toFixed(1)
                            : display.value;
                        const label = s.type_name.charAt(0).toUpperCase() + s.type_name.slice(1);
                        return (
                          <span key={`${s.type_name}-${s.channel}`}>
                            {label} {val}
                            {display.unit ? ` ${display.unit}` : ''}
                          </span>
                        );
                      })}
                      <span className="ml-auto">
                        {t('settings.radioApp.checked', { time: formatTime(snap.timestamp) })}
                      </span>
                    </div>
                  ) : snap === null ? (
                    <div className="mt-1 text-[0.625rem] text-muted-foreground italic">
                      {t('settings.radioApp.noTelemetryYet')}
                    </div>
                  ) : null}
                </div>
              );
            })}
          </div>
        )}
      </div>

      <Separator />

      {/* ── Tracked Contact Telemetry ── */}
      <div className="space-y-3">
        <SettingsGroupHeader
          title={t('settings.radioApp.trackedContacts')}
          storedOn="server"
          instant
        />
        <p className="text-[0.8125rem] text-muted-foreground">
          {t('settings.radioApp.trackedContactsHelp')}
        </p>

        {trackedTelemetryContacts.length === 0 ? (
          <p className="text-sm text-muted-foreground italic">
            {t('settings.radioApp.noContactsTracked')}
          </p>
        ) : (
          <div className="space-y-2">
            {trackedTelemetryContacts.map((key) => {
              const contact = contacts.find((c) => c.public_key === key);
              const displayName = contact?.name ?? key.slice(0, 12);
              const routeSource = contact?.effective_route_source ?? 'flood';
              const hasRealPath =
                contact?.effective_route != null && contact.effective_route.path_len >= 0;
              const routeLabel = !hasRealPath
                ? t('settings.radioApp.routeFlood')
                : routeSource === 'override'
                  ? t('settings.radioApp.routeRouted')
                  : routeSource === 'direct'
                    ? t('settings.radioApp.routeDirect')
                    : t('settings.radioApp.routeFlood');
              const routeColor = hasRealPath
                ? 'text-primary bg-primary/10'
                : 'text-muted-foreground bg-muted';
              const snap = latestContactTelemetry[key];
              const d = snap?.data;
              return (
                <div key={key} className="rounded-md border border-border px-3 py-2">
                  <div className="flex items-center justify-between gap-2">
                    <div className="flex-1 min-w-0">
                      <span className="text-sm truncate block">{displayName}</span>
                      <div className="flex items-center gap-1.5">
                        <span className="text-[0.625rem] text-muted-foreground font-mono">
                          {key.slice(0, 12)}
                        </span>
                        <span
                          className={`text-[0.625rem] uppercase tracking-wider px-1.5 py-0.5 rounded font-medium ${routeColor}`}
                        >
                          {routeLabel}
                        </span>
                      </div>
                    </div>
                    {onToggleTrackedTelemetryContact && (
                      <Button
                        variant="ghost"
                        size="sm"
                        onClick={() => onToggleTrackedTelemetryContact(key)}
                        className="h-7 text-xs flex-shrink-0 text-destructive hover:text-destructive"
                      >
                        {t('settings.remove')}
                      </Button>
                    )}
                  </div>
                  {d ? (
                    <div className="mt-1.5 flex flex-wrap gap-x-3 gap-y-0.5 text-[0.625rem] text-muted-foreground">
                      {d.lpp_sensors?.map((s) => {
                        if (typeof s.value !== 'number') return null;
                        const display = lppDisplayUnit(s.type_name, s.value, distanceUnit);
                        const val =
                          typeof display.value === 'number'
                            ? display.value % 1 === 0
                              ? display.value
                              : display.value.toFixed(1)
                            : display.value;
                        const label = s.type_name.charAt(0).toUpperCase() + s.type_name.slice(1);
                        return (
                          <span key={`${s.type_name}-${s.channel}`}>
                            {label} {val}
                            {display.unit ? ` ${display.unit}` : ''}
                          </span>
                        );
                      })}
                      <span className="ml-auto">
                        {t('settings.radioApp.checked', { time: formatTime(snap.timestamp) })}
                      </span>
                    </div>
                  ) : snap === null ? (
                    <div className="mt-1 text-[0.625rem] text-muted-foreground italic">
                      {t('settings.radioApp.noTelemetryYet')}
                    </div>
                  ) : null}
                </div>
              );
            })}
          </div>
        )}
      </div>

      <Separator />

      {/* ── Contact Management ── */}
      <div className="space-y-5">
        <SettingsGroupHeader
          title={t('settings.radioApp.contactManagement')}
          storedOn="server"
          instant
        />

        <ContactGroupsEditor contacts={contacts} />

        <div className="space-y-3">
          <h4 className="text-sm font-semibold">{t('settings.radioApp.blockDiscovery')}</h4>
          <p className="text-[0.8125rem] text-muted-foreground">
            {t('settings.radioApp.blockDiscoveryHelp')}
          </p>
          <div className="space-y-1.5">
            {(
              [
                [1, t('settings.radioApp.blockClients')],
                [2, t('settings.radioApp.blockRepeaters')],
                [3, t('settings.radioApp.blockRooms')],
                [4, t('settings.radioApp.blockSensors')],
              ] as const
            ).map(([typeCode, label]) => {
              const checked = discoveryBlockedTypes.includes(typeCode);
              return (
                <label key={typeCode} className="flex items-center gap-2 text-sm cursor-pointer">
                  <input
                    type="checkbox"
                    checked={checked}
                    onChange={() => {
                      const prev = discoveryBlockedTypes;
                      const next = checked
                        ? prev.filter((t) => t !== typeCode)
                        : [...prev, typeCode];
                      setDiscoveryBlockedTypes(next);
                      void persistAppSettings({ discovery_blocked_types: next }, () =>
                        setDiscoveryBlockedTypes(prev)
                      );
                    }}
                    className="rounded border-input"
                  />
                  {label}
                </label>
              );
            })}
          </div>
          {discoveryBlockedTypes.length > 0 && (
            <p className="text-xs text-warning">
              {t('settings.radioApp.blockedTypesWarn', {
                types: discoveryBlockedTypes
                  .map((typeCode) =>
                    typeCode === 1
                      ? t('settings.radioApp.typeClients')
                      : typeCode === 2
                        ? t('settings.radioApp.typeRepeaters')
                        : typeCode === 3
                          ? t('settings.radioApp.typeRooms')
                          : t('settings.radioApp.typeSensors')
                  )
                  .join(', '),
              })}
            </p>
          )}
        </div>

        <div className="space-y-3">
          <h4 className="text-sm font-semibold">{t('settings.radioApp.blockedContacts')}</h4>
          <p className="text-[0.8125rem] text-muted-foreground">
            {t('settings.radioApp.blockedContactsHelp')}
          </p>

          {blockedKeys.length === 0 && blockedNames.length === 0 ? (
            <p className="text-sm text-muted-foreground italic">
              {t('settings.radioApp.noBlocked')}
            </p>
          ) : (
            <div className="space-y-2">
              {blockedKeys.length > 0 && (
                <div>
                  <span className="text-xs text-muted-foreground font-medium">
                    {t('settings.radioApp.blockedKeys')}
                  </span>
                  <div className="mt-1 space-y-1">
                    {blockedKeys.map((key) => (
                      <div key={key} className="flex items-center justify-between gap-2">
                        <span className="text-xs font-mono truncate flex-1">{key}</span>
                        {onToggleBlockedKey && (
                          <Button
                            variant="ghost"
                            size="sm"
                            onClick={() => onToggleBlockedKey(key)}
                            className="h-7 text-xs flex-shrink-0"
                          >
                            {t('settings.radioApp.unblock')}
                          </Button>
                        )}
                      </div>
                    ))}
                  </div>
                </div>
              )}
              {blockedNames.length > 0 && (
                <div>
                  <span className="text-xs text-muted-foreground font-medium">
                    {t('settings.radioApp.blockedNames')}
                  </span>
                  <div className="mt-1 space-y-1">
                    {blockedNames.map((name) => (
                      <div key={name} className="flex items-center justify-between gap-2">
                        <span className="text-sm truncate flex-1">{name}</span>
                        {onToggleBlockedName && (
                          <Button
                            variant="ghost"
                            size="sm"
                            onClick={() => onToggleBlockedName(name)}
                            className="h-7 text-xs flex-shrink-0"
                          >
                            {t('settings.radioApp.unblock')}
                          </Button>
                        )}
                      </div>
                    ))}
                  </div>
                </div>
              )}
            </div>
          )}
        </div>

        <div className="space-y-3">
          <h4 className="text-sm font-semibold">{t('settings.radioApp.bulkDelete')}</h4>
          <p className="text-[0.8125rem] text-muted-foreground">
            {t('settings.radioApp.bulkDeleteHelp')}
          </p>
          <Button variant="outline" className="w-full" onClick={() => setBulkDeleteOpen(true)}>
            {t('settings.radioApp.openBulkDelete')}
          </Button>
          <BulkDeleteContactsModal
            open={bulkDeleteOpen}
            onClose={() => setBulkDeleteOpen(false)}
            contacts={contacts}
            onDeleted={(keys) => onBulkDeleteContacts?.(keys)}
          />
        </div>

        <div className="space-y-3">
          <h4 className="text-sm font-semibold">{t('settings.staleTitle')}</h4>
          <p className="text-[0.8125rem] text-muted-foreground">{t('settings.staleHelp')}</p>
          <div className="flex gap-2 items-end">
            <div className="space-y-1">
              <Label htmlFor="stale-contact-days" className="text-xs text-muted-foreground">
                {t('settings.staleDays')}
              </Label>
              <Input
                id="stale-contact-days"
                type="number"
                min="0"
                max="3650"
                value={staleDays}
                onChange={(e) => setStaleDays(e.target.value)}
                onBlur={() => {
                  const days = parseInt(staleDays, 10);
                  if (isNaN(days) || days < 0) {
                    setStaleDays(String(appSettings.stale_contact_days ?? 0));
                    return;
                  }
                  const prev = appSettings.stale_contact_days ?? 0;
                  if (days === prev) return;
                  void persistAppSettings({ stale_contact_days: days }, () =>
                    setStaleDays(String(prev))
                  );
                }}
                className="w-24"
              />
            </div>
          </div>
        </div>
      </div>

      <Separator />

      <div className="space-y-3">
        <SettingsGroupHeader title={t('settings.directoryTitle')} storedOn="server" instant />
        <div
          data-testid={directoryViaStats ? 'directory-via-community' : 'directory-off'}
          className={
            directoryViaStats
              ? 'rounded-md border border-border bg-muted/60 px-3 py-2 text-[0.8125rem] text-muted-foreground'
              : 'rounded-md border border-warning/30 bg-warning/10 px-3 py-2 text-[0.8125rem] text-warning'
          }
        >
          {directoryViaStats ? t('settings.directoryViaStats') : t('settings.directoryOff')}
        </div>
        <Button
          variant="outline"
          className="w-full"
          disabled={cacheResetting}
          onClick={() => {
            setCacheResetting(true);
            void api
              .resetDirectoryCache()
              .then((res) => {
                toast.success(t('settings.directoryCleared'), {
                  description: t('settings.directoryClearedDetail', { count: res.deleted }),
                });
              })
              .catch((err: unknown) => {
                toast.error(t('settings.directoryClearFailed'), {
                  description:
                    err instanceof Error ? err.message : t('settings.radioApp.unknownError'),
                });
              })
              .finally(() => setCacheResetting(false));
          }}
        >
          {t('settings.directoryReset')}
        </Button>
      </div>
    </div>
  );
}
