import { useTranslation } from 'react-i18next';

import type { StoreStatus } from './pluginsTypes';
import { Button } from '../../ui/button';

/**
 * Shown while the Store is off: what a request reveals, which hosts it may reach (read
 * from `GET /api/store/status`, so the list is the server's own allowlist and cannot
 * drift), what the instance does not receive while it stays off, and the one-click
 * consent. Nothing is requested until it is given.
 */
export function StoreActivationCard({
  status,
  canManage,
  busy,
  onEnable,
}: {
  status: StoreStatus;
  canManage: boolean;
  busy: boolean;
  onEnable: () => void;
}) {
  const { t } = useTranslation();
  const hours = Math.max(1, Math.round(status.refresh_interval_s / 3600));
  return (
    <div
      className="space-y-3 rounded-md border border-border p-3 text-sm"
      data-testid="store-offline"
    >
      <div>
        <h3 className="font-medium">{t('pluginStore.activation.title')}</h3>
        <p className="text-muted-foreground">{t('pluginStore.activation.intro')}</p>
      </div>
      <div>
        <div className="font-medium">{t('pluginStore.activation.sendsTitle')}</div>
        <ul className="list-disc pl-5 text-muted-foreground" data-testid="store-sends">
          {status.sends.map((item) => (
            <li key={item}>
              {t(`pluginStore.activation.sends.${item}`, {
                userAgent: status.user_agent,
                defaultValue: item,
              })}
            </li>
          ))}
        </ul>
        <p className="text-muted-foreground">{t('pluginStore.activation.sendsNone')}</p>
      </div>
      <div>
        <div className="font-medium">{t('pluginStore.activation.hostsTitle')}</div>
        <ul className="list-disc pl-5 font-mono text-xs" data-testid="store-hosts">
          {status.hosts.map((host) => (
            <li key={host} className="break-all">
              {host}
            </li>
          ))}
        </ul>
        <p className="text-muted-foreground">{t('pluginStore.activation.cadence', { hours })}</p>
      </div>
      <div>
        <div className="font-medium">{t('pluginStore.activation.missedTitle')}</div>
        <ul className="list-disc pl-5 text-muted-foreground" data-testid="store-missed">
          {status.missed_when_disabled.map((item) => (
            <li key={item}>{t(`pluginStore.activation.missed.${item}`, { defaultValue: item })}</li>
          ))}
        </ul>
        <p className="text-muted-foreground" data-testid="store-revocation-later">
          {t('pluginStore.activation.revocationLater')}
        </p>
      </div>
      {canManage ? (
        <Button size="sm" onClick={onEnable} disabled={busy}>
          {t('settings.extensions.store.goOnline')}
        </Button>
      ) : (
        <p className="text-muted-foreground">{t('settings.extensions.store.adminOnly')}</p>
      )}
    </div>
  );
}
