import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, describe, expect, it, vi } from 'vitest';

import { RadioFactoryReset } from '../components/settings/RadioFactoryReset';
import { RadioSelfTelemetrySettings } from '../components/settings/RadioSelfTelemetrySettings';
import { api } from '../api';
import i18n from '../i18n';
import { DEFAULT_LOCALE } from '../utils/languagePreference';
import { resetRadioWifiStoreForTests, setRadioWifiState } from '../stores/radioWifiStore';
import type { RadioTransportConfig, RadioWifiState } from '../types';

const TELEMETRY = {
  sensors: [{ channel: 1, type_name: 'temperature', value: 23.5 }],
  battery_mv: 4012,
  storage_used_kb: 250,
  storage_total_kb: 1000,
};

afterEach(() => {
  vi.restoreAllMocks();
  resetRadioWifiStoreForTests();
  void i18n.changeLanguage(DEFAULT_LOCALE);
});

describe('RadioSelfTelemetrySettings', () => {
  it('shows sensors, battery and storage percentage, and refreshes', async () => {
    const get = vi.spyOn(api, 'getRadioSelfTelemetry').mockResolvedValue(TELEMETRY);
    render(<RadioSelfTelemetrySettings />);

    expect(
      await screen.findByText(
        i18n.t('settings.radio.selfTelemetry.batteryValue', { value: '4.01' })
      )
    ).toBeVisible();
    expect(screen.getByText(/^250 \/ 1\D000 .+\(25 %\)$/)).toBeVisible();
    expect(screen.getByText(/^Temperature Ch1$/)).toBeVisible();
    expect(get).toHaveBeenCalledTimes(1);

    await userEvent.click(
      screen.getByRole('button', { name: i18n.t('settings.radio.selfTelemetry.refresh') })
    );
    await waitFor(() => expect(get).toHaveBeenCalledTimes(2));
  });

  it('shows a muted note when there is nothing to show', async () => {
    vi.spyOn(api, 'getRadioSelfTelemetry').mockResolvedValue({
      sensors: [],
      battery_mv: null,
      storage_used_kb: null,
      storage_total_kb: null,
    });
    render(<RadioSelfTelemetrySettings />);
    expect(await screen.findByText(i18n.t('settings.radio.selfTelemetry.empty'))).toBeVisible();
  });

  it('shows the error', async () => {
    vi.spyOn(api, 'getRadioSelfTelemetry').mockRejectedValue(new Error('Radio busy'));
    render(<RadioSelfTelemetrySettings />);
    expect(await screen.findByRole('alert')).toHaveTextContent('Radio busy');
  });
});

describe('RadioFactoryReset', () => {
  const open = async () => {
    await userEvent.click(
      screen.getByRole('button', { name: i18n.t('settings.radio.factoryReset.button') })
    );
    return screen.findByLabelText(/MyRadio/);
  };

  it('enables the confirm button only for the exact name, then resets', async () => {
    const reset = vi
      .spyOn(api, 'factoryResetRadio')
      .mockResolvedValue({ status: 'reset_sent', message: 'ok' });
    render(<RadioFactoryReset radioName="MyRadio" />);

    const input = await open();
    const confirm = screen.getByRole('button', {
      name: i18n.t('settings.radio.factoryReset.confirm'),
    });
    expect(confirm).toBeDisabled();
    await userEvent.type(input, 'myradio');
    expect(confirm).toBeDisabled();
    await userEvent.clear(input);
    await userEvent.type(input, 'MyRadio');
    expect(confirm).toBeEnabled();
    await userEvent.click(confirm);

    await waitFor(() => expect(reset).toHaveBeenCalledWith('MyRadio'));
    expect(await screen.findByRole('status')).toHaveTextContent(
      i18n.t('settings.radio.factoryReset.sent')
    );
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
  });

  it('shows the API error', async () => {
    vi.spyOn(api, 'factoryResetRadio').mockRejectedValue(new Error('Radio refused'));
    render(<RadioFactoryReset radioName="MyRadio" />);

    await userEvent.type(await open(), 'MyRadio');
    await userEvent.click(
      screen.getByRole('button', { name: i18n.t('settings.radio.factoryReset.confirm') })
    );
    expect(await screen.findByRole('alert')).toHaveTextContent('Radio refused');
    expect(screen.queryByRole('status')).not.toBeInTheDocument();
  });

  describe('Wi-Fi credentials', () => {
    const stored = {
      check: 'done',
      supported: true,
      ssid: 'Home',
      enabled: true,
      connected: true,
      ip: '192.168.1.50',
      tcp_port: 5000,
      credentials_stored: true,
      open_network: false,
      prompt_needed: false,
      reboot_pending: false,
      store_incomplete: false,
    } as RadioWifiState;

    it('says the stored credentials are erased, without the TCP warning off TCP', async () => {
      vi.spyOn(api, 'getRadioTransport').mockResolvedValue({
        transport: 'ble',
      } as RadioTransportConfig);
      setRadioWifiState(stored);
      render(<RadioFactoryReset radioName="MyRadio" />);
      await open();
      expect(
        screen.getByText(i18n.t('settings.radio.factoryReset.wifiStoredWarning'))
      ).toBeVisible();
      await waitFor(() => expect(api.getRadioTransport).toHaveBeenCalled());
      expect(screen.queryByText(i18n.t('settings.radio.factoryReset.wifiTcpWarning'))).toBeNull();
      expect(screen.getByText(i18n.t('settings.radio.factoryReset.keptWifi'))).toBeVisible();
    });

    it('adds the stronger warning when Meshloom reaches the radio over TCP', async () => {
      vi.spyOn(api, 'getRadioTransport').mockResolvedValue({
        transport: 'tcp',
      } as RadioTransportConfig);
      setRadioWifiState(stored);
      render(<RadioFactoryReset radioName="MyRadio" />);
      await open();
      expect(
        await screen.findByText(i18n.t('settings.radio.factoryReset.wifiTcpWarning'))
      ).toBeVisible();
    });

    it('shows no Wi-Fi warning when nothing is stored', async () => {
      const transport = vi.spyOn(api, 'getRadioTransport');
      setRadioWifiState({ ...stored, credentials_stored: false });
      render(<RadioFactoryReset radioName="MyRadio" />);
      await open();
      expect(
        screen.queryByText(i18n.t('settings.radio.factoryReset.wifiStoredWarning'))
      ).toBeNull();
      expect(transport).not.toHaveBeenCalled();
    });
  });
});
