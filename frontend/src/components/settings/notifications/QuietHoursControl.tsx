import { useState } from 'react';
import { useTranslation } from 'react-i18next';

import type {
  NotificationQuietHours,
  NotificationRules,
  NotificationRulesPatch,
} from '../../../notifications/types';
import { cn } from '../../../lib/utils';
import { Checkbox } from '../../ui/checkbox';
import { Input } from '../../ui/input';
import { Label } from '../../ui/label';

const ISO_WEEKDAYS = [0, 1, 2, 3, 4, 5, 6] as const;

/** What the server reads when `rules.quiet_hours` is absent (`quiet_hours.py`). */
export const DEFAULT_QUIET_HOURS: NotificationQuietHours = {
  enabled: false,
  start: '22:00',
  end: '07:00',
  weekdays: [...ISO_WEEKDAYS],
  digest_at_end: true,
};

/**
 * Quiet hours: a recurring window (host time zone) during which non-urgent notifications are
 * held and summed up in one message at the end. Writes `rules.quiet_hours` through `onPatch`
 * (a partial object, validated by the server). Which channels ignore the window is set on
 * each channel.
 */
export function QuietHoursControl({
  rules,
  disabled,
  onPatch,
}: {
  rules: NotificationRules;
  disabled?: boolean;
  onPatch: (body: NotificationRulesPatch) => void;
}) {
  const { t } = useTranslation();
  const quiet = { ...DEFAULT_QUIET_HOURS, ...rules.quiet_hours };
  const [start, setStart] = useState(quiet.start);
  const [end, setEnd] = useState(quiet.end);
  // Re-sync the drafts when the saved values change (adjust state during render).
  const [saved, setSaved] = useState({ start: quiet.start, end: quiet.end });
  if (saved.start !== quiet.start || saved.end !== quiet.end) {
    setSaved({ start: quiet.start, end: quiet.end });
    setStart(quiet.start);
    setEnd(quiet.end);
  }

  const save = (body: Partial<NotificationQuietHours>) => onPatch({ quiet_hours: body });
  const commit = (field: 'start' | 'end', value: string) => {
    if (!value || value === quiet[field]) {
      // An emptied time input snaps back to what is saved.
      if (field === 'start') setStart(quiet.start);
      else setEnd(quiet.end);
      return;
    }
    save({ [field]: value });
  };
  const toggleDay = (day: number) => {
    const selected = new Set(quiet.weekdays);
    if (selected.has(day)) selected.delete(day);
    else selected.add(day);
    save({ weekdays: [...selected].sort((a, b) => a - b) });
  };

  return (
    <div className="space-y-3" data-testid="notification-quiet-hours">
      <div className="flex items-start gap-3">
        <Checkbox
          id="quiet-hours-enabled"
          checked={quiet.enabled}
          disabled={disabled}
          onCheckedChange={(checked) => save({ enabled: checked === true })}
          className="mt-0.5"
        />
        <Label htmlFor="quiet-hours-enabled">{t('settings.notifications.quiet.enabled')}</Label>
      </div>

      <div className="grid gap-3 sm:grid-cols-2">
        <div className="space-y-2">
          <Label htmlFor="quiet-hours-start">{t('settings.notifications.quiet.start')}</Label>
          <Input
            id="quiet-hours-start"
            type="time"
            value={start}
            disabled={disabled}
            onChange={(event) => setStart(event.target.value)}
            onBlur={() => commit('start', start)}
          />
        </div>
        <div className="space-y-2">
          <Label htmlFor="quiet-hours-end">{t('settings.notifications.quiet.end')}</Label>
          <Input
            id="quiet-hours-end"
            type="time"
            value={end}
            disabled={disabled}
            onChange={(event) => setEnd(event.target.value)}
            onBlur={() => commit('end', end)}
          />
        </div>
      </div>

      <div className="space-y-2">
        <p className="text-[0.625rem] font-medium uppercase tracking-wider text-muted-foreground">
          {t('settings.notifications.quiet.weekdays')}
        </p>
        <div className="flex flex-wrap gap-1.5">
          {ISO_WEEKDAYS.map((day) => {
            const selected = quiet.weekdays.includes(day);
            return (
              <button
                key={day}
                type="button"
                aria-pressed={selected}
                disabled={disabled}
                onClick={() => toggleDay(day)}
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

      <div className="flex items-start gap-3">
        <Checkbox
          id="quiet-hours-digest"
          checked={quiet.digest_at_end}
          disabled={disabled}
          onCheckedChange={(checked) => save({ digest_at_end: checked === true })}
          className="mt-0.5"
        />
        <div className="min-w-0 space-y-1">
          <Label htmlFor="quiet-hours-digest">{t('settings.notifications.quiet.digest')}</Label>
          <p className="text-[0.8125rem] text-muted-foreground">
            {t('settings.notifications.quiet.digestHelp')}
          </p>
        </div>
      </div>

      <p className="text-[0.8125rem] text-muted-foreground">
        {t('settings.notifications.quiet.timezone', { zone: rules.host_timezone })}
      </p>
    </div>
  );
}
