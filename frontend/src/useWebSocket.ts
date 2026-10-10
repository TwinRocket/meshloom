import { useEffect, useRef, useCallback } from 'react';
import type {
  Channel,
  CommunityLiveStatus,
  CommunityPacket,
  HealthStatus,
  Contact,
  Message,
  MessagePath,
  RawPacket,
} from './types';
import { isDispatchableWsEvent, parseWsEvent } from './wsEvents';

interface ErrorEvent {
  message: string;
  details?: string;
  code?: string;
  params?: Record<string, unknown>;
}

interface SuccessEvent {
  message: string;
  details?: string;
  code?: string;
  params?: Record<string, unknown>;
}

export interface UseWebSocketOptions {
  onHealth?: (health: HealthStatus) => void;
  onMessage?: (message: Message) => void;
  onContact?: (contact: Contact) => void;
  onContactResolved?: (previousPublicKey: string, contact: Contact) => void;
  onContactDeleted?: (publicKey: string) => void;
  onChannel?: (channel: Channel) => void;
  onChannelDeleted?: (key: string) => void;
  onRawPacket?: (packet: RawPacket) => void;
  onCommunityPacket?: (packet: CommunityPacket) => void;
  onCommunityLive?: (status: CommunityLiveStatus) => void;
  onMessageAcked?: (
    messageId: number,
    ackCount: number,
    paths?: MessagePath[],
    packetId?: number | null,
    extras?: { packet_hash?: string | null; observer_reach_eligible?: boolean | null }
  ) => void;
  onMessageDeleted?: (messageId: number) => void;
  onError?: (error: ErrorEvent) => void;
  onSuccess?: (success: SuccessEvent) => void;
  onReconnect?: () => void;
}

const RECONNECT_BASE_MS = 1000;
const RECONNECT_MAX_MS = 30000;
/** A connection must survive this long (or deliver a message) before backoff resets. */
const STABLE_CONNECTION_MS = 10000;

/** Capped exponential backoff with +/-25% jitter, so a restarted server is not hit by
 *  every client at the same instant. `attempt` is 0 for the first retry. */
export function reconnectDelayMs(attempt: number, random: () => number = Math.random): number {
  const base = Math.min(RECONNECT_MAX_MS, RECONNECT_BASE_MS * 2 ** Math.min(attempt, 10));
  return Math.round(base * (0.75 + random() * 0.5));
}

export function useWebSocket(options: UseWebSocketOptions) {
  const wsRef = useRef<WebSocket | null>(null);
  const reconnectTimeoutRef = useRef<number | null>(null);
  const shouldReconnectRef = useRef(true);
  const hasConnectedRef = useRef(false);
  const attemptRef = useRef(0);
  const stableTimerRef = useRef<number | null>(null);

  // Store options in ref to avoid stale closures in WebSocket handlers.
  // The onmessage callback captures this ref, and we keep the ref updated
  // with the latest handlers. This way, even though the WebSocket connection
  // is only created once, it always calls the current handlers.
  const optionsRef = useRef<UseWebSocketOptions>(options);

  // Keep the ref updated with latest options
  useEffect(() => {
    optionsRef.current = options;
  }, [options]);

  // Connect function - uses ref for handlers to avoid stale closures
  // No dependencies needed since we access handlers through ref
  const connect = useCallback(() => {
    // Determine WebSocket URL based on current location
    const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
    // Resolve relative to the page so sub-path reverse proxies work
    const base = new URL('./api/ws', window.location.href);
    const wsUrl = `${protocol}//${base.host}${base.pathname}`;

    const ws = new WebSocket(wsUrl);

    // Every handler ignores sockets that are no longer current. Closing a socket is
    // asynchronous, so under StrictMode (mount, cleanup, mount) or after a reconnect the
    // previous socket's late events must not clobber wsRef or schedule extra reconnects.
    wsRef.current = ws;
    const isCurrent = () => wsRef.current === ws;
    const clearStableTimer = () => {
      if (stableTimerRef.current !== null) {
        clearTimeout(stableTimerRef.current);
        stableTimerRef.current = null;
      }
    };

    ws.onopen = () => {
      if (!isCurrent()) return;
      // Do not reset the backoff yet: a server that accepts then immediately closes would
      // otherwise be retried at the base delay forever. Reset once the link proves stable.
      clearStableTimer();
      stableTimerRef.current = window.setTimeout(() => {
        attemptRef.current = 0;
      }, STABLE_CONNECTION_MS);
      // Connection established (or re-established after disconnect)
      if (reconnectTimeoutRef.current) {
        clearTimeout(reconnectTimeoutRef.current);
        reconnectTimeoutRef.current = null;
      }
      if (hasConnectedRef.current) {
        optionsRef.current.onReconnect?.();
      }
      hasConnectedRef.current = true;
    };

    ws.onclose = () => {
      if (!isCurrent()) return;
      clearStableTimer();
      // Connection lost — will auto-reconnect after delay
      wsRef.current = null;

      if (!shouldReconnectRef.current) {
        return;
      }

      if (reconnectTimeoutRef.current) {
        clearTimeout(reconnectTimeoutRef.current);
      }
      const delay = reconnectDelayMs(attemptRef.current);
      attemptRef.current += 1;
      reconnectTimeoutRef.current = window.setTimeout(() => {
        reconnectTimeoutRef.current = null;
        connect();
      }, delay);
    };

    ws.onerror = (error) => {
      if (!isCurrent()) return;
      console.error('WebSocket error:', error);
    };

    ws.onmessage = (event) => {
      if (!isCurrent()) return;
      if (attemptRef.current !== 0) {
        attemptRef.current = 0;
        clearStableTimer();
      }
      try {
        const msg = parseWsEvent(event.data);
        if (!isDispatchableWsEvent(msg)) {
          console.warn('Skipping WebSocket event with missing fields:', msg.type);
          return;
        }
        // Access handlers through ref to always use current versions
        const handlers = optionsRef.current;

        switch (msg.type) {
          case 'health':
            handlers.onHealth?.(msg.data as HealthStatus);
            break;
          case 'message':
            handlers.onMessage?.(msg.data as Message);
            break;
          case 'contact':
            handlers.onContact?.(msg.data as Contact);
            break;
          case 'contact_resolved': {
            const resolved = msg.data as {
              previous_public_key: string;
              contact: Contact;
            };
            handlers.onContactResolved?.(resolved.previous_public_key, resolved.contact);
            break;
          }
          case 'channel':
            handlers.onChannel?.(msg.data as Channel);
            break;
          case 'contact_deleted':
            handlers.onContactDeleted?.((msg.data as { public_key: string }).public_key);
            break;
          case 'channel_deleted':
            handlers.onChannelDeleted?.((msg.data as { key: string }).key);
            break;
          case 'raw_packet':
            handlers.onRawPacket?.(msg.data as RawPacket);
            break;
          case 'community_packet':
            handlers.onCommunityPacket?.(msg.data as CommunityPacket);
            break;
          case 'community_live':
            handlers.onCommunityLive?.(msg.data as CommunityLiveStatus);
            break;
          case 'message_acked': {
            const ackData = msg.data as {
              message_id: number;
              ack_count: number;
              paths?: MessagePath[];
              packet_id?: number | null;
              packet_hash?: string | null;
              observer_reach_eligible?: boolean | null;
            };
            if (
              ackData.packet_hash !== undefined ||
              ackData.observer_reach_eligible !== undefined
            ) {
              handlers.onMessageAcked?.(
                ackData.message_id,
                ackData.ack_count,
                ackData.paths,
                ackData.packet_id,
                {
                  packet_hash: ackData.packet_hash,
                  observer_reach_eligible: ackData.observer_reach_eligible,
                }
              );
            } else {
              handlers.onMessageAcked?.(
                ackData.message_id,
                ackData.ack_count,
                ackData.paths,
                ackData.packet_id
              );
            }
            break;
          }
          case 'message_deleted':
            handlers.onMessageDeleted?.((msg.data as { message_id: number }).message_id);
            break;
          case 'error':
            handlers.onError?.(msg.data as ErrorEvent);
            break;
          case 'success':
            handlers.onSuccess?.(msg.data as SuccessEvent);
            break;
          case 'pong':
            // Heartbeat response, ignore
            break;
          case 'unknown':
            console.warn('Unknown WebSocket message type:', msg.rawType);
        }
      } catch (e) {
        console.error('Failed to parse WebSocket message:', e);
      }
    };
  }, []); // No dependencies - handlers accessed through ref

  useEffect(() => {
    shouldReconnectRef.current = true;
    attemptRef.current = 0;
    connect();

    // Ping every 30 seconds to keep connection alive
    const pingInterval = setInterval(() => {
      if (wsRef.current?.readyState === WebSocket.OPEN) {
        wsRef.current.send('ping');
      }
    }, 30000);

    // Coming back online or to the foreground: do not sit out a long backoff.
    const reconnectNow = () => {
      if (!shouldReconnectRef.current || wsRef.current !== null) return;
      if (reconnectTimeoutRef.current) {
        clearTimeout(reconnectTimeoutRef.current);
        reconnectTimeoutRef.current = null;
      }
      connect();
    };
    const onVisibility = () => {
      if (document.visibilityState === 'visible') reconnectNow();
    };
    window.addEventListener('online', reconnectNow);
    document.addEventListener('visibilitychange', onVisibility);

    return () => {
      shouldReconnectRef.current = false;
      window.removeEventListener('online', reconnectNow);
      document.removeEventListener('visibilitychange', onVisibility);
      if (stableTimerRef.current !== null) {
        clearTimeout(stableTimerRef.current);
        stableTimerRef.current = null;
      }
      clearInterval(pingInterval);
      if (reconnectTimeoutRef.current) {
        clearTimeout(reconnectTimeoutRef.current);
        reconnectTimeoutRef.current = null;
      }
      // Detach first so the socket's own close event is ignored as stale.
      const current = wsRef.current;
      wsRef.current = null;
      current?.close();
    };
  }, [connect]);
}
