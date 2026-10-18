import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import './qSlices';
import './tSlices';

const mocks = vi.hoisted(() => ({
  getMessages: vi.fn(),
  toast: { success: vi.fn(), error: vi.fn() },
}));

vi.mock('../api', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../api')>();
  return { ...actual, api: { ...actual.api, getMessages: mocks.getMessages } };
});
vi.mock('../components/ui/sonner', () => ({ toast: mocks.toast }));

import { FeedView } from '../components/feed/FeedView';
import i18n from '../i18n';
import { resetFeedStore } from '../stores/feedStore';
import type { Channel, Contact, Message } from '../types';

const CHANNEL_KEY = '8B3387E9C5CDEA6AC9E5EDBAA115CD72';
const ALICE_KEY = 'ab'.repeat(32);
const channels = [
  { key: CHANNEL_KEY, name: 'Public', is_hashtag: false, on_radio: false, favorite: false },
] as unknown as Channel[];
const contacts = [{ public_key: ALICE_KEY, name: 'Alice', last_advert: 1 }] as unknown as Contact[];

function msg(id: number, extra: Partial<Message> = {}): Message {
  return {
    id,
    type: 'CHAN',
    conversation_key: CHANNEL_KEY,
    text: `Alice: hello ${id}`,
    sender_timestamp: 1700000000 + id,
    received_at: 1700000000 + id * 10,
    paths: null,
    txt_type: 0,
    signature: null,
    sender_key: null,
    outgoing: false,
    acked: 0,
    sender_name: null,
    ...extra,
  };
}

function renderFeed() {
  const handlers = {
    onNavigateToMessage: vi.fn(),
    onSelectConversation: vi.fn(),
    onOpenContactInfo: vi.fn(),
  };
  render(<FeedView contacts={contacts} channels={channels} {...handlers} />);
  return handlers;
}

async function firstItem() {
  const list = await screen.findByRole('list', { name: i18n.t('feed.listLabel') });
  return within(list).getAllByRole('listitem')[0];
}

function setClipboard(value: unknown) {
  Object.defineProperty(navigator, 'clipboard', { configurable: true, value });
}

beforeEach(() => {
  mocks.getMessages.mockReset();
  mocks.toast.success.mockReset();
  mocks.toast.error.mockReset();
  resetFeedStore();
  localStorage.clear();
  setClipboard({ writeText: vi.fn().mockResolvedValue(undefined) });
});

afterEach(() => {
  cleanup();
  setClipboard(undefined);
});

describe('FeedView row actions', () => {
  it('a click on the row navigates with the exact payload', async () => {
    mocks.getMessages.mockResolvedValue([msg(7)]);
    const h = renderFeed();
    fireEvent.click(await firstItem());
    expect(h.onNavigateToMessage).toHaveBeenCalledWith({
      id: 7,
      type: 'CHAN',
      conversation_key: CHANNEL_KEY,
      conversation_name: 'Public',
    });
  });

  it('Enter on the focused row navigates, Enter inside a child does not', async () => {
    mocks.getMessages.mockResolvedValue([msg(7, { type: 'PRIV', conversation_key: ALICE_KEY })]);
    const h = renderFeed();
    const item = await firstItem();
    fireEvent.keyDown(within(item).getByTestId('feed-row-menu-button'), { key: 'Enter' });
    expect(h.onNavigateToMessage).not.toHaveBeenCalled();
    fireEvent.keyDown(item, { key: 'Enter' });
    expect(h.onNavigateToMessage).toHaveBeenCalledWith(
      expect.objectContaining({ id: 7, type: 'PRIV', conversation_name: 'Alice' })
    );
  });

  it('the conversation name opens the conversation at the bottom, not at the message', async () => {
    mocks.getMessages.mockResolvedValue([msg(7)]);
    const h = renderFeed();
    fireEvent.click(await screen.findByRole('button', { name: /Public/ }));
    expect(h.onSelectConversation).toHaveBeenCalledWith({
      type: 'channel',
      id: CHANNEL_KEY,
      name: 'Public',
    });
    expect(h.onNavigateToMessage).not.toHaveBeenCalled();
  });

  it('the sender opens the contact sheet when the full key is known, else is plain text', async () => {
    mocks.getMessages.mockResolvedValue([
      msg(1, { sender_key: ALICE_KEY }),
      msg(2, { sender_key: 'abcd' }),
    ]);
    const h = renderFeed();
    const list = await screen.findByRole('list');
    const senders = within(list).getAllByTestId('feed-row-sender');
    expect(senders[0].tagName).toBe('BUTTON');
    expect(senders[1].tagName).toBe('SPAN');
    fireEvent.click(senders[0]);
    expect(h.onOpenContactInfo).toHaveBeenCalledWith(ALICE_KEY, true);
    expect(h.onNavigateToMessage).not.toHaveBeenCalled();
  });

  it('the menu offers open, copy and contact details; copy toasts', async () => {
    mocks.getMessages.mockResolvedValue([msg(7, { sender_key: ALICE_KEY })]);
    const h = renderFeed();
    fireEvent.click(
      within(await firstItem()).getByRole('button', { name: i18n.t('feed.row.actions') })
    );
    const menu = screen.getByRole('menu');
    expect(
      within(menu)
        .getAllByRole('menuitem')
        .map((i) => i.textContent)
    ).toEqual([
      i18n.t('feed.row.openInConversation'),
      i18n.t('feed.row.copy'),
      i18n.t('feed.row.contactDetails'),
    ]);

    fireEvent.click(within(menu).getByText(i18n.t('feed.row.copy')));
    await waitFor(() =>
      expect(mocks.toast.success).toHaveBeenCalledWith(i18n.t('feed.row.copied'))
    );
    expect(navigator.clipboard.writeText).toHaveBeenCalledWith('hello 7');
    expect(screen.queryByRole('menu')).toBeNull();

    fireEvent.click(screen.getByTestId('feed-row-menu-button'));
    fireEvent.click(screen.getByText(i18n.t('feed.row.contactDetails')));
    expect(h.onOpenContactInfo).toHaveBeenCalledWith(ALICE_KEY, true);

    fireEvent.click(screen.getByTestId('feed-row-menu-button'));
    fireEvent.click(screen.getByText(i18n.t('feed.row.openInConversation')));
    expect(h.onNavigateToMessage).toHaveBeenCalledWith(expect.objectContaining({ id: 7 }));
  });

  it('a failed copy toasts an error', async () => {
    setClipboard({ writeText: vi.fn().mockRejectedValue(new Error('denied')) });
    mocks.getMessages.mockResolvedValue([msg(7)]);
    renderFeed();
    fireEvent.click(await screen.findByTestId('feed-row-menu-button'));
    fireEvent.click(screen.getByText(i18n.t('feed.row.copy')));
    await waitFor(() => expect(mocks.toast.error).toHaveBeenCalled());
  });

  it('hides Copy without navigator.clipboard, and Contact details without a full key', async () => {
    setClipboard(undefined);
    mocks.getMessages.mockResolvedValue([msg(7)]);
    renderFeed();
    fireEvent.click(await screen.findByTestId('feed-row-menu-button'));
    const items = within(screen.getByRole('menu')).getAllByRole('menuitem');
    expect(items.map((i) => i.textContent)).toEqual([i18n.t('feed.row.openInConversation')]);
  });

  it('the menu is keyboard operable: opens from the row, arrows move, Escape closes', async () => {
    mocks.getMessages.mockResolvedValue([msg(7)]);
    renderFeed();
    const item = await firstItem();
    fireEvent.keyDown(item, { key: 'ContextMenu' });
    const [first, second] = within(screen.getByRole('menu')).getAllByRole('menuitem');
    expect(first).toHaveFocus();
    fireEvent.keyDown(first, { key: 'ArrowDown' });
    expect(second).toHaveFocus();
    fireEvent.keyDown(second, { key: 'Escape' });
    expect(screen.queryByRole('menu')).toBeNull();
    expect(within(item).getByTestId('feed-row-menu-button')).toHaveFocus();
  });

  it('a read-only view (no callbacks) has no menu and no buttons', async () => {
    mocks.getMessages.mockResolvedValue([msg(7)]);
    render(<FeedView contacts={contacts} channels={channels} />);
    await firstItem();
    expect(screen.queryByTestId('feed-row-menu-button')).toBeNull();
  });
});
