import type { Channel, Contact, Conversation, Message } from '../types';
import type { ConversationTimes } from '../utils/conversationState';

/**
 * What the navigation surfaces need to draw a conversation, whichever surface is
 * drawing it — the phone's list screen, the desktop column, the tools screen.
 *
 * It lived on the sidebar's props while the sidebar was the only navigation there
 * was. It outlived it, so it is named on its own rather than derived from whatever
 * component happens to consume it this month.
 *
 * Every per-conversation map here is keyed by the conversation's state key
 * (`channel-<key>`, `contact-<pubkey>`), never by the raw channel key or public
 * key. Reading them raw does not error; it silently yields nothing.
 */
export interface NavigationData {
  contacts: Contact[];
  channels: Channel[];
  activeConversation: Conversation | null;
  onSelectConversation: (conversation: Conversation) => void;
  onNewMessage: (event?: React.MouseEvent<HTMLButtonElement>) => void;
  lastMessageTimes: ConversationTimes;
  lastMessagePreviews?: Record<string, string>;
  lastMessageOutgoing?: Record<string, boolean>;
  radioName?: string | null;
  activeMessages?: Message[];
  unreadCounts: Record<string, number>;
  /** Which conversations hold unread messages that mention this node. */
  mentions: Record<string, boolean>;
  showCracker: boolean;
  crackerRunning: boolean;
  crackerQueueCount?: number;
  onToggleCracker: () => void;
  onMarkAllRead: () => void;
  blockedKeys?: string[];
  blockedNames?: string[];
}
