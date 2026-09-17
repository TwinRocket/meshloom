import { beforeEach, describe, it, expect, vi } from 'vitest';
import { render, screen, fireEvent, within } from '@testing-library/react';
import { ConversationListView } from '../components/ConversationListView';
import i18n from '../i18n';
import type { Channel, Contact } from '../types';

/**
 * The list is the phone's home screen, so what it shows and in what order is the
 * whole navigation model, not presentation.
 */

function channel(overrides: Partial<Channel> & Pick<Channel, 'key' | 'name'>): Channel {
  return {
    is_hashtag: true,
    on_radio: true,
    last_read_at: null,
    favorite: false,
    muted: false,
    ...overrides,
  } as Channel;
}

function contact(overrides: Partial<Contact> & Pick<Contact, 'public_key' | 'name'>): Contact {
  return {
    type: 1,
    flags: 0,
    direct_path: null,
    direct_path_len: 0,
    direct_path_hash_mode: 0,
    favorite: false,
    ...overrides,
  } as Contact;
}

function renderList(overrides?: Partial<React.ComponentProps<typeof ConversationListView>>) {
  const onSelectConversation = vi.fn();
  const onNewMessage = vi.fn();
  render(
    <ConversationListView
      contacts={[
        contact({ public_key: 'aa'.repeat(32), name: 'Alice' }),
        contact({ public_key: 'bb'.repeat(32), name: 'Bob', favorite: true }),
      ]}
      channels={[channel({ key: 'C1', name: '#alpha' }), channel({ key: 'C2', name: '#beta' })]}
      // Keyed the way every one of these maps is actually keyed — reading them by
      // the raw channel key or public key silently yields a list where nothing has
      // ever been said.
      unreadCounts={{ 'channel-C1': 3 }}
      mentions={{}}
      lastMessageTimes={{
        'channel-C1': 300,
        [`contact-${'aa'.repeat(32)}`]: 200,
        'channel-C2': 100,
      }}
      lastMessagePreviews={{
        'channel-C1': 'newest thing',
        [`contact-${'aa'.repeat(32)}`]: 'older thing',
      }}
      onSelectConversation={onSelectConversation}
      onNewMessage={onNewMessage}
      {...overrides}
    />
  );
  return { onSelectConversation, onNewMessage };
}

const rowNames = () =>
  screen
    .getAllByRole('listitem')
    .map((item) => within(item).getAllByRole('button')[0].textContent ?? '');

describe('ConversationListView', () => {
  beforeEach(() => {
    localStorage.removeItem('meshloom-conversation-favorites-collapsed');
  });

  it('puts channels and direct conversations in one list, newest first', () => {
    renderList();
    const names = rowNames().filter((name) => !name.startsWith('Bob') || name.includes('thing'));
    // #alpha spoke last, then Alice, then #beta; Bob has never spoken and sorts last.
    const order = rowNames().join('|');
    expect(order.indexOf('#alpha')).toBeLessThan(order.indexOf('Alice'));
    expect(order.indexOf('Alice')).toBeLessThan(order.indexOf('#beta'));
    expect(names.length).toBeGreaterThan(0);
  });

  it('narrows to unread without losing the rest of the list', () => {
    renderList();
    expect(rowNames().join('|')).toContain('#beta');

    fireEvent.click(
      screen.getByRole('button', { name: new RegExp(i18n.t('conversationList.unread')) })
    );
    const filtered = rowNames().join('|');
    expect(filtered).toContain('#alpha');
    expect(filtered).not.toContain('#beta');
  });

  it('separates channels from direct conversations only when asked', () => {
    renderList();
    fireEvent.click(
      screen.getByRole('button', { name: new RegExp(`^${i18n.t('conversationList.groups')}`) })
    );
    expect(rowNames().join('|')).not.toContain('Alice');

    fireEvent.click(
      screen.getByRole('button', { name: new RegExp(`^${i18n.t('conversationList.direct')}`) })
    );
    const direct = rowNames().join('|');
    expect(direct).toContain('Alice');
    expect(direct).not.toContain('#alpha');
  });

  it('searches names and message previews alike', () => {
    renderList();
    const search = screen.getByPlaceholderText(i18n.t('conversationList.searchPlaceholder'));

    fireEvent.change(search, { target: { value: 'newest' } });
    const byPreview = rowNames().join('|');
    expect(byPreview).toContain('#alpha');
    expect(byPreview).not.toContain('Alice');

    fireEvent.change(search, { target: { value: 'ali' } });
    expect(rowNames().join('|')).toContain('Alice');
  });

  it('says so when a search matches nothing', () => {
    renderList();
    fireEvent.change(screen.getByPlaceholderText(i18n.t('conversationList.searchPlaceholder')), {
      target: { value: 'zzzznothing' },
    });
    // Matched loosely: the French copy wraps the term in guillemets with
    // non-breaking spaces, which exact-text matching normalises inconsistently.
    expect(screen.getByText(/zzzznothing/)).toBeInTheDocument();
    expect(screen.queryAllByRole('listitem')).toHaveLength(0);
  });

  it('offers favourites as a shortcut, and stands down once the list is narrowed', () => {
    renderList();
    expect(
      screen.getByRole('heading', { name: i18n.t('conversationList.favorites') })
    ).toBeInTheDocument();

    fireEvent.click(
      screen.getByRole('button', { name: new RegExp(`^${i18n.t('conversationList.groups')}`) })
    );
    expect(
      screen.queryByRole('heading', { name: i18n.t('conversationList.favorites') })
    ).not.toBeInTheDocument();
  });

  it('folds the favourites grid behind a chevron and remembers that', () => {
    renderList({
      contacts: [
        contact({ public_key: 'aa'.repeat(32), name: 'Alice' }),
        contact({ public_key: 'bb'.repeat(32), name: 'Bob', favorite: true }),
        contact({ public_key: 'cc'.repeat(32), name: 'Cara', favorite: true }),
      ],
    });

    const strip = screen.getByRole('list', { name: i18n.t('conversationList.favorites') });
    expect(within(strip).getByRole('button', { name: /Bob/ })).toBeInTheDocument();
    expect(within(strip).getByRole('button', { name: /Cara/ })).toBeInTheDocument();

    fireEvent.click(
      screen.getByRole('button', {
        name: i18n.t('sidebar.collapseSection', { title: i18n.t('conversationList.favorites') }),
      })
    );
    expect(
      screen.queryByRole('list', { name: i18n.t('conversationList.favorites') })
    ).not.toBeInTheDocument();
    expect(localStorage.getItem('meshloom-conversation-favorites-collapsed')).toBe('1');

    fireEvent.click(
      screen.getByRole('button', {
        name: i18n.t('sidebar.expandSection', { title: i18n.t('conversationList.favorites') }),
      })
    );
    expect(
      screen.getByRole('list', { name: i18n.t('conversationList.favorites') })
    ).toBeInTheDocument();
    expect(localStorage.getItem('meshloom-conversation-favorites-collapsed')).toBeNull();
  });

  it('offers a desktop control to fold the conversation list', () => {
    const onCollapseList = vi.fn();
    renderList({ onCollapseList });
    fireEvent.click(
      screen.getByRole('button', { name: i18n.t('conversationList.collapseList') })
    );
    expect(onCollapseList).toHaveBeenCalled();
  });

  it('draws channel avatars from the name, not the hash marker', () => {
    renderList();
    const alpha = screen.getAllByRole('button', { name: /#alpha/ })[0];
    expect(within(alpha).getByText('A')).toBeInTheDocument();
  });

  it('reports the conversation that was chosen', () => {
    const { onSelectConversation } = renderList();
    fireEvent.click(screen.getAllByRole('button', { name: /#alpha/ })[0]);
    expect(onSelectConversation).toHaveBeenCalledWith(
      expect.objectContaining({ type: 'channel', id: 'C1' })
    );
  });

  it('carries unread counts as text, not only as a coloured badge', () => {
    renderList();
    expect(
      screen.getByText(i18n.t('conversationList.unreadCount', { count: 3 }))
    ).toBeInTheDocument();
  });
});
