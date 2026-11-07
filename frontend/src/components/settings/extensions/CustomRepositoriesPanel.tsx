import { useCallback, useEffect, useState } from 'react';
import { useTranslation } from 'react-i18next';

import { api, formatApiError } from '../../../api';
import { Button } from '../../ui/button';
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '../../ui/dialog';
import { Input } from '../../ui/input';
import { toast } from '../../ui/sonner';
import { CustomInstallBody } from './CustomInstallBody';
import { PluginPage } from './PluginPage';
import { localizedText } from './pluginsShared';
import type { StoreRepositories, StoreRepository } from './pluginsTypes';

/** Same shape the server checks (`owner/name`, GitHub's rules); it stays authoritative. */
const REPOSITORY_RE =
  /^[A-Za-z0-9](?:[A-Za-z0-9]|-(?=[A-Za-z0-9])){0,38}\/(?!\.\.?$)[A-Za-z0-9._-]{1,100}$/;

/**
 * Store tab, custom repositories (MSHL-196): a GitHub `owner/name` whose latest release
 * carries `manifest.json`, `plugin.zip` and `plugin.zip.sha256`. Listing makes no request
 * to GitHub; adding one and installing need the Store online (the server refuses
 * otherwise). The warning is the UI's own i18n copy: unsigned, sha256 is an integrity
 * check only, no online revocation. The routes are admin-only: members see the warning
 * and a notice, and nothing is requested.
 */
export function CustomRepositoriesPanel({
  canManage,
  locked,
  onInstalled,
  reloadKey = 0,
}: {
  canManage: boolean;
  locked: boolean;
  onInstalled: () => void;
  /** Bumped by the Store tab after a refresh: the list is read again. */
  reloadKey?: string | number;
}) {
  const { t, i18n } = useTranslation();
  const [data, setData] = useState<StoreRepositories | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [value, setValue] = useState('');
  const [busy, setBusy] = useState(false);
  const [removing, setRemoving] = useState<StoreRepository | null>(null);
  const [installing, setInstalling] = useState<StoreRepository | null>(null);
  const [viewing, setViewing] = useState<StoreRepository | null>(null);

  const [tick, setTick] = useState(0);
  const load = useCallback(() => setTick((n) => n + 1), []);

  useEffect(() => {
    if (!canManage) return;
    let alive = true;
    api
      .getStoreRepositories()
      .then((rows) => {
        if (!alive) return;
        setData(rows);
        setError(null);
      })
      .catch((err) => alive && setError(formatApiError(err, t)));
    return () => {
      alive = false;
    };
  }, [canManage, t, tick, reloadKey]);

  const trimmed = value.trim();
  const valid = REPOSITORY_RE.test(trimmed);
  const online = data?.store_online ?? false;
  const full = data ? data.repositories.length >= data.max_repositories : false;

  const add = async () => {
    if (!valid) return;
    setBusy(true);
    try {
      const row = await api.addStoreRepository(trimmed);
      toast.success(t('settings.extensions.store.added', { repository: row.display_name }));
      setValue('');
      load();
    } catch (err) {
      toast.error(formatApiError(err, t));
    } finally {
      setBusy(false);
    }
  };

  const remove = async () => {
    if (!removing) return;
    setBusy(true);
    try {
      await api.removeStoreRepository(removing.repository);
      toast.success(t('settings.extensions.store.removed', { repository: removing.display_name }));
      setRemoving(null);
      load();
    } catch (err) {
      toast.error(formatApiError(err, t));
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="space-y-4 text-sm">
      <div>
        <h3 className="font-medium">{t('settings.extensions.store.title')}</h3>
        <p className="text-muted-foreground">{t('settings.extensions.store.intro')}</p>
      </div>

      <p
        role="note"
        className="rounded-md border border-amber-500/50 bg-amber-500/10 p-3"
        data-testid="custom-repo-warning"
      >
        {t('settings.extensions.store.warning')}
      </p>

      {!canManage && (
        <p className="text-muted-foreground" data-testid="custom-repo-admin-only">
          {t('settings.extensions.store.adminOnly')}
        </p>
      )}
      {error && (
        <p role="alert" className="text-destructive">
          {error}
        </p>
      )}
      {canManage && data === null && !error && (
        <p className="text-muted-foreground">{t('settings.extensions.loading')}</p>
      )}

      {data && !online && (
        <p className="text-muted-foreground" data-testid="store-offline-note">
          {t('settings.extensions.store.offline')}
        </p>
      )}

      {data && canManage && (
        <form
          className="flex flex-wrap gap-2"
          onSubmit={(e) => {
            e.preventDefault();
            void add();
          }}
        >
          <Input
            className="min-w-0 flex-1"
            value={value}
            onChange={(e) => setValue(e.target.value)}
            placeholder="owner/name"
            aria-label={t('settings.extensions.store.repositoryLabel')}
            autoComplete="off"
            spellCheck={false}
          />
          <Button type="submit" disabled={!valid || !online || full || busy}>
            {t('settings.extensions.store.add')}
          </Button>
          {trimmed !== '' && !valid && (
            <p className="w-full text-destructive">{t('errors.repository_invalid')}</p>
          )}
          {full && (
            <p className="w-full text-muted-foreground">
              {t('errors.repository_limit', { limit: data.max_repositories })}
            </p>
          )}
        </form>
      )}

      {data && data.repositories.length === 0 && (
        <p className="text-muted-foreground">{t('settings.extensions.store.none')}</p>
      )}

      {data && data.repositories.length > 0 && (
        <ul className="space-y-2">
          {data.repositories.map((repo) => {
            const entry = repo.entry;
            return (
              <li
                key={repo.repository}
                className="space-y-1 rounded-md border border-border p-3"
                data-testid="custom-repo-row"
              >
                <div className="flex flex-wrap items-start gap-2">
                  <div className="min-w-0 flex-1">
                    <div className="font-medium break-words">
                      {entry
                        ? localizedText(entry.name, i18n.language) || entry.plugin_id
                        : repo.display_name}
                    </div>
                    <div className="text-xs text-muted-foreground break-words">
                      {repo.display_name}
                      {entry && ` · v${entry.version} (${entry.tag})`}
                      {` · ${t('settings.extensions.store.unsigned')}`}
                    </div>
                    <div className="mt-1 flex flex-wrap gap-1.5" data-testid="custom-repo-badges">
                      <span className="rounded bg-muted px-1.5 py-0.5 text-xs font-medium">
                        {t('pluginStore.card.custom')}
                      </span>
                      <span className="rounded bg-amber-500/15 px-1.5 py-0.5 text-xs font-medium">
                        {t('pluginStore.card.unreviewed')}
                      </span>
                      {entry && !entry.compatible && (
                        <span className="rounded bg-destructive/15 px-1.5 py-0.5 text-xs font-medium text-destructive">
                          {t('pluginStore.card.incompatibleBadge')}
                        </span>
                      )}
                    </div>
                  </div>
                  {entry && (
                    <Button size="sm" variant="outline" onClick={() => setViewing(repo)}>
                      {t('pluginStore.card.details')}
                    </Button>
                  )}
                  {canManage && entry && (
                    <Button
                      size="sm"
                      onClick={() => setInstalling(repo)}
                      disabled={!online || locked || busy || !entry.compatible}
                    >
                      {t('settings.extensions.store.install')}
                    </Button>
                  )}
                  {canManage && (
                    <Button
                      size="sm"
                      variant="outline"
                      onClick={() => setRemoving(repo)}
                      disabled={busy}
                    >
                      {t('settings.extensions.store.remove')}
                    </Button>
                  )}
                </div>
                {entry && (
                  <p className="text-muted-foreground break-words">
                    {localizedText(entry.summary, i18n.language)}
                  </p>
                )}
                {repo.instances.length > 0 && (
                  <p className="text-xs text-muted-foreground">
                    {t('settings.extensions.add.instances', { count: repo.instances.length })}
                    {repo.update_available &&
                      ` · ${t('settings.extensions.store.updateAvailable')}`}
                  </p>
                )}
                <p className="text-xs text-muted-foreground" data-testid="custom-repo-checked">
                  {repo.checked_at
                    ? t('pluginStore.card.checkedAt', {
                        time: new Date(repo.checked_at * 1000).toLocaleString(i18n.language),
                      })
                    : t('pluginStore.card.neverChecked')}
                </p>
                {entry && !entry.compatible && (
                  <p className="text-xs text-destructive" data-testid="custom-repo-incompatible">
                    {t('pluginStore.card.incompatible')}
                  </p>
                )}
                {entry && online && locked && canManage && (
                  <p className="text-xs text-muted-foreground">
                    {t('pluginStore.card.disabledLocked')}
                  </p>
                )}
                {repo.last_error && (
                  <p className="text-xs text-destructive" data-testid="custom-repo-error">
                    {t(`errors.${repo.last_error}`, {
                      defaultValue: t('settings.extensions.store.checkFailed'),
                    })}
                  </p>
                )}
              </li>
            );
          })}
        </ul>
      )}

      <Dialog open={removing !== null} onOpenChange={(o) => !o && setRemoving(null)}>
        <DialogContent className="w-[calc(100%-2rem)] max-w-md">
          <DialogHeader>
            <DialogTitle>
              {t('settings.extensions.store.removeTitle', {
                repository: removing?.display_name ?? '',
              })}
            </DialogTitle>
            <DialogDescription>{t('settings.extensions.store.removeIntro')}</DialogDescription>
          </DialogHeader>
          <DialogFooter className="gap-2">
            <Button variant="outline" onClick={() => setRemoving(null)} disabled={busy}>
              {t('settings.extensions.cancel')}
            </Button>
            <Button variant="destructive" onClick={remove} disabled={busy}>
              {t('settings.extensions.store.remove')}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      <Dialog open={viewing !== null} onOpenChange={(o) => !o && setViewing(null)}>
        <DialogContent className="max-h-[90dvh] w-[calc(100%-2rem)] max-w-2xl overflow-y-auto">
          {viewing && (
            <PluginPage
              repo={viewing}
              canManage={canManage}
              canInstall={online && !locked && !busy}
              onClose={() => setViewing(null)}
              onInstall={() => {
                setInstalling(viewing);
                setViewing(null);
              }}
            />
          )}
        </DialogContent>
      </Dialog>

      <Dialog open={installing !== null} onOpenChange={(o) => !o && setInstalling(null)}>
        <DialogContent className="max-h-[90dvh] w-[calc(100%-2rem)] max-w-lg overflow-y-auto">
          {installing && (
            <CustomInstallBody
              repo={installing}
              onClose={() => setInstalling(null)}
              onInstalled={() => {
                setInstalling(null);
                onInstalled();
                load();
              }}
            />
          )}
        </DialogContent>
      </Dialog>
    </div>
  );
}
