import { afterEach, describe, expect, it, vi } from 'vitest';

const mocks = vi.hoisted(() => ({
  toast: { info: vi.fn(), success: vi.fn(), error: vi.fn() },
  getRadioTransport: vi.fn(),
  updateRadioTransport: vi.fn(),
}));

vi.mock('../components/ui/sonner', () => ({ toast: mocks.toast }));
vi.mock('../api', () => ({
  api: {
    getRadioTransport: () => mocks.getRadioTransport(),
    updateRadioTransport: (body: unknown) => mocks.updateRadioTransport(body),
  },
  formatApiError: (err: unknown) => String(err),
}));

import { showWifiSwitchToast } from '../components/radioWifiSwitchToast';
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

describe('showWifiSwitchToast', () => {
  afterEach(() => vi.clearAllMocks());

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
});
