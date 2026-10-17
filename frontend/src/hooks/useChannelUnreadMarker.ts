import { useCallback, useState } from 'react';

import type { Conversation } from '../types';
import { getStateKey } from '../utils/conversationState';

export interface ChannelUnreadMarker {
  channelId: string;
  /** Id of the oldest unread message, straight from the server. */
  messageId: number | null;
}

interface MarkerInputs {
  channelId: string | null;
  unreadCount: number;
  boundaryId: number | null;
}

/**
 * Anchor of the unread divider for the active channel.
 *
 * Updated during render when its inputs change (React "adjust state on prop
 * change" pattern), not in an effect. It must see the unread count of the render
 * that opens the channel: `useUnreadCounts` clears that count for the viewed
 * conversation in the same render pass, and an effect would only see the
 * cleared value.
 */
export function useChannelUnreadMarker(
  activeConversation: Conversation | null,
  unreadCounts: Record<string, number>,
  firstUnreadIds: Record<string, number | null>
): { channelUnreadMarker: ChannelUnreadMarker | null; dismissChannelUnreadMarker: () => void } {
  const [channelUnreadMarker, setChannelUnreadMarker] = useState<ChannelUnreadMarker | null>(null);
  const [markerInputs, setMarkerInputs] = useState<MarkerInputs | null>(null);

  const channelId = activeConversation?.type === 'channel' ? activeConversation.id : null;
  const stateKey = channelId !== null ? getStateKey('channel', channelId) : null;
  const unreadCount = stateKey !== null ? (unreadCounts[stateKey] ?? 0) : 0;
  const boundaryId = stateKey !== null ? (firstUnreadIds[stateKey] ?? null) : null;

  if (
    markerInputs === null ||
    markerInputs.channelId !== channelId ||
    markerInputs.unreadCount !== unreadCount ||
    markerInputs.boundaryId !== boundaryId
  ) {
    setMarkerInputs({ channelId, unreadCount, boundaryId });
    if (channelId === null) {
      setChannelUnreadMarker(null);
    } else {
      setChannelUnreadMarker((prev) => {
        if (prev?.channelId === channelId) {
          // Same channel: hold the marker steady so it does not move under the
          // reader, except to fill in a boundary we did not have yet. A marker
          // created before /unreads resolved would otherwise stay blank for as long
          // as the user stays put.
          if (prev.messageId === null && boundaryId !== null) {
            return { channelId, messageId: boundaryId };
          }
          return prev;
        }
        if (unreadCount <= 0) {
          return null;
        }
        return { channelId, messageId: boundaryId };
      });
    }
  }

  const dismissChannelUnreadMarker = useCallback(() => setChannelUnreadMarker(null), []);

  return { channelUnreadMarker, dismissChannelUnreadMarker };
}
