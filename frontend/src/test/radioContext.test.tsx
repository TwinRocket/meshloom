import { act, renderHook } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import {
  ACTIVE_RADIO_STORAGE_KEY,
  RadioProvider,
  useRadioContext,
} from '../contexts/RadioContext';
import { api } from '../api';
import type { RadioRecord } from '../types';

vi.mock('../api', () => ({
  api: {
    getRadios: vi.fn(),
  },
}));

const radioA: RadioRecord = {
  id: 'radio-a',
  name: 'Radio Alpha',
  transport: 'tcp',
  tcp_host: '10.0.0.1',
  tcp_port: 5000,
  serial_port: '',
  serial_baudrate: 115200,
  ble_address: '',
  ble_pin_configured: false,
  enabled: true,
  auto_connect: true,
  bound_public_key: null,
  identity_state: null,
  is_connected: true,
  is_reconnecting: false,
  last_connected_at: 100,
  sort_order: 0,
  connection_info: null,
  last_error: null,
  device_model: null,
  firmware_version: null,
  created_at: 100,
  updated_at: 100,
};

const radioB: RadioRecord = {
  id: 'radio-b',
  name: 'Radio Bravo',
  transport: 'serial',
  tcp_host: '',
  tcp_port: null,
  serial_port: '/dev/ttyUSB1',
  serial_baudrate: 115200,
  ble_address: '',
  ble_pin_configured: false,
  enabled: true,
  auto_connect: false,
  bound_public_key: null,
  identity_state: null,
  is_connected: false,
  is_reconnecting: false,
  last_connected_at: null,
  sort_order: 1,
  connection_info: null,
  last_error: null,
  device_model: null,
  firmware_version: null,
  created_at: 200,
  updated_at: 200,
};

describe('RadioContext and RadioProvider', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    localStorage.clear();
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  it('initializes with provided initialRadios and initialActiveRadioId', () => {
    const { result } = renderHook(() => useRadioContext(), {
      wrapper: ({ children }) => (
        <RadioProvider initialRadios={[radioA, radioB]} initialActiveRadioId="radio-b">
          {children}
        </RadioProvider>
      ),
    });

    expect(result.current.radios).toHaveLength(2);
    expect(result.current.activeRadioId).toBe('radio-b');
    expect(result.current.activeRadio?.name).toBe('Radio Bravo');
  });

  it('fetches radios on mount when none are initially provided', async () => {
    vi.mocked(api.getRadios).mockResolvedValueOnce([radioA]);

    const { result } = renderHook(() => useRadioContext(), {
      wrapper: ({ children }) => <RadioProvider>{children}</RadioProvider>,
    });

    expect(api.getRadios).toHaveBeenCalledTimes(1);
    await act(async () => {});
    expect(result.current.radios).toHaveLength(1);
    expect(result.current.radios[0].id).toBe('radio-a');
  });

  it('updates activeRadioId and persists to localStorage', () => {
    const { result } = renderHook(() => useRadioContext(), {
      wrapper: ({ children }) => (
        <RadioProvider initialRadios={[radioA, radioB]}>
          {children}
        </RadioProvider>
      ),
    });

    act(() => {
      result.current.setActiveRadioId('radio-b');
    });

    expect(result.current.activeRadioId).toBe('radio-b');
    expect(localStorage.getItem(ACTIVE_RADIO_STORAGE_KEY)).toBe('radio-b');
    expect(result.current.activeRadio?.id).toBe('radio-b');
  });

  it('handles custom window events for live radio lifecycle updates', async () => {
    const { result } = renderHook(() => useRadioContext(), {
      wrapper: ({ children }) => (
        <RadioProvider initialRadios={[radioA]}>
          {children}
        </RadioProvider>
      ),
    });

    expect(result.current.radios).toHaveLength(1);

    // Simulate meshloom_ws_radio_created event
    await act(async () => {
      window.dispatchEvent(
        new CustomEvent('meshloom_ws_radio_created', { detail: radioB })
      );
    });

    expect(result.current.radios).toHaveLength(2);
    expect(result.current.radios.find((r) => r.id === 'radio-b')).toBeDefined();

    // Simulate meshloom_ws_radio_updated event
    const updatedB = { ...radioB, name: 'Radio Bravo Updated', is_connected: true };
    await act(async () => {
      window.dispatchEvent(
        new CustomEvent('meshloom_ws_radio_updated', { detail: updatedB })
      );
    });

    expect(result.current.radios.find((r) => r.id === 'radio-b')?.name).toBe('Radio Bravo Updated');
    expect(result.current.radios.find((r) => r.id === 'radio-b')?.is_connected).toBe(true);

    // Simulate meshloom_ws_radio_deleted event
    await act(async () => {
      window.dispatchEvent(
        new CustomEvent('meshloom_ws_radio_deleted', { detail: { radio_id: 'radio-b' } })
      );
    });

    expect(result.current.radios).toHaveLength(1);
    expect(result.current.radios.find((r) => r.id === 'radio-b')).toBeUndefined();
  });

  it('falls back to default or first radio if active radio is deleted', async () => {
    const { result } = renderHook(() => useRadioContext(), {
      wrapper: ({ children }) => (
        <RadioProvider initialRadios={[radioA, radioB]} initialActiveRadioId="radio-b">
          {children}
        </RadioProvider>
      ),
    });

    expect(result.current.activeRadioId).toBe('radio-b');

    await act(async () => {
      result.current.handleRadioDeleted('radio-b');
    });

    expect(result.current.activeRadioId).toBe('radio-a');
  });
});
