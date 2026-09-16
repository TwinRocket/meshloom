import type { PushDefaults } from '../types';

export interface ConversationEnablementInput {
  stateKey: string;
  messageType: string;
  defaults: PushDefaults;
  overrides: Record<string, boolean>;
  isHashtag?: boolean;
  isPublic?: boolean;
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
 */
export function conversationIsEnabled({
  stateKey,
  messageType,
  defaults,
  overrides,
  isHashtag = false,
  isPublic = false,
}: ConversationEnablementInput): boolean {
  if (stateKey in overrides) {
    return Boolean(overrides[stateKey]);
  }
  if (messageType === 'PRIV') {
    return Boolean(defaults.new_dm);
  }
  return Boolean(isPublic || isHashtag);
}
