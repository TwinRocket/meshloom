import { useEffect, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { versionsMatch } from '../../hooks/useOssUpdates';
import type { OssUpdateSettingsPatch, OssUpdateStatus } from '../../types';
import { cn } from '../../lib/utils';
import { Button } from '../ui/button';
import { Checkbox } from '../ui/checkbox';
import { Input } from '../ui/input';
import { Label } from '../ui/label';
import { SettingsGroup, SettingsGroupHeader } from './settingsPrimitives';

const ISO_WEEKDAYS = [0, 1, 2, 3, 4, 5, 6] as const;

function Recipe({ title, code }: { title: string; code: string }) {
  return (
    <div className="space-y-1.5">
      <h3 className="text-[0.625rem] font-medium uppercase tracking-wider text-muted-foreground">
        {title}
      </h3>
      <pre className="overflow-x-auto rounded-xl bg-muted/40 px-4 py-3 font-mono text-[0.8125rem] leading-relaxed text-foreground">
        {code}
      </pre>
    </div>
  );
}

function StatusRow({ label, value }: { label: string; value: string }) {
  return (
    <div className="flex items-baseline justify-between gap-4 border-b border-border/30 py-2 last:border-b-0">
      <dt className="shrink-0 text-[0.6875rem] uppercase tracking-wider text-muted-foreground">
        {label}
      </dt>
      <dd className="min-w-0 break-words text-right text-sm">{value}</dd>
    </div>
  );
}

function formatUnixSeconds(value: number | null | undefined): string | null {
  if (value == null || !Number.isFinite(value) || value <= 0) return null;
  return new Date(value * 1000).toLocaleString();
}

function normalizeTime(value: string | null | undefined): string {
  if (!value) return '';
  return value.length >= 5 ? value.slice(0, 5) : value;
}

function resolvedWeekdays(updates: OssUpdateStatus | null | undefined): number[] {
  return updates?.auto_update_weekdays ?? [...ISO_WEEKDAYS];
}

export function SettingsUpdatesSection({
  updates,
  onCheckNow,
  onApply,
  onPatchSettings,
  onOpenManualHelp,
  checking = false,
  applying = false,
  className,
}: {
  updates?: OssUpdateStatus | null;
  onCheckNow?: () => void;
  onApply?: () => void;
  onPatchSettings?: (settings: OssUpdateSettingsPatch) => void;
  onOpenManualHelp?: () => void;
  checking?: boolean;
  applying?: boolean;
  className?: string;
}) {
  const { t } = useTranslation();
  const applySupported = updates?.apply_supported === true;
  const updateAvailable = updates?.update_available === true;
  const kind = updates?.install_kind;
  const showAddonHelp = kind === 'addon' && !applySupported;
  const showManualHelp = (kind === 'container' || kind === 'source') && !applySupported;
  const showRecipes = !applySupported && kind !== 'addon';
  const showInstall = applySupported && updateAvailable && Boolean(onApply);
  const showJobError =
    Boolean(updates?.job?.error) &&
    updateAvailable &&
    !versionsMatch(updates?.current, updates?.latest);
  const checkedAt = formatUnixSeconds(updates?.checked_at);
  const nextAutoApply = formatUnixSeconds(updates?.next_auto_apply_at);
  const weekdays = resolvedWeekdays(updates);

  const [windowStart, setWindowStart] = useState(() =>
    normalizeTime(updates?.auto_update_window_start)
  );
  const [windowEnd, setWindowEnd] = useState(() => normalizeTime(updates?.auto_update_window_end));

  useEffect(() => {
    setWindowStart(normalizeTime(updates?.auto_update_window_start));
    setWindowEnd(normalizeTime(updates?.auto_update_window_end));
  }, [updates?.auto_update_window_start, updates?.auto_update_window_end]);

  const job = updates?.job;
  const jobLabel = job
    ? job.phase
      ? `${t(`settings.updates.jobState.${job.state}`)} · ${t(`updates.phase.${job.phase}`)}`
      : t(`settings.updates.jobState.${job.state}`)
    : '—';

  const installKindLabel = kind
    ? t(`settings.updates.installKind.${kind}`, { defaultValue: kind })
    : '—';

  const commitWindow = (
    field: 'auto_update_window_start' | 'auto_update_window_end',
    value: string
  ) => {
    const current =
      field === 'auto_update_window_start'
        ? normalizeTime(updates?.auto_update_window_start)
        : normalizeTime(updates?.auto_update_window_end);
    if (value === current) return;
    onPatchSettings?.({ [field]: value });
  };

  const toggleWeekday = (day: number) => {
    const selected = new Set(weekdays);
    if (selected.has(day)) selected.delete(day);
    else selected.add(day);
    onPatchSettings?.({ auto_update_weekdays: [...selected].sort((a, b) => a - b) });
  };

  return (
    <div className={cn('space-y-4', className)}>
      <SettingsGroup id="settings-updates-status">
        <SettingsGroupHeader
          id="settings-updates-status"
          title={t('settings.updates.status')}
          storedOn="server"
          instant
        />
        <dl>
          <StatusRow
            label={t('updates.current')}
            value={updates?.current ? `v${updates.current}` : '—'}
          />
          <StatusRow
            label={t('updates.latest')}
            value={updates?.latest ? `v${updates.latest}` : '—'}
          />
          <StatusRow
            label={t('settings.updates.checkedAt')}
            value={checkedAt ?? t('settings.updates.neverChecked')}
          />
          <StatusRow label={t('settings.updates.timezone')} value={updates?.tz_name || '—'} />
          <StatusRow label={t('settings.updates.installKind.label')} value={installKindLabel} />
          <StatusRow label={t('settings.updates.job')} value={jobLabel} />
        </dl>
        {showJobError ? <p className="text-[0.8125rem] text-destructive">{job?.error}</p> : null}
      </SettingsGroup>

      <SettingsGroup id="settings-updates-actions">
        <SettingsGroupHeader
          id="settings-updates-actions"
          title={t('settings.updates.actions')}
          storedOn="server"
          instant
        />
        <div className="flex flex-wrap gap-2">
          <Button
            type="button"
            variant="outline"
            onClick={() => onCheckNow?.()}
            disabled={!onCheckNow || checking || applying}
          >
            {t('settings.updates.checkNow')}
          </Button>
          {showInstall ? (
            <Button type="button" onClick={() => onApply?.()} disabled={applying || checking}>
              {t('settings.updates.installNow')}
            </Button>
          ) : null}
        </div>
        {showAddonHelp ? (
          <p className="text-[0.8125rem] text-muted-foreground">{t('updates.addonManual')}</p>
        ) : null}
        {showManualHelp ? (
          <p className="text-[0.8125rem] text-muted-foreground">{t('updates.manual')}</p>
        ) : null}
        {showRecipes ? (
          <>
            {/* English comments match README upgrade recipes; UI chrome is i18n. */}
            <Recipe
              title={t('updates.packagesTitle')}
              code={[
                'sudo apt-get install --only-upgrade meshloom   # Debian / Ubuntu',
                'sudo dnf install meshloom                      # Fedora / Rocky / Alma',
              ].join('\n')}
            />
            <Recipe
              title={t('updates.dockerTitle')}
              code="sudo docker compose pull && sudo docker compose up -d"
            />
          </>
        ) : null}
        {onOpenManualHelp && !applySupported ? (
          <button
            type="button"
            onClick={onOpenManualHelp}
            className="text-xs text-muted-foreground hover:text-primary hover:underline"
          >
            {t('settings.updates.manualHelp')}
          </button>
        ) : null}
      </SettingsGroup>

      {applySupported ? (
        <SettingsGroup id="settings-updates-auto">
          <SettingsGroupHeader
            id="settings-updates-auto"
            title={t('settings.updates.autoUpdate')}
            storedOn="server"
            instant
          />
          <div className="flex items-start gap-3 rounded-md border border-border/60 p-3">
            <Checkbox
              id="updates-auto-update"
              checked={updates?.auto_update === true}
              onCheckedChange={(checked) => onPatchSettings?.({ auto_update: checked === true })}
              className="mt-0.5"
            />
            <div className="space-y-1">
              <Label htmlFor="updates-auto-update">{t('updates.autoUpdate')}</Label>
              <p className="text-[0.8125rem] text-muted-foreground">
                {t('updates.autoUpdateHelp')}
              </p>
            </div>
          </div>

          <div className="grid gap-3 sm:grid-cols-2">
            <div className="space-y-2">
              <Label htmlFor="updates-window-start">{t('settings.updates.windowStart')}</Label>
              <Input
                id="updates-window-start"
                type="time"
                value={windowStart}
                onChange={(event) => setWindowStart(event.target.value)}
                onBlur={() => commitWindow('auto_update_window_start', windowStart)}
              />
            </div>
            <div className="space-y-2">
              <Label htmlFor="updates-window-end">{t('settings.updates.windowEnd')}</Label>
              <Input
                id="updates-window-end"
                type="time"
                value={windowEnd}
                onChange={(event) => setWindowEnd(event.target.value)}
                onBlur={() => commitWindow('auto_update_window_end', windowEnd)}
              />
            </div>
          </div>

          <div className="space-y-2">
            <p className="text-[0.625rem] font-medium uppercase tracking-wider text-muted-foreground">
              {t('settings.updates.weekdays')}
            </p>
            <div className="flex flex-wrap gap-1.5">
              {ISO_WEEKDAYS.map((day) => {
                const selected = weekdays.includes(day);
                return (
                  <button
                    key={day}
                    type="button"
                    aria-pressed={selected}
                    onClick={() => toggleWeekday(day)}
                    className={cn(
                      'rounded px-1.5 py-0.5 text-[0.625rem] font-medium uppercase tracking-wider',
                      selected
                        ? 'bg-primary/10 text-primary'
                        : 'bg-muted text-muted-foreground hover:text-foreground'
                    )}
                  >
                    {t(`settings.updates.weekday.${day}`)}
                  </button>
                );
              })}
            </div>
          </div>

          <p className="text-[0.8125rem] text-muted-foreground">
            {t('settings.updates.nodeClockHelp')}
          </p>
          {nextAutoApply ? (
            <p className="text-[0.8125rem] text-muted-foreground">
              {t('settings.updates.nextAutoApply')}: {nextAutoApply}
            </p>
          ) : null}
        </SettingsGroup>
      ) : null}
    </div>
  );
}
