import { fireEvent, render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';

import { DesktopRail } from '../components/DesktopRail';
import i18n from '../i18n';
import type { HealthStatus } from '../types';

const health = { radio_connected: true, radio_state: 'connected' } as HealthStatus;

function renderRail(updateAvailable: boolean, onOpenUpdate = vi.fn()) {
  render(
    <DesktopRail
      active={null}
      unreadTotal={0}
      onSelect={vi.fn()}
      health={health}
      onOpenRadioStatus={vi.fn()}
      onSelectTool={vi.fn()}
      updateAvailable={updateAvailable}
      onOpenUpdate={onOpenUpdate}
    />
  );
  return { onOpenUpdate };
}

describe('DesktopRail', () => {
  it('badges the settings gear when an update is available', () => {
    const { onOpenUpdate } = renderRail(true);
    const badge = screen.getByRole('button', { name: i18n.t('updates.badgeLabel'), hidden: true });
    fireEvent.click(badge);
    expect(onOpenUpdate).toHaveBeenCalled();
  });

  it('hides the settings update badge when no update is available', () => {
    renderRail(false);
    expect(
      screen.queryByRole('button', { name: i18n.t('updates.badgeLabel'), hidden: true })
    ).not.toBeInTheDocument();
  });
});
