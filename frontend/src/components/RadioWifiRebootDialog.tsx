import { useTranslation } from 'react-i18next';

import { Button } from './ui/button';
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from './ui/dialog';

/**
 * Confirm before the radio reboots to apply Wi-Fi settings. Confirming calls
 * `POST /api/radio/wifi/reboot` (a planned `wifi_config` reboot); "Later" leaves
 * the change pending, shown in Settings > Radio with "Reboot now".
 */
export function RadioWifiRebootDialog({
  open,
  isTcp,
  busy,
  error,
  onConfirm,
  onLater,
}: {
  open: boolean;
  isTcp: boolean;
  busy: boolean;
  error?: string | null;
  onConfirm: () => void;
  onLater: () => void;
}) {
  const { t } = useTranslation();
  return (
    <Dialog
      open={open}
      onOpenChange={(next) => {
        if (!next && !busy) onLater();
      }}
    >
      <DialogContent className="max-w-md">
        <DialogHeader>
          <DialogTitle>{t('settings.radio.wifi.confirmRebootTitle')}</DialogTitle>
          <DialogDescription>{t('settings.radio.wifi.confirmRebootBody')}</DialogDescription>
        </DialogHeader>
        {isTcp && (
          <p className="text-sm text-warning" role="note">
            {t('settings.radio.wifi.tcpWarning')}
          </p>
        )}
        {error && (
          <p className="text-sm text-destructive" role="alert">
            {error}
          </p>
        )}
        <DialogFooter className="gap-2">
          <Button type="button" variant="secondary" disabled={busy} onClick={onLater}>
            {t('settings.radio.wifi.rebootLater')}
          </Button>
          <Button type="button" disabled={busy} onClick={onConfirm}>
            {busy ? t('settings.radio.wifi.rebooting') : t('settings.radio.wifi.rebootNow')}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
