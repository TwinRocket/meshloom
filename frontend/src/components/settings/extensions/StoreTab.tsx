import { useCallback, useEffect, useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';

import { api, formatApiError } from '../../../api';
import { Button } from '../../ui/button';
import { toast } from '../../ui/sonner';
import { CustomRepositoriesPanel } from './CustomRepositoriesPanel';
import { StoreActivationCard } from './StoreActivationCard';
import type { StoreStatus } from './pluginsTypes';

/** The Refresh button is held for this long after a click (the server limits it too). */
export const REFRESH_COOLDOWN_MS = 10_000;

/**
 * Settings > Extensions > Store: the activation card while the Store is off (admins
 * only: the status route is admin-only), then the custom repositories and their plugins
 * with a Refresh button and the switch that turns the Store off again. A member sees the warning and a notice, and nothing is requested.
 */
export function StoreTab({
  canManage,
  locked,
  onInstalled,
  reloadKey = 0,
}: {
  canManage: boolean;
  locked: boolean;
  onInstalled: () => void;
  /** Bumped when plugin statuses move (the WebSocket announces them through health). */
  reloadKey?: string | number;
}) {
  const { t, i18n } = useTranslation();
  const [status, setStatus] = useState<StoreStatus | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [refreshing, setRefreshing] = useState(false);
  const [cooling, setCooling] = useState(false);
  const [listKey, setListKey] = useState(0);
  const [tick, setTick] = useState(0);
  const cooldown = useRef<number | null>(null);
  const reload = useCallback(() => setTick((n) => n + 1), []);

  useEffect(
    () => () => {
      if (cooldown.current !== null) window.clearTimeout(cooldown.current);
    },
    []
  );

  useEffect(() => {
    if (!canManage) return;
    let alive = true;
    api
      .getStoreStatus()
      .then((s) => {
        if (!alive) return;
        setStatus(s);
        setError(null);
      })
      .catch((err) => alive && setError(formatApiError(err, t)));
    return () => {
      alive = false;
    };
  }, [canManage, t, tick, reloadKey]);

  const enable = async () => {
    setBusy(true);
    try {
      await api.updateSettings({ store_online: true });
      reload();
      setListKey((n) => n + 1);
    } catch (err) {
      toast.error(formatApiError(err, t));
    } finally {
      setBusy(false);
    }
  };

  const disable = async () => {
    setBusy(true);
    try {
      await api.updateSettings({ store_online: false });
      reload();
      setListKey((n) => n + 1);
    } catch (err) {
      toast.error(formatApiError(err, t));
    } finally {
      setBusy(false);
    }
  };

  const refresh = async () => {
    setRefreshing(true);
    setCooling(true);
    cooldown.current = window.setTimeout(() => setCooling(false), REFRESH_COOLDOWN_MS);
    try {
      const result = await api.refreshStore();
      toast.success(t(result.ran ? 'pluginStore.refresh.done' : 'pluginStore.refresh.skipped'));
      reload();
      setListKey((n) => n + 1);
    } catch (err) {
      toast.error(formatApiError(err, t));
    } finally {
      setRefreshing(false);
    }
  };

  const online = status?.enabled === true;
  const lastRefresh = status?.last_refresh_at
    ? new Date(status.last_refresh_at * 1000).toLocaleString(i18n.language)
    : null;

  return (
    <div className="space-y-4">
      {error && (
        <p role="alert" className="text-sm text-destructive">
          {error}
        </p>
      )}
      {canManage && status && !online && (
        <StoreActivationCard status={status} canManage busy={busy} onEnable={() => void enable()} />
      )}
      {canManage && online && (
        <div
          className="space-y-2 rounded-md border border-border p-3 text-sm"
          data-testid="store-online"
        >
          <div className="flex flex-wrap items-center gap-2">
            <label className="flex items-center gap-2 font-medium">
              <input
                type="checkbox"
                role="switch"
                checked
                disabled={busy}
                aria-describedby="store-online-help"
                onChange={() => void disable()}
              />
              <span>{t('pluginStore.online.toggle')}</span>
            </label>
            <span className="min-w-0 flex-1 text-muted-foreground">
              {lastRefresh
                ? t('pluginStore.refresh.last', { time: lastRefresh })
                : t('pluginStore.refresh.never')}
            </span>
            <Button
              size="sm"
              variant="outline"
              onClick={() => void refresh()}
              disabled={refreshing || cooling}
            >
              {t('pluginStore.refresh.button')}
            </Button>
          </div>
          <p id="store-online-help" className="text-muted-foreground">
            {t('pluginStore.online.help')}
          </p>
        </div>
      )}
      <CustomRepositoriesPanel
        canManage={canManage}
        locked={locked}
        onInstalled={onInstalled}
        reloadKey={`${listKey}|${reloadKey}`}
      />
    </div>
  );
}
