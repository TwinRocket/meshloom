import { render, screen } from '@testing-library/react';
import { lazy } from 'react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { LazyBoundary } from '../components/LazyBoundary';
import i18n from '../i18n';
import {
  installPreloadErrorHandler,
  loadOptional,
  resetPendingReload,
  scheduleReload,
} from '../utils/chunkReload';

describe('chunk reload guard', () => {
  beforeEach(() => {
    sessionStorage.clear();
    resetPendingReload();
    vi.useFakeTimers();
  });
  afterEach(() => {
    vi.useRealTimers();
    vi.restoreAllMocks();
  });

  it('waits longer before each reload, then refuses', () => {
    const reload = vi.fn();
    for (const [attempt, delay] of [1000, 2000, 4000].entries()) {
      expect(scheduleReload(reload)).toBe(true);
      vi.advanceTimersByTime(delay - 1);
      expect(reload).toHaveBeenCalledTimes(attempt);
      vi.advanceTimersByTime(1);
      expect(reload).toHaveBeenCalledTimes(attempt + 1);
      // The real reload starts a new page; the fake one has to end this page's attempt.
      resetPendingReload();
    }
    expect(scheduleReload(reload)).toBe(false);
    vi.advanceTimersByTime(50000);
    expect(scheduleReload(reload)).toBe(false);
    expect(reload).toHaveBeenCalledTimes(3);
  });

  it('gets its attempts again only a minute after the last reload', () => {
    const reload = vi.fn();
    sessionStorage.setItem('meshloom-boot-reloads', `3:${Date.now()}`);
    vi.advanceTimersByTime(60000);
    expect(scheduleReload(reload)).toBe(false);
    vi.advanceTimersByTime(1);
    expect(scheduleReload(reload)).toBe(true);
    expect(sessionStorage.getItem('meshloom-boot-reloads')?.startsWith('1:')).toBe(true);
  });

  it('never reloads by itself when sessionStorage is unavailable', () => {
    const reload = vi.fn();
    vi.spyOn(Storage.prototype, 'getItem').mockImplementation(() => {
      throw new Error('denied');
    });
    expect(scheduleReload(reload)).toBe(false);
    vi.advanceTimersByTime(10000);
    expect(reload).not.toHaveBeenCalled();
  });

  it('never reloads by itself when the attempt cannot be recorded', () => {
    const reload = vi.fn();
    vi.spyOn(Storage.prototype, 'setItem').mockImplementation(() => {
      throw new Error('full');
    });
    expect(scheduleReload(reload)).toBe(false);
    vi.advanceTimersByTime(10000);
    expect(reload).not.toHaveBeenCalled();
  });

  it('counts several failures of one page as one attempt', () => {
    const reload = vi.fn();
    expect(scheduleReload(reload)).toBe(true);
    expect(scheduleReload(reload)).toBe(true);
    expect(scheduleReload(reload)).toBe(true);
    vi.advanceTimersByTime(1000);
    expect(reload).toHaveBeenCalledTimes(1);
    expect(sessionStorage.getItem('meshloom-boot-reloads')?.startsWith('1:')).toBe(true);
  });

  it('handles vite:preloadError with one delayed reload, and stops when attempts run out', () => {
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
    vi.advanceTimersByTime(1000);
    expect(reloadSpy).toHaveBeenCalledTimes(1);
    expect(first.defaultPrevented).toBe(true);
    expect(second.defaultPrevented).toBe(true);

    resetPendingReload();
    sessionStorage.setItem('meshloom-boot-reloads', `3:${Date.now()}`);
    const last = new Event('vite:preloadError', { cancelable: true });
    target.dispatchEvent(last);
    Object.defineProperty(window, 'location', { configurable: true, value: original });
    expect(last.defaultPrevented).toBe(false);
  });
});

describe('loadOptional', () => {
  beforeEach(() => {
    sessionStorage.clear();
    resetPendingReload();
  });

  it('gives the result of a load that works', async () => {
    await expect(loadOptional(async () => 'module')).resolves.toBe('module');
  });

  it('gives null and schedules no reload when the load fails', async () => {
    const target = new EventTarget();
    installPreloadErrorHandler(target as unknown as Window);
    const event = new Event('vite:preloadError', { cancelable: true });
    const result = await loadOptional(async () => {
      target.dispatchEvent(event);
      throw new Error('chunk 404');
    });
    expect(result).toBeNull();
    expect(event.defaultPrevented).toBe(false);
    expect(sessionStorage.getItem('meshloom-boot-reloads')).toBeNull();
  });

  it('leaves the reload guard on once it is over', async () => {
    const setTimeoutSpy = vi.spyOn(window, 'setTimeout').mockReturnValue(0 as never);
    const target = new EventTarget();
    installPreloadErrorHandler(target as unknown as Window);
    await loadOptional(async () => {
      throw new Error('chunk 404');
    });
    const event = new Event('vite:preloadError', { cancelable: true });
    target.dispatchEvent(event);
    expect(event.defaultPrevented).toBe(true);
    setTimeoutSpy.mockRestore();
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
