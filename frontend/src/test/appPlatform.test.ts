import { describe, it, expect, afterEach, vi } from 'vitest';
import { initAppViewport } from '../utils/appViewport';

/**
 * The platform attribute is the seam a second set of chrome conventions hangs on,
 * so what it resolves to is a contract, not a detail.
 */

// jsdom defines neither property as configurable getters, so they are installed
// rather than spied on.
function withUserAgent(ua: string, maxTouchPoints = 0) {
  Object.defineProperty(window.navigator, 'userAgent', { value: ua, configurable: true });
  Object.defineProperty(window.navigator, 'maxTouchPoints', {
    value: maxTouchPoints,
    configurable: true,
  });
  return initAppViewport();
}

afterEach(() => {
  vi.restoreAllMocks();
  delete document.documentElement.dataset.platform;
});

describe('platform marking', () => {
  it('names iOS from the phone and tablet user agents alike', () => {
    withUserAgent('Mozilla/5.0 (iPhone; CPU iPhone OS 18_0 like Mac OS X) AppleWebKit/605.1.15');
    expect(document.documentElement.dataset.platform).toBe('ios');
  });

  it('names iOS for an iPad that reports itself as a Mac', () => {
    // iPadOS sends a desktop Safari string; the touch points are what give it away.
    withUserAgent('Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15', 5);
    expect(document.documentElement.dataset.platform).toBe('ios');
  });

  it('names Android', () => {
    withUserAgent('Mozilla/5.0 (Linux; Android 14; Pixel 8) AppleWebKit/537.36');
    expect(document.documentElement.dataset.platform).toBe('android');
  });

  it('claims neither for a desktop browser rather than guessing', () => {
    withUserAgent('Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 Chrome/120');
    expect(document.documentElement.dataset.platform).toBe('other');
  });
});
