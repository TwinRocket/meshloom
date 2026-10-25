import { act, cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import './qSlices';
import './wSlices';

const mocks = vi.hoisted(() => ({ getMessages: vi.fn(), getPacketsHistory: vi.fn() }));

vi.mock('../api', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../api')>();
  return {
    ...actual,
    api: {
      ...actual.api,
      getMessages: mocks.getMessages,
      getPacketsHistory: mocks.getPacketsHistory,
    },
  };
});

import { FeedView } from '../components/feed/FeedView';
import { recordFeedMessage, resetFeedStore } from '../stores/feedStore';
import i18n from '../i18n';
import { FEED_FILTERS_STORAGE_KEY } from '../utils/feedFilters';
import type { Channel, Contact, Message } from '../types';

const PUBLIC_KEY = '8B3387E9C5CDEA6AC9E5EDBAA115CD72';
const OTHER_KEY = '11111111111111111111111111111111';
const PEER_KEY = 'cc'.repeat(32);

const channels = [
  { key: PUBLIC_KEY, name: 'Public', muted: false },
  { key: OTHER_KEY, name: 'Hikers', muted: false },
] as unknown as Channel[];
const contacts = [
  { public_key: PEER_KEY, name: 'Walker', type: 1, last_advert: 1 },
] as unknown as Contact[];

function msg(id: number, extra: Partial<Message> = {}): Message {
  return {
    id,
    type: 'CHAN',
    conversation_key: PUBLIC_KEY,
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

const page = [
  msg(1),
  msg(2),
  msg(3, { conversation_key: OTHER_KEY }),
  msg(4, { type: 'PRIV', conversation_key: PEER_KEY, text: 'hi there' }),
];

function renderFeed() {
  return render(<FeedView contacts={contacts} channels={channels} onBackToTools={vi.fn()} />);
}

const column = async () => within(await screen.findByRole('complementary'));
const itemFor = (scope: ReturnType<typeof within>, label: string) =>
  scope.getByText(label).closest('label') as HTMLLabelElement;
const messageCount = (n: number) =>
  i18n.t('feed.facets.messageCount', { count: n, formatted: String(n) });

beforeEach(() => {
  mocks.getMessages.mockReset();
  mocks.getMessages.mockResolvedValue([...page].reverse());
  mocks.getPacketsHistory.mockReset();
  mocks.getPacketsHistory.mockResolvedValue({ items: [], total: 0, truncated: false, scanned: 0 });
  resetFeedStore();
  localStorage.clear();
});

afterEach(cleanup);

describe('feed conversation facets', () => {
  it('lists channels and contacts of the loaded rows with their counts', async () => {
    renderFeed();
    const facets = await column();
    expect(await facets.findByText('Public')).toBeInTheDocument();
    expect(itemFor(facets, 'Public')).toHaveTextContent(messageCount(2));
    expect(itemFor(facets, 'Hikers')).toHaveTextContent(messageCount(1));
    expect(itemFor(facets, 'Walker')).toHaveTextContent(messageCount(1));
    expect(facets.getByText(i18n.t('feed.facets.scope'))).toBeInTheDocument();
    expect(facets.getByText(i18n.t('feed.facets.channels'))).toBeInTheDocument();
    expect(facets.getByText(i18n.t('feed.facets.contacts'))).toBeInTheDocument();
  });

  it('counts a row that arrives, and narrows the timeline to the picked conversations', async () => {
    renderFeed();
    const facets = await column();
    await facets.findByText('Public');

    act(() => {
      recordFeedMessage(msg(5));
    });
    await waitFor(() => expect(itemFor(facets, 'Public')).toHaveTextContent(messageCount(3)));

    fireEvent.click(within(itemFor(facets, 'Hikers')).getByRole('checkbox'));
    await waitFor(() => expect(screen.queryByText(/hello 1\b/)).not.toBeInTheDocument());
    expect(screen.getByText(/hello 3\b/)).toBeInTheDocument();
    // The other conversations keep their counts while one is picked.
    expect(itemFor(facets, 'Public')).toHaveTextContent(messageCount(3));

    fireEvent.click(within(itemFor(facets, 'Walker')).getByRole('checkbox'));
    expect(await screen.findByText('hi there')).toBeInTheDocument();
    expect(screen.getByText(/hello 3\b/)).toBeInTheDocument();
    expect(screen.queryByText(/hello 2\b/)).not.toBeInTheDocument();

    fireEvent.click(facets.getByTestId('feed-facets-reset'));
    expect(await screen.findByText(/hello 2\b/)).toBeInTheDocument();
    expect(screen.getAllByRole('checkbox').every((box) => !(box as HTMLInputElement).checked)).toBe(
      true
    );
  });

  it('remembers the pick in this browser and never writes it to the address', async () => {
    window.location.hash = '#feed';
    renderFeed();
    const facets = await column();
    await facets.findByText('Hikers');
    fireEvent.click(within(itemFor(facets, 'Hikers')).getByRole('checkbox'));
    await waitFor(() => {
      const stored = JSON.parse(localStorage.getItem(FEED_FILTERS_STORAGE_KEY) ?? '{}');
      expect(stored.conversations).toEqual([`chan:${OTHER_KEY.toLowerCase()}`]);
    });
    expect(window.location.hash).not.toContain(OTHER_KEY.toLowerCase());
    expect(window.location.hash).not.toContain('conversations');
  });

  it('keeps a picked conversation that left the window listed, so it can be unpicked', async () => {
    localStorage.setItem(
      FEED_FILTERS_STORAGE_KEY,
      JSON.stringify({ conversations: [`chan:${'22'.repeat(16)}`] })
    );
    renderFeed();
    const facets = await column();
    const boxes = await facets.findAllByRole('checkbox');
    // The window's three conversations plus the picked one that is not in it (count 0).
    expect(boxes).toHaveLength(4);
    expect((boxes as HTMLInputElement[]).filter((box) => box.checked)).toHaveLength(1);
    expect(facets.getByTestId('feed-facets-reset')).toBeEnabled();
  });

  it('opens the phone sheet from "Filters (n)" with the active count', async () => {
    renderFeed();
    const facets = await column();
    await facets.findByText('Public');
    const open = screen.getByTestId('feed-facets-open');
    expect(open).toHaveTextContent(i18n.t('feed.facets.filters'));

    fireEvent.click(within(itemFor(facets, 'Public')).getByRole('checkbox'));
    fireEvent.click(within(itemFor(facets, 'Walker')).getByRole('checkbox'));
    await waitFor(() =>
      expect(open).toHaveTextContent(
        i18n.t('feed.facets.filtersActive', { count: 2, formatted: '2' })
      )
    );

    fireEvent.click(open);
    const dialog = await screen.findByRole('dialog');
    const boxes = within(dialog).getAllByRole('checkbox') as HTMLInputElement[];
    expect(boxes).toHaveLength(3);
    expect(boxes.filter((box) => box.checked)).toHaveLength(2);
    // Keyboard: native checkboxes in tab order, reset reachable.
    expect(boxes.every((box) => box.tabIndex >= 0)).toBe(true);
    fireEvent.click(within(dialog).getByTestId('feed-facets-reset'));
    await waitFor(() => expect(open).toHaveTextContent(i18n.t('feed.facets.filters')));
  });
});
