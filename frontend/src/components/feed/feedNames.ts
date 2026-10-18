import type { Channel, Contact, Message } from '../../types';
import { isPendingChannel } from '../../utils/channelMembership';
import { parseSenderFromText } from '../../utils/messageParser';
import { getContactDisplayName } from '../../utils/pubkey';

export interface FeedRowLabels {
  /** Channel name, or contact display name, as shown in the row header. */
  conversation: string;
  /** Channel messages only: who wrote it. */
  sender: string | null;
  /** Abbreviated sender key, shown on hover to tell homonyms apart. */
  senderKey: string | null;
  /** The text without the `Sender: ` prefix of a channel message. */
  content: string;
  /** The channel is not adopted yet: its media must not load. */
  pendingChannel: boolean;
}

export interface FeedNameResolver {
  (row: Message): FeedRowLabels;
}

const CHANNEL_KEY_PREFIX = 8;
const CONTACT_KEY_PREFIX = 12;

/**
 * Names for feed rows. REST rows carry no `channel_name`, so the conversation is resolved
 * by key from the channels and contacts the client holds; if the key is unknown the name
 * of the WebSocket frame (`channel_name`, `sender_name`) is used, then the abbreviated
 * key (a deleted channel, a contact never advertised).
 */
export function createFeedNameResolver(
  channels: readonly Channel[],
  contacts: readonly Contact[]
): FeedNameResolver {
  const channelByKey = new Map(channels.map((c) => [c.key.toUpperCase(), c]));
  const contactNames = new Map(
    contacts.map((c) => [
      c.public_key.toLowerCase(),
      getContactDisplayName(c.name, c.public_key, c.last_advert),
    ])
  );

  return (row) => {
    if (row.type === 'CHAN') {
      const channel = channelByKey.get(row.conversation_key.toUpperCase());
      const parsed = parseSenderFromText(row.text);
      const sender = row.sender_name || parsed.sender;
      return {
        conversation:
          channel?.name || row.channel_name || row.conversation_key.slice(0, CHANNEL_KEY_PREFIX),
        sender,
        senderKey: row.sender_key ? row.sender_key.slice(0, CONTACT_KEY_PREFIX) : null,
        content: parsed.sender ? parsed.content : row.text,
        pendingChannel: isPendingChannel(channel),
      };
    }
    return {
      conversation:
        contactNames.get(row.conversation_key.toLowerCase()) ||
        (!row.outgoing && row.sender_name) ||
        row.conversation_key.slice(0, CONTACT_KEY_PREFIX),
      sender: null,
      senderKey: null,
      content: row.text,
      pendingChannel: false,
    };
  };
}
