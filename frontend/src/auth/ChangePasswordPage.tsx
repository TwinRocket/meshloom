import { useState, type FormEvent } from 'react';
import { useTranslation } from 'react-i18next';

import { api, formatApiError, getApiErrorCode, ApiError } from '../api';
import { Button } from '../components/ui/button';
import { Input } from '../components/ui/input';
import { Label } from '../components/ui/label';
import { AuthLayout } from './AuthLayout';
import { passwordStrength } from './passwordStrength';
import { PASSWORD_MIN_LENGTH } from './types';

/**
 * Shown instead of the app while the account must change its password (an administrator
 * set it): the server refuses every other route until `PATCH /auth/me` succeeds.
 */
export function ChangePasswordPage({
  username,
  role,
  onChanged,
  onSignOut,
}: {
  username: string;
  role?: string;
  /** The password changed: re-read the session. */
  onChanged: () => void;
  onSignOut?: () => void;
}) {
  const { t } = useTranslation();
  const [current, setCurrent] = useState('');
  const [next, setNext] = useState('');
  const [confirm, setConfirm] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [policy, setPolicy] = useState(false);

  const strength = passwordStrength(next);
  const mismatch = confirm.length > 0 && confirm !== next;
  const ready =
    current.length > 0 &&
    strength !== 'empty' &&
    strength !== 'tooShort' &&
    confirm === next &&
    next !== current;

  async function submit(event: FormEvent) {
    event.preventDefault();
    if (busy || !ready) return;
    setBusy(true);
    setError(null);
    setPolicy(false);
    try {
      await api.changeOwnPassword(current, next);
      onChanged();
    } catch (err) {
      setError(formatApiError(err, t));
      setPolicy(
        err instanceof ApiError && getApiErrorCode(err.detail)?.code === 'auth_password_policy'
      );
      setBusy(false);
    }
  }

  if (role === 'kiosk') {
    // A display has no password to change (the server never sets the flag on one).
    return (
      <AuthLayout
        title={t('auth.changePassword.kioskTitle')}
        description={t('auth.changePassword.kioskMessage')}
      >
        {onSignOut && (
          <Button type="button" variant="outline" className="w-full" onClick={onSignOut}>
            {t('auth.userMenu.signOut')}
          </Button>
        )}
      </AuthLayout>
    );
  }

  return (
    <AuthLayout
      title={t('auth.changePassword.title')}
      description={t('auth.changePassword.description', { username })}
    >
      <form onSubmit={submit} className="space-y-4" noValidate>
        <div className="space-y-1.5">
          <Label htmlFor="auth-current-password">{t('auth.changePassword.current')}</Label>
          <Input
            id="auth-current-password"
            name="current-password"
            type="password"
            autoComplete="current-password"
            autoFocus
            value={current}
            onChange={(event) => setCurrent(event.target.value)}
          />
        </div>
        <div className="space-y-1.5">
          <Label htmlFor="auth-new-password">{t('auth.changePassword.new')}</Label>
          <Input
            id="auth-new-password"
            name="new-password"
            type="password"
            autoComplete="new-password"
            value={next}
            onChange={(event) => setNext(event.target.value)}
            aria-describedby="auth-new-password-hint"
          />
          <div id="auth-new-password-hint" className="space-y-0.5 text-xs text-muted-foreground">
            <p>{t('auth.setup.passwordPolicy', { min_length: PASSWORD_MIN_LENGTH })}</p>
            <p aria-live="polite" data-strength={strength}>
              {strength !== 'empty' && t(`auth.setup.strength.${strength}`)}
            </p>
          </div>
        </div>
        <div className="space-y-1.5">
          <Label htmlFor="auth-confirm-new-password">{t('auth.setup.confirmPassword')}</Label>
          <Input
            id="auth-confirm-new-password"
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
          <div role="alert" className="space-y-1 text-sm text-destructive">
            <p>{error}</p>
            {policy && (
              <p className="text-xs">
                {t('errors.auth_password_policyDetail', { min_length: PASSWORD_MIN_LENGTH })}
              </p>
            )}
          </div>
        )}
        <Button type="submit" className="w-full" disabled={busy || !ready}>
          {t('auth.changePassword.submit')}
        </Button>
        {onSignOut && (
          <Button type="button" variant="ghost" className="w-full" onClick={onSignOut}>
            {t('auth.userMenu.signOut')}
          </Button>
        )}
      </form>
    </AuthLayout>
  );
}
