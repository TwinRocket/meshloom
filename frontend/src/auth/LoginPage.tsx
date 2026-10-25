import { useCallback, useState, type FormEvent } from 'react';
import { useTranslation } from 'react-i18next';
import { KeyRound, TriangleAlert } from 'lucide-react';

import { api, formatApiError } from '../api';
import { Button } from '../components/ui/button';
import { Checkbox } from '../components/ui/checkbox';
import { Input } from '../components/ui/input';
import { Label } from '../components/ui/label';
import { AuthLayout } from './AuthLayout';
import {
  formatPasskeyError,
  passkeyLoginAvailable,
  passkeyUnavailableHere,
  signInWithPasskey,
} from './passkeys';
import { SecondFactorStep } from './SecondFactorStep';
import {
  behindUntrustedHttpsProxy,
  noSessionReason,
  type AuthSessionInfo,
  type SecondFactorChallenge,
} from './types';

/** The sign-in form fields, shared with the re-login dialog. */
export function LoginForm({
  initialUsername = '',
  autoFocusPassword = false,
  passkey = false,
  onDone,
}: {
  initialUsername?: string;
  autoFocusPassword?: boolean;
  /** Offer "Sign in with a passkey" (the server and the browser both support it). */
  passkey?: boolean;
  onDone: (session: AuthSessionInfo) => void;
}) {
  const { t } = useTranslation();
  const [username, setUsername] = useState(initialUsername);
  const [password, setPassword] = useState('');
  const [remember, setRemember] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  // The password checked and the account owes a code: the second step replaces the form.
  const [challenge, setChallenge] = useState<SecondFactorChallenge | null>(null);

  const restart = useCallback((reason?: string) => {
    setChallenge(null);
    setBusy(false);
    setError(reason ?? null);
  }, []);

  async function submit(event: FormEvent) {
    event.preventDefault();
    if (busy) return;
    setBusy(true);
    setError(null);
    try {
      const session = await api.authLogin({ username: username.trim(), password, remember });
      if (!session.authenticated && session.second_factor) {
        setChallenge(session.second_factor);
        setPassword('');
        setBusy(false);
        return;
      }
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

  async function submitPasskey() {
    if (busy) return;
    setBusy(true);
    setError(null);
    try {
      const session = await signInWithPasskey(remember);
      const reason = noSessionReason(session);
      if (reason) {
        setError(t(`errors.${reason}`));
        setBusy(false);
        return;
      }
      onDone(session);
    } catch (err) {
      setError(formatPasskeyError(err, t));
      setBusy(false);
    }
  }

  if (challenge) {
    return <SecondFactorStep challenge={challenge} onDone={onDone} onRestart={restart} />;
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
      <div className="flex items-start gap-2">
        <Checkbox
          id="auth-remember"
          className="mt-0.5"
          checked={remember}
          onCheckedChange={(value) => setRemember(value === true)}
          aria-describedby="auth-remember-hint"
        />
        <div className="space-y-0.5">
          <Label htmlFor="auth-remember" className="font-normal">
            {t('auth.login.remember')}
          </Label>
          <p id="auth-remember-hint" className="text-xs text-muted-foreground">
            {t('auth.login.rememberHint')}
          </p>
        </div>
      </div>
      {error && (
        <p role="alert" className="text-sm text-destructive">
          {error}
        </p>
      )}
      <Button type="submit" className="w-full" disabled={busy || !username.trim() || !password}>
        {t('auth.login.submit')}
      </Button>
      {passkey && (
        <>
          <div className="flex items-center gap-3 text-xs text-muted-foreground" aria-hidden="true">
            <span className="h-px flex-1 bg-border" />
            {t('auth.passkey.or')}
            <span className="h-px flex-1 bg-border" />
          </div>
          <Button
            type="button"
            variant="outline"
            className="w-full"
            disabled={busy}
            onClick={() => void submitPasskey()}
          >
            <KeyRound className="mr-2 h-4 w-4" aria-hidden="true" />
            {t('auth.passkey.signIn')}
          </Button>
        </>
      )}
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
          <p>
            {t('auth.login.insecureContext')} {t('auth.passkey.insecureHint')}
          </p>
        </div>
      )}
      <LoginForm onDone={onDone} passkey={passkeyLoginAvailable(session)} />
      {passkeyUnavailableHere(session) && (
        <p role="note" className="text-xs text-muted-foreground">
          {t('auth.passkey.unavailableHint')}
        </p>
      )}
    </AuthLayout>
  );
}
