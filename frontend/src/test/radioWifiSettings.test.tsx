import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { ApiError, api } from '../api';
import {
  RadioWifiSettings,
  TCP_SWITCH_REVERT_DELAY_MS,
} from '../components/settings/RadioWifiSettings';
import i18n from '../i18n';
import {
  noteRadioWifiHealth,
  resetRadioWifiStoreForTests,
  setRadioWifiState,
} from '../stores/radioWifiStore';
import type { HealthStatus, RadioTransportConfig, RadioWifiState } from '../types';

const PK = 'ab'.repeat(32);

function wifiState(overrides: Partial<RadioWifiState> = {}): RadioWifiState {
  return {
    check: 'done',
    supported: true,
    ssid: 'FlasherNet',
    enabled: true,
    connected: false,
    ip: null,
    tcp_port: 5000,
    credentials_stored: false,
    open_network: false,
    prompt_needed: false,
    reboot_pending: false,
    store_incomplete: false,
    radio_public_key: PK,
    ...overrides,
  };
}

function transport(overrides: Partial<RadioTransportConfig> = {}): RadioTransportConfig {
  return {
    configured: true,
    transport: 'ble',
    serial_port: '',
    serial_baudrate: 115200,
    tcp_host: '',
    tcp_port: 5000,
    ble_address: 'AA:BB:CC:DD:EE:FF',
    ble_pin_configured: true,
    bound_public_key: PK,
    previous_transport_kind: null,
    capabilities: {
      tcp: true,
      serial: true,
      ble: true,
      serial_unavailable_reason: null,
      ble_unavailable_reason: null,
    },
    serial_ports: [],
    ...overrides,
  };
}

const connectedHealth: HealthStatus = {
  status: 'ok',
  radio_connected: true,
  radio_initializing: false,
  radio_state: 'connected',
  connection_info: 'BLE: AA:BB:CC:DD:EE:FF',
  database_size_mb: 1,
  oldest_undecrypted_timestamp: null,
  fanout_statuses: {},
  bots_disabled: false,
};

const disconnectedHealth: HealthStatus = {
  ...connectedHealth,
  status: 'degraded',
  radio_connected: false,
  radio_state: 'connecting',
};

function changeResult(state: RadioWifiState, rebootRequired = false, rebooting = false) {
  return { state, reboot_required: rebootRequired, rebooting };
}

function show(state: RadioWifiState) {
  act(() => setRadioWifiState(state));
}

async function fillCredentials(ssid: string | null, password: string) {
  const dialog = await screen.findByRole('dialog');
  const ssidInput = within(dialog).getByLabelText(i18n.t('settings.radio.wifi.ssid'));
  if (ssid !== null) {
    fireEvent.change(ssidInput, { target: { value: ssid } });
  }
  fireEvent.change(within(dialog).getByLabelText(i18n.t('settings.radio.wifi.password')), {
    target: { value: password },
  });
  fireEvent.change(within(dialog).getByLabelText(i18n.t('settings.radio.wifi.passwordConfirm')), {
    target: { value: password },
  });
  return dialog;
}

describe('RadioWifiSettings', () => {
  beforeEach(() => {
    resetRadioWifiStoreForTests();
    try {
      window.localStorage.clear();
    } catch {
      // ignore
    }
  });

  afterEach(() => {
    vi.restoreAllMocks();
    vi.useRealTimers();
  });

  it('is hidden when the radio has no CLI or no Wi-Fi', async () => {
    vi.spyOn(api, 'getRadioTransport').mockResolvedValue(transport());
    const { container } = render(<RadioWifiSettings health={connectedHealth} />);
    show(wifiState({ supported: false, unsupported_reason: 'no_wifi', ssid: null }));
    await waitFor(() => expect(api.getRadioTransport).toHaveBeenCalled());
    expect(container).toBeEmptyDOMElement();
  });

  it('says it is checking while the state is pending', () => {
    vi.spyOn(api, 'getRadioTransport').mockResolvedValue(transport());
    render(<RadioWifiSettings health={connectedHealth} />);
    show(wifiState({ check: 'pending', supported: null }));
    expect(screen.getByText(i18n.t('settings.radio.wifi.checking'))).toBeInTheDocument();
  });

  it('shows stored yes/no and the open network note', () => {
    vi.spyOn(api, 'getRadioTransport').mockResolvedValue(transport());
    render(<RadioWifiSettings health={connectedHealth} />);
    show(wifiState({ credentials_stored: false }));
    expect(screen.getByText(i18n.t('settings.radio.wifi.storedNo'))).toBeInTheDocument();
    expect(
      screen.getByRole('button', { name: i18n.t('settings.radio.wifi.storeNow') })
    ).toBeInTheDocument();
    show(wifiState({ credentials_stored: true, open_network: true }));
    expect(screen.getByText(i18n.t('settings.radio.wifi.storedYes'))).toBeInTheDocument();
    expect(
      screen.getByText(i18n.t('settings.radio.wifi.openNetworkStored'), { exact: false })
    ).toBeInTheDocument();
    expect(
      screen.queryByRole('button', { name: i18n.t('settings.radio.wifi.storeNow') })
    ).not.toBeInTheDocument();
  });

  it('offers Reboot now next to a pending change, after a confirm', async () => {
    vi.spyOn(api, 'getRadioTransport').mockResolvedValue(transport());
    const reboot = vi
      .spyOn(api, 'rebootRadioForWifi')
      .mockResolvedValue(changeResult(wifiState(), false, true));
    render(<RadioWifiSettings health={connectedHealth} />);
    show(wifiState({ reboot_pending: true, credentials_stored: true }));

    expect(screen.getByText(i18n.t('settings.radio.wifi.rebootPending'))).toBeInTheDocument();
    await userEvent.click(
      screen.getByRole('button', { name: i18n.t('settings.radio.wifi.rebootNow') })
    );
    const dialog = await screen.findByRole('dialog');
    expect(reboot).not.toHaveBeenCalled();
    await userEvent.click(
      within(dialog).getByRole('button', { name: i18n.t('settings.radio.wifi.rebootNow') })
    );
    await waitFor(() => expect(reboot).toHaveBeenCalledTimes(1));
  });

  it('replaces Enable with Set up Wi-Fi when the radio has no network name', async () => {
    vi.spyOn(api, 'getRadioTransport').mockResolvedValue(transport());
    render(<RadioWifiSettings health={connectedHealth} />);
    show(wifiState({ ssid: null, enabled: false }));

    expect(
      screen.queryByRole('button', { name: i18n.t('settings.radio.wifi.enable') })
    ).not.toBeInTheDocument();
    expect(screen.getByText(i18n.t('settings.radio.wifi.setUpHelp'))).toBeInTheDocument();
    await userEvent.click(
      screen.getByRole('button', { name: i18n.t('settings.radio.wifi.setUp') })
    );
    const dialog = await screen.findByRole('dialog');
    expect(within(dialog).getByLabelText(i18n.t('settings.radio.wifi.ssid'))).toHaveValue('');
  });

  it('shows the interrupted-store alert from the server state, with no reboot action', async () => {
    vi.spyOn(api, 'getRadioTransport').mockResolvedValue(transport());
    render(<RadioWifiSettings health={connectedHealth} />);
    show(
      wifiState({
        store_incomplete: true,
        store_target_ssid: 'NewNet',
        reboot_pending: true,
        credentials_stored: false,
      })
    );
    expect(
      screen.getByText(i18n.t('settings.radio.wifi.storeIncompleteWarning', { ssid: 'NewNet' }))
    ).toBeInTheDocument();
    expect(
      screen.queryByRole('button', { name: i18n.t('settings.radio.wifi.rebootNow') })
    ).not.toBeInTheDocument();
    await userEvent.click(
      screen.getByRole('button', { name: i18n.t('settings.radio.wifi.storeNow') })
    );
    const dialog = await screen.findByRole('dialog');
    expect(within(dialog).getByLabelText(i18n.t('settings.radio.wifi.ssid'))).toHaveValue('NewNet');
  });

  it('offers Retry for a failed check, which runs the full check', async () => {
    vi.spyOn(api, 'getRadioTransport').mockResolvedValue(transport());
    const get = vi.spyOn(api, 'getRadioWifi').mockResolvedValue(wifiState());
    await act(async () => noteRadioWifiHealth(connectedHealth));
    render(<RadioWifiSettings health={connectedHealth} />);
    show(
      wifiState({
        check: 'failed',
        check_error: 'cli_timeout',
        supported: null,
        next_retry_at: null,
      })
    );

    expect(screen.getByText(i18n.t('settings.radio.wifi.checkFailed'))).toBeInTheDocument();
    await userEvent.click(
      screen.getByRole('button', { name: i18n.t('settings.radio.wifi.retry') })
    );
    await waitFor(() => expect(get).toHaveBeenCalledWith(true));
    expect(await screen.findByText(i18n.t('settings.radio.wifi.storedNo'))).toBeInTheDocument();
  });

  it('stores the SSID untrimmed, clears the password and asks before rebooting', async () => {
    vi.spyOn(api, 'getRadioTransport').mockResolvedValue(transport());
    const store = vi
      .spyOn(api, 'setRadioWifiCredentials')
      .mockResolvedValue(
        changeResult(
          wifiState({ ssid: '  My Net ', credentials_stored: true, reboot_pending: true }),
          true
        )
      );
    const reboot = vi.spyOn(api, 'rebootRadioForWifi');
    render(<RadioWifiSettings health={connectedHealth} />);
    show(wifiState());

    await userEvent.click(
      screen.getByRole('button', { name: i18n.t('settings.radio.wifi.changeNetwork') })
    );
    const dialog = await fillCredentials('  My Net ', 'secret-pw');
    await userEvent.click(
      within(dialog).getByRole('button', { name: i18n.t('settings.radio.wifi.save') })
    );

    await waitFor(() =>
      expect(store).toHaveBeenCalledWith({
        ssid: '  My Net ',
        password: 'secret-pw',
        open_network: false,
        reboot: false,
      })
    );
    const rebootDialog = await screen.findByRole('dialog');
    expect(
      within(rebootDialog).getByText(i18n.t('settings.radio.wifi.confirmRebootTitle'))
    ).toBeInTheDocument();
    expect(reboot).not.toHaveBeenCalled();
    expect(document.body.innerHTML).not.toContain('secret-pw');
    await userEvent.click(
      within(rebootDialog).getByRole('button', { name: i18n.t('settings.radio.wifi.rebootLater') })
    );
    expect(reboot).not.toHaveBeenCalled();
    expect(screen.getByText(i18n.t('settings.radio.wifi.rebootPending'))).toBeInTheDocument();
  });

  it('blocks submit when the passwords differ', async () => {
    vi.spyOn(api, 'getRadioTransport').mockResolvedValue(transport());
    const store = vi.spyOn(api, 'setRadioWifiCredentials');
    render(<RadioWifiSettings health={connectedHealth} />);
    show(wifiState());
    await userEvent.click(
      screen.getByRole('button', { name: i18n.t('settings.radio.wifi.storeNow') })
    );
    const dialog = await screen.findByRole('dialog');
    fireEvent.change(within(dialog).getByLabelText(i18n.t('settings.radio.wifi.password')), {
      target: { value: 'one-password' },
    });
    fireEvent.change(within(dialog).getByLabelText(i18n.t('settings.radio.wifi.passwordConfirm')), {
      target: { value: 'other-password' },
    });
    await userEvent.click(
      within(dialog).getByRole('button', { name: i18n.t('settings.radio.wifi.save') })
    );
    expect(store).not.toHaveBeenCalled();
    expect(
      within(dialog).getAllByText(i18n.t('settings.radio.wifi.passwordMismatch')).length
    ).toBeGreaterThan(0);
  });

  it('shows the partial-store warning and no reboot action', async () => {
    vi.spyOn(api, 'getRadioTransport').mockResolvedValue(transport());
    vi.spyOn(api, 'setRadioWifiCredentials').mockRejectedValue(
      new ApiError('interrupted', 422, { code: 'wifi_store_partial', message: 'interrupted' })
    );
    render(<RadioWifiSettings health={connectedHealth} />);
    show(wifiState({ reboot_pending: true }));

    await userEvent.click(
      screen.getByRole('button', { name: i18n.t('settings.radio.wifi.storeNow') })
    );
    const dialog = await fillCredentials(null, 'secret-pw');
    await userEvent.click(
      within(dialog).getByRole('button', { name: i18n.t('settings.radio.wifi.save') })
    );
    expect(
      await within(dialog).findByText(i18n.t('errors.wifi_store_partial'))
    ).toBeInTheDocument();
    await userEvent.click(
      within(dialog).getByRole('button', { name: i18n.t('settings.radio.wifi.cancel') })
    );
    expect(screen.getByText(i18n.t('settings.radio.wifi.storePartialWarning'))).toBeInTheDocument();
    expect(
      screen.queryByRole('button', { name: i18n.t('settings.radio.wifi.rebootNow') })
    ).not.toBeInTheDocument();
  });

  it.each([
    ['an IP address', '192.168.1.50'],
    ['a host name', 'meshradio.local'],
  ])('warns on a TCP transport (%s) before changing network', async (_label, host) => {
    vi.spyOn(api, 'getRadioTransport').mockResolvedValue(
      transport({ transport: 'tcp', tcp_host: host, ble_address: '' })
    );
    render(<RadioWifiSettings health={connectedHealth} />);
    show(wifiState({ connected: true, ip: '192.168.1.50' }));
    await waitFor(() => expect(api.getRadioTransport).toHaveBeenCalled());
    await userEvent.click(
      screen.getByRole('button', { name: i18n.t('settings.radio.wifi.changeNetwork') })
    );
    const dialog = await screen.findByRole('dialog');
    expect(within(dialog).getByText(i18n.t('settings.radio.wifi.tcpWarning'))).toBeInTheDocument();
    // No TCP offer while already on TCP.
    expect(
      screen.queryByText(i18n.t('settings.radio.wifi.switchTcpTitle'))
    ).not.toBeInTheDocument();
  });

  it('asks before turning Wi-Fi off, with the reboot as an explicit choice', async () => {
    vi.spyOn(api, 'getRadioTransport').mockResolvedValue(transport());
    const setEnabled = vi
      .spyOn(api, 'setRadioWifiEnabled')
      .mockResolvedValue(changeResult(wifiState({ enabled: false }), true, true));
    render(<RadioWifiSettings health={connectedHealth} />);
    show(wifiState());
    await userEvent.click(
      screen.getByRole('button', { name: i18n.t('settings.radio.wifi.disable') })
    );
    expect(setEnabled).not.toHaveBeenCalled();
    const dialog = await screen.findByRole('dialog');
    await userEvent.click(
      within(dialog).getByRole('button', { name: i18n.t('settings.radio.wifi.disableAndReboot') })
    );
    await waitFor(() => expect(setEnabled).toHaveBeenCalledWith({ enabled: false, reboot: true }));
  });

  it('offers the switch to TCP and remembers a dismissal per radio', async () => {
    vi.spyOn(api, 'getRadioTransport').mockResolvedValue(transport());
    const { unmount } = render(<RadioWifiSettings health={connectedHealth} />);
    show(wifiState({ connected: true, ip: '192.168.1.50' }));
    expect(
      await screen.findByText(
        i18n.t('settings.radio.wifi.switchTcpBody', { ip: '192.168.1.50', port: '5000' })
      )
    ).toBeInTheDocument();
    await userEvent.click(
      screen.getByRole('button', { name: i18n.t('settings.radio.wifi.switchTcpDismiss') })
    );
    expect(
      screen.queryByText(i18n.t('settings.radio.wifi.switchTcpTitle'))
    ).not.toBeInTheDocument();
    unmount();
    render(<RadioWifiSettings health={connectedHealth} />);
    await waitFor(() => expect(api.getRadioTransport).toHaveBeenCalledTimes(2));
    expect(
      screen.queryByText(i18n.t('settings.radio.wifi.switchTcpTitle'))
    ).not.toBeInTheDocument();
  });

  it('switches to TCP and offers the way back only if the radio stays unreachable', async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    vi.spyOn(api, 'getRadioTransport').mockResolvedValue(transport());
    let resolvePut: (value: RadioTransportConfig) => void = () => {};
    const put = vi.spyOn(api, 'updateRadioTransport').mockReturnValue(
      new Promise<RadioTransportConfig>((resolve) => {
        resolvePut = resolve;
      })
    );
    const revert = vi
      .spyOn(api, 'revertRadioTransport')
      .mockResolvedValue(transport({ previous_transport_kind: 'tcp' }));
    const { rerender } = render(<RadioWifiSettings health={connectedHealth} />);
    show(wifiState({ connected: true, ip: '192.168.1.50' }));
    await screen.findByText(i18n.t('settings.radio.wifi.switchTcpTitle'));

    fireEvent.click(
      screen.getByRole('button', { name: i18n.t('settings.radio.wifi.switchTcpAction') })
    );
    expect(put).toHaveBeenCalledWith({
      transport: 'tcp',
      tcp_host: '192.168.1.50',
      tcp_port: 5000,
    });

    // While the PUT runs (it reconnects synchronously), no revert is offered.
    rerender(<RadioWifiSettings health={disconnectedHealth} />);
    await act(async () => {
      vi.advanceTimersByTime(TCP_SWITCH_REVERT_DELAY_MS * 2);
    });
    expect(screen.queryByRole('button', { name: /Bluetooth/ })).not.toBeInTheDocument();

    // The PUT resolves: the 30 s start now.
    await act(async () => {
      resolvePut(
        transport({ transport: 'tcp', tcp_host: '192.168.1.50', previous_transport_kind: 'ble' })
      );
    });
    await act(async () => {
      vi.advanceTimersByTime(TCP_SWITCH_REVERT_DELAY_MS - 1000);
    });
    const revertLabel = i18n.t('settings.radio.wifi.switchTcpRevert', {
      kind: i18n.t('settings.radio.wifi.kindBle'),
    });
    expect(screen.queryByRole('button', { name: revertLabel })).not.toBeInTheDocument();
    await act(async () => {
      vi.advanceTimersByTime(2000);
    });
    const button = await screen.findByRole('button', { name: revertLabel });
    fireEvent.click(button);
    await waitFor(() => expect(revert).toHaveBeenCalledWith());
  });

  it('offers no way back when the radio came back over TCP', async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    vi.spyOn(api, 'getRadioTransport').mockResolvedValue(transport());
    vi.spyOn(api, 'updateRadioTransport').mockResolvedValue(
      transport({ transport: 'tcp', tcp_host: '192.168.1.50', previous_transport_kind: 'ble' })
    );
    const { rerender } = render(<RadioWifiSettings health={connectedHealth} />);
    show(wifiState({ connected: true, ip: '192.168.1.50' }));
    await screen.findByText(i18n.t('settings.radio.wifi.switchTcpTitle'));
    fireEvent.click(
      screen.getByRole('button', { name: i18n.t('settings.radio.wifi.switchTcpAction') })
    );
    await waitFor(() => expect(api.updateRadioTransport).toHaveBeenCalled());
    rerender(
      <RadioWifiSettings
        health={{ ...connectedHealth, connection_info: 'TCP: 192.168.1.50:5000' }}
      />
    );
    await act(async () => {
      vi.advanceTimersByTime(TCP_SWITCH_REVERT_DELAY_MS + 1000);
    });
    expect(screen.queryByRole('button', { name: /Bluetooth/ })).not.toBeInTheDocument();
  });

  it('offers the way back when health still shows the old session as the PUT resolves', async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    const transportMock = vi.spyOn(api, 'getRadioTransport').mockResolvedValue(transport());
    let resolvePut: (value: RadioTransportConfig) => void = () => {};
    vi.spyOn(api, 'updateRadioTransport').mockReturnValue(
      new Promise<RadioTransportConfig>((resolve) => {
        resolvePut = resolve;
      })
    );
    const revert = vi.spyOn(api, 'revertRadioTransport').mockResolvedValue(transport());
    const { rerender } = render(<RadioWifiSettings health={connectedHealth} />);
    show(wifiState({ connected: true, ip: '192.168.1.50' }));
    await screen.findByText(i18n.t('settings.radio.wifi.switchTcpTitle'));
    fireEvent.click(
      screen.getByRole('button', { name: i18n.t('settings.radio.wifi.switchTcpAction') })
    );
    // The reconnect inside the PUT failed: 200 with nothing broadcast, so health
    // still says "connected" over Bluetooth when it resolves.
    const saved = transport({
      transport: 'tcp',
      tcp_host: '192.168.1.50',
      previous_transport_kind: 'ble',
    });
    transportMock.mockResolvedValue(saved);
    await act(async () => {
      resolvePut(saved);
    });
    // The connection monitor reports the radio gone about 5 s later.
    await act(async () => {
      vi.advanceTimersByTime(5000);
    });
    rerender(<RadioWifiSettings health={disconnectedHealth} />);
    await act(async () => {
      vi.advanceTimersByTime(TCP_SWITCH_REVERT_DELAY_MS);
    });
    const button = await screen.findByRole('button', {
      name: i18n.t('settings.radio.wifi.switchTcpRevert', {
        kind: i18n.t('settings.radio.wifi.kindBle'),
      }),
    });
    expect(screen.getByText(i18n.t('settings.radio.wifi.switchTcpRevertTimeout'))).toBeVisible();
    fireEvent.click(button);
    await waitFor(() => expect(revert).toHaveBeenCalledWith());
  });

  it('offers no way back when the switch was refused before anything was saved', async () => {
    // An older, successful change (serial -> BLE) left a previous transport.
    vi.spyOn(api, 'getRadioTransport').mockResolvedValue(
      transport({ previous_transport_kind: 'serial' })
    );
    vi.spyOn(api, 'updateRadioTransport').mockRejectedValue(
      new ApiError('Radio is busy', 503, 'Radio is busy')
    );
    const revert = vi.spyOn(api, 'revertRadioTransport');
    render(<RadioWifiSettings health={connectedHealth} />);
    show(wifiState({ connected: true, ip: '192.168.1.50' }));
    await screen.findByText(i18n.t('settings.radio.wifi.switchTcpTitle'));
    await userEvent.click(
      screen.getByRole('button', { name: i18n.t('settings.radio.wifi.switchTcpAction') })
    );
    expect(await screen.findByText('Radio is busy')).toBeInTheDocument();
    expect(
      screen.queryByRole('button', {
        name: i18n.t('settings.radio.wifi.switchTcpRevert', {
          kind: i18n.t('settings.radio.wifi.kindSerial'),
        }),
      })
    ).not.toBeInTheDocument();
    // The offer stays, to try again.
    expect(
      screen.getByRole('button', { name: i18n.t('settings.radio.wifi.switchTcpAction') })
    ).toBeEnabled();
    expect(revert).not.toHaveBeenCalled();
  });

  it('offers the way back when the switch was saved but the request failed', async () => {
    const transportMock = vi.spyOn(api, 'getRadioTransport').mockResolvedValue(transport());
    vi.spyOn(api, 'updateRadioTransport').mockImplementation(async () => {
      transportMock.mockResolvedValue(
        transport({ transport: 'tcp', tcp_host: '192.168.1.50', previous_transport_kind: 'ble' })
      );
      throw new ApiError('Gateway Timeout', 504, 'Gateway Timeout');
    });
    const revert = vi.spyOn(api, 'revertRadioTransport').mockResolvedValue(transport());
    render(<RadioWifiSettings health={connectedHealth} />);
    show(wifiState({ connected: true, ip: '192.168.1.50' }));
    await screen.findByText(i18n.t('settings.radio.wifi.switchTcpTitle'));
    await userEvent.click(
      screen.getByRole('button', { name: i18n.t('settings.radio.wifi.switchTcpAction') })
    );
    const button = await screen.findByRole('button', {
      name: i18n.t('settings.radio.wifi.switchTcpRevert', {
        kind: i18n.t('settings.radio.wifi.kindBle'),
      }),
    });
    await userEvent.click(button);
    await waitFor(() => expect(revert).toHaveBeenCalledWith());
  });

  it('follows a transport change made while it stays mounted', async () => {
    const transportMock = vi.spyOn(api, 'getRadioTransport').mockResolvedValue(transport());
    const { rerender } = render(<RadioWifiSettings health={connectedHealth} />);
    show(wifiState({ connected: true, ip: '192.168.1.50' }));
    await screen.findByText(i18n.t('settings.radio.wifi.switchTcpTitle'));

    // Switched to TCP in the transport panel above: the radio reconnects.
    transportMock.mockResolvedValue(
      transport({ transport: 'tcp', tcp_host: '192.168.1.50', ble_address: '' })
    );
    rerender(<RadioWifiSettings health={disconnectedHealth} />);
    rerender(
      <RadioWifiSettings
        health={{ ...connectedHealth, connection_info: 'TCP: 192.168.1.50:5000' }}
      />
    );
    await waitFor(() =>
      expect(
        screen.queryByText(i18n.t('settings.radio.wifi.switchTcpTitle'))
      ).not.toBeInTheDocument()
    );
    await userEvent.click(
      screen.getByRole('button', { name: i18n.t('settings.radio.wifi.changeNetwork') })
    );
    const dialog = await screen.findByRole('dialog');
    expect(within(dialog).getByText(i18n.t('settings.radio.wifi.tcpWarning'))).toBeInTheDocument();
  });

  it('offers no reboot while the stored credentials may be incomplete', async () => {
    vi.spyOn(api, 'getRadioTransport').mockResolvedValue(transport());
    const setEnabled = vi
      .spyOn(api, 'setRadioWifiEnabled')
      .mockResolvedValue(
        changeResult(
          wifiState({ enabled: true, store_incomplete: true, store_target_ssid: 'NewNet' }),
          true
        )
      );
    render(<RadioWifiSettings health={connectedHealth} />);
    show(wifiState({ store_incomplete: true, store_target_ssid: 'NewNet' }));
    await userEvent.click(
      screen.getByRole('button', { name: i18n.t('settings.radio.wifi.disable') })
    );
    const dialog = await screen.findByRole('dialog');
    expect(
      within(dialog).queryByRole('button', { name: i18n.t('settings.radio.wifi.disableAndReboot') })
    ).not.toBeInTheDocument();
    await userEvent.click(
      within(dialog).getByRole('button', { name: i18n.t('settings.radio.wifi.cancel') })
    );

    show(wifiState({ enabled: false, store_incomplete: true, store_target_ssid: 'NewNet' }));
    await userEvent.click(
      screen.getByRole('button', { name: i18n.t('settings.radio.wifi.enable') })
    );
    await waitFor(() => expect(setEnabled).toHaveBeenCalledWith({ enabled: true, reboot: false }));
    expect(
      screen.queryByText(i18n.t('settings.radio.wifi.confirmRebootTitle'))
    ).not.toBeInTheDocument();
  });

  it('Set up Wi-Fi also turns Wi-Fi on, then offers one reboot', async () => {
    vi.spyOn(api, 'getRadioTransport').mockResolvedValue(transport());
    vi.spyOn(api, 'setRadioWifiCredentials').mockResolvedValue(
      changeResult(
        wifiState({ ssid: 'Home', enabled: false, credentials_stored: true, reboot_pending: true }),
        true
      )
    );
    const setEnabled = vi.spyOn(api, 'setRadioWifiEnabled').mockResolvedValue(
      changeResult(
        wifiState({
          ssid: 'Home',
          enabled: true,
          credentials_stored: true,
          reboot_pending: true,
        }),
        true
      )
    );
    render(<RadioWifiSettings health={connectedHealth} />);
    show(wifiState({ ssid: null, enabled: false }));
    await userEvent.click(
      screen.getByRole('button', { name: i18n.t('settings.radio.wifi.setUp') })
    );
    const dialog = await fillCredentials('Home', 'secret-pw');
    expect(within(dialog).getByText(i18n.t('settings.radio.wifi.setUpEnables'))).toBeVisible();
    await userEvent.click(
      within(dialog).getByRole('button', { name: i18n.t('settings.radio.wifi.save') })
    );
    await waitFor(() => expect(setEnabled).toHaveBeenCalledWith({ enabled: true, reboot: false }));
    const rebootDialog = await screen.findByRole('dialog');
    expect(
      within(rebootDialog).getByText(i18n.t('settings.radio.wifi.confirmRebootTitle'))
    ).toBeInTheDocument();
  });

  it('forgets a typed password when the radio goes away', async () => {
    vi.spyOn(api, 'getRadioTransport').mockResolvedValue(transport());
    render(<RadioWifiSettings health={connectedHealth} />);
    show(wifiState());
    await userEvent.click(
      screen.getByRole('button', { name: i18n.t('settings.radio.wifi.changeNetwork') })
    );
    await fillCredentials('Typed Net', 'secret-pw');
    show(null as unknown as RadioWifiState);
    show(wifiState());
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
    await userEvent.click(
      screen.getByRole('button', { name: i18n.t('settings.radio.wifi.changeNetwork') })
    );
    const dialog = await screen.findByRole('dialog');
    expect(within(dialog).getByLabelText(i18n.t('settings.radio.wifi.ssid'))).toHaveValue(
      'FlasherNet'
    );
    expect(within(dialog).getByLabelText(i18n.t('settings.radio.wifi.password'))).toHaveValue('');
  });

  it('says when the Wi-Fi state could not be loaded, with Retry', async () => {
    vi.spyOn(api, 'getRadioTransport').mockResolvedValue(transport());
    const get = vi
      .spyOn(api, 'getRadioWifi')
      .mockRejectedValueOnce(new ApiError('Server error', 500, 'Server error'))
      .mockResolvedValue(wifiState());
    await act(async () => noteRadioWifiHealth(connectedHealth));
    render(<RadioWifiSettings health={connectedHealth} />);
    expect(await screen.findByText(i18n.t('settings.radio.wifi.loadFailed'))).toBeInTheDocument();
    await userEvent.click(
      screen.getByRole('button', { name: i18n.t('settings.radio.wifi.retry') })
    );
    await waitFor(() => expect(get).toHaveBeenCalledWith(true));
    expect(await screen.findByText(i18n.t('settings.radio.wifi.storedNo'))).toBeInTheDocument();
  });
});
