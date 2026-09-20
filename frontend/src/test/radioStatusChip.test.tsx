import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { render, screen, fireEvent, act } from '@testing-library/react';
import {
  RadioStatusChip,
  STATUS_DOT_UPDATE_AVAILABLE_CLASS,
  radioTransportHint,
} from '../components/RadioStatusChip';
import i18n from '../i18n';
import type { HealthStatus } from '../types';
import {
  BATTERY_DISPLAY_CHANGE_EVENT,
  setShowBatteryPercent,
  setShowBatteryVoltage,
} from '../utils/batteryDisplay';
import {
  STATUS_DOT_PULSE_CHANGE_EVENT,
  STATUS_DOT_PULSE_DURATION_MS,
  emitStatusDotPulse,
  pulseColorFor,
  setStatusDotPulseEnabled,
} from '../utils/statusDotPulse';

/**
 * The one piece of state a radio client cannot be coy about: everything anyone
 * might try next depends on it, so it must never be carried by colour alone.
 */

const connected = {
  radio_connected: true,
  radio_state: 'connected',
  connection_info: 'TCP: 192.168.1.204:5051',
  radio_stats: { battery_mv: 4050 },
} as HealthStatus;

describe('radioTransportHint', () => {
  it('keeps the transport kind and drops the address', () => {
    expect(radioTransportHint('TCP: 192.168.1.204:5051')).toBe('TCP');
    expect(radioTransportHint('Serial: /dev/ttyUSB0')).toBe('Serial');
    expect(radioTransportHint('BLE: AA:BB:CC:DD:EE:FF')).toBe('BLE');
    expect(radioTransportHint(null)).toBeNull();
  });
});

describe('RadioStatusChip', () => {
  beforeEach(() => {
    setShowBatteryPercent(false);
    setShowBatteryVoltage(false);
    setStatusDotPulseEnabled(true);
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  it('says the state in words, not only in colour', () => {
    render(<RadioStatusChip health={connected} />);
    expect(screen.getByText(i18n.t('statusBar.radioOk'))).toBeInTheDocument();
  });

  it('shows the short word and the transport on the rail, not a coloured dot', () => {
    render(<RadioStatusChip health={connected} compact />);
    const status = screen.getByRole('status');
    expect(status).toHaveAttribute(
      'title',
      `${i18n.t('statusBar.radioOk')} — TCP: 192.168.1.204:5051`
    );
    expect(screen.getByText(i18n.t('statusBar.radioOkShort'))).toBeInTheDocument();
    expect(screen.getByText('TCP')).toBeInTheDocument();
    expect(screen.queryByText(i18n.t('statusBar.radioOk'))).not.toBeInTheDocument();
  });

  it('is a status, not a control, unless it is given somewhere to go', () => {
    const { unmount } = render(<RadioStatusChip health={connected} compact />);
    expect(screen.queryByRole('button')).not.toBeInTheDocument();
    unmount();

    const onOpenRadioSettings = vi.fn();
    render(<RadioStatusChip health={connected} onOpenRadioSettings={onOpenRadioSettings} />);
    fireEvent.click(screen.getByRole('button'));
    expect(onOpenRadioSettings).toHaveBeenCalled();
  });

  it('reports a disconnected radio as disconnected', () => {
    render(<RadioStatusChip health={{ radio_connected: false } as HealthStatus} />);
    expect(screen.getByText(i18n.t('statusBar.radioDisconnected'))).toBeInTheDocument();
  });

  it('pulses the status pip when an update is available', () => {
    const { container } = render(<RadioStatusChip health={connected} updateAvailable />);
    expect(container.querySelector(`.${STATUS_DOT_UPDATE_AVAILABLE_CLASS}`)).toBeInTheDocument();
  });

  it('does not pulse the status pip when no update is available', () => {
    const { container } = render(<RadioStatusChip health={connected} />);
    expect(
      container.querySelector(`.${STATUS_DOT_UPDATE_AVAILABLE_CLASS}`)
    ).not.toBeInTheDocument();
  });

  it('keeps battery off the chip until a Local setting asks for it', () => {
    render(<RadioStatusChip health={connected} compact />);
    expect(screen.queryByText('90%')).not.toBeInTheDocument();
    expect(screen.queryByText('4.05V')).not.toBeInTheDocument();
  });

  it('shows battery percent on the rail tile when that setting is on', () => {
    setShowBatteryPercent(true);
    render(<RadioStatusChip health={connected} compact />);
    expect(screen.getByText('90%')).toBeInTheDocument();
    expect(screen.getByText('TCP')).toBeInTheDocument();
    expect(screen.getByRole('status')).toHaveAttribute(
      'title',
      `${i18n.t('statusBar.radioOk')} — TCP: 192.168.1.204:5051 — 90%`
    );
  });

  it('shows volts on the rail tile when only voltage is requested', () => {
    setShowBatteryVoltage(true);
    render(<RadioStatusChip health={connected} compact />);
    expect(screen.getByText('4.05V')).toBeInTheDocument();
  });

  it('picks up a battery-display change without remounting', () => {
    render(<RadioStatusChip health={connected} compact />);
    expect(screen.queryByText('90%')).not.toBeInTheDocument();
    act(() => {
      setShowBatteryPercent(true);
      window.dispatchEvent(new Event(BATTERY_DISPLAY_CHANGE_EVENT));
    });
    expect(screen.getByText('90%')).toBeInTheDocument();
  });

  it('glitters the existing pip when a packet arrives', () => {
    vi.useFakeTimers();
    setStatusDotPulseEnabled(true);
    const { container } = render(<RadioStatusChip health={connected} />);
    const dot = container.querySelector('[aria-hidden="true"].rounded-full');
    expect(dot).not.toHaveAttribute('data-pulse-kind');

    act(() => {
      emitStatusDotPulse('GROUP_TEXT');
    });
    expect(dot).toHaveAttribute('data-pulse-kind', 'channel');
    expect(dot).toHaveStyle({ backgroundColor: pulseColorFor('channel') });

    act(() => {
      vi.advanceTimersByTime(STATUS_DOT_PULSE_DURATION_MS);
    });
    expect(dot).not.toHaveAttribute('data-pulse-kind');
    vi.useRealTimers();
  });

  it('leaves the pip alone when glittering is turned off', () => {
    setStatusDotPulseEnabled(false);
    const { container } = render(<RadioStatusChip health={connected} />);
    act(() => {
      emitStatusDotPulse('TEXT_MESSAGE');
    });
    expect(container.querySelector('[data-pulse-kind]')).not.toBeInTheDocument();
  });

  it('stops an in-flight glitter when the setting is turned off', () => {
    vi.useFakeTimers();
    setStatusDotPulseEnabled(true);
    const { container } = render(<RadioStatusChip health={connected} />);
    act(() => {
      emitStatusDotPulse('ADVERT');
    });
    expect(container.querySelector('[data-pulse-kind="advert"]')).toBeInTheDocument();

    act(() => {
      setStatusDotPulseEnabled(false);
      window.dispatchEvent(new Event(STATUS_DOT_PULSE_CHANGE_EVENT));
    });
    expect(container.querySelector('[data-pulse-kind]')).not.toBeInTheDocument();
    vi.useRealTimers();
  });
});
