import { useState } from 'react';
import { useTranslation } from 'react-i18next';
import { Button } from '../ui/button';
import { Input } from '../ui/input';
import { Label } from '../ui/label';
import { toast } from '../ui/sonner';
import { api, formatApiError } from '../../api';

export const PASSPHRASE_MIN_LENGTH = 8;

/** Export warning codes -> i18n keys (codes come from the backend export). */
export function fullBackupWarningKey(code: string): string | null {
  if (code === 'private_key_export_disabled') return 'settings.fullBackup.warningIdentityDisabled';
  if (code === 'private_key_unavailable') return 'settings.fullBackup.warningNoIdentity';
  if (code === 'radio_not_connected') return 'settings.fullBackup.warningRadioDisconnected';
  if (code === 'radio_not_ready') return 'settings.fullBackup.warningRadioNotReady';
  if (code === 'wifi_not_stored') return 'settings.fullBackup.warningWifiNotStored';
  if (code === 'wifi_read_failed') return 'settings.fullBackup.warningWifiReadFailed';
  if (code.startsWith('radio_read_failed:')) return 'settings.fullBackup.warningReadFailed';
  if (code.startsWith('section_sensitive_unencrypted:')) {
    return 'settings.fullBackup.warningSensitiveUnencrypted';
  }
  if (code.startsWith('section_export_failed:')) {
    return 'settings.fullBackup.warningSectionExportFailed';
  }
  return null;
}

/** Warnings that mean the file cannot bring the radio's identity back. */
function missesIdentity(code: string): boolean {
  return (
    code === 'private_key_export_disabled' ||
    code === 'private_key_unavailable' ||
    code === 'radio_not_connected' ||
    code === 'radio_not_ready'
  );
}

function saveJson(data: unknown, filename: string) {
  const blob = new Blob([JSON.stringify(data, null, 2)], { type: 'application/json' });
  const href = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = href;
  a.download = filename;
  a.click();
  URL.revokeObjectURL(href);
}

/** Settings > Database: download the full backup, optionally encrypted. */
export function FullBackupExport() {
  const { t } = useTranslation();
  const [encrypt, setEncrypt] = useState(false);
  const [passphrase, setPassphrase] = useState('');
  const [confirmation, setConfirmation] = useState('');
  const [busy, setBusy] = useState(false);
  const [warnings, setWarnings] = useState<string[]>([]);

  const tooShort = encrypt && passphrase.length < PASSPHRASE_MIN_LENGTH;
  const mismatch = encrypt && confirmation !== passphrase;
  const canDownload = !busy && (!encrypt || (!tooShort && !mismatch));

  const download = async () => {
    setBusy(true);
    try {
      const result = await api.exportFullBackup(encrypt ? passphrase : undefined);
      const keys = [
        ...new Set(
          result.warnings.map(fullBackupWarningKey).filter((key): key is string => key !== null)
        ),
      ];
      setWarnings(keys);
      saveJson(result.data, result.filename);
      // Done with the passphrase: never keep it in state or in the inputs.
      setPassphrase('');
      setConfirmation('');
      if (result.warnings.some(missesIdentity)) {
        // A user about to reflash must not read this as a complete backup.
        toast.warning(t('settings.fullBackup.downloadedNoIdentity'));
      } else {
        toast.success(t('settings.fullBackup.downloaded'));
      }
    } catch (err) {
      toast.error(t('settings.fullBackup.failed'), { description: formatApiError(err, t) });
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="rounded-md border border-border p-3 space-y-3">
      <h4 className="text-sm font-semibold">{t('settings.fullBackup.title')}</h4>
      <p className="text-[0.8125rem] text-muted-foreground">{t('settings.fullBackup.help')}</p>
      <p
        className="text-[0.8125rem] rounded-md border border-warning/40 bg-warning/10 p-2"
        role="note"
      >
        {t('settings.fullBackup.keyWarning')}
      </p>

      <label className="flex items-center gap-3 cursor-pointer">
        <input
          type="checkbox"
          checked={encrypt}
          onChange={(e) => setEncrypt(e.target.checked)}
          className="w-4 h-4 rounded border-input accent-primary"
        />
        <span className="text-sm">{t('settings.fullBackup.encrypt')}</span>
      </label>

      {encrypt && (
        <div className="space-y-2">
          <p className="text-[0.8125rem] text-muted-foreground">
            {t('settings.fullBackup.encryptHelp')}
          </p>
          <div className="space-y-1">
            <Label htmlFor="full-backup-passphrase" className="text-xs">
              {t('settings.fullBackup.passphrase')}
            </Label>
            <Input
              id="full-backup-passphrase"
              type="password"
              autoComplete="new-password"
              value={passphrase}
              onChange={(e) => setPassphrase(e.target.value)}
            />
          </div>
          <div className="space-y-1">
            <Label htmlFor="full-backup-passphrase-confirm" className="text-xs">
              {t('settings.fullBackup.passphraseConfirm')}
            </Label>
            <Input
              id="full-backup-passphrase-confirm"
              type="password"
              autoComplete="new-password"
              value={confirmation}
              onChange={(e) => setConfirmation(e.target.value)}
            />
          </div>
          {passphrase.length > 0 && tooShort && (
            <p className="text-xs text-destructive">
              {t('settings.fullBackup.passphraseTooShort')}
            </p>
          )}
          {!tooShort && confirmation.length > 0 && mismatch && (
            <p className="text-xs text-destructive">
              {t('settings.fullBackup.passphraseMismatch')}
            </p>
          )}
        </div>
      )}

      <Button variant="outline" disabled={!canDownload} onClick={download}>
        {busy ? t('settings.fullBackup.downloading') : t('settings.fullBackup.download')}
      </Button>

      {warnings.length > 0 && (
        <div className="space-y-1" role="status">
          <p className="text-xs font-semibold">{t('settings.fullBackup.warningsTitle')}</p>
          <ul className="list-disc pl-5 text-[0.8125rem] text-muted-foreground space-y-0.5">
            {warnings.map((key) => (
              <li key={key}>{t(key)}</li>
            ))}
          </ul>
        </div>
      )}
    </div>
  );
}
