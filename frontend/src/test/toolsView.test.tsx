import { describe, it, expect, vi } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import { ToolsView } from '../components/ToolsView';
import i18n from '../i18n';

/**
 * Tools is the only way to these screens on a phone — the rail that used to list
 * them is gone — so what it offers is navigation, not presentation.
 */

function renderTools(overrides?: Partial<React.ComponentProps<typeof ToolsView>>) {
  const onSelectConversation = vi.fn();
  const onMarkAllRead = vi.fn();
  render(
    <ToolsView
      onSelectConversation={onSelectConversation}
      onMarkAllRead={onMarkAllRead}
      {...overrides}
    />
  );
  return { onSelectConversation, onMarkAllRead };
}

describe('ToolsView', () => {
  it('offers every tool the rail used to list, the live map included', () => {
    renderTools();
    for (const key of [
      'sidebar.packetFeed',
      'sidebar.live',
      'sidebar.meshVisualizer',
      'sidebar.trace',
      'locate.title',
      'sidebar.messageSearch',
      'sidebar.discoveredChannels',
    ]) {
      expect(screen.getByText(i18n.t(key))).toBeInTheDocument();
    }
  });

  it('opens the live map as its own conversation', () => {
    const { onSelectConversation } = renderTools();
    fireEvent.click(screen.getByText(i18n.t('sidebar.live')));
    expect(onSelectConversation).toHaveBeenCalledWith(
      expect.objectContaining({ type: 'live', id: 'live' })
    );
  });

  it('says what each tool is for, since the names alone do not', () => {
    renderTools();
    expect(screen.getByText(i18n.t('toolsView.liveDescription'))).toBeInTheDocument();
    expect(screen.getByText(i18n.t('toolsView.packetFeedDescription'))).toBeInTheDocument();
  });

  it('reports mark-all-read to the shell', () => {
    const { onMarkAllRead } = renderTools();
    fireEvent.click(screen.getByText(i18n.t('sidebar.markAllRead')));
    expect(onMarkAllRead).toHaveBeenCalled();
  });
});
