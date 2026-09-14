import { describe, it, expect, vi } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import { RadioStatusChip } from '../components/RadioStatusChip';
import i18n from '../i18n';
import type { HealthStatus } from '../types';

/**
 * The one piece of state a radio client cannot be coy about: everything anyone
 * might try next depends on it, so it must never be carried by colour alone.
 */

const connected = { radio_connected: true, radio_state: 'connected' } as HealthStatus;

describe('RadioStatusChip', () => {
  it('says the state in words, not only in colour', () => {
    render(<RadioStatusChip health={connected} />);
    expect(screen.getByText(i18n.t('statusBar.radioOk'))).toBeInTheDocument();
  });

  it('keeps the word reachable when the compact form hides it', () => {
    // A screen reader still hears it; a sighted reader needs the hover, or the
    // chip is a coloured dot and nothing else.
    render(<RadioStatusChip health={connected} compact />);
    const status = screen.getByRole('status');
    expect(status).toHaveAttribute('title', i18n.t('statusBar.radioOk'));
    expect(screen.getByText(i18n.t('statusBar.radioOk'))).toHaveClass('sr-only');
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
