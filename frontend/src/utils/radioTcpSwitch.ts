import type { HealthStatus, RadioTransportConfig, RadioTransportKind } from '../types';

/**
 * Rules shared by the Wi-Fi panel and the post-reboot toast when they switch
 * Meshloom to TCP at the radio's Wi-Fi address.
 */

/** How long after the TCP switch resolved Meshloom may still be reconnecting. */
export const TCP_SWITCH_REVERT_DELAY_MS = 30_000;

/**
 * Health says Meshloom reaches the radio at `ip:port` over TCP. Health from
 * before the switch (the Bluetooth or USB session, still `radio_connected`
 * because neither the disconnect nor a failed reconnect broadcasts health) never
 * matches.
 */
export function connectedOverTcp(
  health: HealthStatus | null | undefined,
  ip: string,
  port: number
): boolean {
  if (!health?.radio_connected) return false;
  return (health.connection_info ?? '').replace(/\s+/g, '') === `TCP:${ip}:${port}`;
}

/**
 * The saved transport is the TCP target the switch asked for. A PUT that failed
 * before saving (validation, proxy loop, busy radio) leaves the old transport,
 * whose `previous_transport` belongs to some older change: going back to it then
 * would move Meshloom somewhere the user never left from here.
 */
export function tcpSwitchApplied(
  transport: RadioTransportConfig | null | undefined,
  ip: string,
  port: number
): boolean {
  return Boolean(
    transport &&
    transport.transport === 'tcp' &&
    transport.tcp_host === ip &&
    transport.tcp_port === port
  );
}

export function transportKindLabelKey(kind: RadioTransportKind | null | undefined): string {
  if (kind === 'serial') return 'settings.radio.wifi.kindSerial';
  if (kind === 'ble') return 'settings.radio.wifi.kindBle';
  if (kind === 'tcp') return 'settings.radio.wifi.kindTcp';
  return 'settings.radio.wifi.kindPrevious';
}
