import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { api } from '../api';
import { RadioWifiPrompt } from '../components/RadioWifiPrompt';
import i18n from '../i18n';
import {
  noteRadioWifiHealth,
  refreshRadioWifiAfterReconnect,
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
    connected: true,
    ip: '192.168.1.50',
    tcp_port: 5000,
    credentials_stored: false,
    open_network: false,
    prompt_needed: true,
    reboot_pending: false,
    store_incomplete: false,
    radio_public_key: PK,
    ...overrides,
  };
}

function transport(kind: 'ble' | 'tcp'): RadioTransportConfig {
  return {
    configured: true,
    transport: kind,
    serial_port: '',
    serial_baudrate: 115200,
    tcp_host: kind === 'tcp' ? '192.168.1.50' : '',
    tcp_port: 5000,
    ble_address: kind === 'ble' ? 'AA:BB:CC:DD:EE:FF' : '',
    ble_pin_configured: kind === 'ble',
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
  };
}

const connected: HealthStatus = {
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

function typePasswords(dialog: HTMLElement, first: string, second = first) {
  fireEvent.change(within(dialog).getByLabelText(i18n.t('settings.radio.wifi.password')), {
    target: { value: first },
  });
  fireEvent.change(within(dialog).getByLabelText(i18n.t('settings.radio.wifi.passwordConfirm')), {
    target: { value: second },
  });
}

describe('RadioWifiPrompt', () => {
  beforeEach(() => {
    resetRadioWifiStoreForTests();
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  it('opens from the REST snapshot without any WebSocket event, SSID pre-filled', async () => {
    vi.spyOn(api, 'getRadioTransport').mockResolvedValue(transport('ble'));
    vi.spyOn(api, 'getRadioWifi').mockResolvedValue(wifiState());
    render(<RadioWifiPrompt />);
    act(() => noteRadioWifiHealth(connected));

    const dialog = await screen.findByRole('dialog');
    expect(within(dialog).getByText(i18n.t('wifiPrompt.reason'))).toBeInTheDocument();
    expect(within(dialog).getByLabelText(i18n.t('settings.radio.wifi.ssid'))).toHaveValue(
      'FlasherNet'
    );
  });

  it('opens again after a WebSocket reconnect that still needs it', async () => {
    vi.spyOn(api, 'getRadioTransport').mockResolvedValue(transport('ble'));
    const get = vi
      .spyOn(api, 'getRadioWifi')
      .mockResolvedValueOnce(wifiState({ prompt_needed: false }))
      .mockResolvedValueOnce(wifiState());
    render(<RadioWifiPrompt />);
    act(() => noteRadioWifiHealth(connected));
    await waitFor(() => expect(get).toHaveBeenCalledTimes(1));
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument();

    act(() => refreshRadioWifiAfterReconnect());
    expect(await screen.findByRole('dialog')).toBeInTheDocument();
  });

  it('ignores Escape and clicks outside', async () => {
    vi.spyOn(api, 'getRadioTransport').mockResolvedValue(transport('ble'));
    render(<RadioWifiPrompt />);
    act(() => setRadioWifiState(wifiState()));
    const dialog = await screen.findByRole('dialog');
    fireEvent.keyDown(dialog, { key: 'Escape' });
    fireEvent.pointerDown(document.body);
    expect(screen.getByRole('dialog')).toBeInTheDocument();
    expect(screen.getAllByRole('button')).toHaveLength(2);
  });

  it('stores, then asks before the reboot the server says is needed', async () => {
    vi.spyOn(api, 'getRadioTransport').mockResolvedValue(transport('ble'));
    const store = vi.spyOn(api, 'setRadioWifiCredentials').mockResolvedValue({
      state: wifiState({ prompt_needed: false, credentials_stored: true, reboot_pending: true }),
      reboot_required: true,
      rebooting: false,
    });
    const reboot = vi.spyOn(api, 'rebootRadioForWifi').mockResolvedValue({
      state: wifiState({ prompt_needed: false, credentials_stored: true }),
      reboot_required: false,
      rebooting: true,
    });
    render(<RadioWifiPrompt />);
    act(() => setRadioWifiState(wifiState({ connected: false, ip: null })));
    const dialog = await screen.findByRole('dialog');
    typePasswords(dialog, 'secret-pw');
    await userEvent.click(within(dialog).getByRole('button', { name: i18n.t('wifiPrompt.store') }));

    await waitFor(() =>
      expect(store).toHaveBeenCalledWith({
        ssid: 'FlasherNet',
        password: 'secret-pw',
        open_network: false,
        reboot: false,
      })
    );
    const follow = await screen.findByRole('dialog');
    expect(
      within(follow).getByText(i18n.t('settings.radio.wifi.confirmRebootTitle'))
    ).toBeInTheDocument();
    expect(within(follow).queryByText(i18n.t('settings.radio.wifi.tcpWarning'))).toBeNull();
    expect(reboot).not.toHaveBeenCalled();
    await userEvent.click(
      within(follow).getByRole('button', { name: i18n.t('settings.radio.wifi.rebootNow') })
    );
    await waitFor(() => expect(reboot).toHaveBeenCalledTimes(1));
    await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument());
  });

  it('warns on TCP when the SSID is edited, and in the follow-up confirm', async () => {
    vi.spyOn(api, 'getRadioTransport').mockResolvedValue(transport('tcp'));
    vi.spyOn(api, 'setRadioWifiCredentials').mockResolvedValue({
      state: wifiState({ prompt_needed: false, credentials_stored: true }),
      reboot_required: true,
      rebooting: false,
    });
    render(<RadioWifiPrompt />);
    act(() => setRadioWifiState(wifiState()));
    const dialog = await screen.findByRole('dialog');
    await waitFor(() => expect(api.getRadioTransport).toHaveBeenCalled());
    expect(within(dialog).queryByText(i18n.t('settings.radio.wifi.tcpWarning'))).toBeNull();

    fireEvent.change(within(dialog).getByLabelText(i18n.t('settings.radio.wifi.ssid')), {
      target: { value: 'OtherNet' },
    });
    expect(within(dialog).getByText(i18n.t('settings.radio.wifi.tcpWarning'))).toBeInTheDocument();

    typePasswords(dialog, 'secret-pw');
    await userEvent.click(within(dialog).getByRole('button', { name: i18n.t('wifiPrompt.store') }));
    const follow = await screen.findByText(i18n.t('settings.radio.wifi.confirmRebootTitle'));
    expect(
      within(follow.closest('[role="dialog"]') as HTMLElement).getByText(
        i18n.t('settings.radio.wifi.tcpWarning')
      )
    ).toBeInTheDocument();
  });

  it('records "don\'t ask again" for the radio it was shown for and stays closed', async () => {
    vi.spyOn(api, 'getRadioTransport').mockResolvedValue(transport('ble'));
    const dismiss = vi
      .spyOn(api, 'dismissRadioWifiPrompt')
      .mockResolvedValue(wifiState({ prompt_needed: false }));
    render(<RadioWifiPrompt />);
    act(() => setRadioWifiState(wifiState()));
    const dialog = await screen.findByRole('dialog');
    await userEvent.click(
      within(dialog).getByRole('button', { name: i18n.t('wifiPrompt.dismiss') })
    );
    await waitFor(() => expect(dismiss).toHaveBeenCalledWith({ radio_public_key: PK }));
    await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument());
  });

  it('stores an open network without a password', async () => {
    vi.spyOn(api, 'getRadioTransport').mockResolvedValue(transport('ble'));
    const store = vi.spyOn(api, 'setRadioWifiCredentials').mockResolvedValue({
      state: wifiState({ prompt_needed: false, credentials_stored: true, open_network: true }),
      reboot_required: false,
      rebooting: false,
    });
    render(<RadioWifiPrompt />);
    act(() => setRadioWifiState(wifiState()));
    const dialog = await screen.findByRole('dialog');
    await userEvent.click(
      within(dialog).getByRole('checkbox', { name: i18n.t('settings.radio.wifi.openNetwork') })
    );
    expect(within(dialog).queryByLabelText(i18n.t('settings.radio.wifi.password'))).toBeNull();
    await userEvent.click(within(dialog).getByRole('button', { name: i18n.t('wifiPrompt.store') }));
    await waitFor(() =>
      expect(store).toHaveBeenCalledWith({
        ssid: 'FlasherNet',
        password: null,
        open_network: true,
        reboot: false,
      })
    );
    await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument());
  });

  it('blocks the store while the passwords differ', async () => {
    vi.spyOn(api, 'getRadioTransport').mockResolvedValue(transport('ble'));
    const store = vi.spyOn(api, 'setRadioWifiCredentials');
    render(<RadioWifiPrompt />);
    act(() => setRadioWifiState(wifiState()));
    const dialog = await screen.findByRole('dialog');
    typePasswords(dialog, 'secret-pw', 'secret-pX');
    await userEvent.click(within(dialog).getByRole('button', { name: i18n.t('wifiPrompt.store') }));
    expect(store).not.toHaveBeenCalled();
    expect(screen.getByRole('dialog')).toBeInTheDocument();
  });

  it('closes in this tab when another tab answered (state without prompt_needed)', async () => {
    vi.spyOn(api, 'getRadioTransport').mockResolvedValue(transport('ble'));
    render(<RadioWifiPrompt />);
    act(() => setRadioWifiState(wifiState()));
    await screen.findByRole('dialog');
    act(() => setRadioWifiState(wifiState({ prompt_needed: false, credentials_stored: true })));
    await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument());
  });

  it('forgets what was typed when it closes for another reason', async () => {
    vi.spyOn(api, 'getRadioTransport').mockResolvedValue(transport('ble'));
    render(<RadioWifiPrompt />);
    act(() => setRadioWifiState(wifiState()));
    let dialog = await screen.findByRole('dialog');
    fireEvent.change(within(dialog).getByLabelText(i18n.t('settings.radio.wifi.ssid')), {
      target: { value: 'Edited' },
    });
    typePasswords(dialog, 'secret-pw');
    // The radio goes away, then another radio needs the prompt.
    act(() => setRadioWifiState(null));
    await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument());
    act(() => setRadioWifiState(wifiState({ radio_public_key: 'cd'.repeat(32), ssid: 'Other' })));
    dialog = await screen.findByRole('dialog');
    expect(within(dialog).getByLabelText(i18n.t('settings.radio.wifi.ssid'))).toHaveValue('Other');
    expect(within(dialog).getByLabelText(i18n.t('settings.radio.wifi.password'))).toHaveValue('');
    expect(
      within(dialog).getByLabelText(i18n.t('settings.radio.wifi.passwordConfirm'))
    ).toHaveValue('');
  });
});
