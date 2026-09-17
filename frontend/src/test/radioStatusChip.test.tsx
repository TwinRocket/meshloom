import { describe, it, expect, vi } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import { RadioStatusChip, radioTransportHint } from '../components/RadioStatusChip';
import i18n from '../i18n';
import type { HealthStatus } from '../types';

/**
 * The one piece of state a radio client cannot be coy about: everything anyone
 * might try next depends on it, so it must never be carried by colour alone.
 */

const connected = {
  radio_connected: true,
  radio_state: 'connected',
  connection_info: 'TCP: 192.168.1.204:5051',
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
});
