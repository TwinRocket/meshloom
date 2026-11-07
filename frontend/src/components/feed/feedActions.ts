import type { Message } from '../../types';

/** What the feed asks of the app when a row is acted on; all optional so the view renders alone. */
export interface FeedRowActions {
  /** Row click / Enter / "Open in conversation": the conversation, scrolled to the message. */
  onOpenMessage: (row: Message, conversationName: string) => void;
  /** Channel or contact name: the conversation, at the bottom. */
  onOpenConversation: (row: Message, conversationName: string) => void;
  /** Contact sheet. `fromChannel` is true for a channel message. */
  onOpenContact: (publicKey: string, fromChannel: boolean) => void;
}

const FULL_KEY_LENGTH = 64;

/**
 * The full public key a row's contact sheet can open: the channel sender's `sender_key`,
 * or the other party of a direct message. A prefix is not a contact, so it gives null
 * and the sender stays plain text.
 */
export function feedContactKey(row: Message): string | null {
  const key = row.type === 'CHAN' ? row.sender_key : row.conversation_key;
  return key && key.length === FULL_KEY_LENGTH ? key : null;
}

/** `navigator.clipboard` is undefined outside a secure context (HTTP on the LAN). */
export function canCopyToClipboard(): boolean {
  return typeof navigator !== 'undefined' && typeof navigator.clipboard?.writeText === 'function';
}
