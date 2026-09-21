import { fireEvent, render, screen } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import { CommandPalette } from '../components/CommandPalette';
import i18n from '../i18n';

beforeEach(() => {
  Object.defineProperty(HTMLElement.prototype, 'scrollIntoView', {
    configurable: true,
    value: vi.fn(),
  });
});

function renderPalette() {
  const onSelectConversation = vi.fn();
  const onOpenSettings = vi.fn();
  const onRepeaterAutoLogin = vi.fn();
  render(
    <CommandPalette
      contacts={[]}
      channels={[]}
      onSelectConversation={onSelectConversation}
      onOpenSettings={onOpenSettings}
      onRepeaterAutoLogin={onRepeaterAutoLogin}
    />
  );
  return { onSelectConversation };
}

describe('CommandPalette', () => {
  it('opens the control journal as its own conversation', () => {
    const { onSelectConversation } = renderPalette();

    fireEvent.keyDown(document, { key: 'k', ctrlKey: true });

    fireEvent.click(screen.getByText(i18n.t('sidebar.controlJournal')));
    expect(onSelectConversation).toHaveBeenCalledWith({
      type: 'control',
      id: 'control',
      name: i18n.t('sidebar.controlJournal'),
    });
  });
});
