import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest';
import { initAppViewport } from '../utils/appViewport';

/**
 * The composer sits at the bottom of a `100dvh` column, and `dvh` does not react to
 * the virtual keyboard. These cover the fallback that does.
 */

interface FakeVisualViewport {
  height: number;
  listeners: Record<string, (() => void)[]>;
  addEventListener: (type: string, fn: () => void) => void;
  removeEventListener: (type: string, fn: () => void) => void;
  emit: (type: string) => void;
}

function installVisualViewport(height: number): FakeVisualViewport {
  const vv: FakeVisualViewport = {
    height,
    listeners: {},
    addEventListener(type, fn) {
      (vv.listeners[type] ??= []).push(fn);
    },
    removeEventListener(type, fn) {
      vv.listeners[type] = (vv.listeners[type] ?? []).filter((f) => f !== fn);
    },
    emit(type) {
      for (const fn of vv.listeners[type] ?? []) fn();
    },
  };
  Object.defineProperty(window, 'visualViewport', {
    value: vv,
    configurable: true,
    writable: true,
  });
  return vv;
}

const appHeight = () => document.documentElement.style.getPropertyValue('--app-height');

describe('initAppViewport', () => {
  let cleanup: (() => void) | null = null;

  beforeEach(() => {
    Object.defineProperty(window, 'innerHeight', {
      value: 844,
      configurable: true,
      writable: true,
    });
    document.documentElement.style.removeProperty('--app-height');
  });

  afterEach(() => {
    cleanup?.();
    cleanup = null;
    Reflect.deleteProperty(window as unknown as Record<string, unknown>, 'visualViewport');
  });

  it('writes nothing while the whole viewport is visible', () => {
    installVisualViewport(844);
    cleanup = initAppViewport();
    expect(appHeight()).toBe('');
  });

  it('takes the visible height once the keyboard covers part of the screen', () => {
    const vv = installVisualViewport(844);
    cleanup = initAppViewport();

    vv.height = 430;
    vv.emit('resize');

    expect(appHeight()).toBe('430px');
  });

  it('hands the height back to dvh when the keyboard closes', () => {
    const vv = installVisualViewport(844);
    cleanup = initAppViewport();

    vv.height = 430;
    vv.emit('resize');
    expect(appHeight()).toBe('430px');

    vv.height = 844;
    vv.emit('resize');
    expect(appHeight()).toBe('');
  });

  it('ignores the small changes that are browser chrome, not a keyboard', () => {
    const vv = installVisualViewport(844);
    cleanup = initAppViewport();

    // A collapsing address bar moves the visible area by tens of pixels; reacting
    // would fight dvh and make the shell twitch while scrolling.
    vv.height = 784;
    vv.emit('resize');

    expect(appHeight()).toBe('');
  });

  it('reacts to the visual viewport being panned, not only resized', () => {
    const vv = installVisualViewport(844);
    cleanup = initAppViewport();

    vv.height = 400;
    vv.emit('scroll');

    expect(appHeight()).toBe('400px');
  });

  it('removes what it wrote when torn down', () => {
    const vv = installVisualViewport(844);
    const stop = initAppViewport();

    vv.height = 430;
    vv.emit('resize');
    expect(appHeight()).toBe('430px');

    stop();
    expect(appHeight()).toBe('');
    // And stops listening: a later change must not resurrect the property.
    vv.height = 300;
    vv.emit('resize');
    expect(appHeight()).toBe('');
  });

  it('is a no-op where visualViewport does not exist', () => {
    Reflect.deleteProperty(window as unknown as Record<string, unknown>, 'visualViewport');
    const stop = initAppViewport();
    expect(() => stop()).not.toThrow();
    expect(appHeight()).toBe('');
  });
});

describe('initAppViewport when installed', () => {
  let cleanup: (() => void) | null = null;

  beforeEach(() => {
    Object.defineProperty(window, 'innerHeight', {
      value: 844,
      configurable: true,
      writable: true,
    });
    document.documentElement.style.removeProperty('--app-height');
    Object.defineProperty(window, 'matchMedia', {
      configurable: true,
      writable: true,
      value: (query: string) => ({
        matches: query.includes('standalone'),
        media: query,
        addEventListener() {},
        removeEventListener() {},
      }),
    });
  });

  afterEach(() => {
    cleanup?.();
    cleanup = null;
    Reflect.deleteProperty(window as unknown as Record<string, unknown>, 'visualViewport');
    Reflect.deleteProperty(window as unknown as Record<string, unknown>, 'matchMedia');
  });

  it('takes the visible height even when nothing is covering the screen', () => {
    // Installed there is no chrome to collapse, and dvh leaves a band at the bottom
    // the app cannot draw into. The measurement is the rule here, not the exception.
    const vv = installVisualViewport(812);
    cleanup = initAppViewport();
    expect(appHeight()).toBe('812px');

    vv.height = 800;
    vv.emit('resize');
    expect(appHeight()).toBe('800px');
  });
});

describe('initAppViewport teardown', () => {
  it('detaches both listeners', () => {
    const vv = installVisualViewport(844);
    const remove = vi.spyOn(vv, 'removeEventListener');
    initAppViewport()();
    expect(remove).toHaveBeenCalledTimes(2);
    Reflect.deleteProperty(window as unknown as Record<string, unknown>, 'visualViewport');
  });
});
