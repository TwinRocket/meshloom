import { renderHook, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import * as hashtagKey from '../utils/hashtagKey';
import { useCommunityHashtagNames } from './useCommunityHashtagNames';

const mocks = vi.hoisted(() => ({
  api: {
    getCommunity: vi.fn(),
    getCommunityHashtags: vi.fn(),
  },
}));

vi.mock('../api', () => ({ api: mocks.api }));

describe('useCommunityHashtagNames', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it('does not fetch hashtags or derive secrets when Community is off', async () => {
    const deriveSpy = vi.spyOn(hashtagKey, 'deriveHashtagKeyHex');

    const { result } = renderHook(() => useCommunityHashtagNames(false));

    expect(mocks.api.getCommunity).not.toHaveBeenCalled();
    expect(mocks.api.getCommunityHashtags).not.toHaveBeenCalled();
    expect(deriveSpy).not.toHaveBeenCalled();
    expect(result.current).toEqual([]);
    deriveSpy.mockRestore();
  });

  it('fetches names after opt-in', async () => {
    mocks.api.getCommunityHashtags.mockResolvedValue({
      hashtags: [{ name: 'mesh', hash_byte: 'ab' }],
    });

    const { result, rerender } = renderHook(
      ({ enabled }: { enabled: boolean }) => useCommunityHashtagNames(enabled),
      { initialProps: { enabled: false } }
    );

    expect(result.current).toEqual([]);
    expect(mocks.api.getCommunityHashtags).not.toHaveBeenCalled();

    rerender({ enabled: true });
    await waitFor(() => {
      expect(result.current).toEqual(['mesh']);
    });
    expect(mocks.api.getCommunityHashtags).toHaveBeenCalledTimes(1);
  });

  it('clears catalogue names and stops fetching after opt-out', async () => {
    mocks.api.getCommunityHashtags.mockResolvedValue({
      hashtags: [{ name: 'mesh', hash_byte: 'ab' }],
    });

    const { result, rerender } = renderHook(
      ({ enabled }: { enabled: boolean }) => useCommunityHashtagNames(enabled),
      { initialProps: { enabled: true } }
    );

    await waitFor(() => {
      expect(result.current).toEqual(['mesh']);
    });

    rerender({ enabled: false });
    await waitFor(() => {
      expect(result.current).toEqual([]);
    });
    expect(mocks.api.getCommunityHashtags).toHaveBeenCalledTimes(1);
  });

  it('fetches names when Community is enabled, without deriving keys', async () => {
    const deriveSpy = vi.spyOn(hashtagKey, 'deriveHashtagKeyHex');
    mocks.api.getCommunityHashtags.mockResolvedValue({
      hashtags: [
        { name: 'mesh', hash_byte: 'ab' },
        { name: 'fr', hash_byte: 'cd' },
      ],
    });

    const { result } = renderHook(() => useCommunityHashtagNames(true));

    await waitFor(() => {
      expect(result.current).toEqual(['mesh', 'fr']);
    });
    expect(mocks.api.getCommunity).not.toHaveBeenCalled();
    expect(mocks.api.getCommunityHashtags).toHaveBeenCalledTimes(1);
    expect(deriveSpy).not.toHaveBeenCalled();
    deriveSpy.mockRestore();
  });

  it('returns [] on fetch error', async () => {
    mocks.api.getCommunityHashtags.mockRejectedValue(new Error('network'));

    const { result } = renderHook(() => useCommunityHashtagNames(true));

    await waitFor(() => {
      expect(mocks.api.getCommunityHashtags).toHaveBeenCalled();
    });
    expect(result.current).toEqual([]);
  });

  it('merges optional local extra names after Community names', async () => {
    mocks.api.getCommunityHashtags.mockResolvedValue({
      hashtags: [{ name: 'mesh', hash_byte: 'ab' }],
    });

    const { result } = renderHook(() => useCommunityHashtagNames(true, ['mesh', 'local']));

    await waitFor(() => {
      expect(result.current).toEqual(['mesh', 'local']);
    });
  });

  it('keeps extra local names when Community is off', () => {
    const { result } = renderHook(() => useCommunityHashtagNames(false, ['local']));
    expect(result.current).toEqual(['local']);
    expect(mocks.api.getCommunityHashtags).not.toHaveBeenCalled();
  });
});
