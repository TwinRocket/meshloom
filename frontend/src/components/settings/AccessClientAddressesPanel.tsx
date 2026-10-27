import { useEffect, useState } from 'react';
import { useTranslation } from 'react-i18next';

import { api } from '../../api';
import { formatAccessError } from '../../auth/accessErrors';
import type { AuthSettings } from '../../auth/types';
import { Checkbox } from '../ui/checkbox';
import { Label } from '../ui/label';
import { toast } from '../ui/sonner';
import { SettingsGroup, SettingsGroupHeader } from './settingsPrimitives';

/**
 * Administrator only: `addresses_identify_clients`. Under Docker without a declared
 * proxy the server keeps the per-IP login rules off, since every client may share the
 * gateway's address; this setting states that the peer address is the client's own.
 * Shown on Docker installs (`shared_address_install`), or whenever it is already on.
 * The suggestion appears when the caller arrives from a public address and no proxy
 * is declared.
 */
export function AccessClientAddressesPanel() {
  const { t } = useTranslation();
  const [settings, setSettings] = useState<AuthSettings | null>(null);
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    let cancelled = false;
    api
      .getAuthSettings()
      .then((value) => {
        if (!cancelled) setSettings(value);
      })
      .catch(() => {
        // Not readable (older server, refused): the control stays hidden.
      });
    return () => {
      cancelled = true;
    };
  }, []);

  if (!settings || typeof settings.addresses_identify_clients !== 'boolean') return null;
  const enabled = settings.addresses_identify_clients;
  if (!settings.shared_address_install && !enabled) return null;

  const suggest =
    !enabled && settings.client_public === true && (settings.trusted_proxies ?? []).length === 0;

  const change = async (next: boolean) => {
    setSaving(true);
    try {
      const saved = await api.patchAuthSettings({ addresses_identify_clients: next });
      setSettings(saved);
      toast.success(
        t(next ? 'settings.access.clientAddresses.on' : 'settings.access.clientAddresses.off')
      );
    } catch (err) {
      toast.error(formatAccessError(err, t));
    } finally {
      setSaving(false);
    }
  };

  return (
    <SettingsGroup>
      <SettingsGroupHeader
        id="access-client-addresses"
        title={t('settings.access.clientAddresses.title')}
        storedOn="server"
      />
      <div className="flex items-center gap-2">
        <Checkbox
          id="access-addresses-identify-clients"
          checked={enabled}
          disabled={saving}
          onCheckedChange={(checked) => void change(checked === true)}
        />
        <Label htmlFor="access-addresses-identify-clients">
          {t('settings.access.clientAddresses.toggle')}
        </Label>
      </div>
      <p className="text-[0.8125rem] text-muted-foreground">
        {t('settings.access.clientAddresses.help')}
      </p>
      {typeof settings.client_public === 'boolean' && (
        <p className="text-[0.8125rem] text-muted-foreground">
          {t('settings.access.clientAddresses.seen', {
            kind: t(
              settings.client_public
                ? 'settings.access.clientAddresses.seenPublic'
                : 'settings.access.clientAddresses.seenLocal'
            ),
          })}
        </p>
      )}
      {suggest && (
        <p className="text-[0.8125rem] text-warning" data-testid="client-addresses-suggest">
          {t('settings.access.clientAddresses.suggest')}
        </p>
      )}
    </SettingsGroup>
  );
}
