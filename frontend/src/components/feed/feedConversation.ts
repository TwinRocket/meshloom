import type { Conversation } from '../../types';

/**
 * The `feed` destination, kept out of `types.ts` for now.
 *
 * TODO(MSHL-147): once `types.ts` is free (MSHL-104 merged), add `'feed'` to
 * `ConversationType` and `feedPreset?: 'control'` to `Conversation`, then delete this
 * module's cast and let `isFeedConversation` narrow on the union.
 */
export interface FeedConversation extends Omit<Conversation, 'type'> {
  type: 'feed';
  /** Unused until the Control preset (T12) folds `#control` into the feed. */
  feedPreset?: 'control';
}

/** Rows asked of `GET /api/messages` per page. */
export const FEED_PAGE_SIZE = 200;
/** Rows mounted at once, whatever the memory window holds (`FEED_MAX_ROWS`). */
export const FEED_DOM_MAX = 300;

export function isFeedConversation(
  conversation: { type: string } | null | undefined
): conversation is FeedConversation {
  return conversation?.type === 'feed';
}

export function makeFeedConversation(name: string, feedPreset?: 'control'): Conversation {
  const feed: FeedConversation = {
    type: 'feed',
    id: 'feed',
    name,
    ...(feedPreset && { feedPreset }),
  };
  // Until `ConversationType` lists 'feed' the shared type cannot carry it.
  return feed as unknown as Conversation;
}
