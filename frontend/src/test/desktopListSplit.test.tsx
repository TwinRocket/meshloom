import { fireEvent, render, screen } from '@testing-library/react';
import { afterEach, describe, expect, it } from 'vitest';

import { DesktopListSplit } from '../components/DesktopListSplit';
import i18n from '../i18n';
import {
  DESKTOP_SPLIT_DEFAULT,
  DESKTOP_SPLIT_WIDTH_KEY,
  desktopSplitStorageKey,
} from '../utils/desktopSplitPreference';

describe('DesktopListSplit', () => {
  afterEach(() => {
    localStorage.removeItem(DESKTOP_SPLIT_WIDTH_KEY);
    localStorage.removeItem(desktopSplitStorageKey(window.innerWidth));
  });

  it('exposes a keyboard-resizable separator and remembers the width', () => {
    render(
      <DesktopListSplit>
        <div>list</div>
      </DesktopListSplit>
    );

    const handle = screen.getByRole('separator', {
      name: i18n.t('conversationList.resizeList'),
    });
    expect(handle.querySelector('.conversation-split-grip')).not.toBeNull();
    expect(document.querySelector('[data-desktop-list-pane]')).toHaveClass('border-r');
    expect(handle).toHaveAttribute('aria-valuenow', String(DESKTOP_SPLIT_DEFAULT));

    fireEvent.keyDown(handle, { key: 'ArrowRight' });
    expect(handle).toHaveAttribute('aria-valuenow', String(DESKTOP_SPLIT_DEFAULT + 16));
    expect(localStorage.getItem(desktopSplitStorageKey(window.innerWidth))).toBe(
      String(DESKTOP_SPLIT_DEFAULT + 16)
    );
  });
});
