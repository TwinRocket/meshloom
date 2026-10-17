import { afterEach, describe, expect, it, vi } from 'vitest';

const mocks = vi.hoisted(() => ({
  toast: { info: vi.fn(), success: vi.fn(), error: vi.fn() },
  getRadioTransport: vi.fn(),
  updateRadioTransport: vi.fn(),
  revertRadioTransport: vi.fn(),
  getHealth: vi.fn(),
}));

vi.mock('../components/ui/sonner', () => ({ toast: mocks.toast }));
vi.mock('../api', () => ({
  api: {
    getRadioTransport: () => mocks.getRadioTransport(),
    updateRadioTransport: (body: unknown) => mocks.updateRadioTransport(body),
    revertRadioTransport: () => mocks.revertRadioTransport(),
    getHealth: () => mocks.getHealth(),
  },
  formatApiError: (err: unknown) => String(err),
}));

import { showWifiSwitchToast, switchToTcpFromToast } from '../components/radioWifiSwitchToast';
import { TCP_SWITCH_REVERT_DELAY_MS } from '../utils/radioTcpSwitch';
import type { RadioWifiState } from '../types';

const state = {
  check: 'done',
  supported: true,
  connected: true,
  ip: '192.168.1.50',
  tcp_port: 5000,
  open_network: false,
  prompt_needed: false,
  reboot_pending: false,
  store_incomplete: false,
} as RadioWifiState;

const savedTcp = {
  transport: 'tcp',
  tcp_host: '192.168.1.50',
  tcp_port: 5000,
  previous_transport_kind: 'ble',
};

describe('showWifiSwitchToast', () => {
  afterEach(() => {
    vi.clearAllMocks();
    vi.useRealTimers();
  });

  it('offers TCP when Meshloom is not on TCP, and switches on click', async () => {
    mocks.getRadioTransport.mockResolvedValue({ transport: 'ble' });
    mocks.updateRadioTransport.mockResolvedValue({ transport: 'tcp' });
    await showWifiSwitchToast(state);
    expect(mocks.toast.info).toHaveBeenCalledTimes(1);
    const options = mocks.toast.info.mock.calls[0][1];
    options.action.onClick();
    expect(mocks.updateRadioTransport).toHaveBeenCalledWith({
      transport: 'tcp',
      tcp_host: '192.168.1.50',
      tcp_port: 5000,
    });
  });

  it('stays quiet when Meshloom already uses TCP', async () => {
    mocks.getRadioTransport.mockResolvedValue({ transport: 'tcp' });
    await showWifiSwitchToast(state);
    expect(mocks.toast.info).not.toHaveBeenCalled();
  });

  it('offers the way back when the switch was saved but the request failed', async () => {
    mocks.updateRadioTransport.mockRejectedValue(new Error('504'));
    mocks.getRadioTransport.mockResolvedValue(savedTcp);
    mocks.revertRadioTransport.mockResolvedValue({ transport: 'ble' });
    await switchToTcpFromToast('192.168.1.50', 5000);
    expect(mocks.toast.error).toHaveBeenCalledTimes(1);
    const options = mocks.toast.error.mock.calls[0][1];
    options.action.onClick();
    expect(mocks.revertRadioTransport).toHaveBeenCalledWith();
  });

  it('offers no way back when the switch was refused before saving', async () => {
    mocks.updateRadioTransport.mockRejectedValue(new Error('503'));
    mocks.getRadioTransport.mockResolvedValue({
      transport: 'ble',
      previous_transport_kind: 'serial',
    });
    await switchToTcpFromToast('192.168.1.50', 5000);
    expect(mocks.toast.error).toHaveBeenCalledTimes(1);
    expect(mocks.toast.error.mock.calls[0][1].action).toBeUndefined();
  });

  it('offers the way back when the radio is still unreachable 30 s after the switch', async () => {
    vi.useFakeTimers();
    mocks.updateRadioTransport.mockResolvedValue(savedTcp);
    mocks.getRadioTransport.mockResolvedValue(savedTcp);
    // Stale: still the Bluetooth session's "connected".
    mocks.getHealth.mockResolvedValue({
      radio_connected: true,
      connection_info: 'BLE: AA:BB:CC:DD:EE:FF',
    });
    await switchToTcpFromToast('192.168.1.50', 5000);
    expect(mocks.toast.success).toHaveBeenCalledTimes(1);
    expect(mocks.toast.error).not.toHaveBeenCalled();
    await vi.advanceTimersByTimeAsync(TCP_SWITCH_REVERT_DELAY_MS);
    expect(mocks.toast.error).toHaveBeenCalledTimes(1);
    expect(mocks.toast.error.mock.calls[0][1].action).toBeDefined();
  });

  it('stays quiet 30 s later when the radio answers over TCP', async () => {
    vi.useFakeTimers();
    mocks.updateRadioTransport.mockResolvedValue(savedTcp);
    mocks.getHealth.mockResolvedValue({
      radio_connected: true,
      connection_info: 'TCP: 192.168.1.50:5000',
    });
    await switchToTcpFromToast('192.168.1.50', 5000);
    await vi.advanceTimersByTimeAsync(TCP_SWITCH_REVERT_DELAY_MS);
    expect(mocks.toast.error).not.toHaveBeenCalled();
  });
});
