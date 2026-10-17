import { beforeEach, describe, expect, it, vi } from 'vitest';

import type { HealthStatus, RadioWifiState } from '../types';

const mocks = vi.hoisted(() => ({
  getRadioWifi: vi.fn<(refresh?: boolean) => Promise<RadioWifiState>>(),
}));

vi.mock('../api', () => ({
  api: { getRadioWifi: (refresh?: boolean) => mocks.getRadioWifi(refresh) },
}));

import {
  getRadioWifiSnapshot,
  loadRadioWifi,
  noteRadioWifiHealth,
  refreshRadioWifiAfterReconnect,
  resetRadioWifiStoreForTests,
  setRadioWifiState,
} from '../stores/radioWifiStore';

const connected: HealthStatus = {
  status: 'ok',
  radio_connected: true,
  radio_initializing: false,
  radio_state: 'connected',
  connection_info: 'Serial: /dev/ttyUSB0',
  database_size_mb: 1,
  oldest_undecrypted_timestamp: null,
  fanout_statuses: {},
  bots_disabled: false,
};

function wifiState(overrides: Partial<RadioWifiState> = {}): RadioWifiState {
  return {
    check: 'done',
    supported: true,
    ssid: 'Home',
    enabled: true,
    connected: false,
    ip: null,
    tcp_port: 5000,
    credentials_stored: false,
    open_network: false,
    prompt_needed: true,
    reboot_pending: false,
    store_incomplete: false,
    radio_public_key: 'ab'.repeat(32),
    ...overrides,
  };
}

async function flush(): Promise<void> {
  for (let i = 0; i < 5; i++) await Promise.resolve();
}

describe('radioWifiStore', () => {
  beforeEach(() => {
    resetRadioWifiStoreForTests();
    mocks.getRadioWifi.mockReset();
    vi.useRealTimers();
  });

  it('fetches once the radio is connected and set up, not before', async () => {
    mocks.getRadioWifi.mockResolvedValue(wifiState());
    noteRadioWifiHealth({ ...connected, radio_initializing: true, radio_state: 'initializing' });
    expect(mocks.getRadioWifi).not.toHaveBeenCalled();

    noteRadioWifiHealth(connected);
    await flush();
    expect(mocks.getRadioWifi).toHaveBeenCalledTimes(1);
    expect(mocks.getRadioWifi).toHaveBeenCalledWith(false);
    expect(getRadioWifiSnapshot()?.prompt_needed).toBe(true);

    // Same health again: no new fetch.
    noteRadioWifiHealth({ ...connected });
    await flush();
    expect(mocks.getRadioWifi).toHaveBeenCalledTimes(1);
  });

  it('asks again a little later while the check is pending', async () => {
    vi.useFakeTimers();
    mocks.getRadioWifi
      .mockResolvedValueOnce(wifiState({ check: 'pending', supported: null }))
      .mockResolvedValueOnce(wifiState());
    noteRadioWifiHealth(connected);
    await flush();
    expect(getRadioWifiSnapshot()?.check).toBe('pending');

    await vi.advanceTimersByTimeAsync(3000);
    expect(mocks.getRadioWifi).toHaveBeenCalledTimes(2);
    expect(getRadioWifiSnapshot()?.check).toBe('done');
  });

  it('resets when the radio goes away and fetches again when it is back', async () => {
    mocks.getRadioWifi.mockResolvedValue(wifiState());
    noteRadioWifiHealth(connected);
    await flush();
    noteRadioWifiHealth({ ...connected, radio_connected: false, radio_state: 'rebooting' });
    expect(getRadioWifiSnapshot()).toBeNull();
    noteRadioWifiHealth(connected);
    await flush();
    expect(mocks.getRadioWifi).toHaveBeenCalledTimes(2);
  });

  it('keeps a WebSocket state that arrived while a fetch was in flight', async () => {
    let resolve: (state: RadioWifiState) => void = () => {};
    mocks.getRadioWifi.mockReturnValue(
      new Promise<RadioWifiState>((r) => {
        resolve = r;
      })
    );
    noteRadioWifiHealth(connected);
    setRadioWifiState(wifiState({ prompt_needed: false, credentials_stored: true }));
    resolve(wifiState({ prompt_needed: true }));
    await flush();
    expect(getRadioWifiSnapshot()?.prompt_needed).toBe(false);
  });

  it('fetches again after a WebSocket reconnect only while the radio is ready', async () => {
    mocks.getRadioWifi.mockResolvedValue(wifiState());
    refreshRadioWifiAfterReconnect();
    expect(mocks.getRadioWifi).not.toHaveBeenCalled();
    noteRadioWifiHealth(connected);
    await flush();
    refreshRadioWifiAfterReconnect();
    await flush();
    expect(mocks.getRadioWifi).toHaveBeenCalledTimes(2);
  });

  it('a manual refresh reports failures when asked to', async () => {
    mocks.getRadioWifi.mockRejectedValue(new Error('423'));
    noteRadioWifiHealth(connected);
    await flush();
    await expect(loadRadioWifi(true, { throwOnError: true })).rejects.toThrow('423');
    expect(await loadRadioWifi(true)).toBeNull();
  });
});
