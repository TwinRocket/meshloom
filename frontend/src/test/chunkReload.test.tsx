import { render, screen } from '@testing-library/react';
import { lazy } from 'react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { LazyBoundary } from '../components/LazyBoundary';
import i18n from '../i18n';
import { clearReloadFlag, installPreloadErrorHandler, reloadOnce } from '../utils/chunkReload';

describe('chunk reload guard', () => {
  beforeEach(() => sessionStorage.clear());
  afterEach(() => vi.restoreAllMocks());

  it('reloads once, then refuses until the flag is cleared', () => {
    const reload = vi.fn();
    expect(reloadOnce(reload)).toBe(true);
    expect(reloadOnce(reload)).toBe(false);
    expect(reload).toHaveBeenCalledTimes(1);
    clearReloadFlag();
    expect(reloadOnce(reload)).toBe(true);
  });

  it('handles vite:preloadError with a single guarded reload', () => {
    const target = new EventTarget() as unknown as Window;
    installPreloadErrorHandler(target);
    const reloadSpy = vi.fn();
    const original = window.location;
    Object.defineProperty(window, 'location', {
      configurable: true,
      value: { ...original, reload: reloadSpy },
    });
    const first = new Event('vite:preloadError', { cancelable: true });
    target.dispatchEvent(first);
    const second = new Event('vite:preloadError', { cancelable: true });
    target.dispatchEvent(second);
    Object.defineProperty(window, 'location', { configurable: true, value: original });
    expect(reloadSpy).toHaveBeenCalledTimes(1);
    expect(first.defaultPrevented).toBe(true);
    expect(second.defaultPrevented).toBe(false);
  });
});

describe('LazyBoundary', () => {
  it('shows a reload prompt when the lazy chunk fails to load', async () => {
    vi.spyOn(console, 'error').mockImplementation(() => {});
    const Broken = lazy(() =>
      Promise.reject(new Error('Failed to fetch dynamically imported module'))
    );
    render(
      <LazyBoundary fallback={<div>loading</div>}>
        <Broken />
      </LazyBoundary>
    );
    expect(await screen.findByRole('alert')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: i18n.t('chunkError.reload') })).toBeInTheDocument();
  });
});
