import type { Channel, Contact } from '../types';
import { getStateKey } from './conversationState';

/**
 * How many conversations hold unread messages.
 *
 * Counted from the conversations themselves rather than from the counter map,
 * because those are different questions: the map holds a counter per state key,
 * and a key can outlive the conversation it belonged to — a channel that was
 * deleted, a contact the client has not loaded. Counting entries then reports
 * something the reader cannot open, and the badge said one while the unread
 * filter said there was nothing.
 *
 * One function so the bar, the rail and the filter chip cannot drift apart again:
 * they are all asking the same thing.
 */
export function countUnreadConversations(
  channels: Channel[],
  contacts: Contact[],
  unreadCounts: Record<string, number>
): number {
  let total = 0;
  for (const channel of channels) {
    if ((unreadCounts[getStateKey('channel', channel.key)] ?? 0) > 0) total += 1;
  }
  for (const contact of contacts) {
    if ((unreadCounts[getStateKey('contact', contact.public_key)] ?? 0) > 0) total += 1;
  }
  return total;
}
