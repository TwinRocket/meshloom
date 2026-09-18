import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import {
  LIVE_PACKET_LEGEND_OPEN_KEY,
  getSavedLivePacketLegendOpen,
  setSavedLivePacketLegendOpen,
} from '../utils/liveLegendPreference';

describe('liveLegendPreference', () => {
  beforeEach(() => {
    localStorage.clear();
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  it('defaults to open when unset', () => {
    expect(getSavedLivePacketLegendOpen()).toBe(true);
  });

  it('returns false only when stored as false', () => {
    localStorage.setItem(LIVE_PACKET_LEGEND_OPEN_KEY, 'false');
    expect(getSavedLivePacketLegendOpen()).toBe(false);
  });

  it('defaults to open when localStorage throws', () => {
    vi.spyOn(Storage.prototype, 'getItem').mockImplementation(() => {
      throw new Error('blocked');
    });
    expect(getSavedLivePacketLegendOpen()).toBe(true);
  });

  it('persists open and closed', () => {
    setSavedLivePacketLegendOpen(false);
    expect(localStorage.getItem(LIVE_PACKET_LEGEND_OPEN_KEY)).toBe('false');
    setSavedLivePacketLegendOpen(true);
    expect(localStorage.getItem(LIVE_PACKET_LEGEND_OPEN_KEY)).toBe('true');
  });
});
