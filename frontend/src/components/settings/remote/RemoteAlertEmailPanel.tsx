import { useEffect, useRef, useState, type FormEvent } from 'react';
import { useTranslation } from 'react-i18next';

import { api } from '../../../api';
import { formatAccessError } from '../../../auth/accessErrors';
import { userDocsUrl } from '../../../utils/userDocsUrl';
import { Button } from '../../ui/button';
import { Input } from '../../ui/input';
import { Label } from '../../ui/label';
import { toast } from '../../ui/sonner';
import { SettingsGroup, SettingsGroupHeader } from '../settingsPrimitives';
import { REMOTE_ALERT_EMAIL_MAX } from './remoteShared';
import type { RemoteAlertEmailState } from './remoteTypes';

/**
 * The relay's alert e-mail (`/api/remote/alert-email`, docs/dev/remote.md, "Alert
 * e-mail"). Administrators only (the server refuses anyone else). The relay keeps the
 * address and never returns it, so the field starts empty and only the state is shown;
 * nothing is stored here. A failed state read leaves the field usable, without a state.
 * The section keys it by slug: a new key starts with no address, so it reads again.
 */
export function RemoteAlertEmailPanel() {
  const { t, i18n } = useTranslation();
  const [state, setState] = useState<RemoteAlertEmailState | null>(null);
  const [email, setEmail] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  // Set by any action: a slow first read must not overwrite what the action answered.
  const acted = useRef(false);

  useEffect(() => {
    let cancelled = false;
    api
      .getRemoteAlertEmail()
      .then((next) => {
        if (!cancelled && !acted.current) setState(next.state);
      })
      .catch(() => undefined);
    return () => {
      cancelled = true;
    };
  }, []);

  const act = async (action: () => Promise<{ state: RemoteAlertEmailState }>, done: string) => {
    acted.current = true;
    setBusy(true);
    setError(null);
    try {
      setState((await action()).state);
      setEmail('');
      toast.success(t(done));
    } catch (err) {
      setError(formatAccessError(err, t));
    } finally {
      setBusy(false);
    }
  };

  const submit = (event: FormEvent) => {
    event.preventDefault();
    const address = email.trim();
    if (!address) return;
    void act(() => api.setRemoteAlertEmail(address), 'settings.remote.alertEmail.savedToast');
  };

  return (
    <SettingsGroup>
      <SettingsGroupHeader
        id="remote-alert-email"
        title={t('settings.remote.alertEmail.title')}
        storedOn="relay"
      />
      <p className="text-[0.8125rem] text-muted-foreground">
        {t('settings.remote.alertEmail.help')}{' '}
        <a
          href={userDocsUrl(i18n.language, 'deep/remote')}
          target="_blank"
          rel="noreferrer"
          className="underline underline-offset-2 hover:text-primary transition-colors"
        >
          {t('settings.remote.alertEmail.learnMore')}
        </a>
      </p>
      {state && (
        <p role="status" data-testid="remote-alert-email-state" className="text-[0.8125rem]">
          {t(`settings.remote.alertEmail.state.${state}`)}
        </p>
      )}
      <form onSubmit={submit} noValidate className="flex flex-wrap items-end gap-3">
        <div className="min-w-[14rem] flex-1 space-y-1">
          <Label htmlFor="remote-alert-email-input">{t('settings.remote.alertEmail.label')}</Label>
          <Input
            id="remote-alert-email-input"
            type="email"
            dir="ltr"
            value={email}
            maxLength={REMOTE_ALERT_EMAIL_MAX}
            autoComplete="email"
            autoCapitalize="none"
            spellCheck={false}
            placeholder="you@example.org"
            onChange={(event) => setEmail(event.target.value)}
          />
        </div>
        <Button type="submit" disabled={busy || !email.trim()}>
          {busy ? t('settings.remote.alertEmail.saving') : t('settings.remote.alertEmail.save')}
        </Button>
        {state && state !== 'none' && (
          <Button
            type="button"
            variant="outline"
            disabled={busy}
            onClick={() =>
              void act(() => api.clearRemoteAlertEmail(), 'settings.remote.alertEmail.removedToast')
            }
          >
            {t('settings.remote.alertEmail.remove')}
          </Button>
        )}
      </form>
      {error && (
        <p role="alert" className="text-sm text-destructive">
          {error}
        </p>
      )}
    </SettingsGroup>
  );
}
