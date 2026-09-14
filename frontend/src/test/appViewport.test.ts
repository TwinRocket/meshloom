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

/** A keyboard implies a focused field; the module refuses to believe in one without. */
function focusAField(): HTMLTextAreaElement {
  const field = document.createElement('textarea');
  document.body.appendChild(field);
  field.focus();
  return field;
}

function blurField(field: HTMLTextAreaElement) {
  field.blur();
  field.remove();
}

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
    const field = focusAField();

    vv.height = 430;
    vv.emit('resize');

    expect(appHeight()).toBe('430px');
    blurField(field);
  });

  it('ignores a short viewport when nothing is focused', () => {
    // An installed app reports a much shorter visual viewport while it is opening.
    // Taken at face value that looked like a keyboard and left the shell a third
    // shorter than the screen for the rest of the session.
    const vv = installVisualViewport(844);
    cleanup = initAppViewport();

    vv.height = 609;
    vv.emit('resize');

    expect(appHeight()).toBe('');
  });

  it('lets go as soon as the field is blurred', () => {
    const vv = installVisualViewport(844);
    cleanup = initAppViewport();
    const field = focusAField();
    vv.height = 430;
    vv.emit('resize');
    expect(appHeight()).toBe('430px');

    blurField(field);
    expect(appHeight()).toBe('');
  });

  it('hands the height back when the keyboard closes', () => {
    const vv = installVisualViewport(844);
    cleanup = initAppViewport();
    const field = focusAField();

    vv.height = 430;
    vv.emit('resize');
    expect(appHeight()).toBe('430px');

    vv.height = 844;
    vv.emit('resize');
    expect(appHeight()).toBe('');
    blurField(field);
  });

  it('ignores the small changes that are browser chrome, not a keyboard', () => {
    const vv = installVisualViewport(844);
    cleanup = initAppViewport();
    const field = focusAField();

    // A collapsing address bar moves the visible area by tens of pixels; reacting
    // would fight the layout viewport and make the shell twitch while scrolling.
    vv.height = 784;
    vv.emit('resize');

    expect(appHeight()).toBe('');
    blurField(field);
  });

  it('reacts to the visual viewport being panned, not only resized', () => {
    const vv = installVisualViewport(844);
    cleanup = initAppViewport();
    const field = focusAField();

    vv.height = 400;
    vv.emit('scroll');

    expect(appHeight()).toBe('400px');
    blurField(field);
  });

  it('removes what it wrote when torn down', () => {
    const vv = installVisualViewport(844);
    const stop = initAppViewport();
    const field = focusAField();

    vv.height = 430;
    vv.emit('resize');
    expect(appHeight()).toBe('430px');
    blurField(field);

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

  it('leaves the document height alone when nothing is covering the screen', () => {
    // Installed is not a reason to override the height. Doing that made the document
    // a different height from the layout viewport, and iOS anchors `position: fixed`
    // to the layout viewport — which put the bottom bar off screen entirely.
    const vv = installVisualViewport(844);
    cleanup = initAppViewport();
    expect(appHeight()).toBe('');

    vv.height = 420;
    vv.emit('resize');
    expect(appHeight()).toBe('');
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
