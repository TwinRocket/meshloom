import { useEffect, useState, type FormEvent } from 'react';
import { useTranslation } from 'react-i18next';

import { api } from '../../api';
import { formatAccessError } from '../../auth/accessErrors';
import { RecoveryCodesView } from '../../auth/RecoveryCodesView';
import { useCapabilities, useSession } from '../../auth/SessionProvider';
import { TotpEnrolment } from '../../auth/TotpEnrolment';
import type { AuthMe, TotpStatus } from '../../auth/types';
import { Button } from '../ui/button';
import { Checkbox } from '../ui/checkbox';
import { Input } from '../ui/input';
import { Label } from '../ui/label';
import { toast } from '../ui/sonner';
import { SettingsGroup, SettingsGroupHeader } from './settingsPrimitives';

type Mode = 'idle' | 'enrol' | 'disable' | 'regenerate';

/** Password and a code, for the two actions the server guards with both. */
function ProofForm({
  idPrefix,
  submitLabel,
  recoveryAllowed,
  onSubmit,
  onCancel,
}: {
  idPrefix: string;
  submitLabel: string;
  /** Disabling accepts a recovery code; regenerating needs the authenticator's. */
  recoveryAllowed: boolean;
  onSubmit: (password: string, code: string) => Promise<void>;
  onCancel: () => void;
}) {
  const { t } = useTranslation();
  const [password, setPassword] = useState('');
  const [code, setCode] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const submit = async (event: FormEvent) => {
    event.preventDefault();
    if (busy || !password || !code.trim()) return;
    setBusy(true);
    setError(null);
    try {
      await onSubmit(password, code.trim());
    } catch (err) {
      setError(formatAccessError(err, t));
      setPassword('');
      setCode('');
      setBusy(false);
    }
  };

  return (
    <form onSubmit={submit} className="space-y-3" noValidate>
      <div className="grid gap-3 md:grid-cols-2">
        <div className="space-y-1.5">
          <Label htmlFor={`${idPrefix}-password`}>{t('auth.totp.currentPassword')}</Label>
          <Input
            id={`${idPrefix}-password`}
            type="password"
            autoComplete="current-password"
            value={password}
            onChange={(event) => setPassword(event.target.value)}
          />
        </div>
        <div className="space-y-1.5">
          <Label htmlFor={`${idPrefix}-code`}>{t('auth.totp.codeField')}</Label>
          <Input
            id={`${idPrefix}-code`}
            autoComplete="one-time-code"
            spellCheck={false}
            className="font-mono tracking-wider"
            value={code}
            onChange={(event) => setCode(event.target.value)}
          />
        </div>
      </div>
      <p className="text-[0.8125rem] text-muted-foreground">
        {t(recoveryAllowed ? 'auth.totp.codeOrRecovery' : 'auth.totp.codeOnly')}
      </p>
      {error && (
        <p role="alert" className="text-sm text-destructive">
          {error}
        </p>
      )}
      <div className="flex flex-wrap gap-2">
        <Button type="submit" disabled={busy || !password || !code.trim()}>
          {submitLabel}
        </Button>
        <Button type="button" variant="ghost" onClick={onCancel}>
          {t('auth.totp.cancel')}
        </Button>
      </div>
    </form>
  );
}

/** Administrator only: `require_2fa_for_admins`. */
function RequireTwoFactorForAdmins({ ownFactorOn }: { ownFactorOn: boolean }) {
  const { t } = useTranslation();
  const [required, setRequired] = useState<boolean | null>(null);
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    let cancelled = false;
    api
      .getAuthSettings()
      .then((settings) => {
        if (!cancelled) setRequired(settings.require_2fa_for_admins ?? false);
      })
      .catch(() => {
        // Not readable (older server, refused): the control stays hidden.
      });
    return () => {
      cancelled = true;
    };
  }, []);

  if (required === null) return null;

  const change = async (next: boolean) => {
    setSaving(true);
    try {
      const saved = await api.patchAuthSettings({ require_2fa_for_admins: next });
      setRequired(saved.require_2fa_for_admins ?? next);
      toast.success(
        t(next ? 'settings.access.security.requireOn' : 'settings.access.security.requireOff')
      );
    } catch (err) {
      toast.error(formatAccessError(err, t));
    } finally {
      setSaving(false);
    }
  };

  return (
    <div className="space-y-1.5 border-t border-border pt-3">
      <div className="flex items-center gap-2">
        <Checkbox
          id="access-require-2fa"
          checked={required}
          disabled={saving}
          onCheckedChange={(checked) => void change(checked === true)}
        />
        <Label htmlFor="access-require-2fa">{t('settings.access.security.require')}</Label>
      </div>
      <p className="text-[0.8125rem] text-muted-foreground">
        {t('settings.access.security.requireHelp')}
      </p>
      {!required && !ownFactorOn && (
        <p className="text-[0.8125rem] text-muted-foreground">
          {t('settings.access.security.requireOwnFirst')}
        </p>
      )}
    </div>
  );
}

/**
 * Settings → Users & Access → Two-factor authentication: the caller's own TOTP factor
 * (enrol, regenerate the recovery codes, disable) and, for an administrator, the
 * instance rule that makes it mandatory for administrators. It does not apply under Home
 * Assistant ingress (Home Assistant already authenticated the user) nor to an account
 * without a password; the panel says why instead of hiding.
 */
export function AccessSecurityPanel() {
  const { t } = useTranslation();
  const { via } = useSession();
  const { isAdmin } = useCapabilities();
  const [me, setMe] = useState<AuthMe | null>(null);
  const [status, setStatus] = useState<TotpStatus | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [mode, setMode] = useState<Mode>('idle');
  const [codes, setCodes] = useState<string[] | null>(null);

  const inIngress = via === 'ingress';
  const applicable = !!me && me.provider === 'local' && me.has_password && !inIngress;

  // Bumped to read the account and its factor again after a change.
  const [version, setVersion] = useState(0);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const account = await api.getAuthMe();
        const factor =
          account.provider === 'local' && account.has_password ? await api.getTotpStatus() : null;
        if (cancelled) return;
        setMe(account);
        setStatus(factor);
        setLoadError(null);
      } catch (err) {
        if (!cancelled) setLoadError(formatAccessError(err, t));
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [t, version]);

  if (loadError) {
    return (
      <SettingsGroup>
        <p role="alert" className="text-sm text-destructive">
          {loadError}
        </p>
      </SettingsGroup>
    );
  }
  if (!me) return null;

  const finish = () => {
    setMode('idle');
    setCodes(null);
    setVersion((value) => value + 1);
  };

  return (
    <SettingsGroup>
      <SettingsGroupHeader
        id="access-security"
        title={t('settings.access.security.title')}
        storedOn="server"
      />
      {!applicable && (
        <p className="text-[0.8125rem] text-muted-foreground" data-testid="totp-not-applicable">
          {t(
            inIngress ? 'settings.access.security.ingress' : 'settings.access.security.noPassword'
          )}
        </p>
      )}
      {applicable && status && codes !== null && (
        <RecoveryCodesView codes={codes} onDone={finish} />
      )}
      {applicable && status && codes === null && (
        <>
          <p className="text-[0.8125rem] text-muted-foreground">
            {t(status.enabled ? 'settings.access.security.on' : 'settings.access.security.off')}
            {status.enabled &&
              ` ${t('settings.access.security.codesLeft', { count: status.recovery_codes_left })}`}
          </p>
          {mode === 'idle' && !status.enabled && (
            <>
              {status.pending && (
                <p className="text-[0.8125rem] text-muted-foreground">
                  {t('settings.access.security.pending')}
                </p>
              )}
              <Button type="button" onClick={() => setMode('enrol')}>
                {t('settings.access.security.enable')}
              </Button>
            </>
          )}
          {mode === 'idle' && status.enabled && (
            <div className="flex flex-wrap gap-2">
              <Button type="button" variant="outline" onClick={() => setMode('regenerate')}>
                {t('settings.access.security.regenerate')}
              </Button>
              <Button type="button" variant="outline" onClick={() => setMode('disable')}>
                {t('settings.access.security.disable')}
              </Button>
            </div>
          )}
          {mode === 'enrol' && (
            <TotpEnrolment onFinished={finish} onCancel={() => setMode('idle')} />
          )}
          {mode === 'regenerate' && (
            <ProofForm
              idPrefix="totp-regenerate"
              submitLabel={t('settings.access.security.regenerateSubmit')}
              recoveryAllowed={false}
              onCancel={() => setMode('idle')}
              onSubmit={async (password, code) => {
                const fresh = await api.regenerateRecoveryCodes(password, code);
                setMode('idle');
                setCodes(fresh.recovery_codes);
              }}
            />
          )}
          {mode === 'disable' && (
            <ProofForm
              idPrefix="totp-disable"
              submitLabel={t('settings.access.security.disableSubmit')}
              recoveryAllowed
              onCancel={() => setMode('idle')}
              onSubmit={async (password, code) => {
                await api.disableTotp(password, code);
                toast.success(t('settings.access.security.disabled'));
                finish();
              }}
            />
          )}
        </>
      )}
      {isAdmin && <RequireTwoFactorForAdmins ownFactorOn={status?.enabled ?? false} />}
    </SettingsGroup>
  );
}
