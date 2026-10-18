import { useState, type FormEvent } from 'react';
import { useTranslation } from 'react-i18next';
import { QRCodeSVG } from 'qrcode.react';

import { api } from '../api';
import { Button } from '../components/ui/button';
import { Input } from '../components/ui/input';
import { Label } from '../components/ui/label';
import { formatAccessError } from './accessErrors';
import { RecoveryCodesView } from './RecoveryCodesView';
import type { TotpEnrolment as Enrolment } from './types';

type Step =
  | { name: 'password' }
  | { name: 'code'; enrolment: Enrolment }
  | { name: 'codes'; codes: string[] };

/**
 * Turning the second factor on, in the order the server needs it: the password (a stolen
 * session must not be able to lock the owner out), the QR code and the manual key, the
 * first code, then the recovery codes. The QR code is drawn here from the `otpauth://`
 * URI: the secret never goes to a third-party service.
 */
export function TotpEnrolment({
  onFinished,
  onCancel,
}: {
  /** The factor is on and the recovery codes were acknowledged. */
  onFinished: () => void;
  onCancel?: () => void;
}) {
  const { t } = useTranslation();
  const [step, setStep] = useState<Step>({ name: 'password' });
  const [password, setPassword] = useState('');
  const [code, setCode] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const start = async (event: FormEvent) => {
    event.preventDefault();
    if (busy || !password) return;
    setBusy(true);
    setError(null);
    try {
      const enrolment = await api.enrolTotp(password);
      setPassword('');
      setStep({ name: 'code', enrolment });
    } catch (err) {
      setError(formatAccessError(err, t));
      setPassword('');
    } finally {
      setBusy(false);
    }
  };

  const confirm = async (event: FormEvent) => {
    event.preventDefault();
    if (busy || !code.trim()) return;
    setBusy(true);
    setError(null);
    try {
      const { recovery_codes } = await api.confirmTotp(code.trim());
      setCode('');
      setStep({ name: 'codes', codes: recovery_codes });
    } catch (err) {
      setError(formatAccessError(err, t));
      setCode('');
    } finally {
      setBusy(false);
    }
  };

  if (step.name === 'codes') {
    return <RecoveryCodesView codes={step.codes} onDone={onFinished} />;
  }

  if (step.name === 'password') {
    return (
      <form onSubmit={start} className="space-y-3" noValidate>
        <p className="text-sm text-muted-foreground">{t('auth.totp.passwordHelp')}</p>
        <div className="space-y-1.5">
          <Label htmlFor="totp-enrol-password">{t('auth.totp.currentPassword')}</Label>
          <Input
            id="totp-enrol-password"
            type="password"
            autoComplete="current-password"
            autoFocus
            value={password}
            onChange={(event) => setPassword(event.target.value)}
          />
        </div>
        {error && (
          <p role="alert" className="text-sm text-destructive">
            {error}
          </p>
        )}
        <div className="flex flex-wrap gap-2">
          <Button type="submit" disabled={busy || !password}>
            {t('auth.totp.start')}
          </Button>
          {onCancel && (
            <Button type="button" variant="ghost" onClick={onCancel}>
              {t('auth.totp.cancel')}
            </Button>
          )}
        </div>
      </form>
    );
  }

  const { enrolment } = step;
  return (
    <form onSubmit={confirm} className="space-y-3" noValidate>
      <p className="text-sm text-muted-foreground">{t('auth.totp.scanHelp')}</p>
      <div className="flex justify-center">
        <div className="rounded-md bg-white p-3" data-testid="totp-qr">
          <QRCodeSVG
            value={enrolment.otpauth_uri}
            size={176}
            level="M"
            title={t('auth.totp.qrTitle')}
          />
        </div>
      </div>
      <div className="space-y-1.5">
        <Label htmlFor="totp-manual-key">{t('auth.totp.manualKey')}</Label>
        <Input
          id="totp-manual-key"
          readOnly
          autoComplete="off"
          spellCheck={false}
          className="font-mono"
          value={enrolment.secret}
          onFocus={(event) => event.currentTarget.select()}
        />
        <p className="text-[0.8125rem] text-muted-foreground">
          {t('auth.totp.manualHelp', { digits: enrolment.digits, period: enrolment.period })}
        </p>
      </div>
      <div className="space-y-1.5">
        <Label htmlFor="totp-confirm-code">{t('auth.totp.confirmCode')}</Label>
        <Input
          id="totp-confirm-code"
          autoComplete="one-time-code"
          inputMode="numeric"
          spellCheck={false}
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
      <div className="flex flex-wrap gap-2">
        <Button type="submit" disabled={busy || !code.trim()}>
          {t('auth.totp.confirm')}
        </Button>
        {onCancel && (
          <Button type="button" variant="ghost" onClick={onCancel}>
            {t('auth.totp.cancel')}
          </Button>
        )}
      </div>
    </form>
  );
}
