import { act, renderHook } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { StrictMode } from 'react';

import { reconnectDelayMs, useWebSocket } from '../useWebSocket';

class MockWebSocket {
  static CONNECTING = 0;
  static OPEN = 1;
  static CLOSING = 2;
  static CLOSED = 3;
  static instances: MockWebSocket[] = [];

  url: string;
  readyState = MockWebSocket.OPEN;
  onopen: (() => void) | null = null;
  onclose: (() => void) | null = null;
  onerror: ((error: unknown) => void) | null = null;
  onmessage: ((event: { data: string }) => void) | null = null;

  constructor(url: string) {
    this.url = url;
    MockWebSocket.instances.push(this);
  }

  /** Real sockets deliver `close` asynchronously; tests can opt in to that. */
  static asyncClose = false;

  close(): void {
    this.readyState = MockWebSocket.CLOSED;
    if (MockWebSocket.asyncClose) {
      setTimeout(() => this.onclose?.(), 0);
    } else {
      this.onclose?.();
    }
  }

  send(): void {}
}

const originalWebSocket = globalThis.WebSocket;

describe('useWebSocket lifecycle', () => {
  beforeEach(() => {
    vi.useFakeTimers();
    MockWebSocket.instances = [];
    MockWebSocket.asyncClose = false;
    globalThis.WebSocket = MockWebSocket as unknown as typeof WebSocket;
  });

  afterEach(() => {
    globalThis.WebSocket = originalWebSocket;
    vi.useRealTimers();
  });

  it('does not reconnect after hook unmount cleanup', () => {
    const { unmount } = renderHook(() => useWebSocket({}));

    expect(MockWebSocket.instances).toHaveLength(1);

    act(() => {
      unmount();
    });

    act(() => {
      vi.advanceTimersByTime(3100);
    });

    // Unmount-triggered socket close should not start a new connection.
    expect(MockWebSocket.instances).toHaveLength(1);
  });

  it('keeps exactly one live socket under StrictMode double-mount', () => {
    MockWebSocket.asyncClose = true;
    const { unmount } = renderHook(() => useWebSocket({}), { wrapper: StrictMode });

    // mount -> cleanup -> mount: two sockets were created, the first was closed.
    expect(MockWebSocket.instances).toHaveLength(2);
    expect(MockWebSocket.instances[0].readyState).toBe(MockWebSocket.CLOSED);

    // The stale socket's late close event must not null the live ref or schedule a retry.
    act(() => {
      vi.advanceTimersByTime(60000);
    });
    expect(MockWebSocket.instances).toHaveLength(2);

    act(() => {
      unmount();
    });
    act(() => {
      vi.advanceTimersByTime(60000);
    });
    expect(MockWebSocket.instances).toHaveLength(2);
  });

  it('ignores messages from a stale socket', () => {
    MockWebSocket.asyncClose = true;
    const onHealth = vi.fn();
    renderHook(() => useWebSocket({ onHealth }), { wrapper: StrictMode });
    const [stale, live] = MockWebSocket.instances;
    const frame = { data: JSON.stringify({ type: 'pong', data: null }) };
    stale.onmessage?.(frame);
    live.onmessage?.(frame);
    expect(onHealth).not.toHaveBeenCalled();
  });

  it('reconnects with capped exponential backoff and resets after a successful open', () => {
    renderHook(() => useWebSocket({}));
    const drop = () => {
      const ws = MockWebSocket.instances[MockWebSocket.instances.length - 1];
      act(() => ws.onclose?.());
    };
    const rnd = vi.spyOn(Math, 'random').mockReturnValue(0.5); // jitter factor 1.0

    drop();
    act(() => vi.advanceTimersByTime(999));
    expect(MockWebSocket.instances).toHaveLength(1);
    act(() => vi.advanceTimersByTime(1));
    expect(MockWebSocket.instances).toHaveLength(2);

    drop();
    act(() => vi.advanceTimersByTime(1999));
    expect(MockWebSocket.instances).toHaveLength(2);
    act(() => vi.advanceTimersByTime(1));
    expect(MockWebSocket.instances).toHaveLength(3);

    // A connection that stays up for 10 s resets the backoff to the first step.
    act(() => MockWebSocket.instances[2].onopen?.());
    act(() => vi.advanceTimersByTime(10000));
    drop();
    act(() => vi.advanceTimersByTime(1000));
    expect(MockWebSocket.instances).toHaveLength(4);
    rnd.mockRestore();
  });

  it('keeps backing off when the server accepts then immediately closes', () => {
    renderHook(() => useWebSocket({}));
    const rnd = vi.spyOn(Math, 'random').mockReturnValue(0.5);
    const flap = () => {
      const ws = MockWebSocket.instances[MockWebSocket.instances.length - 1];
      act(() => ws.onopen?.());
      act(() => ws.onclose?.());
    };
    flap();
    act(() => vi.advanceTimersByTime(1000));
    expect(MockWebSocket.instances).toHaveLength(2);
    flap();
    act(() => vi.advanceTimersByTime(1000));
    expect(MockWebSocket.instances).toHaveLength(2); // second delay is 2 s, not 1 s
    act(() => vi.advanceTimersByTime(1000));
    expect(MockWebSocket.instances).toHaveLength(3);
    rnd.mockRestore();
  });

  it('resets the backoff after the first message', () => {
    renderHook(() => useWebSocket({}));
    const rnd = vi.spyOn(Math, 'random').mockReturnValue(0.5);
    const last = () => MockWebSocket.instances[MockWebSocket.instances.length - 1];
    act(() => last().onclose?.());
    act(() => vi.advanceTimersByTime(1000));
    act(() => last().onclose?.());
    act(() => vi.advanceTimersByTime(2000));
    expect(MockWebSocket.instances).toHaveLength(3);
    act(() => last().onmessage?.({ data: JSON.stringify({ type: 'pong', data: null }) }));
    act(() => last().onclose?.());
    act(() => vi.advanceTimersByTime(1000));
    expect(MockWebSocket.instances).toHaveLength(4);
    rnd.mockRestore();
  });

  it('reconnects immediately on online / visible when disconnected', () => {
    renderHook(() => useWebSocket({}));
    act(() => MockWebSocket.instances[0].onclose?.()); // 1 s timer pending
    act(() => {
      window.dispatchEvent(new Event('online'));
    });
    expect(MockWebSocket.instances).toHaveLength(2);

    act(() => MockWebSocket.instances[1].onclose?.());
    act(() => {
      document.dispatchEvent(new Event('visibilitychange'));
    });
    expect(MockWebSocket.instances).toHaveLength(3);

    // While connected, the events are no-ops.
    act(() => {
      window.dispatchEvent(new Event('online'));
    });
    expect(MockWebSocket.instances).toHaveLength(3);
  });

  it('caps the delay and applies +/-25% jitter', () => {
    expect(reconnectDelayMs(20, () => 0.5)).toBe(30000);
    expect(reconnectDelayMs(0, () => 0)).toBe(750);
    expect(reconnectDelayMs(0, () => 1)).toBe(1250);
  });
});
