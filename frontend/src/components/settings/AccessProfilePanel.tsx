import { useEffect, useState, type FormEvent } from 'react';
import { useTranslation } from 'react-i18next';

import { api } from '../../api';
import { formatAccessError } from '../../auth/accessErrors';
import { passwordStrength } from '../../auth/passwordStrength';
import { PASSWORD_MIN_LENGTH, type AuthMe } from '../../auth/types';
import { Button } from '../ui/button';
import { Input } from '../ui/input';
import { Label } from '../ui/label';
import { toast } from '../ui/sonner';
import { SettingsGroup, SettingsGroupHeader } from './settingsPrimitives';

/** The caller's own account: display name, and the password when it has one. */
export function AccessProfilePanel() {
  const { t } = useTranslation();
  const [me, setMe] = useState<AuthMe | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [displayName, setDisplayName] = useState('');
  const [savingName, setSavingName] = useState(false);
  const [current, setCurrent] = useState('');
  const [next, setNext] = useState('');
  const [confirm, setConfirm] = useState('');
  const [savingPassword, setSavingPassword] = useState(false);
  const [passwordError, setPasswordError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    api
      .getAuthMe()
      .then((account) => {
        if (cancelled) return;
        setMe(account);
        setDisplayName(account.display_name);
      })
      .catch((err) => {
        if (!cancelled) setLoadError(formatAccessError(err, t));
      });
    return () => {
      cancelled = true;
    };
  }, [t]);

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

  const nameDirty = displayName.trim() !== me.display_name;
  const strength = passwordStrength(next);
  const passwordReady =
    current.length > 0 &&
    strength !== 'empty' &&
    strength !== 'tooShort' &&
    confirm === next &&
    next !== current;

  const saveName = async (event: FormEvent) => {
    event.preventDefault();
    setSavingName(true);
    try {
      const updated = await api.patchAuthMe({ display_name: displayName.trim() });
      setMe(updated);
      setDisplayName(updated.display_name);
      toast.success(t('settings.access.profile.nameSaved'));
    } catch (err) {
      toast.error(formatAccessError(err, t));
    } finally {
      setSavingName(false);
    }
  };

  const savePassword = async (event: FormEvent) => {
    event.preventDefault();
    if (!passwordReady) return;
    setSavingPassword(true);
    setPasswordError(null);
    try {
      await api.changeOwnPassword(current, next);
      setCurrent('');
      setNext('');
      setConfirm('');
      toast.success(t('settings.access.profile.passwordChanged'));
    } catch (err) {
      setPasswordError(formatAccessError(err, t));
    } finally {
      setSavingPassword(false);
    }
  };

  return (
    <SettingsGroup>
      <SettingsGroupHeader
        id="access-profile"
        title={t('settings.access.profile.title')}
        storedOn="server"
      />
      <p className="text-[0.8125rem] text-muted-foreground">
        {t('settings.access.profile.summary', {
          username: me.username,
          role: t(`auth.role.${me.role}`),
        })}
      </p>
      <form onSubmit={saveName} className="flex flex-wrap items-end gap-2">
        <div className="min-w-48 flex-1 space-y-1.5">
          <Label htmlFor="access-display-name">{t('settings.access.profile.displayName')}</Label>
          <Input
            id="access-display-name"
            value={displayName}
            maxLength={128}
            onChange={(event) => setDisplayName(event.target.value)}
          />
        </div>
        <Button type="submit" disabled={!nameDirty || savingName}>
          {t('settings.access.save')}
        </Button>
      </form>
      {me.has_password ? (
        <form onSubmit={savePassword} className="space-y-3">
          <h4 className="text-sm font-medium">{t('settings.access.profile.passwordTitle')}</h4>
          <div className="grid gap-3 md:grid-cols-3">
            <div className="space-y-1.5">
              <Label htmlFor="access-current-password">
                {t('settings.access.profile.currentPassword')}
              </Label>
              <Input
                id="access-current-password"
                type="password"
                autoComplete="current-password"
                value={current}
                onChange={(event) => setCurrent(event.target.value)}
              />
            </div>
            <div className="space-y-1.5">
              <Label htmlFor="access-new-password">
                {t('settings.access.profile.newPassword')}
              </Label>
              <Input
                id="access-new-password"
                type="password"
                autoComplete="new-password"
                value={next}
                onChange={(event) => setNext(event.target.value)}
              />
            </div>
            <div className="space-y-1.5">
              <Label htmlFor="access-confirm-password">
                {t('settings.access.profile.confirmPassword')}
              </Label>
              <Input
                id="access-confirm-password"
                type="password"
                autoComplete="new-password"
                value={confirm}
                onChange={(event) => setConfirm(event.target.value)}
              />
            </div>
          </div>
          <p className="text-[0.8125rem] text-muted-foreground">
            {t('auth.setup.passwordPolicy', { min_length: PASSWORD_MIN_LENGTH })}
          </p>
          {confirm.length > 0 && confirm !== next && (
            <p className="text-[0.8125rem] text-destructive">{t('auth.setup.passwordMismatch')}</p>
          )}
          {passwordError && (
            <p role="alert" className="text-sm text-destructive">
              {passwordError}
            </p>
          )}
          <Button type="submit" disabled={!passwordReady || savingPassword}>
            {t('settings.access.profile.changePassword')}
          </Button>
        </form>
      ) : (
        <p className="text-[0.8125rem] text-muted-foreground">
          {t('settings.access.profile.noPassword')}
        </p>
      )}
    </SettingsGroup>
  );
}
