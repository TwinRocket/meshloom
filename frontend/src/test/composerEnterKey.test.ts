import { describe, it, expect, afterEach, vi } from 'vitest';
import {
  DESKTOP_ENTER_SENDS_QUERY,
  getDesktopEnterSends,
  subscribeDesktopEnterSends,
} from '../utils/composerEnterKey';

type Listener = () => void;

function stubMatchMedia(matchesByQuery: (query: string) => boolean) {
  const listeners = new Map<string, Set<Listener>>();
  const lists = new Map<
    string,
    {
      matches: boolean;
      media: string;
      addEventListener: (type: string, fn: Listener) => void;
      removeEventListener: (type: string, fn: Listener) => void;
      emit: () => void;
    }
  >();

  vi.stubGlobal('matchMedia', (query: string) => {
    const existing = lists.get(query);
    if (existing) return existing;
    const list = {
      matches: matchesByQuery(query),
      media: query,
      addEventListener(_type: string, fn: Listener) {
        const set = listeners.get(query) ?? new Set();
        set.add(fn);
        listeners.set(query, set);
      },
      removeEventListener(_type: string, fn: Listener) {
        listeners.get(query)?.delete(fn);
      },
      emit() {
        this.matches = matchesByQuery(query);
        for (const fn of listeners.get(query) ?? []) fn();
      },
    };
    lists.set(query, list);
    return list;
  });

  return {
    emit(query: string) {
      lists.get(query)?.emit();
    },
  };
}

afterEach(() => {
  vi.unstubAllGlobals();
});

describe('composerEnterKey', () => {
  it('treats a hover-capable fine pointer as desktop Enter-to-send', () => {
    stubMatchMedia((query) => query === DESKTOP_ENTER_SENDS_QUERY);
    expect(getDesktopEnterSends()).toBe(true);
  });

  it('keeps Enter as newline when the primary pointer is not a desktop mouse', () => {
    stubMatchMedia(() => false);
    expect(getDesktopEnterSends()).toBe(false);
  });

  it('notifies subscribers when the pointer capability changes', () => {
    let desktop = false;
    const media = stubMatchMedia((query) => query === DESKTOP_ENTER_SENDS_QUERY && desktop);
    const onChange = vi.fn();
    const unsub = subscribeDesktopEnterSends(onChange);

    desktop = true;
    media.emit(DESKTOP_ENTER_SENDS_QUERY);
    expect(onChange).toHaveBeenCalledTimes(1);

    unsub();
    media.emit(DESKTOP_ENTER_SENDS_QUERY);
    expect(onChange).toHaveBeenCalledTimes(1);
  });
});
