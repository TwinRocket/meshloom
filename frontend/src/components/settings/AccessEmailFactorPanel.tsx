import { useEffect, useState, type FormEvent } from 'react';
import { useTranslation } from 'react-i18next';

import { api } from '../../api';
import { formatAccessError } from '../../auth/accessErrors';
import { useSession } from '../../auth/SessionProvider';
import type { EmailFactorStatus } from '../../auth/types';
import { Button } from '../ui/button';
import { Checkbox } from '../ui/checkbox';
import { Input } from '../ui/input';
import { Label } from '../ui/label';
import { toast } from '../ui/sonner';
import { SettingsGroup, SettingsGroupHeader } from './settingsPrimitives';

/**
 * Users & Access → E-mail sign-in, the caller's own (MSHL-426): verify an address with a
 * code sent to it, then opt in to an e-mail code after the password and to sign-in links.
 * Every change takes the current password; turning one on also takes a TOTP or recovery
 * code when TOTP is on (the server asks, 403 `auth_2fa_required`). The mails go through
 * the channel an administrator picks in Settings → Connection → E-mail sign-in; without
 * one the panel says so.
 */
export function AccessEmailFactorPanel() {
  const { t, i18n } = useTranslation();
  const { user, via } = useSession();
  const totpOn = user?.totp_enabled === true;
  const [status, setStatus] = useState<EmailFactorStatus | null>(null);
  const [editing, setEditing] = useState(false);
  const [address, setAddress] = useState('');
  const [password, setPassword] = useState('');
  const [totpCode, setTotpCode] = useState('');
  const [code, setCode] = useState('');
  const [otp, setOtp] = useState(false);
  const [link, setLink] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const apply = (next: EmailFactorStatus) => {
    setStatus(next);
    setOtp(next.otp_enabled);
    setLink(next.magic_link_enabled);
  };

  useEffect(() => {
    if (via === 'ingress') return;
    let cancelled = false;
    api
      .getEmailFactor()
      .then((next) => {
        if (!cancelled) apply(next);
      })
      .catch(() => {
        // Not readable (older server, refused): the panel stays hidden.
      });
    return () => {
      cancelled = true;
    };
  }, [via]);

  if (!status || !status.applicable) return null;

  const run = async (action: () => Promise<void>) => {
    if (busy) return;
    setBusy(true);
    setError(null);
    try {
      await action();
      setPassword('');
      setTotpCode('');
    } catch (err) {
      setError(formatAccessError(err, t));
    } finally {
      setBusy(false);
    }
  };

  const sendCode = (event: FormEvent) => {
    event.preventDefault();
    void run(async () => {
      await api.startEmailAddress({
        password,
        address: address.trim(),
        ...(totpOn && totpCode.trim() ? { code: totpCode.trim() } : {}),
        lang: i18n.language,
      });
      setStatus({ ...status, pending_address: address.trim() });
      setCode('');
    });
  };

  const verify = (event: FormEvent) => {
    event.preventDefault();
    void run(async () => {
      apply(await api.verifyEmailAddress(code.trim()));
      setEditing(false);
      setCode('');
      toast.success(t('settings.access.emailFactor.verified'));
    });
  };

  const save = (event: FormEvent) => {
    event.preventDefault();
    void run(async () => {
      apply(
        await api.patchEmailFactor({
          password,
          ...(totpOn && totpCode.trim() ? { code: totpCode.trim() } : {}),
          otp_enabled: otp,
          magic_link_enabled: link,
        })
      );
      toast.success(t('settings.access.emailFactor.saved'));
    });
  };

  const remove = () => {
    void run(async () => {
      await api.deleteEmailFactor(password);
      apply({
        ...status,
        address: null,
        otp_enabled: false,
        magic_link_enabled: false,
        pending_address: null,
      });
      toast.success(t('settings.access.emailFactor.removed'));
    });
  };

  const passwordField = (
    <div className="space-y-1.5">
      <Label htmlFor="email-factor-password">{t('auth.totp.currentPassword')}</Label>
      <Input
        id="email-factor-password"
        type="password"
        autoComplete="current-password"
        value={password}
        onChange={(event) => setPassword(event.target.value)}
      />
    </div>
  );
  const totpField = totpOn && (
    <div className="space-y-1.5">
      <Label htmlFor="email-factor-totp">{t('settings.access.emailFactor.totpCode')}</Label>
      <Input
        id="email-factor-totp"
        autoComplete="one-time-code"
        spellCheck={false}
        className="font-mono tracking-wider"
        value={totpCode}
        onChange={(event) => setTotpCode(event.target.value)}
      />
    </div>
  );
  const errorLine = error && (
    <p role="alert" className="text-sm text-destructive">
      {error}
    </p>
  );

  const showAddressForm = !status.address || editing;
  const pending = status.pending_address;

  return (
    <SettingsGroup>
      <SettingsGroupHeader
        id="access-email-factor"
        title={t('settings.access.emailFactor.title')}
        storedOn="server"
      />
      <p className="text-[0.8125rem] text-muted-foreground">
        {t('settings.access.emailFactor.help')}
      </p>
      {!status.available && (
        <p role="note" className="text-sm text-muted-foreground">
          {t('settings.access.emailFactor.unavailable')}
        </p>
      )}
      {status.address && !editing && (
        <form onSubmit={save} className="space-y-3" noValidate>
          <p className="text-sm">
            {t('settings.access.emailFactor.address')}{' '}
            <span className="font-medium">{status.address}</span>
          </p>
          <div className="flex items-start gap-2">
            <Checkbox
              id="email-factor-otp"
              className="mt-0.5"
              checked={otp}
              onCheckedChange={(value) => setOtp(value === true)}
            />
            <div className="space-y-0.5">
              <Label htmlFor="email-factor-otp" className="font-normal">
                {t('settings.access.emailFactor.otp')}
              </Label>
              <p className="text-xs text-muted-foreground">
                {t('settings.access.emailFactor.otpHelp')}
              </p>
            </div>
          </div>
          <div className="flex items-start gap-2">
            <Checkbox
              id="email-factor-link"
              className="mt-0.5"
              checked={link}
              onCheckedChange={(value) => setLink(value === true)}
            />
            <div className="space-y-0.5">
              <Label htmlFor="email-factor-link" className="font-normal">
                {t('settings.access.emailFactor.link')}
              </Label>
              <p className="text-xs text-muted-foreground">
                {t('settings.access.emailFactor.linkHelp')}
              </p>
            </div>
          </div>
          <div className="grid gap-3 md:grid-cols-2">
            {passwordField}
            {totpField}
          </div>
          {errorLine}
          <div className="flex flex-wrap gap-2">
            <Button
              type="submit"
              disabled={
                busy ||
                !password ||
                (otp === status.otp_enabled && link === status.magic_link_enabled)
              }
            >
              {t('settings.access.emailFactor.save')}
            </Button>
            <Button
              type="button"
              variant="outline"
              disabled={busy}
              onClick={() => {
                setEditing(true);
                setAddress('');
                setError(null);
              }}
            >
              {t('settings.access.emailFactor.change')}
            </Button>
            <Button type="button" variant="ghost" disabled={busy || !password} onClick={remove}>
              {t('settings.access.emailFactor.remove')}
            </Button>
          </div>
        </form>
      )}
      {showAddressForm && status.available && (
        <form onSubmit={sendCode} className="space-y-3" noValidate>
          <div className="space-y-1.5">
            <Label htmlFor="email-factor-address">
              {t('settings.access.emailFactor.newAddress')}
            </Label>
            <Input
              id="email-factor-address"
              type="email"
              autoComplete="email"
              value={address}
              onChange={(event) => setAddress(event.target.value)}
            />
          </div>
          <div className="grid gap-3 md:grid-cols-2">
            {passwordField}
            {totpField}
          </div>
          {!pending && errorLine}
          <div className="flex flex-wrap gap-2">
            <Button type="submit" disabled={busy || !password || !address.trim()}>
              {t('settings.access.emailFactor.sendCode')}
            </Button>
            {editing && (
              <Button type="button" variant="ghost" onClick={() => setEditing(false)}>
                {t('auth.totp.cancel')}
              </Button>
            )}
          </div>
        </form>
      )}
      {showAddressForm && pending && (
        <form onSubmit={verify} className="space-y-3" noValidate>
          <p className="text-sm">
            {t('settings.access.emailFactor.codeSent', { address: pending })}
          </p>
          <div className="space-y-1.5">
            <Label htmlFor="email-factor-code">{t('settings.access.emailFactor.code')}</Label>
            <Input
              id="email-factor-code"
              autoComplete="one-time-code"
              inputMode="numeric"
              spellCheck={false}
              className="w-40 font-mono tracking-wider"
              value={code}
              onChange={(event) => setCode(event.target.value)}
            />
          </div>
          {errorLine}
          <Button type="submit" disabled={busy || !code.trim()}>
            {t('settings.access.emailFactor.confirm')}
          </Button>
        </form>
      )}
    </SettingsGroup>
  );
}
