import { useEffect, useState, type FormEvent } from 'react';
import { useTranslation } from 'react-i18next';

import { api } from '../../api';
import { formatAccessError } from '../../auth/accessErrors';
import { Button } from '../ui/button';
import { Input } from '../ui/input';
import { Label } from '../ui/label';
import { toast } from '../ui/sonner';
import { SettingsGroup, SettingsGroupHeader } from './settingsPrimitives';

/** Same bounds as the server (`MIN/MAX_SESSION_LIFETIME_DAYS`, `app/auth/models.py`). */
export const SESSION_LIFETIME_MIN_DAYS = 1;
export const SESSION_LIFETIME_MAX_DAYS = 365;

/**
 * Administrator only: `session_lifetime_days`, how long a "Keep me signed in" session
 * lasts after its last visit. The server validates the bounds again (422).
 */
export function AccessSessionLifetimePanel() {
  const { t } = useTranslation();
  const [saved, setSaved] = useState<number | null>(null);
  const [value, setValue] = useState('');
  const [saving, setSaving] = useState(false);
  const [invalid, setInvalid] = useState(false);

  useEffect(() => {
    let cancelled = false;
    api
      .getAuthSettings()
      .then((settings) => {
        if (cancelled || typeof settings.session_lifetime_days !== 'number') return;
        setSaved(settings.session_lifetime_days);
        setValue(String(settings.session_lifetime_days));
      })
      .catch(() => {
        // Not readable (older server, refused): the control stays hidden.
      });
    return () => {
      cancelled = true;
    };
  }, []);

  if (saved === null) return null;

  const bounds = { min: SESSION_LIFETIME_MIN_DAYS, max: SESSION_LIFETIME_MAX_DAYS };
  const days = Number(value);
  const valid =
    /^\d+$/.test(value.trim()) &&
    days >= SESSION_LIFETIME_MIN_DAYS &&
    days <= SESSION_LIFETIME_MAX_DAYS;

  const submit = async (event: FormEvent) => {
    event.preventDefault();
    if (saving) return;
    if (!valid) {
      setInvalid(true);
      return;
    }
    setInvalid(false);
    setSaving(true);
    try {
      const result = await api.patchAuthSettings({ session_lifetime_days: days });
      const next = result.session_lifetime_days ?? days;
      setSaved(next);
      setValue(String(next));
      toast.success(t('settings.access.sessionLifetime.saved'));
    } catch (err) {
      toast.error(formatAccessError(err, t));
    } finally {
      setSaving(false);
    }
  };

  return (
    <SettingsGroup>
      <SettingsGroupHeader
        id="access-session-lifetime"
        title={t('settings.access.sessionLifetime.title')}
        storedOn="server"
      />
      <form onSubmit={submit} className="space-y-1.5" noValidate>
        <Label htmlFor="access-session-lifetime-days">
          {t('settings.access.sessionLifetime.days')}
        </Label>
        <div className="flex flex-wrap items-center gap-2">
          <Input
            id="access-session-lifetime-days"
            type="number"
            inputMode="numeric"
            min={SESSION_LIFETIME_MIN_DAYS}
            max={SESSION_LIFETIME_MAX_DAYS}
            className="w-28"
            value={value}
            aria-invalid={invalid}
            onChange={(event) => {
              setValue(event.target.value);
              setInvalid(false);
            }}
          />
          <Button type="submit" disabled={saving || value === String(saved)}>
            {t('settings.access.sessionLifetime.save')}
          </Button>
        </div>
        {invalid && (
          <p role="alert" className="text-sm text-destructive">
            {t('settings.access.sessionLifetime.invalid', bounds)}
          </p>
        )}
        <p className="text-[0.8125rem] text-muted-foreground">
          {t('settings.access.sessionLifetime.help', bounds)}
        </p>
      </form>
    </SettingsGroup>
  );
}
