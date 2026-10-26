import { useCallback, useEffect, useState } from 'react';
import { useTranslation } from 'react-i18next';

import { api, formatApiError } from '../../../api';
import { refreshPluginUpdateCount, setPluginUpdateCount } from '../../../stores/pluginUpdatesStore';
import { Button } from '../../ui/button';
import { Dialog, DialogContent } from '../../ui/dialog';
import { toast } from '../../ui/sonner';
import { UpdateDialogBody } from './UpdateDialog';
import { displayName, localizedText } from './pluginsShared';
import type { PluginInstance, PluginUpdate, StoreRepositories, StoreStatus } from './pluginsTypes';

/** The kinds that take releases from a source on their own (`app/plugins/updates.py`). */
const UPDATABLE_KINDS = ['custom', 'store'];

/**
 * Settings > Extensions > Updates: the plugins with a newer release cached by their
 * source (no request is made to list them), with their changelog and the permission and
 * settings changes, the Update action (with the consent or settings the backend holds an
 * instance for), the per-instance auto-update switch, and a link to Meshloom's own
 * Updates section. Admins only: the routes are admin-only.
 */
export function UpdatesTab({
  instances,
  canManage,
  locked,
  reloadKey,
  onChanged,
  onOpenMeshloomUpdates,
}: {
  instances: PluginInstance[];
  canManage: boolean;
  locked: boolean;
  /** Bumped when plugin statuses move (the WebSocket announces them through health). */
  reloadKey: string | number;
  /** An update or a switch changed instances: the parent reloads its list. */
  onChanged: () => void;
  onOpenMeshloomUpdates?: () => void;
}) {
  const { t, i18n } = useTranslation();
  const [updates, setUpdates] = useState<PluginUpdate[] | null>(null);
  const [repos, setRepos] = useState<StoreRepositories | null>(null);
  const [status, setStatus] = useState<StoreStatus | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [open, setOpen] = useState<PluginUpdate | null>(null);
  const [busyId, setBusyId] = useState<string | null>(null);
  const [tick, setTick] = useState(0);
  const load = useCallback(() => setTick((n) => n + 1), []);

  useEffect(() => {
    if (!canManage) return;
    let alive = true;
    Promise.all([
      api.getStoreUpdates(i18n.language),
      api.getStoreRepositories(),
      api.getStoreStatus(),
    ])
      .then(([list, repositories, s]) => {
        if (!alive) return;
        setUpdates(list);
        setRepos(repositories);
        setStatus(s);
        setError(null);
        setPluginUpdateCount(list.length);
      })
      .catch((err) => alive && setError(formatApiError(err, t)));
    return () => {
      alive = false;
    };
  }, [canManage, i18n.language, t, tick, reloadKey]);

  if (!canManage) {
    return (
      <p className="text-sm text-muted-foreground" data-testid="updates-admin-only">
        {t('pluginStore.updates.adminOnly')}
      </p>
    );
  }

  const online = status?.enabled === true;
  const updatable = instances.filter((i) => UPDATABLE_KINDS.includes(i.kind));

  const setAutoUpdate = async (instance: PluginInstance, value: boolean) => {
    setBusyId(instance.instance_id);
    try {
      await api.patchPlugin(instance.instance_id, { auto_update: value });
      onChanged();
      load();
    } catch (err) {
      toast.error(formatApiError(err, t));
    } finally {
      setBusyId(null);
    }
  };

  return (
    <div className="space-y-4 text-sm">
      <div className="flex flex-wrap items-start gap-2">
        <div className="min-w-0 flex-1">
          <h3 className="font-medium">{t('pluginStore.updates.title')}</h3>
          <p className="text-muted-foreground">{t('pluginStore.updates.intro')}</p>
        </div>
        {onOpenMeshloomUpdates && (
          <Button size="sm" variant="outline" onClick={onOpenMeshloomUpdates}>
            {t('pluginStore.updates.openMeshloomUpdates')}
          </Button>
        )}
      </div>

      {error && (
        <p role="alert" className="text-destructive">
          {error}
        </p>
      )}
      {updates === null && !error && (
        <p className="text-muted-foreground">{t('settings.extensions.loading')}</p>
      )}
      {status && !online && (
        <p className="text-muted-foreground" data-testid="updates-store-offline">
          {t('pluginStore.updates.offline')}
        </p>
      )}
      {updates?.length === 0 && (
        <p className="text-muted-foreground" data-testid="updates-none">
          {t('pluginStore.updates.none')}
        </p>
      )}

      {updates && updates.length > 0 && (
        <ul className="space-y-2">
          {updates.map((update) => {
            const name = localizedText(update.name, i18n.language) || update.plugin_id;
            const holds = update.instances.filter((i) => i.hold !== null);
            return (
              <li
                key={update.plugin_id}
                className="space-y-1 rounded-md border border-border p-3"
                data-testid="plugin-update-row"
              >
                <div className="flex flex-wrap items-start gap-2">
                  <div className="min-w-0 flex-1">
                    <div className="font-medium break-words">{name}</div>
                    <div className="text-xs text-muted-foreground break-words">
                      {update.from_version} → {update.to_version}
                      {update.repository && ` · ${update.repository}`}
                    </div>
                  </div>
                  <Button
                    size="sm"
                    onClick={() => setOpen(update)}
                    disabled={locked || !online}
                    data-testid="plugin-update-open"
                  >
                    {t('pluginStore.updates.review')}
                  </Button>
                </div>
                {holds.map((i) => (
                  <p
                    key={i.instance_id}
                    className="text-xs text-muted-foreground break-words"
                    data-testid="plugin-update-hold"
                  >
                    {t(`pluginStore.updates.hold.${i.hold}`, { label: i.label })}
                  </p>
                ))}
              </li>
            );
          })}
        </ul>
      )}

      {updatable.length > 0 && (
        <section className="space-y-2">
          <div>
            <h3 className="font-medium">{t('pluginStore.updates.autoTitle')}</h3>
            <p className="text-muted-foreground">{t('pluginStore.updates.autoIntro')}</p>
          </div>
          <ul className="space-y-1">
            {updatable.map((instance) => {
              const name = displayName(instance);
              return (
                <li key={instance.instance_id} className="flex items-center gap-2">
                  <label className="flex min-w-0 flex-1 items-center gap-2">
                    <input
                      type="checkbox"
                      role="switch"
                      checked={instance.auto_update}
                      disabled={locked || busyId === instance.instance_id}
                      aria-label={t('pluginStore.updates.autoToggle', { name })}
                      onChange={(e) => void setAutoUpdate(instance, e.target.checked)}
                    />
                    <span className="break-words">{name}</span>
                  </label>
                </li>
              );
            })}
          </ul>
        </section>
      )}

      <Dialog open={open !== null} onOpenChange={(o) => !o && setOpen(null)}>
        <DialogContent className="max-h-[90dvh] w-[calc(100%-2rem)] max-w-lg overflow-y-auto">
          {open && (
            <UpdateDialogBody
              update={open}
              repo={repos?.repositories.find((r) => r.repository === open.repository)}
              instances={instances}
              onClose={() => setOpen(null)}
              onUpdated={() => {
                setOpen(null);
                onChanged();
                load();
                void refreshPluginUpdateCount();
              }}
            />
          )}
        </DialogContent>
      </Dialog>
    </div>
  );
}
