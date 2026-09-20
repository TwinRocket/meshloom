import { fireEvent, render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';

import { ConversationOverflowMenu } from '../components/ConversationOverflowMenu';
import i18n from '../i18n';
import { PUBLIC_CHANNEL_KEY } from '../utils/publicChannel';

function renderMenu(
  overrides: Partial<React.ComponentProps<typeof ConversationOverflowMenu>> = {}
) {
  const onOpenChange = vi.fn();
  const onTogglePin = vi.fn();
  const onToggleFavorite = vi.fn();
  const onDelete = vi.fn();
  render(
    <ConversationOverflowMenu
      open
      onOpenChange={onOpenChange}
      pinned={false}
      favorite={false}
      canNotify
      notifyMediaEnabled={{ push: true, email: false, webhook: false }}
      emailReady
      webhookReady
      canMute
      canRegion
      canDelete
      isChannel
      onTogglePin={onTogglePin}
      onToggleFavorite={onToggleFavorite}
      onSetMedia={vi.fn()}
      onMute={vi.fn()}
      onEditRegion={vi.fn()}
      onDelete={onDelete}
      {...overrides}
    />
  );
  return { onOpenChange, onTogglePin, onToggleFavorite, onDelete };
}

describe('ConversationOverflowMenu', () => {
  it('lists channel actions including mute, region, and leave', () => {
    renderMenu();
    expect(screen.getByRole('menuitem', { name: i18n.t('chatHeader.pin') })).toBeInTheDocument();
    expect(
      screen.getByRole('menuitem', { name: i18n.t('chatHeader.notifications') })
    ).toBeInTheDocument();
    expect(
      screen.getByRole('menuitem', { name: i18n.t('chatHeader.muteChannel') })
    ).toBeInTheDocument();
    expect(
      screen.getByRole('menuitem', { name: i18n.t('chatHeader.addFavorite') })
    ).toBeInTheDocument();
    expect(
      screen.getByRole('menuitem', { name: i18n.t('chatHeader.regionalOverride') })
    ).toBeInTheDocument();
    expect(
      screen.getByRole('menuitem', { name: i18n.t('chatHeader.leaveChannel') })
    ).toBeInTheDocument();
  });

  it('omits mute and region on a direct conversation', () => {
    renderMenu({ canMute: false, canRegion: false, isChannel: false });
    expect(
      screen.queryByRole('menuitem', { name: i18n.t('chatHeader.muteChannel') })
    ).not.toBeInTheDocument();
    expect(
      screen.queryByRole('menuitem', { name: i18n.t('chatHeader.regionalOverride') })
    ).not.toBeInTheDocument();
    expect(
      screen.getByRole('menuitem', { name: i18n.t('chatHeader.deleteConversation') })
    ).toBeInTheDocument();
  });

  it('pins from the menu', () => {
    const { onTogglePin, onOpenChange } = renderMenu();
    fireEvent.click(screen.getByRole('menuitem', { name: i18n.t('chatHeader.pin') }));
    expect(onTogglePin).toHaveBeenCalled();
    expect(onOpenChange).toHaveBeenCalledWith(false);
  });

  it('can hide delete, as for the public channel', () => {
    renderMenu({ canDelete: false, isChannel: true });
    expect(
      screen.queryByRole('menuitem', { name: i18n.t('chatHeader.leaveChannel') })
    ).not.toBeInTheDocument();
    expect(PUBLIC_CHANNEL_KEY).toHaveLength(32);
  });
});
