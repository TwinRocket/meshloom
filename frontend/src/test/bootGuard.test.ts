import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

// The guard is inline in index.html: it has to work when no module could load.
const html = readFileSync(resolve(__dirname, '../../index.html'), 'utf8');
const guard = /<script>\s*\/\/ Boot guard[\s\S]*?<\/script>/.exec(html)?.[0].slice(8, -9) ?? '';

describe('index.html boot guard', () => {
  const controller: { listeners: EventListener[] } = { listeners: [] };
  const reload = vi.fn();

  beforeEach(() => {
    vi.useFakeTimers();
    sessionStorage.clear();
    reload.mockClear();
    document.body.innerHTML = '<div id="root"></div>';
    const add = window.addEventListener.bind(window);
    vi.spyOn(window, 'addEventListener').mockImplementation(
      (type: string, listener: EventListenerOrEventListenerObject, options?: unknown) => {
        if (type === 'error') controller.listeners.push(listener as EventListener);
        add(type, listener, options as AddEventListenerOptions);
      }
    );
    new Function('reloadPage', guard.replace('location.reload()', 'reloadPage()'))(reload);
  });

  afterEach(() => {
    for (const listener of controller.listeners)
      window.removeEventListener('error', listener, true);
    controller.listeners = [];
    vi.useRealTimers();
    vi.restoreAllMocks();
  });

  function failLoad(tag: 'script' | 'link', rel?: string) {
    const element = document.createElement(tag);
    if (rel) (element as HTMLLinkElement).rel = rel;
    document.head.appendChild(element);
    element.dispatchEvent(new Event('error'));
    element.remove();
  }

  it('exists in index.html, before the module script', () => {
    expect(guard).not.toBe('');
    expect(html.indexOf('// Boot guard')).toBeLessThan(html.indexOf('type="module"'));
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
    expect(sessionStorage.getItem('meshloom-boot-reloads')).toBe('1');
  });

  it('ignores icons and anything after the app has rendered', () => {
    failLoad('link', 'icon');
    document.getElementById('root')!.innerHTML = '<main></main>';
    failLoad('script');
    vi.advanceTimersByTime(10000);
    expect(reload).not.toHaveBeenCalled();
  });

  it('stops after three attempts and offers a reload in both languages', () => {
    sessionStorage.setItem('meshloom-boot-reloads', '3');
    failLoad('script');
    vi.advanceTimersByTime(10000);
    expect(reload).not.toHaveBeenCalled();
    const alert = document.querySelector('[role="alert"]');
    expect(alert?.textContent).toContain('Meshloom could not load');
    expect(alert?.textContent).toContain('Meshloom n’a pas pu se charger');
    expect(alert?.querySelector('button')).not.toBeNull();
  });
});
