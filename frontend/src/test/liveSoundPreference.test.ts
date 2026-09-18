import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import {
  LIVE_SOUND_THEME_KEY,
  getSavedLiveSoundTheme,
  isLiveSoundTheme,
  setSavedLiveSoundTheme,
} from '../utils/liveSoundPreference';

describe('liveSoundPreference', () => {
  beforeEach(() => {
    localStorage.clear();
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  it('defaults to off when unset', () => {
    expect(getSavedLiveSoundTheme()).toBe('off');
  });

  it('reads a stored theme', () => {
    localStorage.setItem(LIVE_SOUND_THEME_KEY, 'laser');
    expect(getSavedLiveSoundTheme()).toBe('laser');
  });

  it('falls back to off for unknown values', () => {
    localStorage.setItem(LIVE_SOUND_THEME_KEY, 'siren');
    expect(getSavedLiveSoundTheme()).toBe('off');
  });

  it('defaults to off when localStorage throws', () => {
    vi.spyOn(Storage.prototype, 'getItem').mockImplementation(() => {
      throw new Error('blocked');
    });
    expect(getSavedLiveSoundTheme()).toBe('off');
  });

  it('persists the chosen theme', () => {
    setSavedLiveSoundTheme('bit8');
    expect(localStorage.getItem(LIVE_SOUND_THEME_KEY)).toBe('bit8');
  });

  it('accepts only known theme tokens', () => {
    expect(isLiveSoundTheme('bubbles')).toBe(true);
    expect(isLiveSoundTheme('off')).toBe(true);
    expect(isLiveSoundTheme('pew')).toBe(false);
  });
});
