import { describe, it, expect } from 'vitest';
import { countUnreadConversations } from '../utils/unreadConversations';
import { getStateKey } from '../utils/conversationState';
import type { Channel, Contact } from '../types';

/**
 * The badge and the unread filter disagreed: one said a conversation was waiting,
 * the other found none to show. They were answering different questions — how many
 * counters are non-zero, and how many conversations can be opened.
 */

const channel = (key: string) => ({ key, name: `#${key}` }) as Channel;
const contact = (pk: string) => ({ public_key: pk, name: pk }) as Contact;

describe('countUnreadConversations', () => {
  it('counts a conversation once, whatever its number of unread messages', () => {
    const counts = { [getStateKey('channel', 'A')]: 7, [getStateKey('contact', 'bb')]: 1 };
    expect(countUnreadConversations([channel('A')], [contact('bb')], counts)).toBe(2);
  });

  it('ignores a counter whose conversation is gone', () => {
    // A deleted channel, or a contact this client has not loaded: the counter
    // outlives it, and counting it advertises something nobody can open.
    const counts = { [getStateKey('channel', 'DISPARU')]: 3 };
    expect(countUnreadConversations([channel('A')], [], counts)).toBe(0);
  });

  it('ignores a zero counter', () => {
    const counts = { [getStateKey('channel', 'A')]: 0 };
    expect(countUnreadConversations([channel('A')], [], counts)).toBe(0);
  });

  it('is zero when nothing has been read or written yet', () => {
    expect(countUnreadConversations([], [], {})).toBe(0);
    expect(countUnreadConversations([channel('A')], [contact('bb')], {})).toBe(0);
  });

  it('reads the state key, not the raw key', () => {
    // The raw channel key is not how the map is indexed; reading it that way
    // silently counts nothing.
    expect(countUnreadConversations([channel('A')], [], { A: 5 })).toBe(0);
    expect(countUnreadConversations([channel('A')], [], { 'channel-A': 5 })).toBe(1);
  });
});
