import { useState, type FormEvent } from 'react';
import { useTranslation } from 'react-i18next';
import { TriangleAlert } from 'lucide-react';

import { api, formatApiError } from '../api';
import { Button } from '../components/ui/button';
import { Input } from '../components/ui/input';
import { Label } from '../components/ui/label';
import { AuthLayout } from './AuthLayout';
import { behindUntrustedHttpsProxy, noSessionReason, type AuthSessionInfo } from './types';

/** The sign-in form fields, shared with the re-login dialog. */
export function LoginForm({
  initialUsername = '',
  autoFocusPassword = false,
  onDone,
}: {
  initialUsername?: string;
  autoFocusPassword?: boolean;
  onDone: (session: AuthSessionInfo) => void;
}) {
  const { t } = useTranslation();
  const [username, setUsername] = useState(initialUsername);
  const [password, setPassword] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function submit(event: FormEvent) {
    event.preventDefault();
    if (busy) return;
    setBusy(true);
    setError(null);
    try {
      const session = await api.authLogin({ username: username.trim(), password });
      const reason = noSessionReason(session);
      if (reason) {
        setError(t(`errors.${reason}`));
        setPassword('');
        setBusy(false);
        return;
      }
      onDone(session);
    } catch (err) {
      setError(formatApiError(err, t));
      setPassword('');
      setBusy(false);
    }
  }

  return (
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
          autoFocus={!autoFocusPassword}
          value={username}
          onChange={(event) => setUsername(event.target.value)}
        />
      </div>
      <div className="space-y-1.5">
        <Label htmlFor="auth-password">{t('auth.password')}</Label>
        <Input
          id="auth-password"
          name="password"
          type="password"
          autoComplete="current-password"
          autoFocus={autoFocusPassword}
          value={password}
          onChange={(event) => setPassword(event.target.value)}
        />
      </div>
      {error && (
        <p role="alert" className="text-sm text-destructive">
          {error}
        </p>
      )}
      <Button type="submit" className="w-full" disabled={busy || !username.trim() || !password}>
        {t('auth.login.submit')}
      </Button>
    </form>
  );
}

/** Shown to an anonymous visitor once an account exists. */
export function LoginPage({
  onDone,
  session,
}: {
  onDone: (session: AuthSessionInfo) => void;
  /** The anonymous `GET /auth/session` answer, for the untrusted-proxy hint. */
  session?: AuthSessionInfo;
}) {
  const { t } = useTranslation();
  return (
    <AuthLayout title={t('auth.login.title')} description={t('auth.login.description')}>
      {behindUntrustedHttpsProxy(session) && (
        <div
          role="note"
          className="flex gap-2 rounded-md border border-border bg-muted/50 p-3 text-xs text-muted-foreground"
        >
          <TriangleAlert className="mt-0.5 h-4 w-4 shrink-0" aria-hidden="true" />
          <p>{t('auth.login.untrustedProxy')}</p>
        </div>
      )}
      {!window.isSecureContext && (
        <div
          role="note"
          className="flex gap-2 rounded-md border border-border bg-muted/50 p-3 text-xs text-muted-foreground"
        >
          <TriangleAlert className="mt-0.5 h-4 w-4 shrink-0" aria-hidden="true" />
          <p>{t('auth.login.insecureContext')}</p>
        </div>
      )}
      <LoginForm onDone={onDone} />
    </AuthLayout>
  );
}
