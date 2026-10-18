import { useEffect, useState, type FormEvent } from 'react';
import { useTranslation } from 'react-i18next';

import { api, ApiError, formatApiError, getApiErrorCode } from '../api';
import { Button } from '../components/ui/button';
import { Input } from '../components/ui/input';
import { Label } from '../components/ui/label';
import type { AuthSessionInfo, SecondFactorChallenge } from './types';

/**
 * The second step of a sign-in: the password checked, a TOTP code (or a recovery code)
 * opens the session. The pending login lives `expires_in` seconds on the server; when it
 * runs out, or the server says it is gone, the reader goes back to the password.
 */
export function SecondFactorStep({
  challenge,
  onDone,
  onRestart,
}: {
  challenge: SecondFactorChallenge;
  onDone: (session: AuthSessionInfo) => void;
  /** Back to the password step; `reason` is shown there. */
  onRestart: (reason?: string) => void;
}) {
  const { t } = useTranslation();
  const [code, setCode] = useState('');
  const [recovery, setRecovery] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const canRecover = challenge.methods.includes('recovery');

  // The password step is only good for `expires_in` seconds: do not let the reader type
  // a code into a form the server already forgot.
  useEffect(() => {
    const timer = window.setTimeout(
      () => onRestart(t('auth.secondFactor.expired')),
      Math.max(1, challenge.expires_in) * 1000
    );
    return () => window.clearTimeout(timer);
  }, [challenge, onRestart, t]);

  async function submit(event: FormEvent) {
    event.preventDefault();
    if (busy || !code.trim()) return;
    setBusy(true);
    setError(null);
    try {
      const session = await api.authLogin2fa({
        pending_token: challenge.pending_token,
        code: code.trim(),
      });
      if (!session.authenticated) {
        setError(t('errors.auth_no_session'));
        setBusy(false);
        return;
      }
      onDone(session);
    } catch (err) {
      if (
        err instanceof ApiError &&
        getApiErrorCode(err.detail)?.code === 'auth_2fa_pending_invalid'
      ) {
        onRestart(formatApiError(err, t));
        return;
      }
      setError(formatApiError(err, t));
      setCode('');
      setBusy(false);
    }
  }

  return (
    <form onSubmit={submit} className="space-y-4" noValidate>
      <p className="text-sm text-muted-foreground">
        {t(recovery ? 'auth.secondFactor.recoveryHelp' : 'auth.secondFactor.help')}
      </p>
      <div className="space-y-1.5">
        <Label htmlFor="auth-second-factor-code">
          {t(recovery ? 'auth.secondFactor.recoveryLabel' : 'auth.secondFactor.codeLabel')}
        </Label>
        <Input
          id="auth-second-factor-code"
          name="code"
          key={recovery ? 'recovery' : 'totp'}
          autoComplete="one-time-code"
          autoCapitalize="none"
          autoCorrect="off"
          spellCheck={false}
          autoFocus
          inputMode={recovery ? 'text' : 'numeric'}
          className="font-mono tracking-wider"
          value={code}
          onChange={(event) => setCode(event.target.value)}
        />
      </div>
      {error && (
        <p role="alert" className="text-sm text-destructive">
          {error}
        </p>
      )}
      <Button type="submit" className="w-full" disabled={busy || !code.trim()}>
        {t('auth.secondFactor.submit')}
      </Button>
      <div className="flex flex-wrap justify-between gap-2 text-sm">
        {canRecover && (
          <button
            type="button"
            className="underline underline-offset-2 hover:text-primary"
            onClick={() => {
              setRecovery((value) => !value);
              setCode('');
              setError(null);
            }}
          >
            {t(recovery ? 'auth.secondFactor.useApp' : 'auth.secondFactor.useRecovery')}
          </button>
        )}
        <button
          type="button"
          className="underline underline-offset-2 hover:text-primary"
          onClick={() => onRestart()}
        >
          {t('auth.secondFactor.back')}
        </button>
      </div>
    </form>
  );
}
