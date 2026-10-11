import { afterEach, describe, expect, it, vi } from 'vitest';

import {
  LIVE_IATA_STORAGE_KEY,
  effectiveLiveIataSelection,
  getSavedLiveIataSelection,
  normalizeLiveIataSelection,
  setSavedLiveIataSelection,
} from '../utils/liveIataPreference';

describe('Live region preference', () => {
  afterEach(() => {
    localStorage.removeItem(LIVE_IATA_STORAGE_KEY);
    vi.restoreAllMocks();
  });

  it('nothing saved means the radio IATA, else every region', () => {
    expect(getSavedLiveIataSelection()).toBeUndefined();
    expect(effectiveLiveIataSelection(undefined, 'lys')).toEqual(['LYS']);
    expect(effectiveLiveIataSelection(undefined, '')).toBeNull();
    expect(effectiveLiveIataSelection(undefined, undefined)).toBeNull();
    expect(effectiveLiveIataSelection(null, 'LYS')).toBeNull();
    expect(effectiveLiveIataSelection(['NCE'], 'LYS')).toEqual(['NCE']);
  });

  it('round-trips a selection and "all"', () => {
    setSavedLiveIataSelection(['tln', 'NCE', 'NCE']);
    expect(localStorage.getItem(LIVE_IATA_STORAGE_KEY)).toBe('["NCE","TLN"]');
    expect(getSavedLiveIataSelection()).toEqual(['NCE', 'TLN']);
    setSavedLiveIataSelection(null);
    expect(localStorage.getItem(LIVE_IATA_STORAGE_KEY)).toBe('all');
    expect(getSavedLiveIataSelection()).toBeNull();
    setSavedLiveIataSelection([]);
    expect(getSavedLiveIataSelection()).toBeNull();
  });

  it('ignores garbage and the XXX placeholder', () => {
    localStorage.setItem(LIVE_IATA_STORAGE_KEY, '{not json');
    expect(getSavedLiveIataSelection()).toBeUndefined();
    localStorage.setItem(LIVE_IATA_STORAGE_KEY, '"LYS"');
    expect(getSavedLiveIataSelection()).toBeUndefined();
    expect(normalizeLiveIataSelection(['XXX', 'ab', 3, 'nce'])).toEqual(['NCE']);
    expect(normalizeLiveIataSelection(['XXX'])).toBeNull();
  });

  it('survives storage that throws', () => {
    vi.spyOn(Storage.prototype, 'getItem').mockImplementation(() => {
      throw new Error('denied');
    });
    vi.spyOn(Storage.prototype, 'setItem').mockImplementation(() => {
      throw new Error('denied');
    });
    expect(getSavedLiveIataSelection()).toBeUndefined();
    expect(() => setSavedLiveIataSelection(['NCE'])).not.toThrow();
  });
});
