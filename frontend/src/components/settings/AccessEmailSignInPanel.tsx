import { useEffect, useState } from 'react';
import { useTranslation } from 'react-i18next';

import { api } from '../../api';
import { formatAccessError } from '../../auth/accessErrors';
import type { EmailSignInSettings } from '../../auth/types';
import { Button } from '../ui/button';
import { Label } from '../ui/label';
import { toast } from '../ui/sonner';
import { SettingsGroup, SettingsGroupHeader } from './settingsPrimitives';

const NONE = '';

/**
 * Settings → Connection → E-mail sign-in (administrator only, MSHL-426): which
 * notification e-mail channel sends the sign-in mails (the e-mail code after a password
 * and the sign-in links). Its SMTP settings are the channel's, edited in Notifications;
 * nothing is copied. Without a channel both features are refused. Each account opts in
 * itself in Users & Access.
 */
export function AccessEmailSignInPanel() {
  const { t } = useTranslation();
  const [settings, setSettings] = useState<EmailSignInSettings | null>(null);
  const [choice, setChoice] = useState(NONE);
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    let cancelled = false;
    api
      .getEmailSignInSettings()
      .then((next) => {
        if (cancelled) return;
        setSettings(next);
        setChoice(next.channel_id ?? NONE);
      })
      .catch(() => {
        // Not readable (older server, refused): the panel stays hidden.
      });
    return () => {
      cancelled = true;
    };
  }, []);

  if (!settings) return null;

  const save = async () => {
    if (saving) return;
    setSaving(true);
    try {
      const next = await api.patchEmailSignInSettings(choice || null);
      setSettings(next);
      setChoice(next.channel_id ?? NONE);
      toast.success(t('settings.access.emailSignIn.saved'));
    } catch (err) {
      toast.error(formatAccessError(err, t));
    } finally {
      setSaving(false);
    }
  };

  return (
    <SettingsGroup>
      <SettingsGroupHeader
        id="access-email-sign-in"
        title={t('settings.access.emailSignIn.title')}
        storedOn="server"
      />
      <p className="text-[0.8125rem] text-muted-foreground">
        {t('settings.access.emailSignIn.help')}
      </p>
      {settings.channels.length === 0 ? (
        <p role="note" className="text-sm text-muted-foreground">
          {t('settings.access.emailSignIn.noChannel')}
        </p>
      ) : (
        <div className="space-y-1.5">
          <Label htmlFor="email-sign-in-channel">{t('settings.access.emailSignIn.channel')}</Label>
          <div className="flex flex-wrap items-center gap-2">
            <select
              id="email-sign-in-channel"
              className="h-9 rounded-md border border-input bg-background px-2 text-sm"
              value={choice}
              onChange={(event) => setChoice(event.target.value)}
            >
              <option value={NONE}>{t('settings.access.emailSignIn.none')}</option>
              {settings.channels.map((channel) => (
                <option key={channel.id} value={channel.id}>
                  {channel.usable
                    ? channel.name
                    : t('settings.access.emailSignIn.channelOff', { name: channel.name })}
                </option>
              ))}
            </select>
            <Button
              type="button"
              disabled={saving || choice === (settings.channel_id ?? NONE)}
              onClick={() => void save()}
            >
              {t('settings.access.emailSignIn.save')}
            </Button>
          </div>
        </div>
      )}
      <p role="status" className="text-sm">
        {t(
          settings.available
            ? 'settings.access.emailSignIn.ready'
            : 'settings.access.emailSignIn.notReady'
        )}
      </p>
    </SettingsGroup>
  );
}
