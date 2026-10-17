import { act, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { ApiError, api } from '../api';
import { RadioBlePairing } from '../components/settings/RadioBlePairing';
import { toast } from '../components/ui/sonner';
import i18n from '../i18n';
import type { HealthStatus, RadioBlePairingStatus, RadioTransportConfig } from '../types';

const ADDRESS = 'AA:BB:CC:DD:EE:FF';

const savedBle: RadioTransportConfig = {
  configured: true,
  transport: 'ble',
  serial_port: '',
  serial_baudrate: 115200,
  tcp_host: '',
  tcp_port: 5000,
  ble_address: ADDRESS,
  ble_pin_configured: true,
  bound_public_key: null,
  capabilities: {
    tcp: true,
    serial: true,
    ble: true,
    serial_unavailable_reason: null,
    ble_unavailable_reason: null,
  },
  serial_ports: [],
};

function status(overrides: Partial<RadioBlePairingStatus> = {}): RadioBlePairingStatus {
  return {
    supported: true,
    state: 'not_paired',
    address: ADDRESS,
    adapter: 'hci0',
    paired: false,
    trusted: false,
    connected: false,
    error_code: null,
    error_at: null,
    auto_pair_blocked: false,
    retry_in: null,
    hint: null,
    ...overrides,
  };
}

function health(radioConnected: boolean): HealthStatus {
  return { radio_connected: radioConnected } as HealthStatus;
}

function renderPairing(
  props: Partial<Parameters<typeof RadioBlePairing>[0]> = {}
): ReturnType<typeof render> {
  return render(
    <RadioBlePairing
      savedTransport={savedBle}
      currentAddress={ADDRESS}
      health={null}
      saving={false}
      {...props}
    />
  );
}

describe('RadioBlePairing', () => {
  beforeEach(() => {
    vi.spyOn(api, 'getRadioBlePairing').mockResolvedValue(status());
  });

  afterEach(() => {
    vi.useRealTimers();
    vi.restoreAllMocks();
  });

  it('asks to save first when the saved transport is not this BLE radio', () => {
    renderPairing({ currentAddress: '11:22:33:44:55:66' });

    expect(screen.getByText(i18n.t('settings.radio.blePairingSaveFirst'))).toBeInTheDocument();
    expect(api.getRadioBlePairing).not.toHaveBeenCalled();
  });

  it.each([
    ['paired', 'settings.radio.blePairingState.paired'],
    ['not_paired', 'settings.radio.blePairingState.notPaired'],
    ['pairing', 'settings.radio.blePairingState.pairing'],
    ['unknown_device', 'settings.radio.blePairingState.unknownDevice'],
    ['unsupported', 'settings.radio.blePairingState.unsupported'],
  ] as const)('shows the %s state', async (state, key) => {
    vi.mocked(api.getRadioBlePairing).mockResolvedValue(status({ state }));
    renderPairing();

    expect(await screen.findByText(i18n.t(key))).toBeInTheDocument();
  });

  it('explains a failed attempt, the pause and the next retry', async () => {
    vi.mocked(api.getRadioBlePairing).mockResolvedValue(
      status({ state: 'failed', error_code: 'ble_pair_wrong_pin', auto_pair_blocked: true })
    );
    renderPairing();

    expect(
      await screen.findByText(
        i18n.t('settings.radio.blePairingState.failed', {
          reason: i18n.t('errors.ble_pair_wrong_pin'),
        })
      )
    ).toBeInTheDocument();
    expect(screen.getByText(i18n.t('errors.ble_pair_wrong_pinDetail'))).toBeInTheDocument();
    expect(screen.getByText(i18n.t('settings.radio.blePairingAutoBlocked'))).toBeInTheDocument();
    expect(
      screen.getByRole('button', { name: i18n.t('settings.radio.blePairAgain') })
    ).toBeInTheDocument();
  });

  it('shows the backoff delay', async () => {
    vi.mocked(api.getRadioBlePairing).mockResolvedValue(
      status({ state: 'failed', error_code: 'ble_pair_device_not_found', retry_in: 20 })
    );
    renderPairing();

    expect(
      await screen.findByText(i18n.t('settings.radio.blePairingRetryIn', { seconds: 20 }))
    ).toBeInTheDocument();
  });

  it('counts the backoff delay down and refreshes it', async () => {
    vi.useFakeTimers();
    vi.mocked(api.getRadioBlePairing).mockResolvedValue(
      status({ state: 'failed', error_code: 'ble_pair_device_not_found', retry_in: 20 })
    );
    renderPairing({ health: health(false) });
    await act(async () => {
      await vi.advanceTimersByTimeAsync(0);
    });
    expect(
      screen.getByText(i18n.t('settings.radio.blePairingRetryIn', { seconds: 20 }))
    ).toBeInTheDocument();

    vi.mocked(api.getRadioBlePairing).mockResolvedValue(
      status({ state: 'failed', error_code: 'ble_pair_unreachable', retry_in: 40 })
    );
    await act(async () => {
      await vi.advanceTimersByTimeAsync(3000);
    });
    expect(
      screen.getByText(i18n.t('settings.radio.blePairingRetryIn', { seconds: 17 }))
    ).toBeInTheDocument();

    // The background poll brings the next window.
    await act(async () => {
      await vi.advanceTimersByTimeAsync(2000);
    });
    expect(
      screen.getByText(i18n.t('settings.radio.blePairingRetryIn', { seconds: 40 }))
    ).toBeInTheDocument();
  });

  it('polls in the background while the radio is disconnected, not once connected', async () => {
    vi.useFakeTimers();
    const { rerender } = renderPairing({ health: health(false) });
    await act(async () => {
      await vi.advanceTimersByTimeAsync(0);
    });
    expect(api.getRadioBlePairing).toHaveBeenCalledTimes(1);

    // A pairing started by the connection monitor shows up and disables the buttons.
    vi.mocked(api.getRadioBlePairing).mockResolvedValue(status({ state: 'pairing' }));
    await act(async () => {
      await vi.advanceTimersByTimeAsync(5000);
    });
    expect(api.getRadioBlePairing).toHaveBeenCalledTimes(2);
    expect(screen.getByText(i18n.t('settings.radio.blePairingState.pairing'))).toBeInTheDocument();

    vi.mocked(api.getRadioBlePairing).mockResolvedValue(status({ state: 'paired', paired: true }));
    rerender(
      <RadioBlePairing
        savedTransport={savedBle}
        currentAddress={ADDRESS}
        health={health(true)}
        saving={false}
      />
    );
    await act(async () => {
      await vi.advanceTimersByTimeAsync(2000);
    });
    const connected = vi.mocked(api.getRadioBlePairing).mock.calls.length;
    await act(async () => {
      await vi.advanceTimersByTimeAsync(15000);
    });
    expect(vi.mocked(api.getRadioBlePairing).mock.calls.length).toBe(connected);
  });

  it('offers Forget pairing first after the radio refused the pairing', async () => {
    vi.mocked(api.getRadioBlePairing).mockResolvedValue(
      status({ state: 'failed', error_code: 'ble_pair_rejected', auto_pair_blocked: true })
    );
    renderPairing();

    expect(await screen.findByText(i18n.t('errors.ble_pair_rejectedDetail'))).toBeInTheDocument();
    const buttons = screen.getAllByRole('button');
    expect(buttons.map((button) => button.textContent)).toEqual([
      i18n.t('settings.radio.bleForget'),
      i18n.t('settings.radio.blePairAgain'),
    ]);
  });

  it('offers no Forget pairing for a radio that is just absent', async () => {
    vi.mocked(api.getRadioBlePairing).mockResolvedValue(
      status({ state: 'failed', error_code: 'ble_pair_device_not_found', retry_in: 5 })
    );
    renderPairing();

    expect(
      await screen.findByRole('button', { name: i18n.t('settings.radio.blePairAgain') })
    ).toBeInTheDocument();
    expect(
      screen.queryByRole('button', { name: i18n.t('settings.radio.bleForget') })
    ).not.toBeInTheDocument();
  });

  it('explains why automatic pairing is unavailable', async () => {
    vi.mocked(api.getRadioBlePairing).mockResolvedValue(
      status({ state: 'unavailable', error_code: 'ble_pair_dbus_unavailable' })
    );
    renderPairing();

    expect(
      await screen.findByText(
        i18n.t('settings.radio.blePairingState.unavailable', {
          reason: i18n.t('errors.ble_pair_dbus_unavailable'),
        })
      )
    ).toBeInTheDocument();
    expect(screen.getByText(i18n.t('errors.ble_pair_dbus_unavailableDetail'))).toBeInTheDocument();
  });

  it('polls every 2 s while saving and stops afterwards', async () => {
    vi.useFakeTimers();
    const { rerender } = renderPairing({ saving: true });
    expect(api.getRadioBlePairing).not.toHaveBeenCalled();

    await act(async () => {
      await vi.advanceTimersByTimeAsync(4000);
    });
    expect(api.getRadioBlePairing).toHaveBeenCalledTimes(2);

    rerender(
      <RadioBlePairing
        savedTransport={savedBle}
        currentAddress={ADDRESS}
        health={null}
        saving={false}
      />
    );
    await act(async () => {
      await vi.advanceTimersByTimeAsync(0);
    });
    const afterSave = vi.mocked(api.getRadioBlePairing).mock.calls.length;
    await act(async () => {
      await vi.advanceTimersByTimeAsync(6000);
    });
    expect(vi.mocked(api.getRadioBlePairing).mock.calls.length).toBe(afterSave);
  });

  it('reports busy while pairing', async () => {
    const onBusyChange = vi.fn();
    vi.mocked(api.getRadioBlePairing).mockResolvedValue(status({ state: 'pairing' }));
    renderPairing({ onBusyChange });

    await waitFor(() => expect(onBusyChange).toHaveBeenLastCalledWith(true));
  });

  it('pairs on request and refreshes health', async () => {
    const success = vi.spyOn(toast, 'success');
    const onHealthRefresh = vi.fn().mockResolvedValue(undefined);
    vi.spyOn(api, 'pairRadioBle').mockResolvedValue(status({ state: 'paired', paired: true }));
    renderPairing({ onHealthRefresh });

    fireEvent.click(
      await screen.findByRole('button', { name: i18n.t('settings.radio.blePairNow') })
    );

    await waitFor(() => expect(success).toHaveBeenCalledWith(i18n.t('settings.radio.blePairDone')));
    expect(onHealthRefresh).toHaveBeenCalled();
    expect(
      await screen.findByText(i18n.t('settings.radio.blePairingState.paired'))
    ).toBeInTheDocument();
  });

  it('shows a coded pairing error', async () => {
    vi.spyOn(api, 'pairRadioBle').mockRejectedValue(
      new ApiError('Bluetooth pairing failed: wrong PIN', 422, {
        code: 'ble_pair_wrong_pin',
        message: 'Bluetooth pairing failed: wrong PIN',
        params: {},
      })
    );
    renderPairing();

    fireEvent.click(
      await screen.findByRole('button', { name: i18n.t('settings.radio.blePairNow') })
    );

    expect(await screen.findByRole('alert')).toHaveTextContent(i18n.t('errors.ble_pair_wrong_pin'));
  });

  it('suggests Forget pairing on a stale bond', async () => {
    vi.mocked(api.getRadioBlePairing).mockResolvedValue(
      status({ state: 'paired', paired: true, hint: 'stale_bond_suspected' })
    );
    renderPairing();

    expect(
      await screen.findByText(i18n.t('settings.radio.blePairingStaleBond'))
    ).toBeInTheDocument();
    const buttons = screen.getAllByRole('button');
    expect(buttons[0]).toHaveTextContent(i18n.t('settings.radio.bleForget'));
  });

  it('forgets only after confirmation', async () => {
    vi.mocked(api.getRadioBlePairing).mockResolvedValue(status({ state: 'paired', paired: true }));
    const forget = vi.spyOn(api, 'forgetRadioBlePairing').mockResolvedValue(status());
    const success = vi.spyOn(toast, 'success');
    const confirm = vi.spyOn(window, 'confirm').mockReturnValue(false);
    renderPairing();

    const button = await screen.findByRole('button', { name: i18n.t('settings.radio.bleForget') });
    fireEvent.click(button);
    expect(confirm).toHaveBeenCalledWith(i18n.t('settings.radio.bleForgetConfirm'));
    expect(forget).not.toHaveBeenCalled();

    confirm.mockReturnValue(true);
    fireEvent.click(button);
    await waitFor(() => expect(forget).toHaveBeenCalledTimes(1));
    await waitFor(() =>
      expect(success).toHaveBeenCalledWith(i18n.t('settings.radio.bleForgetDone'))
    );
  });
});
