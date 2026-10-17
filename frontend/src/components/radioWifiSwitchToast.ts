import { api, formatApiError } from '../api';
import i18n from '../i18n';
import type { RadioWifiState } from '../types';
import { toast } from './ui/sonner';

/**
 * After a reboot started from the Wi-Fi panel or prompt, the first Wi-Fi state
 * with an address offers to switch Meshloom to TCP, unless it already uses TCP.
 */
export async function showWifiSwitchToast(state: RadioWifiState): Promise<void> {
  const ip = state.ip;
  if (!ip) return;
  const transport = await api.getRadioTransport().catch(() => null);
  if (!transport || transport.transport === 'tcp') return;
  const port = state.tcp_port || 5000;
  toast.info(i18n.t('settings.radio.wifi.switchTcpToast', { ip }), {
    duration: 20000,
    action: {
      label: i18n.t('settings.radio.wifi.switchTcpAction'),
      onClick: () => {
        api
          .updateRadioTransport({ transport: 'tcp', tcp_host: ip, tcp_port: port })
          .then(() => toast.success(i18n.t('settings.radio.wifi.switchTcpDone', { ip, port })))
          .catch((err: unknown) =>
            toast.error(i18n.t('settings.radio.wifi.switchTcpFailed'), {
              description: formatApiError(err, i18n.t),
            })
          );
      },
    },
  });
}
