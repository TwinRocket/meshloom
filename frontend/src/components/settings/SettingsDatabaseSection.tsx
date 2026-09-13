import { useState, useEffect, useRef } from 'react';
import { Trans, useTranslation } from 'react-i18next';
import { Input } from '../ui/input';
import { Label } from '../ui/label';
import { Button } from '../ui/button';
import { Separator } from '../ui/separator';
import { toast } from '../ui/sonner';
import { api } from '../../api';
import { formatTime } from '../../utils/messageParser';
import type { AppSettings, AppSettingsUpdate, HealthStatus } from '../../types';
import { SettingsGroupHeader } from './settingsPrimitives';

export function SettingsDatabaseSection({
  appSettings,
  health,
  onSaveAppSettings,
  onHealthRefresh,
  className,
}: {
  appSettings: AppSettings;
  health: HealthStatus | null;
  onSaveAppSettings: (update: AppSettingsUpdate) => Promise<void>;
  onHealthRefresh: () => Promise<void>;
  className?: string;
}) {
  const { t } = useTranslation();
  const [retentionDays, setRetentionDays] = useState('14');
  const [cleaning, setCleaning] = useState(false);
  const [purgingDecryptedRaw, setPurgingDecryptedRaw] = useState(false);
  const [autoDecryptOnAdvert, setAutoDecryptOnAdvert] = useState(false);
  const [downloadingDb, setDownloadingDb] = useState(false);
  const [downloadingJson, setDownloadingJson] = useState(false);
  const [restoring, setRestoring] = useState(false);
  const [restoreConfirm, setRestoreConfirm] = useState(false);

  const saveChainRef = useRef<Promise<void>>(Promise.resolve());

  useEffect(() => {
    setAutoDecryptOnAdvert(appSettings.auto_decrypt_dm_on_advert);
  }, [appSettings]);

  const handleCleanup = async () => {
    const days = parseInt(retentionDays, 10);
    if (isNaN(days) || days < 1) {
      toast.error(t('settings.database.invalidRetention'), {
        description: t('settings.database.invalidRetentionHelp'),
      });
      return;
    }

    setCleaning(true);

    try {
      const result = await api.runMaintenance({ pruneUndecryptedDays: days });
      toast.success(t('settings.database.cleanupComplete'), {
        description: t('settings.database.deletedPackets', { count: result.packets_deleted }),
      });
      await onHealthRefresh();
    } catch (err) {
      console.error('Failed to run maintenance:', err);
      toast.error(t('settings.database.cleanupFailed'), {
        description: err instanceof Error ? err.message : t('settings.database.unknownError'),
      });
    } finally {
      setCleaning(false);
    }
  };

  const handlePurgeDecryptedRawPackets = async () => {
    setPurgingDecryptedRaw(true);

    try {
      const result = await api.runMaintenance({ purgeLinkedRawPackets: true });
      toast.success(t('settings.database.purgeComplete'), {
        description: t('settings.database.purgedPackets', { count: result.packets_deleted }),
      });
      await onHealthRefresh();
    } catch (err) {
      console.error('Failed to purge decrypted raw packets:', err);
      toast.error(t('settings.database.purgeFailed'), {
        description: err instanceof Error ? err.message : t('settings.database.unknownError'),
      });
    } finally {
      setPurgingDecryptedRaw(false);
    }
  };

  const persistAppSettings = (update: AppSettingsUpdate, revert: () => void): Promise<void> => {
    const chained = saveChainRef.current.then(async () => {
      try {
        await onSaveAppSettings(update);
      } catch (err) {
        console.error('Failed to save database settings:', err);
        revert();
        toast.error(t('settings.database.saveFailed'), {
          description: err instanceof Error ? err.message : t('settings.database.unknownError'),
        });
      }
    });
    saveChainRef.current = chained;
    return chained;
  };

  return (
    <div className={className}>
      {/* ── Database Overview ── */}
      <div className="space-y-3">
        <SettingsGroupHeader title={t('settings.database.overview')} storedOn="server" instant />
        <div className="rounded-md border border-border bg-muted/30 p-3 space-y-2">
          <div className="flex justify-between items-center">
            <span className="text-sm">{t('settings.database.size')}</span>
            <span className="text-sm font-semibold">{health?.database_size_mb ?? '?'} MB</span>
          </div>
          <div className="flex justify-between items-center">
            <span className="text-sm">{t('settings.database.oldestUndecrypted')}</span>
            {health?.oldest_undecrypted_timestamp ? (
              <span className="text-sm font-semibold">
                {formatTime(health.oldest_undecrypted_timestamp)}
                <span className="font-normal text-muted-foreground ml-1">
                  {t('settings.database.daysOld', {
                    count: Math.floor(
                      (Date.now() / 1000 - health.oldest_undecrypted_timestamp) / 86400
                    ),
                  })}
                </span>
              </span>
            ) : (
              <span className="text-sm text-muted-foreground">{t('settings.database.none')}</span>
            )}
          </div>
        </div>
      </div>

      <Separator />

      {/* ── Storage Cleanup ── */}
      <div className="space-y-4">
        <SettingsGroupHeader title={t('settings.database.cleanup')} storedOn="server" instant />

        <div className="rounded-md border border-border p-3 space-y-2">
          <h3 className="text-sm font-semibold">{t('settings.database.deleteUndecrypted')}</h3>
          <p className="text-[0.8125rem] text-muted-foreground">
            {t('settings.database.deleteUndecryptedHelp')}
          </p>
          <div className="flex gap-2 items-end">
            <div className="space-y-1">
              <Label htmlFor="retention-days" className="text-xs text-muted-foreground">
                {t('settings.database.olderThan')}
              </Label>
              <Input
                id="retention-days"
                type="number"
                min="1"
                max="365"
                value={retentionDays}
                onChange={(e) => setRetentionDays(e.target.value)}
                className="w-24"
              />
            </div>
            <Button
              variant="outline"
              onClick={handleCleanup}
              disabled={cleaning}
              className="border-destructive/50 text-destructive hover:bg-destructive/10"
            >
              {cleaning ? t('settings.database.deleting') : t('settings.delete')}
            </Button>
          </div>
        </div>

        <div className="rounded-md border border-border p-3 space-y-2">
          <h3 className="text-sm font-semibold">{t('settings.database.purgeArchival')}</h3>
          <p className="text-[0.8125rem] text-muted-foreground">
            {t('settings.database.purgeArchivalHelp')}
          </p>
          <Button
            variant="outline"
            onClick={handlePurgeDecryptedRawPackets}
            disabled={purgingDecryptedRaw}
            className="w-full border-warning/50 text-warning hover:bg-warning/10"
          >
            {purgingDecryptedRaw
              ? t('settings.database.purging')
              : t('settings.database.purgeArchivalButton')}
          </Button>
        </div>
      </div>

      <Separator />

      {/* ── DM Decryption ── */}
      <div className="space-y-3">
        <SettingsGroupHeader title={t('settings.database.dmDecrypt')} storedOn="server" instant />
        <label className="flex items-center gap-3 cursor-pointer">
          <input
            type="checkbox"
            checked={autoDecryptOnAdvert}
            onChange={(e) => {
              const next = e.target.checked;
              const prev = autoDecryptOnAdvert;
              setAutoDecryptOnAdvert(next);
              void persistAppSettings({ auto_decrypt_dm_on_advert: next }, () =>
                setAutoDecryptOnAdvert(prev)
              );
            }}
            className="w-4 h-4 rounded border-input accent-primary"
          />
          <span className="text-sm">{t('settings.database.autoDecrypt')}</span>
        </label>
        <p className="text-[0.8125rem] text-muted-foreground">
          {t('settings.database.autoDecryptHelp')}
        </p>
      </div>

      <Separator />

      <div className="space-y-4">
        <SettingsGroupHeader title={t('settings.backupTitle')} storedOn="server" instant />
        <p className="text-[0.8125rem] text-muted-foreground">
          <Trans i18nKey="settings.backupKeyWarning" />
        </p>

        <div className="rounded-md border border-border p-3 space-y-2">
          <h4 className="text-sm font-semibold">{t('settings.backupDbTitle')}</h4>
          <p className="text-[0.8125rem] text-muted-foreground">{t('settings.backupDbHelp')}</p>
          <Button
            variant="outline"
            disabled={downloadingDb}
            onClick={async () => {
              setDownloadingDb(true);
              try {
                await api.downloadDatabaseBackup();
              } catch (err) {
                toast.error(t('settings.backupDbFailed'), {
                  description:
                    err instanceof Error ? err.message : t('settings.database.unknownError'),
                });
              } finally {
                setDownloadingDb(false);
              }
            }}
          >
            {downloadingDb ? t('settings.backupDbDownloading') : t('settings.backupDbDownload')}
          </Button>
        </div>

        <div className="rounded-md border border-border p-3 space-y-2">
          <h4 className="text-sm font-semibold">{t('settings.backupJsonTitle')}</h4>
          <p className="text-[0.8125rem] text-muted-foreground">{t('settings.backupJsonHelp')}</p>
          <Button
            variant="outline"
            disabled={downloadingJson}
            onClick={async () => {
              setDownloadingJson(true);
              try {
                const data = await api.getJsonBackup();
                const blob = new Blob([JSON.stringify(data, null, 2)], {
                  type: 'application/json',
                });
                const href = URL.createObjectURL(blob);
                const a = document.createElement('a');
                a.href = href;
                a.download = 'meshloom-backup.json';
                a.click();
                URL.revokeObjectURL(href);
              } catch (err) {
                toast.error(t('settings.backupJsonFailed'), {
                  description:
                    err instanceof Error ? err.message : t('settings.database.unknownError'),
                });
              } finally {
                setDownloadingJson(false);
              }
            }}
          >
            {downloadingJson ? t('settings.backupJsonExporting') : t('settings.backupJsonDownload')}
          </Button>
        </div>

        <div className="rounded-md border border-warning/40 p-3 space-y-2">
          <h4 className="text-sm font-semibold">{t('settings.restoreJsonTitle')}</h4>
          <p className="text-[0.8125rem] text-muted-foreground">{t('settings.restoreJsonHelp')}</p>
          <label className="flex items-center gap-3 cursor-pointer">
            <input
              type="checkbox"
              checked={restoreConfirm}
              onChange={(e) => setRestoreConfirm(e.target.checked)}
              className="w-4 h-4 rounded border-input accent-primary"
            />
            <span className="text-sm">{t('settings.restoreConfirm')}</span>
          </label>
          <Input
            type="file"
            accept="application/json,.json"
            disabled={!restoreConfirm || restoring}
            onChange={async (e) => {
              const file = e.target.files?.[0];
              e.target.value = '';
              if (!file || !restoreConfirm) return;
              setRestoring(true);
              try {
                const parsed = JSON.parse(await file.text());
                const result = await api.restoreJsonBackup(parsed);
                toast.success(t('settings.restoreMerged'), {
                  description: t('settings.restoreMergedDetail', {
                    contacts: result.contacts_upserted,
                    channels: result.channels_upserted,
                    groups: result.groups_upserted,
                  }),
                });
                await onHealthRefresh();
              } catch (err) {
                toast.error(t('settings.restoreFailed'), {
                  description:
                    err instanceof Error ? err.message : t('settings.database.unknownError'),
                });
              } finally {
                setRestoring(false);
              }
            }}
          />
        </div>
      </div>
    </div>
  );
}
