import { useState } from 'react';
import { useTranslation } from 'react-i18next';

import { api } from '../../../api';
import { formatAccessError } from '../../../auth/accessErrors';
import { Button } from '../../ui/button';
import { toast } from '../../ui/sonner';
import { AccessConfirmDialog } from '../AccessConfirmDialog';
import { formatEpoch } from '../accessShared';
import { SettingsGroup, SettingsGroupHeader } from '../settingsPrimitives';
import type { RemoteDevice } from './remoteTypes';

/**
 * The devices that hold a Remote pass (`devices` of `GET /api/remote`: an
 * administrator sees every account's, anyone else their own). Revoking one is
 * `DELETE /api/remote/devices/{id}`; an administrator can revoke every device at once
 * (`POST /api/remote/devices/revoke-all`, a new pass generation).
 */
export function RemoteDevicesPanel({
  devices,
  isAdmin,
  currentUserId,
  userNames,
  onChanged,
}: {
  devices: RemoteDevice[];
  isAdmin: boolean;
  currentUserId: number | null;
  /** Account names by id, for an administrator's list (best effort). */
  userNames: ReadonlyMap<number, string>;
  onChanged: () => void;
}) {
  const { t, i18n } = useTranslation();
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [confirm, setConfirm] = useState<RemoteDevice | 'all' | null>(null);

  const active = devices.filter((device) => device.active);
  const inactive = devices.filter((device) => !device.active);

  const name = (device: RemoteDevice) =>
    device.label?.trim() || t(`settings.remote.devices.unnamed.${device.kind}`);

  const pushedNotice = (pushed: boolean) =>
    pushed ? null : t('settings.remote.devices.notPushed');

  const revoke = async (target: RemoteDevice | 'all') => {
    setConfirm(null);
    setBusy(true);
    setError(null);
    try {
      const result =
        target === 'all'
          ? await api.revokeAllRemoteDevices()
          : await api.revokeRemoteDevice(target.id);
      toast.success(
        t(
          target === 'all'
            ? 'settings.remote.devices.revokedAll'
            : 'settings.remote.devices.revoked'
        ),
        { description: pushedNotice(result.gate_pushed) ?? undefined }
      );
    } catch (err) {
      setError(formatAccessError(err, t));
    } finally {
      setBusy(false);
      onChanged();
    }
  };

  const owner = (device: RemoteDevice) => {
    if (!isAdmin) return null;
    if (device.user_id === currentUserId) return t('settings.remote.devices.ownerYou');
    return t('settings.remote.devices.owner', {
      user: userNames.get(device.user_id) ?? `#${device.user_id}`,
    });
  };

  const details = (device: RemoteDevice) =>
    [
      owner(device),
      t(`settings.remote.devices.origin.${device.origin}`),
      device.last_seen_at
        ? t('settings.remote.devices.lastSeen', {
            when: formatEpoch(device.last_seen_at, i18n.language),
          })
        : t('settings.remote.devices.neverSeen'),
      device.active
        ? t('settings.remote.devices.passExpires', {
            when: formatEpoch(device.pass_expires_at, i18n.language),
          })
        : device.revoked_at
          ? t('settings.remote.devices.revokedAt', {
              when: formatEpoch(device.revoked_at, i18n.language),
            })
          : t('settings.remote.devices.oldGeneration'),
    ]
      .filter(Boolean)
      .join(' · ');

  return (
    <SettingsGroup>
      <SettingsGroupHeader
        id="remote-devices"
        title={t('settings.remote.devices.title')}
        storedOn="server"
      />
      <p className="text-[0.8125rem] text-muted-foreground">
        {t(isAdmin ? 'settings.remote.devices.helpAdmin' : 'settings.remote.devices.help')}
      </p>
      {active.length === 0 && (
        <p className="text-sm text-muted-foreground">{t('settings.remote.devices.none')}</p>
      )}
      {active.length > 0 && (
        <ul className="divide-y divide-border" data-testid="remote-devices-list">
          {active.map((device) => (
            <li key={device.id} className="flex flex-wrap items-center gap-x-4 gap-y-2 py-2">
              <div className="min-w-0 flex-1">
                <div className="text-sm font-medium break-words">{name(device)}</div>
                <div className="mt-1 text-[0.8125rem] text-muted-foreground">{details(device)}</div>
              </div>
              <Button
                variant="outline"
                size="sm"
                disabled={busy}
                aria-label={t('settings.remote.devices.revokeNamed', { name: name(device) })}
                onClick={() => setConfirm(device)}
              >
                {t('settings.remote.devices.revoke')}
              </Button>
            </li>
          ))}
        </ul>
      )}
      {inactive.length > 0 && (
        <details className="text-[0.8125rem]">
          <summary className="cursor-pointer text-muted-foreground">
            {t('settings.remote.devices.inactive', { count: inactive.length })}
          </summary>
          <ul className="mt-2 divide-y divide-border" data-testid="remote-devices-inactive">
            {inactive.map((device) => (
              <li key={device.id} className="py-2 text-muted-foreground">
                <span className="font-medium">{name(device)}</span> · {details(device)}
              </li>
            ))}
          </ul>
        </details>
      )}
      {isAdmin && active.length > 0 && (
        <div>
          <Button variant="destructive" disabled={busy} onClick={() => setConfirm('all')}>
            {t('settings.remote.devices.revokeAll')}
          </Button>
        </div>
      )}
      {error && (
        <p role="alert" className="text-sm text-destructive">
          {error}
        </p>
      )}
      <AccessConfirmDialog
        open={confirm !== null}
        title={
          confirm === 'all'
            ? t('settings.remote.devices.revokeAllTitle')
            : t('settings.remote.devices.revokeTitle', {
                name: confirm ? name(confirm) : '',
              })
        }
        help={t(
          confirm === 'all'
            ? 'settings.remote.devices.revokeAllHelp'
            : 'settings.remote.devices.revokeHelp'
        )}
        confirmLabel={t(
          confirm === 'all' ? 'settings.remote.devices.revokeAll' : 'settings.remote.devices.revoke'
        )}
        destructive
        onCancel={() => setConfirm(null)}
        onConfirm={() => confirm && void revoke(confirm)}
      />
    </SettingsGroup>
  );
}
