import { useCallback, useEffect, useMemo, useState } from 'react';
import { useTranslation } from 'react-i18next';

import { api, formatApiError } from '../../api';
import type { HealthStatus } from '../../types';
import { Tabs, TabsContent, TabsList, TabsTrigger } from '../ui/tabs';
import { AddBundledDialog } from './extensions/AddBundledDialog';
import { InstalledTab } from './extensions/InstalledTab';
import { InstanceDrawer } from './extensions/InstanceDrawer';
import type { PluginInstance } from './extensions/pluginsTypes';
import { SettingsGroupHeader } from './settingsPrimitives';

const ISOLATION_LEVELS = ['bwrap', 'setuid', 'none'] as const;

/** Settings > Extensions: the isolation level, then Installed / Store / Updates. */
export function SettingsExtensionsSection({
  className,
  health,
}: {
  className?: string;
  health: HealthStatus | null;
}) {
  const { t } = useTranslation();
  const [instances, setInstances] = useState<PluginInstance[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [openId, setOpenId] = useState<string | null>(null);
  const [adding, setAdding] = useState(false);

  const [reloadTick, setReloadTick] = useState(0);
  const reload = useCallback(() => setReloadTick((n) => n + 1), []);

  // The host announces every status change (`plugin_status`) and a health refresh
  // follows: reload the list whenever the statuses in health move.
  const statusSignature = useMemo(
    () =>
      JSON.stringify(
        Object.entries(health?.plugin_statuses ?? {}).map(([id, s]) => [
          id,
          s.status,
          s.last_error,
          s.restarts,
          s.summary ?? null,
        ])
      ),
    [health?.plugin_statuses]
  );
  useEffect(() => {
    let alive = true;
    api
      .getPlugins()
      .then((rows) => {
        if (!alive) return;
        setInstances(rows);
        setError(null);
      })
      .catch((err) => alive && setError(formatApiError(err, t)));
    return () => {
      alive = false;
    };
  }, [reloadTick, statusSignature, t]);

  const replace = useCallback((next: PluginInstance) => {
    setInstances((prev) =>
      prev === null
        ? [next]
        : prev.some((i) => i.instance_id === next.instance_id)
          ? prev.map((i) => (i.instance_id === next.instance_id ? next : i))
          : [...prev, next]
    );
  }, []);

  const opened = instances?.find((i) => i.instance_id === openId) ?? null;
  const isolation = health?.plugins_isolation ?? null;
  const disabledSource = health?.plugins_disabled_source ?? null;
  const locked = disabledSource !== null;

  return (
    <div className={className}>
      <SettingsGroupHeader
        id="settings-extensions"
        title={t('settings.extensions.title')}
        storedOn="server"
      />
      <p className="mb-3 text-sm text-muted-foreground">{t('settings.extensions.intro')}</p>

      <div
        className="mb-3 rounded-md border border-border p-3 text-sm"
        data-testid="plugin-isolation"
      >
        <div className="font-medium">
          {t('settings.extensions.isolation.title')}:{' '}
          {isolation && (ISOLATION_LEVELS as readonly string[]).includes(isolation)
            ? t(`settings.extensions.isolation.level.${isolation}`)
            : t('settings.extensions.isolation.level.unknown')}
        </div>
        <p className="text-muted-foreground">
          {isolation && (ISOLATION_LEVELS as readonly string[]).includes(isolation)
            ? t(`settings.extensions.isolation.explain.${isolation}`)
            : t('settings.extensions.isolation.explain.unknown')}
        </p>
      </div>

      {locked && (
        <p
          role="alert"
          className="mb-3 rounded-md border border-destructive/50 bg-destructive/10 p-3 text-sm"
        >
          {t(`settings.extensions.disabled.${disabledSource}`)}
        </p>
      )}

      <Tabs defaultValue="installed">
        <TabsList className="h-auto w-full flex-wrap justify-start">
          <TabsTrigger value="installed">{t('settings.extensions.tabs.installed')}</TabsTrigger>
          <TabsTrigger value="store">{t('settings.extensions.tabs.store')}</TabsTrigger>
          <TabsTrigger value="updates">{t('settings.extensions.tabs.updates')}</TabsTrigger>
        </TabsList>
        <TabsContent value="installed" className="mt-3">
          {error && (
            <p role="alert" className="mb-2 text-sm text-destructive">
              {error}
            </p>
          )}
          {instances === null && !error && (
            <p className="text-sm text-muted-foreground">{t('settings.extensions.loading')}</p>
          )}
          {instances !== null && (
            <InstalledTab
              instances={instances}
              locked={locked}
              onOpen={(i) => setOpenId(i.instance_id)}
              onAdd={() => setAdding(true)}
              onChanged={replace}
              onReordered={reload}
            />
          )}
        </TabsContent>
        <TabsContent value="store" className="mt-3 text-sm text-muted-foreground">
          {t('settings.extensions.tabs.storeSoon')}
        </TabsContent>
        <TabsContent value="updates" className="mt-3 text-sm text-muted-foreground">
          {t('settings.extensions.tabs.updatesSoon')}
        </TabsContent>
      </Tabs>

      <AddBundledDialog
        open={adding}
        onOpenChange={setAdding}
        onInstalled={(instance) => {
          replace(instance);
          setOpenId(instance.instance_id);
        }}
      />
      <InstanceDrawer
        instance={opened}
        locked={locked}
        onClose={() => setOpenId(null)}
        onChanged={replace}
        onRemoved={(id) => {
          setOpenId(null);
          setInstances((prev) => prev?.filter((i) => i.instance_id !== id) ?? prev);
        }}
      />
    </div>
  );
}
