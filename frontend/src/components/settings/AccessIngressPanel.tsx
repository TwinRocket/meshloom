import { useEffect, useState } from 'react';
import { useTranslation } from 'react-i18next';

import { api } from '../../api';
import { formatAccessError } from '../../auth/accessErrors';
import type { AuthRole } from '../../auth/types';
import { toast } from '../ui/sonner';
import { Label } from '../ui/label';
import { ACCESS_ROLES, SELECT_CLASS } from './accessShared';
import { SettingsGroup, SettingsGroupHeader } from './settingsPrimitives';

/**
 * Administrator only: the role a Home Assistant user gets on first sight
 * (`ingress_default_role`), and the way to the trusted proxies, which stay in
 * Local configuration.
 */
export function AccessIngressPanel() {
  const { t } = useTranslation();
  const [role, setRole] = useState<AuthRole | null>(null);
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    let cancelled = false;
    api
      .getAuthSettings()
      .then((settings) => {
        if (!cancelled) setRole(settings.ingress_default_role);
      })
      .catch(() => {
        // Not readable (older server, refused): the control stays hidden.
      });
    return () => {
      cancelled = true;
    };
  }, []);

  const change = async (next: AuthRole) => {
    const previous = role;
    setRole(next);
    setSaving(true);
    try {
      const saved = await api.patchAuthSettings({ ingress_default_role: next });
      setRole(saved.ingress_default_role);
      toast.success(t('settings.access.ingress.saved'));
    } catch (err) {
      setRole(previous);
      toast.error(formatAccessError(err, t));
    } finally {
      setSaving(false);
    }
  };

  return (
    <SettingsGroup>
      <SettingsGroupHeader
        id="access-ingress"
        title={t('settings.access.ingress.title')}
        storedOn="server"
        instant
      />
      {role !== null && (
        <div className="space-y-1.5">
          <Label htmlFor="access-ingress-role">{t('settings.access.ingress.defaultRole')}</Label>
          <select
            id="access-ingress-role"
            className={SELECT_CLASS}
            value={role}
            disabled={saving}
            onChange={(event) => void change(event.target.value as AuthRole)}
          >
            {ACCESS_ROLES.map((value) => (
              <option key={value} value={value}>
                {t(`auth.role.${value}`)}
              </option>
            ))}
          </select>
          <p className="text-[0.8125rem] text-muted-foreground">
            {t('settings.access.ingress.help')}
          </p>
        </div>
      )}
      <p className="text-[0.8125rem] text-muted-foreground">
        {t('settings.access.ingress.proxiesPrefix')}{' '}
        <a href="#settings/local" className="underline underline-offset-2 hover:text-primary">
          {t('settings.access.ingress.proxiesLink')}
        </a>
        .
      </p>
    </SettingsGroup>
  );
}
