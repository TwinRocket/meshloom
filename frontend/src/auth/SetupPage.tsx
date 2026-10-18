import { useState, type FormEvent } from 'react';
import { useTranslation } from 'react-i18next';

import { api, formatApiError } from '../api';
import { Button } from '../components/ui/button';
import { Input } from '../components/ui/input';
import { Label } from '../components/ui/label';
import { AuthLayout } from './AuthLayout';
import { passwordStrength } from './passwordStrength';
import { PASSWORD_MIN_LENGTH, type AuthSessionInfo } from './types';

/**
 * First run: create the administrator. A database that existed before the upgrade also
 * asks for the installation code the server printed, which proves the visitor can read
 * the server's log and is not just the first one to find the page.
 */
export function SetupPage({
  setupCodeRequired,
  onDone,
}: {
  setupCodeRequired: boolean;
  onDone: (session: AuthSessionInfo) => void;
}) {
  const { t } = useTranslation();
  const [username, setUsername] = useState('');
  const [displayName, setDisplayName] = useState('');
  const [password, setPassword] = useState('');
  const [confirm, setConfirm] = useState('');
  const [setupCode, setSetupCode] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const strength = passwordStrength(password);
  const mismatch = confirm.length > 0 && confirm !== password;
  const ready =
    username.trim().length > 0 &&
    strength !== 'empty' &&
    strength !== 'tooShort' &&
    confirm === password &&
    (!setupCodeRequired || setupCode.trim().length > 0);

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
        ...(setupCodeRequired && { setup_code: setupCode.trim() }),
      });
      onDone(session);
    } catch (err) {
      setError(formatApiError(err, t));
      setBusy(false);
    }
  }

  return (
    <AuthLayout title={t('auth.setup.title')} description={t('auth.setup.description')}>
      <form onSubmit={submit} className="space-y-4" noValidate>
        {setupCodeRequired && (
          <div className="space-y-1.5">
            <Label htmlFor="auth-setup-code">{t('auth.setup.code')}</Label>
            <Input
              id="auth-setup-code"
              name="setup-code"
              inputMode="numeric"
              autoComplete="one-time-code"
              autoFocus
              value={setupCode}
              onChange={(event) => setSetupCode(event.target.value)}
              aria-describedby="auth-setup-code-hint"
            />
            <div id="auth-setup-code-hint" className="space-y-1 text-xs text-muted-foreground">
              <p>{t('auth.setup.codeHint')}</p>
              <ul className="list-disc space-y-0.5 pl-4">
                <li>{t('auth.setup.codeWhereDocker')}</li>
                <li>{t('auth.setup.codeWhereSystemd')}</li>
                <li>{t('auth.setup.codeWhereAddon')}</li>
                <li>{t('auth.setup.codeWhereCli')}</li>
              </ul>
            </div>
          </div>
        )}
        <div className="space-y-1.5">
          <Label htmlFor="auth-username">{t('auth.username')}</Label>
          <Input
            id="auth-username"
            name="username"
            autoComplete="username"
            autoCapitalize="none"
            autoCorrect="off"
            spellCheck={false}
            autoFocus={!setupCodeRequired}
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
