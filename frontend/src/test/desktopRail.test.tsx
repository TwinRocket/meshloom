import { fireEvent, render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';

import { DesktopRail, MESHLOOM_SITE_URL } from '../components/DesktopRail';
import i18n from '../i18n';
import type { HealthStatus } from '../types';

const health = {
  radio_connected: true,
  radio_state: 'connected',
  connection_info: 'TCP: 192.168.1.204:5051',
} as HealthStatus;

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
  it('shows the radio word and transport above settings, not a coloured dot', () => {
    renderRail(false);
    const rail = screen.getByRole('navigation', { hidden: true });
    expect(rail).toHaveTextContent(i18n.t('statusBar.radioOkShort'));
    expect(rail).toHaveTextContent('TCP');
  });

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

  it('opens discovered channels as a tool conversation', () => {
    const onSelectTool = vi.fn();
    render(
      <DesktopRail
        active={null}
        unreadTotal={0}
        pendingCount={2}
        onSelect={vi.fn()}
        health={health}
        onOpenRadioStatus={vi.fn()}
        onSelectTool={onSelectTool}
      />
    );
    const button = screen.getByRole('button', {
      name: i18n.t('sidebar.discoveredChannels'),
      hidden: true,
    });
    expect(button).toHaveTextContent('2');
    fireEvent.click(button);
    expect(onSelectTool).toHaveBeenCalledWith('discovered');
  });

  it('opens meshloom.app from the mark and conversations from the chat glyph', () => {
    const onSelect = vi.fn();
    render(
      <DesktopRail
        active="conversations"
        unreadTotal={3}
        onSelect={onSelect}
        health={health}
        onOpenRadioStatus={vi.fn()}
        onSelectTool={vi.fn()}
      />
    );
    const site = screen.getByRole('link', {
      name: i18n.t('desktopRail.siteLink'),
      hidden: true,
    });
    expect(site).toHaveAttribute('href', MESHLOOM_SITE_URL);
    expect(site).toHaveAttribute('target', '_blank');
    expect(site).toHaveAttribute('rel', 'noopener noreferrer');
    expect(site).toHaveClass('mb-16');
    expect(site.querySelector('img')).toHaveAttribute('src', './meshloom-mark.svg');

    const chat = screen.getByRole('button', {
      name: i18n.t('bottomNav.conversations'),
      hidden: true,
    });
    expect(chat).toHaveAttribute('aria-current', 'page');
    expect(chat).toHaveTextContent('3');
    fireEvent.click(chat);
    expect(onSelect).toHaveBeenCalledWith('conversations');
  });

  it('marks the discovered-channels tool as current when it is open', () => {
    render(
      <DesktopRail
        active={null}
        unreadTotal={0}
        onSelect={vi.fn()}
        health={health}
        onOpenRadioStatus={vi.fn()}
        onSelectTool={vi.fn()}
        activeToolId="discovered"
      />
    );
    expect(
      screen.getByRole('button', { name: i18n.t('sidebar.discoveredChannels'), hidden: true })
    ).toHaveAttribute('aria-current', 'page');
  });
});
