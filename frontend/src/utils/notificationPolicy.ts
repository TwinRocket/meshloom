import type { NotificationRules } from '../notifications/types';

/** The slice of `NotificationRules` the policy reads (a stored document may lack any part). */
export type PolicyRules = Partial<
  Pick<NotificationRules, 'paused_until' | 'conversations'> & {
    events: Record<string, { channels?: string[]; always_deliver?: boolean }>;
  }
>;

export interface ConversationChannelInput {
  stateKey: string;
  messageType: string;
  rules: PolicyRules;
  channelId: string;
  isHashtag?: boolean;
  isPublic?: boolean;
  /** The conversation's effective mute. */
  muted?: boolean;
  blocked?: boolean;
  /** Epoch seconds, for the global pause; `null` leaves the pause unevaluated. */
  now?: number | null;
  /** Asks about the `message.mention` notification of a channel message. */
  mention?: boolean;
  /** The channel is a third party (a private-key channel's mention never reaches it unprompted). */
  cloud?: boolean;
}

export const MENTION_EVENT = 'message.mention';

function eventName(messageType: string, mention: boolean): string {
  if (mention) return MENTION_EVENT;
  return messageType === 'PRIV' ? 'message.dm' : 'message.channel';
}

/**
 * Mirror of ``app/notifications/rules.py`` ``conversation_channel_enabled``, driven by the
 * same cases (`tests/fixtures/notification_policy_cases.json`, `notificationPolicy.test.ts`).
 * Change one side, change the other.
 *
 * A stored exception for (conversation, channel) wins; else a DM follows the channels of
 * ``message.dm``, a Public or ``#`` channel those of ``message.channel``, a channel with its
 * own key none. Mute, a blocked sender and the global pause cut everything but an
 * ``always_deliver`` event. A mention only reaches the channels of ``message.mention`` (and
 * stored ``true`` exceptions); on a private-key channel only a non-cloud one.
 */
export function conversationChannelEnabled({
  stateKey,
  messageType,
  rules,
  channelId,
  isHashtag = false,
  isPublic = false,
  muted = false,
  blocked = false,
  now = null,
  mention = false,
  cloud = false,
}: ConversationChannelInput): boolean {
  const name = eventName(messageType, mention);
  const eventRule = rules.events?.[name];
  const until = rules.paused_until;
  const paused = now !== null && typeof until === 'number' && until > now;
  if (!eventRule?.always_deliver && (muted || blocked || paused)) return false;
  const listed = (eventRule?.channels ?? []).includes(channelId);
  const stored = rules.conversations?.[stateKey]?.[channelId];
  if (mention) {
    if (messageType === 'PRIV') return false;
    if (typeof stored === 'boolean') return stored;
    return listed && (isPublic || isHashtag || !cloud);
  }
  if (typeof stored === 'boolean') return stored;
  if (messageType !== 'PRIV' && !(isPublic || isHashtag)) return false;
  return listed;
}
