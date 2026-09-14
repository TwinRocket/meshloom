import { describe, it, expect, beforeEach, afterEach } from 'vitest';
import { applyStartupTheme, cacheTheme, serverChoseTheme, getSavedTheme } from '../utils/theme';

/**
 * Where the theme comes from on the first frame, which is the whole point: a
 * theme learned after paint is a flash, and the page's background is the most
 * visible thing there is to get wrong.
 */

beforeEach(() => {
  localStorage.clear();
  document.documentElement.removeAttribute('data-theme-server');
  delete document.documentElement.dataset.theme;
});

afterEach(() => {
  document.documentElement.removeAttribute('data-theme-server');
  delete document.documentElement.dataset.theme;
});

describe('startup theme', () => {
  it("leaves the server's theme alone when the server wrote one", () => {
    document.documentElement.setAttribute('data-theme-server', '');
    document.documentElement.dataset.theme = 'light';
    // The cache says otherwise, and must not win: the page is already painted.
    cacheTheme('midnight');

    applyStartupTheme();

    expect(document.documentElement.dataset.theme).toBe('light');
  });

  it('respects a server that chose to follow the operating system', () => {
    // No data-theme is itself the answer: the prefers-color-scheme rules take it
    // from there, and writing the cached theme over it would undo that.
    document.documentElement.setAttribute('data-theme-server', '');
    cacheTheme('light');

    applyStartupTheme();

    expect(document.documentElement.dataset.theme).toBeUndefined();
  });

  it('paints from the cache when nothing was injected', () => {
    // The dev server and a static host in front of dist/ serve the page unmarked.
    cacheTheme('light');

    applyStartupTheme();

    expect(serverChoseTheme()).toBe(false);
    expect(document.documentElement.dataset.theme).toBe('light');
  });

  it('remembers a theme without applying it', () => {
    cacheTheme('light');
    expect(getSavedTheme()).toBe('light');
    expect(document.documentElement.dataset.theme).toBeUndefined();
  });
});
