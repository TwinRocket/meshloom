import { useCallback, useEffect, useState, type FormEvent } from 'react';
import { useTranslation } from 'react-i18next';

import { api } from '../../api';
import { formatAccessError } from '../../auth/accessErrors';
import type { AuthSettings, IpBansView } from '../../auth/types';
import { Button } from '../ui/button';
import { Checkbox } from '../ui/checkbox';
import { Input } from '../ui/input';
import { Label } from '../ui/label';
import { toast } from '../ui/sonner';
import { formatEpoch } from './accessShared';
import { SettingsGroup, SettingsGroupHeader } from './settingsPrimitives';

/** Same bounds as the server (`MIN/MAX_IP_BAN_*`, `app/auth/models.py`). */
export const IP_BAN_BOUNDS = {
  minFailures: 3,
  maxFailures: 1000,
  min: 1,
  maxWindow: 1440,
  maxDuration: 43200,
} as const;

type Thresholds = { failures: string; window: string; duration: string };

const thresholdsOf = (s: AuthSettings): Thresholds => ({
  failures: String(s.ip_ban_max_failures ?? ''),
  window: String(s.ip_ban_window_minutes ?? ''),
  duration: String(s.ip_ban_duration_minutes ?? ''),
});

const whole = (value: string, min: number, max: number): number | null => {
  if (!/^\d+$/.test(value.trim())) return null;
  const n = Number(value);
  return n >= min && n <= max ? n : null;
};

/**
 * Administrator only: the built-in address ban (MSHL-422, `app/auth/ip_ban.py`). Its
 * settings, the running bans (each with Unban) and the whitelist of networks never
 * banned. The server validates everything again; `meshloom-admin` does the same from
 * the host.
 */
export function AccessIpBanPanel() {
  const { t, i18n } = useTranslation();
  const [settings, setSettings] = useState<AuthSettings | null>(null);
  const [values, setValues] = useState<Thresholds>({ failures: '', window: '', duration: '' });
  const [view, setView] = useState<IpBansView | null>(null);
  const [busy, setBusy] = useState(false);
  const [invalid, setInvalid] = useState(false);
  const [network, setNetwork] = useState('');
  const [note, setNote] = useState('');

  const reload = useCallback(async () => {
    try {
      setView(await api.getIpBans());
    } catch {
      // Older server or refused: the lists stay hidden.
    }
  }, []);

  useEffect(() => {
    let cancelled = false;
    api
      .getAuthSettings()
      .then((value) => {
        if (cancelled || typeof value.ip_ban_enabled !== 'boolean') return;
        setSettings(value);
        setValues(thresholdsOf(value));
      })
      .catch(() => {
        // Not readable (older server, refused): the panel stays hidden.
      });
    api
      .getIpBans()
      .then((value) => {
        if (!cancelled) setView(value);
      })
      .catch(() => {
        // Older server or refused: the lists stay hidden.
      });
    return () => {
      cancelled = true;
    };
  }, []);

  if (!settings) return null;

  const when = (seconds: number) => formatEpoch(seconds, i18n.language);
  const saved = thresholdsOf(settings);
  const unchanged =
    values.failures === saved.failures &&
    values.window === saved.window &&
    values.duration === saved.duration;

  const run = async (work: () => Promise<void>) => {
    setBusy(true);
    try {
      await work();
    } catch (err) {
      toast.error(formatAccessError(err, t));
    } finally {
      setBusy(false);
    }
  };

  const toggle = (next: boolean) =>
    run(async () => {
      setSettings(await api.patchAuthSettings({ ip_ban_enabled: next }));
      toast.success(t(next ? 'settings.access.ipBan.on' : 'settings.access.ipBan.off'));
    });

  const save = (event: FormEvent) => {
    event.preventDefault();
    if (busy) return;
    const failures = whole(values.failures, IP_BAN_BOUNDS.minFailures, IP_BAN_BOUNDS.maxFailures);
    const window = whole(values.window, IP_BAN_BOUNDS.min, IP_BAN_BOUNDS.maxWindow);
    const duration = whole(values.duration, IP_BAN_BOUNDS.min, IP_BAN_BOUNDS.maxDuration);
    if (failures === null || window === null || duration === null) {
      setInvalid(true);
      return;
    }
    setInvalid(false);
    void run(async () => {
      const next = await api.patchAuthSettings({
        ip_ban_max_failures: failures,
        ip_ban_window_minutes: window,
        ip_ban_duration_minutes: duration,
      });
      setSettings(next);
      setValues(thresholdsOf(next));
      toast.success(t('settings.access.ipBan.saved'));
    });
  };

  const unban = (address: string) =>
    run(async () => {
      await api.unbanIp(address);
      toast.success(t('settings.access.ipBan.unbanned', { address }));
      await reload();
    });

  const add = (event: FormEvent) => {
    event.preventDefault();
    if (busy || !network.trim()) return;
    void run(async () => {
      const entry = await api.addIpBanWhitelist(network.trim(), note.trim());
      toast.success(t('settings.access.ipBan.added', { network: entry.network }));
      setNetwork('');
      setNote('');
      await reload();
    });
  };

  const remove = (entry: string) =>
    run(async () => {
      await api.removeIpBanWhitelist(entry);
      toast.success(t('settings.access.ipBan.removed', { network: entry }));
      await reload();
    });

  const field = (key: keyof Thresholds, id: string, label: string, max: number, min: number) => (
    <div className="space-y-1">
      <Label htmlFor={id}>{label}</Label>
      <Input
        id={id}
        type="number"
        inputMode="numeric"
        min={min}
        max={max}
        className="w-28"
        value={values[key]}
        aria-invalid={invalid}
        onChange={(event) => {
          setValues((current) => ({ ...current, [key]: event.target.value }));
          setInvalid(false);
        }}
      />
    </div>
  );

  return (
    <SettingsGroup>
      <SettingsGroupHeader
        id="access-ip-ban"
        title={t('settings.access.ipBan.title')}
        storedOn="server"
      />
      <p className="text-[0.8125rem] text-muted-foreground">{t('settings.access.ipBan.help')}</p>
      <div className="flex items-center gap-2">
        <Checkbox
          id="access-ip-ban-enabled"
          checked={settings.ip_ban_enabled === true}
          disabled={busy}
          onCheckedChange={(checked) => void toggle(checked === true)}
        />
        <Label htmlFor="access-ip-ban-enabled">{t('settings.access.ipBan.toggle')}</Label>
      </div>
      <form onSubmit={save} className="space-y-1.5" noValidate>
        <div className="flex flex-wrap items-end gap-3">
          {field(
            'failures',
            'access-ip-ban-failures',
            t('settings.access.ipBan.maxFailures'),
            IP_BAN_BOUNDS.maxFailures,
            IP_BAN_BOUNDS.minFailures
          )}
          {field(
            'window',
            'access-ip-ban-window',
            t('settings.access.ipBan.windowMinutes'),
            IP_BAN_BOUNDS.maxWindow,
            IP_BAN_BOUNDS.min
          )}
          {field(
            'duration',
            'access-ip-ban-duration',
            t('settings.access.ipBan.durationMinutes'),
            IP_BAN_BOUNDS.maxDuration,
            IP_BAN_BOUNDS.min
          )}
          <Button type="submit" disabled={busy || unchanged}>
            {t('settings.access.ipBan.save')}
          </Button>
        </div>
        {invalid && (
          <p role="alert" className="text-sm text-destructive">
            {t('settings.access.ipBan.invalid', IP_BAN_BOUNDS)}
          </p>
        )}
      </form>

      {view && (
        <>
          <h4 className="pt-2 text-sm font-medium">{t('settings.access.ipBan.bans')}</h4>
          {view.bans.length === 0 ? (
            <p className="text-[0.8125rem] text-muted-foreground">
              {t('settings.access.ipBan.noBans')}
            </p>
          ) : (
            <ul className="space-y-1" data-testid="ip-ban-list">
              {view.bans.map((ban) => (
                <li key={ban.address} className="flex flex-wrap items-center gap-2 text-sm">
                  <span className="break-all font-mono">{ban.address}</span>
                  <span className="text-muted-foreground">
                    {t('settings.access.ipBan.banRow', {
                      when: when(ban.expires_at),
                      count: ban.failures,
                    })}
                  </span>
                  <Button
                    size="sm"
                    variant="outline"
                    disabled={busy}
                    aria-label={`${t('settings.access.ipBan.unban')} ${ban.address}`}
                    onClick={() => void unban(ban.address)}
                  >
                    {t('settings.access.ipBan.unban')}
                  </Button>
                </li>
              ))}
            </ul>
          )}

          <h4 className="pt-2 text-sm font-medium">{t('settings.access.ipBan.whitelist')}</h4>
          {view.whitelist.length === 0 ? (
            <p className="text-[0.8125rem] text-muted-foreground">
              {t('settings.access.ipBan.noWhitelist')}
            </p>
          ) : (
            <ul className="space-y-1" data-testid="ip-ban-whitelist">
              {view.whitelist.map((entry) => (
                <li key={entry.network} className="flex flex-wrap items-center gap-2 text-sm">
                  <span className="break-all font-mono">{entry.network}</span>
                  {entry.note && <span className="text-muted-foreground">{entry.note}</span>}
                  <Button
                    size="sm"
                    variant="outline"
                    disabled={busy}
                    aria-label={`${t('settings.access.ipBan.remove')} ${entry.network}`}
                    onClick={() => void remove(entry.network)}
                  >
                    {t('settings.access.ipBan.remove')}
                  </Button>
                </li>
              ))}
            </ul>
          )}
          <form onSubmit={add} className="flex flex-wrap items-end gap-2">
            <div className="space-y-1">
              <Label htmlFor="access-ip-ban-network">{t('settings.access.ipBan.network')}</Label>
              <Input
                id="access-ip-ban-network"
                className="w-48"
                maxLength={64}
                placeholder={t('settings.access.ipBan.networkPlaceholder')}
                value={network}
                onChange={(event) => setNetwork(event.target.value)}
              />
            </div>
            <div className="min-w-0 flex-1 space-y-1">
              <Label htmlFor="access-ip-ban-note">{t('settings.access.ipBan.note')}</Label>
              <Input
                id="access-ip-ban-note"
                maxLength={200}
                value={note}
                onChange={(event) => setNote(event.target.value)}
              />
            </div>
            <Button type="submit" disabled={busy || !network.trim()}>
              {t('settings.access.ipBan.add')}
            </Button>
          </form>
        </>
      )}
      <p className="text-[0.8125rem] text-muted-foreground">{t('settings.access.ipBan.cli')}</p>
    </SettingsGroup>
  );
}
