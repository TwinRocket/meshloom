import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import {
  RadioSelector,
  getRadioStatusDotClass,
  getRadioTransportBadge,
} from '../components/RadioSelector';
import { RadioContext, type RadioContextValue } from '../contexts/RadioContext';
import type { RadioRecord } from '../types';

vi.mock('../i18n', () => ({
  default: {
    t: (key: string, defaultVal?: string) => defaultVal ?? key,
  },
}));

vi.mock('react-i18next', () => ({
  useTranslation: () => ({
    t: (key: string, defaultVal?: string) => defaultVal ?? key,
  }),
}));

const mockRadio1: RadioRecord = {
  id: 'radio-1',
  name: 'Primary Roof',
  transport: 'tcp',
  tcp_host: '192.168.1.10',
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
  last_connected_at: 1000,
  sort_order: 0,
  connection_info: null,
  last_error: null,
  device_model: null,
  firmware_version: null,
  created_at: 100,
  updated_at: 100,
};

const mockRadio2: RadioRecord = {
  id: 'radio-2',
  name: 'Mobile Van',
  transport: 'serial',
  tcp_host: '',
  tcp_port: null,
  serial_port: '/dev/ttyUSB0',
  serial_baudrate: 115200,
  ble_address: '',
  ble_pin_configured: false,
  enabled: true,
  auto_connect: false,
  bound_public_key: null,
  identity_state: null,
  is_connected: false,
  is_reconnecting: true,
  last_connected_at: 500,
  sort_order: 1,
  connection_info: null,
  last_error: null,
  device_model: null,
  firmware_version: null,
  created_at: 200,
  updated_at: 200,
};

function renderWithContext(
  contextOverrides?: Partial<RadioContextValue>,
  props?: { className?: string; onOpenSettings?: () => void }
) {
  const value: RadioContextValue = {
    radios: [mockRadio1, mockRadio2],
    activeRadioId: 'radio-1',
    setActiveRadioId: vi.fn(),
    activeRadio: mockRadio1,
    refreshRadios: vi.fn(async () => [mockRadio1, mockRadio2]),
    isLoading: false,
    handleRadioCreated: vi.fn(),
    handleRadioUpdated: vi.fn(),
    handleRadioDeleted: vi.fn(),
    ...contextOverrides,
  };

  return {
    ...render(
      <RadioContext.Provider value={value}>
        <RadioSelector {...props} />
      </RadioContext.Provider>
    ),
    contextValue: value,
  };
}

describe('RadioSelector', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  describe('single-radio backward compatibility invariant', () => {
    it('renders null when only 1 radio exists', () => {
      const { container } = renderWithContext({
        radios: [mockRadio1],
        activeRadioId: 'radio-1',
        activeRadio: mockRadio1,
      });

      expect(container.firstChild).toBeNull();
    });

    it('renders null when no radios exist', () => {
      const { container } = renderWithContext({
        radios: [],
        activeRadioId: 'default',
        activeRadio: undefined,
      });

      expect(container.firstChild).toBeNull();
    });
  });

  describe('multi-radio mode', () => {
    it('renders selector trigger with active radio name and status', () => {
      renderWithContext();

      const button = screen.getByRole('button', { name: /Select active radio/i });
      expect(button).toBeInTheDocument();
      expect(button).toHaveTextContent('Primary Roof');
    });

    it('toggles dropdown listbox when clicked', async () => {
      renderWithContext();

      const button = screen.getByRole('button', { name: /Select active radio/i });
      expect(screen.queryByRole('listbox')).not.toBeInTheDocument();

      fireEvent.click(button);
      expect(screen.getByRole('listbox')).toBeInTheDocument();
      expect(screen.getByText('Mobile Van')).toBeInTheDocument();
    });

    it('calls setActiveRadioId when a radio item is selected', async () => {
      const setActiveRadioId = vi.fn();
      renderWithContext({ setActiveRadioId });

      const button = screen.getByRole('button', { name: /Select active radio/i });
      fireEvent.click(button);

      const option2 = screen.getByRole('option', { name: /Mobile Van/i });
      fireEvent.click(option2);

      expect(setActiveRadioId).toHaveBeenCalledWith('radio-2');
      await waitFor(() => {
        expect(screen.queryByRole('listbox')).not.toBeInTheDocument();
      });
    });

    it('calls onOpenSettings when manage radios is clicked', () => {
      const onOpenSettings = vi.fn();
      renderWithContext({}, { onOpenSettings });

      const button = screen.getByRole('button', { name: /Select active radio/i });
      fireEvent.click(button);

      const manageBtn = screen.getByText(/Manage radios/i);
      fireEvent.click(manageBtn);

      expect(onOpenSettings).toHaveBeenCalled();
    });

    it('closes menu on Escape key', () => {
      renderWithContext();

      const button = screen.getByRole('button', { name: /Select active radio/i });
      fireEvent.click(button);
      expect(screen.getByRole('listbox')).toBeInTheDocument();

      fireEvent.keyDown(window, { key: 'Escape' });
      expect(screen.queryByRole('listbox')).not.toBeInTheDocument();
    });
  });

  describe('status helper functions', () => {
    it('returns correct dot class based on radio state', () => {
      expect(getRadioStatusDotClass(mockRadio1)).toBe('bg-status-connected');
      expect(getRadioStatusDotClass(mockRadio2)).toBe('bg-warning animate-pulse');
      expect(getRadioStatusDotClass({ ...mockRadio1, enabled: false })).toBe(
        'bg-muted-foreground/40'
      );
      expect(
        getRadioStatusDotClass({ ...mockRadio1, is_connected: false, is_reconnecting: false })
      ).toBe('bg-status-disconnected');
      expect(getRadioStatusDotClass(undefined)).toBe('bg-status-disconnected');
    });

    it('returns uppercase transport badges', () => {
      expect(getRadioTransportBadge(mockRadio1)).toBe('TCP');
      expect(getRadioTransportBadge(mockRadio2)).toBe('SERIAL');
      expect(getRadioTransportBadge({ ...mockRadio1, transport: 'ble' })).toBe('BLE');
      expect(getRadioTransportBadge(undefined)).toBe('TCP');
    });
  });
});
