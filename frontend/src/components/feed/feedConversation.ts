import type { Conversation } from '../../types';

/** Rows asked of `GET /api/messages` per page. */
export const FEED_PAGE_SIZE = 200;
/** Rows mounted at once, whatever the memory window holds (`FEED_MAX_ROWS`). */
export const FEED_DOM_MAX = 300;

export function isFeedConversation(
  conversation: { type: string } | null | undefined
): conversation is Conversation & { type: 'feed' } {
  return conversation?.type === 'feed';
}

/** The tool a conversation lights in the rail, the Tools list and the command palette. */
export function toolIdOf(conversation: { type: string; feedPreset?: string } | null | undefined) {
  if (!conversation) return null;
  return conversation.type === 'feed' && conversation.feedPreset === 'control'
    ? 'control'
    : conversation.type;
}

export function makeFeedConversation(
  name: string,
  feedPreset?: 'control',
  feedQuery?: string
): Conversation {
  return {
    type: 'feed',
    id: 'feed',
    name,
    ...(feedPreset && { feedPreset }),
    ...(feedQuery && { feedQuery }),
  };
}
