import type { Message } from '../types';

/** The message fields the block lists are matched against. */
export type BlockableMessage = Pick<
  Message,
  'type' | 'conversation_key' | 'sender_key' | 'sender_name' | 'outgoing'
>;

/**
 * True when the message comes from a blocked sender (`blocked_keys`, `blocked_names`).
 * Outgoing messages are never blocked. A DM matches its conversation key, a channel
 * message its sender key; both match the sender name against `blockedNames`.
 */
export function isMessageBlocked(
  msg: BlockableMessage,
  blockedKeys: readonly string[],
  blockedNames: readonly string[]
): boolean {
  if (msg.outgoing) {
    return false;
  }

  if (blockedKeys.length > 0) {
    if (msg.type === 'PRIV' && blockedKeys.includes(msg.conversation_key.toLowerCase())) {
      return true;
    }
    if (
      msg.type === 'CHAN' &&
      msg.sender_key &&
      blockedKeys.includes(msg.sender_key.toLowerCase())
    ) {
      return true;
    }
  }

  return blockedNames.length > 0 && !!msg.sender_name && blockedNames.includes(msg.sender_name);
}
