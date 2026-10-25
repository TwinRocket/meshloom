import { useCallback, useEffect, useMemo, useState } from 'react';
import { useTranslation } from 'react-i18next';

import { api, formatApiError } from '../../api';
import { useCapabilities } from '../../auth/SessionProvider';
import type { HealthStatus } from '../../types';
import { Button } from '../ui/button';
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '../ui/dialog';
import { toast } from '../ui/sonner';
import { Tabs, TabsContent, TabsList, TabsTrigger } from '../ui/tabs';
import { AddBundledDialog } from './extensions/AddBundledDialog';
import { CreateLocalDialog } from './extensions/CreateLocalDialog';
import { CustomRepositoriesPanel } from './extensions/CustomRepositoriesPanel';
import { InstalledTab } from './extensions/InstalledTab';
import { InstanceDrawer } from './extensions/InstanceDrawer';
import { MigrationBanner } from './extensions/MigrationBanner';
import type { PluginInstance, PluginPauseState } from './extensions/pluginsTypes';
import { SettingsGroupHeader } from './settingsPrimitives';

// The lease files of the setuid level (app/plugins/isolation/uid_lease.py, LEASE_DIR_NAME).
const UID_LEASE_DIR = '/.meshloom-plugin-uids/';
const ISOLATION_LEVELS = ['bwrap', 'setuid', 'none'] as const;

/**
 * Settings > Extensions: the isolation level, the global pause, then Installed / Store /
 * Updates. Members (`send`) see the list and statuses read-only; every write, the pause
 * included, is admin-only (the server refuses it too) and hidden with an explanation.
 */
export function SettingsExtensionsSection({
  className,
  health,
  onHealthRefresh,
}: {
  className?: string;
  health: HealthStatus | null;
  onHealthRefresh?: () => Promise<void>;
}) {
  const { t, i18n } = useTranslation();
  const { isAdmin } = useCapabilities();
  const [instances, setInstances] = useState<PluginInstance[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [openId, setOpenId] = useState<string | null>(null);
  const [adding, setAdding] = useState(false);
  const [creating, setCreating] = useState(false);

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
  // The host lock, the platform or a failed isolation forbid every write; the global
  // pause does not (changes are saved and apply on resume).
  const locked = disabledSource !== null && disabledSource !== 'paused';
  const pausedSince = health?.plugins_paused_since ?? null;

  // Who paused and since when (`GET /plugins/pause`), read again whenever health moves.
  const [pause, setPause] = useState<PluginPauseState | null>(null);
  const [confirmPause, setConfirmPause] = useState(false);
  const [pauseBusy, setPauseBusy] = useState(false);
  useEffect(() => {
    let alive = true;
    api
      .getPluginsPause()
      .then((state) => alive && setPause(state))
      .catch(() => alive && setPause(null));
    return () => {
      alive = false;
    };
  }, [reloadTick, pausedSince, disabledSource]);
  const paused = pause?.paused ?? disabledSource === 'paused';
  const since = pause?.since ?? pausedSince;
  // The pause state could not be read at boot: held as paused (fail closed), no date.
  const pauseUnreadable = health?.plugins_pause_unreadable === true;
  // Temp directories a uid purge could not list: those uids stay taken (MSHL-349).
  const purgeBlocked = health?.plugins_purge_blocked ?? [];

  const applyPause = async (next: boolean) => {
    setPauseBusy(true);
    try {
      setPause(await api.setPluginsPause(next));
      setConfirmPause(false);
      toast.success(
        t(next ? 'settings.extensions.pause.paused' : 'settings.extensions.pause.resumed')
      );
      reload();
    } catch (err) {
      toast.error(formatApiError(err, t));
    } finally {
      setPauseBusy(false);
    }
  };
  const convertedBots = health?.converted_bots ?? null;
  const [ackBusy, setAckBusy] = useState(false);
  const acknowledgeBots = async () => {
    setAckBusy(true);
    try {
      await api.acknowledgeConvertedBots();
      await onHealthRefresh?.();
      reload();
    } catch (err) {
      toast.error(formatApiError(err, t));
    } finally {
      setAckBusy(false);
    }
  };
  const sinceText = since ? new Date(since * 1000).toLocaleString(i18n.language) : '';

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

      {purgeBlocked.length > 0 && isAdmin && (
        <div
          role="status"
          data-testid="plugins-purge-blocked"
          className="mb-3 space-y-2 rounded-md border border-warning/50 bg-warning/10 p-3 text-sm"
        >
          <p className="font-medium">{t('settings.extensions.isolation.purgeBlocked.title')}</p>
          <p className="text-muted-foreground">
            {t('settings.extensions.isolation.purgeBlocked.text')}
          </p>
          <ul className="list-disc pl-5 font-mono text-xs">
            {purgeBlocked.map((path) => (
              <li key={path} className="break-all">
                {path}
              </li>
            ))}
          </ul>
          <p className="text-muted-foreground">
            {t('settings.extensions.isolation.purgeBlocked.fix')}
          </p>
          {purgeBlocked.some((path) => path.includes(UID_LEASE_DIR)) && (
            <p className="text-muted-foreground" data-testid="plugins-purge-blocked-lease">
              {t('settings.extensions.isolation.purgeBlocked.leaseFix')}
            </p>
          )}
        </div>
      )}

      {convertedBots && isAdmin && (
        <div className="mb-3">
          <MigrationBanner
            converted={convertedBots.converted}
            attention={convertedBots.attention}
            onAcknowledge={() => void acknowledgeBots()}
            busy={ackBusy}
          />
        </div>
      )}

      {locked && (
        <p
          role="alert"
          className="mb-3 rounded-md border border-destructive/50 bg-destructive/10 p-3 text-sm"
        >
          {t(`settings.extensions.disabled.${disabledSource}`)}
        </p>
      )}

      {paused && (
        <div
          role="status"
          data-testid="plugins-paused-banner"
          className="mb-3 space-y-2 rounded-md border border-warning/50 bg-warning/10 p-3 text-sm"
        >
          <p className="font-medium">
            {pauseUnreadable && !since
              ? t('settings.extensions.pause.bannerUnreadable')
              : !sinceText
                ? t('settings.extensions.pause.bannerNoTime')
                : pause?.by
                  ? t('settings.extensions.pause.banner', { time: sinceText, user: pause.by })
                  : t('settings.extensions.pause.bannerNoUser', { time: sinceText })}
          </p>
          <p className="text-muted-foreground">{t('settings.extensions.pause.bannerExplain')}</p>
          {isAdmin && (
            <Button size="sm" onClick={() => void applyPause(false)} disabled={pauseBusy}>
              {t('settings.extensions.pause.resume')}
            </Button>
          )}
        </div>
      )}
      {!paused && isAdmin && (
        <div className="mb-3 flex flex-wrap items-center gap-2 rounded-md border border-border p-3 text-sm">
          <p className="min-w-0 flex-1 text-muted-foreground">
            {t('settings.extensions.pause.hint')}
          </p>
          <Button
            size="sm"
            variant="outline"
            onClick={() => setConfirmPause(true)}
            disabled={pauseBusy}
          >
            {t('settings.extensions.pause.button')}
          </Button>
        </div>
      )}
      {!isAdmin && (
        <p
          data-testid="plugins-member-readonly"
          className="mb-3 rounded-md border border-border p-3 text-sm text-muted-foreground"
        >
          {t('settings.extensions.memberReadOnly')}
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
              canManage={isAdmin}
              onOpen={(i) => setOpenId(i.instance_id)}
              onAdd={() => setAdding(true)}
              onCreateLocal={() => setCreating(true)}
              onChanged={replace}
              onReordered={reload}
            />
          )}
        </TabsContent>
        <TabsContent value="store" className="mt-3">
          <CustomRepositoriesPanel canManage={isAdmin} locked={locked} onInstalled={reload} />
        </TabsContent>
        <TabsContent value="updates" className="mt-3 text-sm text-muted-foreground">
          {t('settings.extensions.tabs.updatesSoon')}
        </TabsContent>
      </Tabs>

      {isAdmin && (
        <>
          <AddBundledDialog
            open={adding}
            onOpenChange={setAdding}
            onInstalled={(instance) => {
              replace(instance);
              setOpenId(instance.instance_id);
            }}
          />
          <CreateLocalDialog
            open={creating}
            onOpenChange={setCreating}
            onCreated={(instance) => {
              replace(instance);
              setOpenId(instance.instance_id);
            }}
          />
          <Dialog open={confirmPause} onOpenChange={setConfirmPause}>
            <DialogContent className="w-[calc(100%-2rem)] max-w-md">
              <DialogHeader>
                <DialogTitle>{t('settings.extensions.pause.confirmTitle')}</DialogTitle>
                <DialogDescription>{t('settings.extensions.pause.confirmText')}</DialogDescription>
              </DialogHeader>
              <DialogFooter className="gap-2">
                <Button
                  variant="outline"
                  onClick={() => setConfirmPause(false)}
                  disabled={pauseBusy}
                >
                  {t('settings.extensions.cancel')}
                </Button>
                <Button
                  variant="destructive"
                  onClick={() => void applyPause(true)}
                  disabled={pauseBusy}
                >
                  {t('settings.extensions.pause.confirm')}
                </Button>
              </DialogFooter>
            </DialogContent>
          </Dialog>
        </>
      )}
      <InstanceDrawer
        instance={opened}
        locked={locked}
        canManage={isAdmin}
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
