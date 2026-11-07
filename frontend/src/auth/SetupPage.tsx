import { useEffect, useRef, useState, type FormEvent } from 'react';
import { useTranslation } from 'react-i18next';

import { api, ApiError, formatApiError, getApiErrorCode } from '../api';
import { Button } from '../components/ui/button';
import { Input } from '../components/ui/input';
import { Label } from '../components/ui/label';
import { LearnMoreLink } from '../components/settings/LearnMoreLink';
import { AuthLayout } from './AuthLayout';
import { passwordStrength } from './passwordStrength';
import { PASSWORD_MIN_LENGTH, noSessionReason, type AuthSessionInfo } from './types';

/** How often the kiosk screen asks whether the administrator exists yet. */
export const KIOSK_SETUP_POLL_MS = 5000;

/**
 * First run: create the administrator, from the local network. Anywhere else the server
 * refuses, so the page says where to go instead of showing a form.
 */
export function SetupPage({
  setupLocalOnly = false,
  setupLocalUrl,
  kioskScreen = false,
  onDone,
}: {
  setupLocalOnly?: boolean;
  /** Where to go on the local network, when the server can tell. */
  setupLocalUrl?: string;
  /** This browser is the kiosk screen: nobody types on it. */
  kioskScreen?: boolean;
  onDone: (session: AuthSessionInfo) => void;
}) {
  const { t } = useTranslation();
  const [username, setUsername] = useState('');
  const [displayName, setDisplayName] = useState('');
  const [password, setPassword] = useState('');
  const [confirm, setConfirm] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  // The server can refuse a page that loaded as local (a public address declared since).
  const [refused, setRefused] = useState<{ url?: string } | null>(null);

  // The kiosk screen: the administrator is created from a phone. Once that is done, its
  // own session (the launcher's device token) opens the app.
  const onDoneRef = useRef(onDone);
  useEffect(() => {
    onDoneRef.current = onDone;
  });
  useEffect(() => {
    if (!kioskScreen) return;
    const timer = window.setInterval(() => {
      api.getAuthSession().then(
        (session) => {
          if (!session.setup_required) onDoneRef.current(session);
        },
        () => {
          // Server restarting: ask again at the next tick.
        }
      );
    }, KIOSK_SETUP_POLL_MS);
    return () => window.clearInterval(timer);
  }, [kioskScreen]);

  const strength = passwordStrength(password);
  const mismatch = confirm.length > 0 && confirm !== password;
  const ready =
    username.trim().length > 0 &&
    strength !== 'empty' &&
    strength !== 'tooShort' &&
    confirm === password;

  async function submit(event: FormEvent) {
    event.preventDefault();
    if (busy || !ready) return;
    setBusy(true);
    setError(null);
    try {
      const session = await api.authSetup({
        username: username.trim(),
        password,
        ...(displayName.trim() && { display_name: displayName.trim() }),
      });
      const reason = noSessionReason(session);
      if (reason) {
        // The account exists now, but nothing keeps this browser signed in.
        setError(t(`errors.${reason}`));
        setBusy(false);
        return;
      }
      onDone(session);
    } catch (err) {
      setBusy(false);
      if (
        err instanceof ApiError &&
        getApiErrorCode(err.detail)?.code === 'auth_setup_local_only'
      ) {
        setRefused({});
        api.getAuthSession().then(
          (session) => setRefused({ url: session.setup_local_url }),
          () => {
            // The message alone still says what to do.
          }
        );
        return;
      }
      setError(formatApiError(err, t));
    }
  }

  if (setupLocalOnly || refused) {
    const localUrl = setupLocalUrl ?? refused?.url;
    return (
      <AuthLayout
        title={t('auth.setup.localOnly.title')}
        description={t('auth.setup.localOnly.description')}
      >
        <div className="space-y-2 text-center text-sm">
          {localUrl && (
            <p>
              <a href={localUrl} className="font-medium underline underline-offset-2">
                {localUrl}
              </a>
            </p>
          )}
          <p className="text-xs text-muted-foreground">
            <LearnMoreLink page="first-run" />
          </p>
        </div>
      </AuthLayout>
    );
  }

  return (
    <AuthLayout title={t('auth.setup.title')} description={t('auth.setup.description')}>
      {kioskScreen && (
        <div role="status" className="space-y-1 rounded-md border border-border bg-muted/50 p-3">
          <p className="text-sm font-medium">{t('auth.setup.kioskBanner.title')}</p>
          <p className="text-xs text-muted-foreground">{t('auth.setup.kioskBanner.body')}</p>
        </div>
      )}
      <form onSubmit={submit} className="space-y-4" noValidate>
        <div className="space-y-1.5">
          <Label htmlFor="auth-username">{t('auth.username')}</Label>
          <Input
            id="auth-username"
            name="username"
            autoComplete="username"
            autoCapitalize="none"
            autoCorrect="off"
            spellCheck={false}
            autoFocus
            value={username}
            onChange={(event) => setUsername(event.target.value)}
          />
        </div>
        <div className="space-y-1.5">
          <Label htmlFor="auth-display-name">{t('auth.setup.displayName')}</Label>
          <Input
            id="auth-display-name"
            name="display-name"
            autoComplete="name"
            value={displayName}
            onChange={(event) => setDisplayName(event.target.value)}
          />
        </div>
        <div className="space-y-1.5">
          <Label htmlFor="auth-password">{t('auth.password')}</Label>
          <Input
            id="auth-password"
            name="new-password"
            type="password"
            autoComplete="new-password"
            value={password}
            onChange={(event) => setPassword(event.target.value)}
            aria-describedby="auth-password-hint"
          />
          <div id="auth-password-hint" className="space-y-0.5 text-xs text-muted-foreground">
            <p>{t('auth.setup.passwordPolicy', { min_length: PASSWORD_MIN_LENGTH })}</p>
            <p aria-live="polite" data-strength={strength}>
              {strength !== 'empty' && t(`auth.setup.strength.${strength}`)}
            </p>
          </div>
        </div>
        <div className="space-y-1.5">
          <Label htmlFor="auth-password-confirm">{t('auth.setup.confirmPassword')}</Label>
          <Input
            id="auth-password-confirm"
            name="confirm-password"
            type="password"
            autoComplete="new-password"
            value={confirm}
            onChange={(event) => setConfirm(event.target.value)}
            aria-invalid={mismatch}
          />
          {mismatch && (
            <p className="text-xs text-destructive">{t('auth.setup.passwordMismatch')}</p>
          )}
        </div>
        {error && (
          <p role="alert" className="text-sm text-destructive">
            {error}
          </p>
        )}
        <Button type="submit" className="w-full" disabled={busy || !ready}>
          {t('auth.setup.submit')}
        </Button>
      </form>
    </AuthLayout>
  );
}
