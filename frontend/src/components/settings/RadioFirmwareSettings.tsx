import { useCallback, useEffect, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { api, formatApiError } from '../../api';
import type { RadioCustomVars } from '../../types';
import { Button } from '../ui/button';
import { Checkbox } from '../ui/checkbox';
import { Input } from '../ui/input';
import { Label } from '../ui/label';
import { SettingsGroup, SettingsGroupHeader } from './settingsPrimitives';

/**
 * Firmware settings of the companion radio (MeshCore "custom vars"). The firmware
 * lists only the variables it exposes: on a Heltec V4 that is `gps`, and only when a GPS
 * module was connected at boot.
 */
export function RadioFirmwareSettings() {
  const { t } = useTranslation();
  const [data, setData] = useState<RadioCustomVars | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [saving, setSaving] = useState<string | null>(null);
  const [drafts, setDrafts] = useState<Record<string, string>>({});

  useEffect(() => {
    let cancelled = false;
    api
      .getRadioCustomVars()
      .then((res) => {
        if (!cancelled) setData(res);
      })
      .catch((err: unknown) => {
        if (!cancelled) setError(formatApiError(err, t) || t('settings.radio.firmware.loadFailed'));
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [t]);

  const save = useCallback(
    async (name: string, value: string) => {
      setSaving(name);
      setError(null);
      try {
        setData(await api.setRadioCustomVar(name, value));
        setDrafts((prev) => {
          const next = { ...prev };
          delete next[name];
          return next;
        });
      } catch (err) {
        setError(formatApiError(err, t) || t('settings.radio.firmware.saveFailed'));
      } finally {
        setSaving(null);
      }
    },
    [t]
  );

  const vars = data?.vars ?? {};
  const otherNames = Object.keys(vars).filter((name) => name !== 'gps');

  let body;
  if (loading) {
    body = (
      <p className="text-[0.8125rem] text-muted-foreground" role="status">
        {t('settings.radio.firmware.loading')}
      </p>
    );
  } else if (!data) {
    body = null;
  } else if (!data.supported) {
    body = (
      <p className="max-w-prose text-[0.8125rem] text-muted-foreground">
        {t('settings.radio.firmware.unsupported')}
      </p>
    );
  } else if (Object.keys(vars).length === 0) {
    body = (
      <p className="max-w-prose text-[0.8125rem] text-muted-foreground">
        {t('settings.radio.firmware.empty')}
      </p>
    );
  } else {
    body = (
      <div className="space-y-2">
        {'gps' in vars && (
          <div className="flex items-start gap-3 rounded-md border border-border/60 p-3">
            <Checkbox
              id="radio-firmware-gps"
              aria-describedby="radio-firmware-gps-help"
              checked={vars.gps === '1'}
              disabled={saving !== null}
              onCheckedChange={(checked) => void save('gps', checked === true ? '1' : '0')}
              className="mt-0.5"
            />
            <div className="space-y-1">
              <Label htmlFor="radio-firmware-gps">{t('settings.radio.firmware.gps')}</Label>
              <p
                id="radio-firmware-gps-help"
                className="max-w-prose text-[0.8125rem] text-muted-foreground"
              >
                {t('settings.radio.firmware.gpsHelp')}
              </p>
            </div>
          </div>
        )}
        {otherNames.map((name) => {
          const id = `radio-firmware-var-${name}`;
          const draft = drafts[name] ?? vars[name];
          return (
            <div key={name} className="space-y-2 rounded-md border border-border/60 p-3">
              <Label htmlFor={id}>{name}</Label>
              <div className="flex items-center gap-2">
                <Input
                  id={id}
                  value={draft}
                  disabled={saving !== null}
                  onChange={(e) => setDrafts((prev) => ({ ...prev, [name]: e.target.value }))}
                />
                <Button
                  type="button"
                  variant="outline"
                  disabled={saving !== null || draft === vars[name]}
                  onClick={() => void save(name, draft)}
                  aria-label={t('settings.radio.firmware.saveVar', { name })}
                >
                  {saving === name
                    ? t('settings.radio.firmware.saving')
                    : t('settings.radio.firmware.save')}
                </Button>
              </div>
            </div>
          );
        })}
      </div>
    );
  }

  return (
    <SettingsGroup id="radio-firmware-settings">
      <SettingsGroupHeader
        id="radio-firmware-settings"
        title={t('settings.radio.firmware.title')}
        storedOn="radio"
        instant
      />
      {body}
      {error && (
        <div className="text-sm text-destructive" role="alert">
          {error}
        </div>
      )}
    </SettingsGroup>
  );
}
