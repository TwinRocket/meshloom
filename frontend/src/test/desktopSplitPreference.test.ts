import { afterEach, describe, expect, it } from 'vitest';

import {
  clampDesktopSplitWidth,
  DESKTOP_SPLIT_DEFAULT,
  DESKTOP_SPLIT_MIN,
  DESKTOP_SPLIT_WIDTH_KEY,
  getSavedDesktopSplitWidth,
  setSavedDesktopSplitWidth,
} from '../utils/desktopSplitPreference';

describe('desktop split width', () => {
  afterEach(() => {
    localStorage.removeItem(DESKTOP_SPLIT_WIDTH_KEY);
  });

  it('clamps to the min and leaves room for the chat', () => {
    expect(clampDesktopSplitWidth(80, 1280)).toBe(DESKTOP_SPLIT_MIN);
    expect(clampDesktopSplitWidth(900, 1280)).toBe(480);
    expect(clampDesktopSplitWidth(400, 800)).toBeLessThanOrEqual(800 - 56 - 320);
  });

  it('persists a clamped width', () => {
    setSavedDesktopSplitWidth(390);
    expect(getSavedDesktopSplitWidth(1280)).toBe(390);
    expect(localStorage.getItem(DESKTOP_SPLIT_WIDTH_KEY)).toBe('390');
  });

  it('falls back to the default when nothing is stored', () => {
    expect(getSavedDesktopSplitWidth(1280)).toBe(DESKTOP_SPLIT_DEFAULT);
  });
});
