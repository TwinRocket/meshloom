import { render, screen, waitFor, fireEvent } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { SettingsFanoutSection } from '../components/settings/SettingsFanoutSection';
import type { FanoutConfig, HealthStatus } from '../types';
import i18n from '../i18n';

// The real formatApiError: the coded 503 must reach the user translated (MSHL-397).
vi.mock('../api', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../api')>();
  return {
    ...actual,
    api: {
      getFanoutConfigs: vi.fn(),
      createFanoutConfig: vi.fn(),
      updateFanoutConfig: vi.fn(),
      deleteFanoutConfig: vi.fn(),
      getChannels: vi.fn(),
      getContacts: vi.fn(),
      getSettings: vi.fn(),
      getRadioConfig: vi.fn(),
    },
  };
});

vi.mock('../components/ui/sonner', () => ({
  toast: { error: vi.fn(), success: vi.fn() },
}));

import { ApiError, api } from '../api';
import { toast } from '../components/ui/sonner';

const mockedApi = vi.mocked(api);
const RAW = 'The secrets key file cannot be read or created: secrets cannot be saved';

const health: HealthStatus = {
  status: 'connected',
  radio_connected: true,
  radio_initializing: false,
  connection_info: 'Serial: /dev/ttyUSB0',
  database_size_mb: 1.2,
  oldest_undecrypted_timestamp: null,
  fanout_statuses: {},
};

const broker: FanoutConfig = {
  id: 'mq-1',
  type: 'mqtt_private',
  name: 'Broker',
  enabled: true,
  config: { broker_host: 'broker.example.org', broker_port: 1883, password: '********' },
  scope: { messages: 'all', raw_packets: 'none' },
  sort_order: 0,
  created_at: 1000,
};

function unavailable() {
  return new ApiError(RAW, 503, {
    code: 'secret_store_unavailable',
    params: {},
    message: RAW,
  });
}

beforeEach(() => {
  vi.resetAllMocks();
  vi.spyOn(window, 'confirm').mockReturnValue(true);
  mockedApi.getFanoutConfigs.mockResolvedValue([broker]);
  mockedApi.getChannels.mockResolvedValue([]);
  mockedApi.getContacts.mockResolvedValue([]);
  mockedApi.getRadioConfig.mockRejectedValue(new Error('offline'));
  mockedApi.getSettings.mockRejectedValue(new Error('offline'));
});

afterEach(async () => {
  await i18n.changeLanguage('fr');
});

describe('fanout screen: secret_store_unavailable', () => {
  it.each(['fr', 'en'])('shows the translated message on save (%s)', async (lang) => {
    await i18n.changeLanguage(lang);
    mockedApi.updateFanoutConfig.mockRejectedValue(unavailable());
    render(<SettingsFanoutSection health={health} onHealthRefresh={vi.fn(async () => {})} />);
    await waitFor(() => expect(screen.getByText('Broker')).toBeInTheDocument());
    fireEvent.click(screen.getByRole('button', { name: i18n.t('settings.fanout.list.edit') }));
    fireEvent.click(
      await screen.findByRole('button', { name: i18n.t('settings.fanout.list.saveEnabled') })
    );

    const expected = i18n.t('errors.secret_store_unavailable');
    expect(expected).not.toBe('errors.secret_store_unavailable');
    await waitFor(() => expect(toast.error).toHaveBeenCalledWith(expected));
    expect(toast.error).not.toHaveBeenCalledWith(RAW);
  });

  it('shows the translated message when a toggle is refused', async () => {
    mockedApi.updateFanoutConfig.mockRejectedValue(unavailable());
    render(<SettingsFanoutSection health={health} onHealthRefresh={vi.fn(async () => {})} />);
    const toggle = await screen.findByRole('checkbox', { name: /Broker/ });
    fireEvent.click(toggle);
    await waitFor(() =>
      expect(toast.error).toHaveBeenCalledWith(i18n.t('errors.secret_store_unavailable'))
    );
  });
});
