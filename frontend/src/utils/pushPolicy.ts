import type { NotificationMediaChannel, PushDefaults } from '../types';
import { notificationMediaFlag } from '../types';

export interface ConversationEnablementInput {
  stateKey: string;
  messageType: string;
  defaults: PushDefaults;
  overrides: Record<string, boolean>;
  isHashtag?: boolean;
  isPublic?: boolean;
  channel?: NotificationMediaChannel;
}

/**
 * Mirror of ``app/push/policy.py`` conversation_is_enabled.
 *
 * Precedence: explicit override > PRIV (DM and rooms) via ``new_dm`` >
 * Public or hashtag ON > private channel OFF.
 *
 * Mute is a separate manager-level circuit breaker and is not evaluated here.
 * ``channel_found`` and ``telemetry_alert`` are global defaults only — those
 * events have no conversation_key, so overrides never apply to them.
 * Conversation overrides apply to every medium. Channel messages have no
 * matrix row, so email/webhook stay off unless an override forces them on.
 */
export function conversationIsEnabled({
  stateKey,
  messageType,
  defaults,
  overrides,
  isHashtag = false,
  isPublic = false,
  channel = 'push',
}: ConversationEnablementInput): boolean {
  if (stateKey in overrides) {
    return Boolean(overrides[stateKey]);
  }
  if (messageType === 'PRIV') {
    return notificationMediaFlag(defaults.new_dm, channel);
  }
  if (channel !== 'push') {
    return false;
  }
  return Boolean(isPublic || isHashtag);
}
