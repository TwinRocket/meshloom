import { api, formatApiError } from '../api';
import i18n from '../i18n';
import type { RadioTransportConfig, RadioWifiState } from '../types';
import {
  connectedOverTcp,
  TCP_SWITCH_REVERT_DELAY_MS,
  tcpSwitchApplied,
  transportKindLabelKey,
} from '../utils/radioTcpSwitch';
import { toast } from './ui/sonner';

/**
 * After a reboot started from the Wi-Fi panel or prompt, the first Wi-Fi state
 * with an address offers to switch Meshloom to TCP, unless it already uses TCP.
 * The switch follows the panel's rules: the way back is offered when the switch
 * was saved but failed, or when the radio is still unreachable 30 s later.
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
      onClick: () => void switchToTcpFromToast(ip, port),
    },
  });
}

export async function switchToTcpFromToast(ip: string, port: number): Promise<void> {
  try {
    await api.updateRadioTransport({ transport: 'tcp', tcp_host: ip, tcp_port: port });
  } catch (err) {
    const description = formatApiError(err, i18n.t) || undefined;
    const fresh = await api.getRadioTransport().catch(() => null);
    if (tcpSwitchApplied(fresh, ip, port)) {
      offerRevert(fresh as RadioTransportConfig, i18n.t('settings.radio.wifi.switchTcpFailed'), {
        description,
      });
    } else {
      toast.error(i18n.t('settings.radio.wifi.switchTcpFailed'), { description });
    }
    return;
  }
  toast.success(i18n.t('settings.radio.wifi.switchTcpDone', { ip, port }));
  setTimeout(() => {
    void (async () => {
      const health = await api.getHealth().catch(() => null);
      if (!health || connectedOverTcp(health, ip, port)) return;
      const fresh = await api.getRadioTransport().catch(() => null);
      // Changed again meanwhile (by the user, or another tab): not ours to undo.
      if (!tcpSwitchApplied(fresh, ip, port)) return;
      offerRevert(
        fresh as RadioTransportConfig,
        i18n.t('settings.radio.wifi.switchTcpRevertTimeout')
      );
    })();
  }, TCP_SWITCH_REVERT_DELAY_MS);
}

function offerRevert(
  transport: RadioTransportConfig,
  message: string,
  { description }: { description?: string } = {}
): void {
  const kind = transport.previous_transport_kind;
  if (!kind) {
    toast.error(message, { description });
    return;
  }
  const kindLabel = i18n.t(transportKindLabelKey(kind));
  toast.error(message, {
    description,
    duration: 60000,
    action: {
      label: i18n.t('settings.radio.wifi.switchTcpRevert', { kind: kindLabel }),
      onClick: () => {
        api
          .revertRadioTransport()
          .then(() =>
            toast.success(i18n.t('settings.radio.wifi.switchTcpReverted', { kind: kindLabel }))
          )
          .catch((err: unknown) =>
            toast.error(i18n.t('settings.radio.wifi.switchTcpRevertFailed'), {
              description: formatApiError(err, i18n.t),
            })
          );
      },
    },
  });
}
