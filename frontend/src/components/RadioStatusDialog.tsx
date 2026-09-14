import { useTranslation } from 'react-i18next';
import type { HealthStatus } from '../types';
import { Button } from './ui/button';
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from './ui/dialog';
import { cn } from '../lib/utils';

/**
 * What the radio is doing, and how this app is reaching it.
 *
 * The status in the rail is a dot: enough to notice something is wrong, never
 * enough to act on it. The questions it raises — which radio, over what, is the
 * proxy in the way — were answerable only by opening the radio settings and
 * reading a form meant for editing. This answers them without offering to change
 * anything, and the way to the settings is one button rather than the only route.
 */

interface Props {
  open: boolean;
  health: HealthStatus | null;
  onClose: () => void;
  onOpenRadioSettings: () => void;
}

function Row({ label, value }: { label: string; value: string }) {
  return (
    <div className="flex items-baseline justify-between gap-4 border-b border-border/30 py-2 last:border-b-0">
      <dt className="shrink-0 text-[0.6875rem] uppercase tracking-wider text-muted-foreground">
        {label}
      </dt>
      <dd className="min-w-0 break-words text-right text-sm">{value}</dd>
    </div>
  );
}

export function RadioStatusDialog({ open, health, onClose, onOpenRadioSettings }: Props) {
  const { t } = useTranslation();

  const state = health?.radio_state;
  const connected = health?.radio_connected === true;
  const settling = state === 'connecting' || state === 'initializing';
  const stateLabel = settling
    ? t('statusBar.radioConnecting')
    : state === 'paused'
      ? t('statusBar.radioPaused')
      : connected
        ? t('statusBar.radioOk')
        : t('statusBar.radioDisconnected');

  const device = health?.radio_device_info;
  const proxy = health?.radio_proxy;
  const rows: { label: string; value: string }[] = [];

  if (health?.connection_info) {
    rows.push({ label: t('radioStatus.transport'), value: health.connection_info });
  } else if (health?.transport_configured === false) {
    rows.push({ label: t('radioStatus.transport'), value: t('radioStatus.notConfigured') });
  }
  if (device?.model) rows.push({ label: t('radioStatus.model'), value: device.model });
  if (device?.firmware_version) {
    rows.push({ label: t('radioStatus.firmware'), value: device.firmware_version });
  }
  if (proxy?.enabled) {
    rows.push({
      label: t('radioStatus.proxy'),
      value: t('radioStatus.proxyListening', { port: proxy.port }),
    });
  }
  if (health?.app_info?.version) {
    rows.push({ label: t('radioStatus.version'), value: health.app_info.version });
  }

  return (
    <Dialog open={open} onOpenChange={(next) => !next && onClose()}>
      <DialogContent className="sm:max-w-md">
        <DialogHeader>
          <DialogTitle className="flex items-center gap-2">
            {/* Shape and word, never colour alone. */}
            <span
              className={cn(
                'h-2.5 w-2.5 shrink-0 rounded-full',
                settling
                  ? 'bg-warning'
                  : connected
                    ? 'bg-status-connected'
                    : 'bg-status-disconnected'
              )}
              aria-hidden="true"
            />
            {stateLabel}
          </DialogTitle>
          <DialogDescription>
            {connected ? t('radioStatus.connectedHelp') : t('radioStatus.disconnectedHelp')}
          </DialogDescription>
        </DialogHeader>

        {rows.length > 0 && (
          <dl className="rounded-xl bg-muted/40 px-4 py-1">
            {rows.map((row) => (
              <Row key={row.label} {...row} />
            ))}
          </dl>
        )}

        <DialogFooter>
          <Button type="button" variant="outline" onClick={onClose}>
            {t('radioStatus.close')}
          </Button>
          <Button
            type="button"
            onClick={() => {
              onClose();
              onOpenRadioSettings();
            }}
          >
            {t('radioStatus.openSettings')}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
