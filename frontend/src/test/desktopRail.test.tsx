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

  it('toggles the channel-finder overlay instead of opening a conversation', () => {
    const onSelectTool = vi.fn();
    render(
      <DesktopRail
        active={null}
        unreadTotal={0}
        onSelect={vi.fn()}
        health={health}
        onOpenRadioStatus={vi.fn()}
        onSelectTool={onSelectTool}
        overlayPressed={{ cracker: false }}
      />
    );
    fireEvent.click(
      screen.getByRole('button', { name: i18n.t('sidebar.showChannelFinder'), hidden: true })
    );
    expect(onSelectTool).toHaveBeenCalledWith('cracker');
  });

  it('marks the channel-finder overlay as pressed when it is open', () => {
    render(
      <DesktopRail
        active={null}
        unreadTotal={0}
        onSelect={vi.fn()}
        health={health}
        onOpenRadioStatus={vi.fn()}
        onSelectTool={vi.fn()}
        overlayPressed={{ cracker: true }}
      />
    );
    expect(
      screen.getByRole('button', { name: i18n.t('sidebar.showChannelFinder'), hidden: true })
    ).toHaveAttribute('aria-pressed', 'true');
  });
});
