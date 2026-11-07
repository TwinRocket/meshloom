import type { Channel, Conversation } from '../types';
import type { ConversationNotifyTarget } from '../components/ConversationNotifyPanel';
import { getStateKey } from './conversationState';
import { isPublicChannelKey } from './publicChannel';

/** What the notification policy needs to know about a conversation (the bell's input). */
export function notifyTargetOf(
  conversation: Conversation,
  channel?: Pick<Channel, 'is_hashtag'> | null
): ConversationNotifyTarget {
  if (conversation.type === 'channel') {
    return {
      stateKey: getStateKey('channel', conversation.id),
      messageType: 'CHAN',
      isHashtag: channel?.is_hashtag,
      isPublic: isPublicChannelKey(conversation.id),
    };
  }
  return { stateKey: getStateKey('contact', conversation.id), messageType: 'PRIV' };
}
