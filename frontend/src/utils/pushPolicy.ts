import type {
  ConversationMediaOverride,
  NotificationMediaChannel,
  NotificationMediaFlags,
  PushDefaults,
} from '../types';
import { notificationMediaFlag } from '../types';

export type ConversationOverrides = Record<string, boolean | ConversationMediaOverride>;

export interface ConversationEnablementInput {
  stateKey: string;
  messageType: string;
  defaults: PushDefaults;
  overrides: ConversationOverrides;
  isHashtag?: boolean;
  isPublic?: boolean;
  channel?: NotificationMediaChannel;
}

export function conversationOverrideFlags(raw: unknown): ConversationMediaOverride {
  if (typeof raw === 'boolean') {
    return { push: raw };
  }
  if (raw && typeof raw === 'object') {
    const obj = raw as Record<string, unknown>;
    const out: ConversationMediaOverride = {};
    (['push', 'email', 'webhook'] as const).forEach((key) => {
      if (key in obj && obj[key] != null) {
        out[key] = Boolean(obj[key]);
      }
    });
    return out;
  }
  return {};
}

/**
 * Mirror of ``app/push/policy.py`` conversation_is_enabled.
 *
 * A stored per-medium override wins. Otherwise PRIV follows ``new_dm`` and
 * CHAN is push-only for public/hashtag channels.
 *
 * Mute is a separate manager-level circuit breaker and is not evaluated here.
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
  const stored = stateKey in overrides ? conversationOverrideFlags(overrides[stateKey]) : {};
  if (channel in stored) {
    return Boolean(stored[channel as keyof NotificationMediaFlags]);
  }
  if (messageType === 'PRIV') {
    return notificationMediaFlag(defaults.new_dm, channel);
  }
  if (channel !== 'push') {
    return false;
  }
  return Boolean(isPublic || isHashtag);
}
