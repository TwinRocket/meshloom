import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

vi.mock('../App', () => ({ App: () => 'app' }));
vi.mock('../auth/SessionProvider', () => ({
  SessionProvider: ({ children }: { children: unknown }) => children,
}));
vi.mock('../contexts/PushSubscriptionContext', () => ({
  PushSubscriptionProvider: ({ children }: { children: unknown }) => children,
}));

// The guard is inline in index.html: it has to work when no module could load.
const html = readFileSync(resolve(__dirname, '../../index.html'), 'utf8');
const guardStart = html.indexOf('// Boot guard');
const guard = html.slice(guardStart, html.indexOf('</' + 'script>', guardStart));
const COUNT_KEY = 'meshloom-boot-reloads';
const FAKED: ('setTimeout' | 'clearTimeout' | 'Date')[] = ['setTimeout', 'clearTimeout', 'Date'];

describe('index.html boot guard', () => {
  let listeners: EventListener[] = [];
  const reload = vi.fn();

  /** What a new page does first: the inline guard runs again, with nothing pending. */
  function loadGuard() {
    for (const listener of listeners) window.removeEventListener('error', listener, true);
    listeners = [];
    document.body.innerHTML = '<div id="root"></div>';
    new Function('reloadPage', guard.replace('location.reload();', 'reloadPage();'))(reload);
  }

  beforeEach(() => {
    vi.useFakeTimers({ toFake: FAKED });
    sessionStorage.clear();
    reload.mockClear();
    const add = window.addEventListener.bind(window);
    vi.spyOn(window, 'addEventListener').mockImplementation(
      (type: string, listener: EventListenerOrEventListenerObject, options?: unknown) => {
        if (type === 'error') listeners.push(listener as EventListener);
        add(type, listener, options as AddEventListenerOptions);
      }
    );
    loadGuard();
  });

  afterEach(() => {
    for (const listener of listeners) window.removeEventListener('error', listener, true);
    listeners = [];
    vi.useRealTimers();
    vi.restoreAllMocks();
    vi.resetModules();
  });

  function failLoad(tag: 'script' | 'link', rel?: string) {
    const element = document.createElement(tag);
    if (rel) (element as HTMLLinkElement).rel = rel;
    document.head.appendChild(element);
    element.dispatchEvent(new Event('error'));
    element.remove();
  }

  /** The real main.tsx: the JS loads even though the stylesheet was refused. */
  async function bootApp() {
    // Module loading needs real timers; the guard's reload has already been counted.
    vi.useRealTimers();
    vi.resetModules();
    await import('../main');
    const { i18nReady } = await import('../i18n');
    await i18nReady;
    // Let the handlers chained on i18nReady run: they decide whether to render.
    await new Promise((done) => setTimeout(done, 0));
    vi.useFakeTimers({ toFake: FAKED });
  }

  it('exists in index.html, before the module script', () => {
    expect(guardStart).toBeGreaterThan(-1);
    expect(guardStart).toBeLessThan(html.indexOf('type="module"'));
  });

  it('reloads after a delay when a module script is refused', () => {
    failLoad('script');
    expect(reload).not.toHaveBeenCalled();
    vi.advanceTimersByTime(1000);
    expect(reload).toHaveBeenCalledTimes(1);
  });

  it('counts several failures of one page as one attempt', () => {
    failLoad('script');
    failLoad('link', 'stylesheet');
    vi.advanceTimersByTime(1000);
    expect(reload).toHaveBeenCalledTimes(1);
    expect(sessionStorage.getItem(COUNT_KEY)?.startsWith('1:')).toBe(true);
  });

  it('ignores icons and anything after the app has rendered', () => {
    failLoad('link', 'icon');
    document.getElementById('root')!.innerHTML = '<main></main>';
    failLoad('script');
    vi.advanceTimersByTime(10000);
    expect(reload).not.toHaveBeenCalled();
  });

  it('does not loop when the stylesheet stays refused and the JS boots on every page', async () => {
    for (const delay of [1000, 2000, 4000]) {
      const before = reload.mock.calls.length;
      failLoad('link', 'stylesheet');
      vi.advanceTimersByTime(delay);
      expect(reload).toHaveBeenCalledTimes(before + 1);
      await bootApp();
      loadGuard();
    }
    failLoad('link', 'stylesheet');
    await bootApp();
    vi.advanceTimersByTime(30000);
    expect(reload).toHaveBeenCalledTimes(3);
    // The message stays: the app does not render over it.
    expect(document.querySelector('[role="alert"]')).not.toBeNull();
  });

  it('gets its attempts again once a minute has passed without a reload', () => {
    sessionStorage.setItem(COUNT_KEY, `3:${Date.now()}`);
    vi.advanceTimersByTime(60001);
    failLoad('script');
    vi.advanceTimersByTime(1000);
    expect(reload).toHaveBeenCalledTimes(1);
  });

  it('stops after three attempts and offers a reload in both languages', () => {
    sessionStorage.setItem(COUNT_KEY, `3:${Date.now()}`);
    failLoad('script');
    vi.advanceTimersByTime(10000);
    expect(reload).not.toHaveBeenCalled();
    const alert = document.querySelector<HTMLElement>('[role="alert"]');
    expect(alert?.textContent).toContain('Meshloom could not load');
    expect(alert?.textContent).toContain('Meshloom n’a pas pu se charger');
    expect(alert?.querySelector('button')).not.toBeNull();
  });

  it('sets its own colours and background, since the stylesheet may be missing', () => {
    sessionStorage.setItem(COUNT_KEY, `3:${Date.now()}`);
    failLoad('script');
    const alert = document.querySelector<HTMLElement>('[role="alert"]')!;
    expect(alert.style.backgroundColor).not.toBe('');
    expect(alert.style.color).not.toBe('');
    const button = alert.querySelector('button')!;
    expect(button.style.backgroundColor).not.toBe('');
    expect(button.style.color).not.toBe('');
  });

  it('never reloads by itself when sessionStorage is unavailable', () => {
    vi.spyOn(Storage.prototype, 'getItem').mockImplementation(() => {
      throw new Error('denied');
    });
    failLoad('script');
    vi.advanceTimersByTime(10000);
    expect(reload).not.toHaveBeenCalled();
    expect(document.querySelector('[role="alert"]')).not.toBeNull();
  });
});
