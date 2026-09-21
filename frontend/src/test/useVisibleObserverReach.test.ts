import { act, renderHook } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import {
  applyCommunityPacketObserverTick,
  resetObserverReachCountCache,
  useVisibleObserverReach,
} from '../hooks/useVisibleObserverReach';
import type { Message } from '../types';

const getCounts = vi.hoisted(() => vi.fn());

vi.mock('../api', () => ({
  api: {
    getPacketObserverReachCounts: (...args: [string[]]) => getCounts(...args),
  },
}));

function channelMessage(receivedAt: number): Message {
  return {
    id: 1,
    type: 'CHAN',
    conversation_key: 'C3B889530D4F02DB5662EA13C417F530',
    text: 'Alice: hello',
    sender_timestamp: receivedAt,
    received_at: receivedAt,
    paths: null,
    txt_type: 0,
    signature: null,
    sender_key: null,
    outgoing: false,
    acked: 0,
    sender_name: 'Alice',
    packet_hash: 'AABBCCDDEEFF0011',
    observer_reach_eligible: true,
  };
}

describe('useVisibleObserverReach', () => {
  beforeEach(() => {
    vi.useFakeTimers();
    resetObserverReachCountCache();
    getCounts.mockReset();
    getCounts.mockResolvedValue({
      directory_enabled: true,
      counts: { AABBCCDDEEFF0011: 2 },
      sealed: { AABBCCDDEEFF0011: false },
    });
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  it('refetches a 30-second-old visible message', async () => {
    const receivedAt = Math.floor(Date.now() / 1000) - 30;
    renderHook(() =>
      useVisibleObserverReach({
        directoryEnabled: true,
        conversationKey: 'C3B889530D4F02DB5662EA13C417F530',
        messages: [channelMessage(receivedAt)],
        visibleIndexes: [0],
      })
    );

    await act(async () => {
      await vi.advanceTimersByTimeAsync(300);
    });
    expect(getCounts).toHaveBeenCalledTimes(1);

    await act(async () => {
      await vi.advanceTimersByTimeAsync(8_050);
    });
    await act(async () => {
      await vi.advanceTimersByTimeAsync(300);
    });
    expect(getCounts).toHaveBeenCalledTimes(2);
  });

  it('does not loop-refetch a sealed 15-minute-old message', async () => {
    getCounts.mockResolvedValue({
      directory_enabled: true,
      counts: { AABBCCDDEEFF0011: 2 },
      sealed: { AABBCCDDEEFF0011: true },
    });
    const receivedAt = Math.floor(Date.now() / 1000) - 15 * 60;
    renderHook(() =>
      useVisibleObserverReach({
        directoryEnabled: true,
        conversationKey: 'C3B889530D4F02DB5662EA13C417F530',
        messages: [channelMessage(receivedAt)],
        visibleIndexes: [0],
      })
    );

    await act(async () => {
      await vi.advanceTimersByTimeAsync(300);
    });
    expect(getCounts).toHaveBeenCalledTimes(1);

    await act(async () => {
      await vi.advanceTimersByTimeAsync(120_000);
    });
    expect(getCounts).toHaveBeenCalledTimes(1);
  });

  it('does not refetch a sealed count', async () => {
    getCounts.mockResolvedValue({
      directory_enabled: true,
      counts: { AABBCCDDEEFF0011: 3 },
      sealed: { AABBCCDDEEFF0011: true },
    });
    const receivedAt = Math.floor(Date.now() / 1000) - 15 * 60;
    renderHook(() =>
      useVisibleObserverReach({
        directoryEnabled: true,
        conversationKey: 'C3B889530D4F02DB5662EA13C417F530',
        messages: [channelMessage(receivedAt)],
        visibleIndexes: [0],
      })
    );

    await act(async () => {
      await vi.advanceTimersByTimeAsync(300);
    });
    expect(getCounts).toHaveBeenCalledTimes(1);

    await act(async () => {
      await vi.advanceTimersByTimeAsync(5 * 60_000);
    });
    expect(getCounts).toHaveBeenCalledTimes(1);
  });

  it('retries an error after the backoff instead of caching it forever', async () => {
    getCounts.mockRejectedValueOnce(new Error('down'));
    getCounts.mockResolvedValue({
      directory_enabled: true,
      counts: { AABBCCDDEEFF0011: 2 },
      sealed: { AABBCCDDEEFF0011: true },
    });
    const receivedAt = Math.floor(Date.now() / 1000) - 15 * 60;
    renderHook(() =>
      useVisibleObserverReach({
        directoryEnabled: true,
        conversationKey: 'C3B889530D4F02DB5662EA13C417F530',
        messages: [channelMessage(receivedAt)],
        visibleIndexes: [0],
      })
    );

    await act(async () => {
      await vi.advanceTimersByTimeAsync(300);
    });
    expect(getCounts).toHaveBeenCalledTimes(1);

    await act(async () => {
      await vi.advanceTimersByTimeAsync(29_000);
    });
    expect(getCounts).toHaveBeenCalledTimes(1);

    await act(async () => {
      await vi.advanceTimersByTimeAsync(1_050);
    });
    await act(async () => {
      await vi.advanceTimersByTimeAsync(300);
    });
    expect(getCounts).toHaveBeenCalledTimes(2);
  });

  it('keeps counts when the messages array is replaced with the same hashes', async () => {
    const receivedAt = Math.floor(Date.now() / 1000) - 15 * 60;
    const first = channelMessage(receivedAt);
    const { rerender, result } = renderHook(
      ({ messages }: { messages: Message[] }) =>
        useVisibleObserverReach({
          directoryEnabled: true,
          conversationKey: 'C3B889530D4F02DB5662EA13C417F530',
          messages,
          visibleIndexes: [0],
        }),
      { initialProps: { messages: [first] } }
    );

    await act(async () => {
      await vi.advanceTimersByTimeAsync(300);
    });
    expect(getCounts).toHaveBeenCalledTimes(1);
    expect(result.current.counts.AABBCCDDEEFF0011).toEqual({ status: 'ok', count: 2 });

    rerender({ messages: [{ ...first }] });
    await act(async () => {
      await vi.advanceTimersByTimeAsync(300);
    });
    expect(getCounts).toHaveBeenCalledTimes(1);
    expect(result.current.counts.AABBCCDDEEFF0011).toEqual({ status: 'ok', count: 2 });
  });

  it('ticks a young visible hash16 and does not double the same ear', async () => {
    const receivedAt = Math.floor(Date.now() / 1000) - 5;
    const { result } = renderHook(() =>
      useVisibleObserverReach({
        directoryEnabled: true,
        conversationKey: 'C3B889530D4F02DB5662EA13C417F530',
        messages: [channelMessage(receivedAt)],
        visibleIndexes: [0],
      })
    );

    act(() => {
      applyCommunityPacketObserverTick({
        packet_hash: 'aabbccddeeff0011',
        hash8: 'aabbccdd',
        ear_id: 'ear-1',
      });
    });
    expect(result.current.counts.AABBCCDDEEFF0011).toEqual({ status: 'ok', count: 1 });

    act(() => {
      applyCommunityPacketObserverTick({
        packet_hash: 'aabbccddeeff0011',
        hash8: 'aabbccdd',
        ear_id: 'ear-1',
      });
    });
    expect(result.current.counts.AABBCCDDEEFF0011).toEqual({ status: 'ok', count: 1 });

    act(() => {
      applyCommunityPacketObserverTick({
        packet_hash: 'aabbccddeeff0011',
        hash8: 'aabbccdd',
        ear_id: 'ear-2',
      });
    });
    expect(result.current.counts.AABBCCDDEEFF0011).toEqual({ status: 'ok', count: 2 });
  });

  it('does not increment live ticks after a REST poll already owns the hash', async () => {
    const receivedAt = Math.floor(Date.now() / 1000) - 5;
    const { result } = renderHook(() =>
      useVisibleObserverReach({
        directoryEnabled: true,
        conversationKey: 'C3B889530D4F02DB5662EA13C417F530',
        messages: [channelMessage(receivedAt)],
        visibleIndexes: [0],
      })
    );

    await act(async () => {
      await vi.advanceTimersByTimeAsync(300);
    });
    expect(result.current.counts.AABBCCDDEEFF0011).toEqual({ status: 'ok', count: 2 });

    act(() => {
      applyCommunityPacketObserverTick({
        packet_hash: 'aabbccddeeff0011',
        hash8: 'aabbccdd',
        ear_id: 'ear-after-poll',
      });
    });
    expect(result.current.counts.AABBCCDDEEFF0011).toEqual({ status: 'ok', count: 2 });
  });

  it('does not re-render the consumer on unrelated live rain', () => {
    const receivedAt = Math.floor(Date.now() / 1000) - 5;
    let renders = 0;
    renderHook(() => {
      renders += 1;
      return useVisibleObserverReach({
        directoryEnabled: true,
        conversationKey: 'C3B889530D4F02DB5662EA13C417F530',
        messages: [channelMessage(receivedAt)],
        visibleIndexes: [0],
      });
    });
    const afterMount = renders;

    act(() => {
      for (let i = 0; i < 40; i += 1) {
        applyCommunityPacketObserverTick({
          packet_hash: 'ffffffff00000000',
          hash8: 'ffffffff',
          ear_id: `ear-rain-${i}`,
        });
      }
    });
    expect(renders).toBe(afterMount);
  });

  it('still ticks live after directory-off poll that does not own the hash', async () => {
    getCounts.mockResolvedValueOnce({
      directory_enabled: false,
      counts: {},
      sealed: {},
    });
    const receivedAt = Math.floor(Date.now() / 1000) - 5;
    const { result } = renderHook(() =>
      useVisibleObserverReach({
        directoryEnabled: true,
        conversationKey: 'C3B889530D4F02DB5662EA13C417F530',
        messages: [channelMessage(receivedAt)],
        visibleIndexes: [0],
      })
    );

    await act(async () => {
      await vi.advanceTimersByTimeAsync(300);
    });
    expect(result.current.counts.AABBCCDDEEFF0011).toEqual({ status: 'error' });

    act(() => {
      applyCommunityPacketObserverTick({
        packet_hash: 'aabbccddeeff0011',
        hash8: 'aabbccdd',
        ear_id: 'ear-after-directory-off',
      });
    });
    expect(result.current.counts.AABBCCDDEEFF0011).toEqual({ status: 'ok', count: 1 });
  });

  it('still ticks live after a failed poll that does not own the hash', async () => {
    getCounts.mockRejectedValueOnce(new Error('down'));
    const receivedAt = Math.floor(Date.now() / 1000) - 5;
    const { result } = renderHook(() =>
      useVisibleObserverReach({
        directoryEnabled: true,
        conversationKey: 'C3B889530D4F02DB5662EA13C417F530',
        messages: [channelMessage(receivedAt)],
        visibleIndexes: [0],
      })
    );

    await act(async () => {
      await vi.advanceTimersByTimeAsync(300);
    });
    expect(result.current.counts.AABBCCDDEEFF0011).toEqual({ status: 'error' });

    act(() => {
      applyCommunityPacketObserverTick({
        packet_hash: 'aabbccddeeff0011',
        hash8: 'aabbccdd',
        ear_id: 'ear-after-error',
      });
    });
    expect(result.current.counts.AABBCCDDEEFF0011).toEqual({ status: 'ok', count: 1 });
  });

  it('lets a REST poll overwrite a live tick count', async () => {
    const receivedAt = Math.floor(Date.now() / 1000) - 5;
    const { result } = renderHook(() =>
      useVisibleObserverReach({
        directoryEnabled: true,
        conversationKey: 'C3B889530D4F02DB5662EA13C417F530',
        messages: [channelMessage(receivedAt)],
        visibleIndexes: [0],
      })
    );

    act(() => {
      applyCommunityPacketObserverTick({
        packet_hash: 'aabbccddeeff0011',
        hash8: 'aabbccdd',
        ear_id: 'ear-1',
      });
    });
    expect(result.current.counts.AABBCCDDEEFF0011).toEqual({ status: 'ok', count: 1 });

    await act(async () => {
      await vi.advanceTimersByTimeAsync(300);
    });
    expect(result.current.counts.AABBCCDDEEFF0011).toEqual({ status: 'ok', count: 2 });
  });

  it('uses hash8 only when exactly one visible message matches the prefix', () => {
    const receivedAt = Math.floor(Date.now() / 1000) - 5;
    const first = channelMessage(receivedAt);
    const second: Message = {
      ...channelMessage(receivedAt),
      id: 2,
      packet_hash: 'AABBCCDDFFFFFFFF',
    };
    const { result } = renderHook(() =>
      useVisibleObserverReach({
        directoryEnabled: true,
        conversationKey: 'C3B889530D4F02DB5662EA13C417F530',
        messages: [first, second],
        visibleIndexes: [0, 1],
      })
    );

    act(() => {
      applyCommunityPacketObserverTick({
        hash8: 'aabbccdd',
        ear_id: 'ear-ambig',
      });
    });
    expect(result.current.counts.AABBCCDDEEFF0011).toBeUndefined();
    expect(result.current.counts.AABBCCDDFFFFFFFF).toBeUndefined();
  });

  it('does not tick hash8 when a visible older message shares the prefix', () => {
    const youngAt = Math.floor(Date.now() / 1000) - 5;
    const oldAt = Math.floor(Date.now() / 1000) - 180;
    const young = channelMessage(youngAt);
    const older: Message = {
      ...channelMessage(oldAt),
      id: 2,
      packet_hash: 'AABBCCDDFFFFFFFF',
    };
    const { result } = renderHook(() =>
      useVisibleObserverReach({
        directoryEnabled: true,
        conversationKey: 'C3B889530D4F02DB5662EA13C417F530',
        messages: [young, older],
        visibleIndexes: [0, 1],
      })
    );

    act(() => {
      applyCommunityPacketObserverTick({
        hash8: 'aabbccdd',
        ear_id: 'ear-old-prefix',
      });
    });
    expect(result.current.counts.AABBCCDDEEFF0011).toBeUndefined();
    expect(result.current.counts.AABBCCDDFFFFFFFF).toBeUndefined();
  });

  it('does not tick hash8 when the only matching message is old', () => {
    const oldAt = Math.floor(Date.now() / 1000) - 180;
    const { result } = renderHook(() =>
      useVisibleObserverReach({
        directoryEnabled: true,
        conversationKey: 'C3B889530D4F02DB5662EA13C417F530',
        messages: [channelMessage(oldAt)],
        visibleIndexes: [0],
      })
    );

    act(() => {
      applyCommunityPacketObserverTick({
        hash8: 'aabbccdd',
        ear_id: 'ear-only-old',
      });
    });
    expect(result.current.counts.AABBCCDDEEFF0011).toBeUndefined();
  });

  it('does not tick hash8 when an off-window loaded message shares the prefix', () => {
    const youngAt = Math.floor(Date.now() / 1000) - 5;
    const oldAt = Math.floor(Date.now() / 1000) - 180;
    const young = channelMessage(youngAt);
    const older: Message = {
      ...channelMessage(oldAt),
      id: 2,
      packet_hash: 'AABBCCDDFFFFFFFF',
    };
    const { result } = renderHook(() =>
      useVisibleObserverReach({
        directoryEnabled: true,
        conversationKey: 'C3B889530D4F02DB5662EA13C417F530',
        messages: [young, older],
        visibleIndexes: [0],
      })
    );

    act(() => {
      applyCommunityPacketObserverTick({
        hash8: 'aabbccdd',
        ear_id: 'ear-off-window',
      });
    });
    expect(result.current.counts.AABBCCDDEEFF0011).toBeUndefined();
  });

  it('ignores live ticks for hashes that are not visible', () => {
    const receivedAt = Math.floor(Date.now() / 1000) - 5;
    const { result } = renderHook(() =>
      useVisibleObserverReach({
        directoryEnabled: true,
        conversationKey: 'C3B889530D4F02DB5662EA13C417F530',
        messages: [channelMessage(receivedAt)],
        visibleIndexes: [0],
      })
    );

    act(() => {
      applyCommunityPacketObserverTick({
        packet_hash: 'ffffffff00000000',
        hash8: 'ffffffff',
        ear_id: 'ear-other',
      });
    });
    expect(result.current.counts.AABBCCDDEEFF0011).toBeUndefined();
    expect(result.current.counts.FFFFFFFF00000000).toBeUndefined();
  });

  it('ticks hash8 when a single visible young message matches', () => {
    const receivedAt = Math.floor(Date.now() / 1000) - 5;
    const { result } = renderHook(() =>
      useVisibleObserverReach({
        directoryEnabled: true,
        conversationKey: 'C3B889530D4F02DB5662EA13C417F530',
        messages: [channelMessage(receivedAt)],
        visibleIndexes: [0],
      })
    );

    act(() => {
      applyCommunityPacketObserverTick({
        hash8: 'aabbccdd',
        ear_id: 'ear-old-stats',
      });
    });
    expect(result.current.counts.AABBCCDDEEFF0011).toEqual({ status: 'ok', count: 1 });
  });
});
