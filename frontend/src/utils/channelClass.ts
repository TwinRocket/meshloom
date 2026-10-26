import type { ShareConversation } from '../api';
import type { Channel } from '../types';
import { isPublicChannelKey } from './publicChannel';

/**
 * What a conversation is for file sharing (MSHL-256).
 *
 * - `public`: the Public channel, whose key everyone knows.
 * - `hashtag_derived`: a channel whose key is computed from its `#name`
 *   (`Channel.key_derived`, set by the backend; it stays true for a hashtag
 *   channel renamed locally without its `#`), so anyone who knows the name reads it.
 * - `private`: a channel whose key was given, not derived.
 * - `dm`: a direct message with a contact (not a room server).
 * - `room`: a room server, which reads every post; the backend refuses encrypted
 *   uploads to it (`share_mode_not_allowed`).
 */
export type ConversationClass = 'public' | 'hashtag_derived' | 'private' | 'dm' | 'room';

/** The conversation's class, whether encrypted files can be offered, and how the upload names it. */
export interface ShareTarget {
  class: ConversationClass;
  /** True for `private` and `dm`: the only classes where encryption protects anything. */
  encryptable: boolean;
  /**
   * What the encrypted upload sends (`X-Share-Conversation-Type` / `-Id`): a channel's
   * opaque `share_conversation_id` (never its key) or the contact's public key. Null
   * when the class is not encryptable, or when an older backend sent no id.
   */
  conversation: ShareConversation | null;
}

/**
 * The class of a channel. An unknown `key_derived` (an older backend) is treated as
 * derived: encryption is never offered where it cannot be shown to protect anything.
 */
export function channelClass(
  channel: Pick<Channel, 'key' | 'key_derived'>
): 'public' | 'hashtag_derived' | 'private' {
  if (isPublicChannelKey(channel.key)) return 'public';
  return channel.key_derived === false ? 'private' : 'hashtag_derived';
}

/** The share target of a channel. */
export function channelShareTarget(
  channel: Pick<Channel, 'key' | 'key_derived' | 'share_conversation_id'>
): ShareTarget {
  const cls = channelClass(channel);
  if (cls !== 'private') return { class: cls, encryptable: false, conversation: null };
  const id = channel.share_conversation_id;
  return {
    class: cls,
    encryptable: true,
    conversation: id ? { type: 'channel', id } : null,
  };
}

/** The share target of a contact conversation (`publicKey`: the full contact key). */
export function contactShareTarget(publicKey: string, isRoom: boolean): ShareTarget {
  if (isRoom) return { class: 'room', encryptable: false, conversation: null };
  return { class: 'dm', encryptable: true, conversation: { type: 'contact', id: publicKey } };
}
