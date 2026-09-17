import { afterEach, describe, expect, it } from 'vitest';

import {
  clampDesktopSplitWidth,
  DESKTOP_SPLIT_DEFAULT,
  DESKTOP_SPLIT_MIN,
  DESKTOP_SPLIT_WIDTH_KEY,
  desktopSplitStorageKey,
  getSavedDesktopSplitWidth,
  setSavedDesktopSplitWidth,
} from '../utils/desktopSplitPreference';

describe('desktop split width', () => {
  afterEach(() => {
    localStorage.removeItem(DESKTOP_SPLIT_WIDTH_KEY);
    localStorage.removeItem(desktopSplitStorageKey(800));
    localStorage.removeItem(desktopSplitStorageKey(1280));
  });

  it('clamps to the min and leaves room for the chat', () => {
    expect(clampDesktopSplitWidth(80, 1280)).toBe(DESKTOP_SPLIT_MIN);
    expect(clampDesktopSplitWidth(900, 1280)).toBe(480);
    expect(clampDesktopSplitWidth(400, 800)).toBeLessThanOrEqual(800 - 56 - 320);
  });

  it('persists a clamped width per viewport bucket', () => {
    setSavedDesktopSplitWidth(390, 1280);
    setSavedDesktopSplitWidth(280, 800);
    expect(getSavedDesktopSplitWidth(1280)).toBe(390);
    expect(getSavedDesktopSplitWidth(800)).toBe(280);
    expect(localStorage.getItem(desktopSplitStorageKey(1280))).toBe('390');
    expect(localStorage.getItem(desktopSplitStorageKey(800))).toBe('280');
  });

  it('reads the legacy unbucketed key when a bucket is empty', () => {
    localStorage.setItem(DESKTOP_SPLIT_WIDTH_KEY, '400');
    expect(getSavedDesktopSplitWidth(1280)).toBe(400);
  });

  it('falls back to the default when nothing is stored', () => {
    expect(getSavedDesktopSplitWidth(1280)).toBe(DESKTOP_SPLIT_DEFAULT);
  });
});
