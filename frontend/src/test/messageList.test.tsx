import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { useState } from 'react';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import { MessageList } from '../components/MessageList';
import { resetObserverReachCountCache } from '../hooks/useVisibleObserverReach';
import i18n from '../i18n';
import sliceEn from '../i18n/locales/slices/b.en.json';
import sliceFr from '../i18n/locales/slices/b.fr.json';

i18n.addResourceBundle('en', 'translation', sliceEn, true, true);
i18n.addResourceBundle('fr', 'translation', sliceFr, true, true);
import { PathHopWidthProvider } from '../contexts/PathHopWidthContext';
import { RichPayloadProvider } from '../contexts/RichPayloadContext';
import { CONTACT_TYPE_ROOM, type Contact, type Message } from '../types';
import { formatOpenReaction } from '../utils/meshcoreOpenPayloads';
import { formatTime } from '../utils/messageParser';

const apiMocks = vi.hoisted(() => ({
  deleteMessage: vi.fn(async (_id: number) => ({ status: 'ok' })),
  getPacket: vi.fn(),
  getPacketObserverReachCounts: vi.fn(
    async (
      _hashes: string[]
    ): Promise<{
      directory_enabled: boolean;
      counts: Record<string, number>;
    }> => ({
      directory_enabled: true,
      counts: { AABBCCDDEEFF0011: 3 },
    })
  ),
  getPacketObserverReach: vi.fn(async (_hash: string) => ({
    directory_enabled: true,
    packet_hash: 'AABBCCDDEEFF0011',
    observer_count: 3,
    observers: [{ name: 'Lyon', hops: 2, snr: -3, path: ['ab', 'cd'] }],
    max_hops: 2,
    max_distance_km: 12.4,
    origin_available: true,
    origin_lat: 45.76,
    origin_lon: 4.83,
  })),
  resolveDirectoryHops: vi.fn(async (_hops: string[]) => ({ resolved: {} })),
}));

vi.mock('react-leaflet', () => ({
  MapContainer: () => null,
  TileLayer: () => null,
  Marker: () => null,
  CircleMarker: () => null,
  Popup: () => null,
  Tooltip: () => null,
  Polyline: () => null,
  useMap: () => ({
    getContainer: () => document.createElement('div'),
    invalidateSize: () => undefined,
    setView: () => undefined,
    fitBounds: () => undefined,
  }),
}));

vi.mock('../api', () => ({
  api: {
    deleteMessage: (...args: [number]) => apiMocks.deleteMessage(...args),
    getPacket: (...args: [number]) => apiMocks.getPacket(...args),
    getPacketObserverReachCounts: (...args: [string[]]) =>
      apiMocks.getPacketObserverReachCounts(...args),
    getPacketObserverReach: (hash: string) => apiMocks.getPacketObserverReach(hash),
    resolveDirectoryHops: (hops: string[]) => apiMocks.resolveDirectoryHops(hops),
  },
  formatApiError: (err: unknown) => (err instanceof Error ? err.message : String(err)),
}));

const scrollIntoViewMock = vi.fn();
const originalGetBoundingClientRect = HTMLElement.prototype.getBoundingClientRect;

function createMessage(overrides: Partial<Message> = {}): Message {
  return {
    id: 1,
    type: 'CHAN',
    conversation_key: 'C3B889530D4F02DB5662EA13C417F530',
    text: 'Alice: hello world',
    sender_timestamp: 1700000000,
    received_at: 1700000001,
    paths: null,
    txt_type: 0,
    signature: null,
    sender_key: null,
    outgoing: false,
    acked: 0,
    sender_name: null,
    ...overrides,
  };
}

describe('MessageList channel sender rendering', () => {
  beforeEach(() => {
    apiMocks.deleteMessage.mockClear();
    apiMocks.getPacket.mockClear();
    apiMocks.getPacketObserverReachCounts.mockReset();
    apiMocks.getPacketObserverReachCounts.mockResolvedValue({
      directory_enabled: true,
      counts: { AABBCCDDEEFF0011: 3 },
    });
    apiMocks.getPacketObserverReach.mockClear();
    resetObserverReachCountCache();
    scrollIntoViewMock.mockReset();
    Object.defineProperty(HTMLElement.prototype, 'scrollIntoView', {
      configurable: true,
      value: scrollIntoViewMock,
      writable: true,
    });
    Object.defineProperty(HTMLElement.prototype, 'getBoundingClientRect', {
      configurable: true,
      value: originalGetBoundingClientRect,
      writable: true,
    });
  });

  it('renders explicit corrupt placeholder and warning avatar for unnamed corrupt channel packets', () => {
    render(
      <MessageList
        messages={[
          createMessage({
            text: "Nv\x0ek\x16ɩ'\x7fg:",
            sender_name: null,
            sender_key: null,
          }),
        ]}
        contacts={[]}
        loading={false}
      />
    );

    expect(screen.getByText(i18n.t('messageList.corruptSender'))).toBeInTheDocument();
    expect(screen.getByTestId('corrupt-avatar')).toBeInTheDocument();
  });

  it('renders a region badge for region-scoped channel messages', () => {
    render(
      <MessageList
        messages={[createMessage({ sender_name: 'Alice', region: 'nl-gr' })]}
        contacts={[]}
        loading={false}
      />
    );

    expect(screen.getByText('nl-gr')).toBeInTheDocument();
    expect(
      screen.getByTitle(i18n.t('messageList.regionalScope', { region: 'nl-gr' }))
    ).toBeInTheDocument();
  });

  it('renders time, observer, path, and ack metadata on a row under the body', () => {
    render(
      <MessageList
        messages={[
          createMessage({
            outgoing: true,
            acked: 4,
            text: 'Bonsoir comment ils vont tous?',
            sender_name: 'Me',
            paths: [{ path: 'AABB', path_len: 1, received_at: 1700000001 }],
          }),
        ]}
        contacts={[]}
        loading={false}
      />
    );

    const meta = screen.getByTestId('message-meta');
    expect(meta).toHaveTextContent(formatTime(1700000001));
    expect(meta).toHaveTextContent('✓4');
    expect(meta).not.toHaveTextContent('Bonsoir comment ils vont tous?');
    expect(screen.getByText('Bonsoir comment ils vont tous?')).toBeInTheDocument();
    expect(meta).toHaveClass('flex-wrap');
    expect(meta).not.toHaveClass('whitespace-nowrap');
    expect(meta).not.toHaveClass('flex-nowrap');
    const row = meta.closest('[data-message-id]');
    expect(row).toHaveClass('w-full');
    const bubble = meta.parentElement;
    expect(bubble).toHaveClass('w-max', 'max-w-[85%]', 'pr-7');
  });

  it('keeps the incoming sender name above the body, not in the metadata row', () => {
    render(
      <MessageList
        messages={[createMessage({ sender_name: 'Alice' })]}
        contacts={[]}
        loading={false}
      />
    );

    expect(screen.getByText('Alice')).toBeInTheDocument();
    expect(screen.getByTestId('message-meta')).not.toHaveTextContent('Alice');
  });

  it('does not render a region badge for unscoped messages', () => {
    render(
      <MessageList
        messages={[createMessage({ sender_name: 'Alice', region: null })]}
        contacts={[]}
        loading={false}
      />
    );

    expect(screen.queryByText('nl-gr')).not.toBeInTheDocument();
  });

  it('shows per-hop byte width in the path badge when the toggle is on', () => {
    render(
      <PathHopWidthProvider showPathHopWidth setShowPathHopWidth={() => {}}>
        <MessageList
          messages={[
            createMessage({
              sender_name: 'Alice',
              // 8 hex chars over 2 hops = 2 bytes/hop.
              paths: [{ path: 'AABBCCDD', path_len: 2, received_at: 1700000001 }],
            }),
          ]}
          contacts={[]}
          loading={false}
        />
      </PathHopWidthProvider>
    );

    expect(screen.getByText('(2 · 2B)')).toBeInTheDocument();
    expect(screen.getByTitle(i18n.t('path.viewPathWidth', { width: '2B' }))).toBeInTheDocument();
  });

  it('hides the width by default (toggle off) and shows only the hop count', () => {
    render(
      <PathHopWidthProvider showPathHopWidth={false} setShowPathHopWidth={() => {}}>
        <MessageList
          messages={[
            createMessage({
              sender_name: 'Alice',
              paths: [{ path: 'AABBCCDD', path_len: 2, received_at: 1700000001 }],
            }),
          ]}
          contacts={[]}
          loading={false}
        />
      </PathHopWidthProvider>
    );

    expect(screen.getByText('(2)')).toBeInTheDocument();
    expect(screen.queryByText('(2 · 2B)')).not.toBeInTheDocument();
    expect(screen.getByTitle(i18n.t('path.viewPath'))).toBeInTheDocument();
  });

  it('omits the width for direct (0-hop) paths even when the toggle is on', () => {
    render(
      <PathHopWidthProvider showPathHopWidth setShowPathHopWidth={() => {}}>
        <MessageList
          messages={[
            createMessage({
              sender_name: 'Alice',
              paths: [{ path: '', path_len: 0, received_at: 1700000001 }],
            }),
          ]}
          contacts={[]}
          loading={false}
        />
      </PathHopWidthProvider>
    );

    expect(screen.getByText('(d)')).toBeInTheDocument();
    expect(screen.getByTitle(i18n.t('path.viewPath'))).toBeInTheDocument();
  });

  it('prefers stored sender_name for channel messages even when text is not sender-prefixed', () => {
    render(
      <MessageList
        messages={[
          createMessage({
            text: 'garbled payload with no sender prefix',
            sender_name: 'Alice',
            sender_key: 'ab'.repeat(32),
          }),
        ]}
        contacts={[]}
        loading={false}
      />
    );

    expect(screen.getByText('Alice')).toBeInTheDocument();
    expect(screen.getByTestId('contact-avatar')).toHaveAttribute('data-avatar-text', 'Al');
  });

  it('renders room-server DM messages using stored sender attribution instead of the room contact', () => {
    const roomContact: Contact = {
      public_key: 'ab'.repeat(32),
      name: 'Ops Board',
      type: CONTACT_TYPE_ROOM,
      flags: 0,
      direct_path: null,
      direct_path_len: -1,
      direct_path_hash_mode: -1,
      last_advert: null,
      lat: null,
      lon: null,
      last_seen: null,
      on_radio: false,
      favorite: false,
      last_contacted: null,
      last_read_at: null,
      first_seen: null,
    };

    render(
      <MessageList
        messages={[
          createMessage({
            type: 'PRIV',
            conversation_key: roomContact.public_key,
            text: 'status update: ready',
            sender_name: 'Alice',
            sender_key: '12'.repeat(32),
          }),
        ]}
        contacts={[roomContact]}
        loading={false}
      />
    );

    expect(screen.getByText('Alice')).toBeInTheDocument();
    expect(screen.queryByText('Ops Board')).not.toBeInTheDocument();
    expect(screen.getByText('status update: ready')).toBeInTheDocument();
  });

  it('gives clickable sender avatars an accessible label', () => {
    render(
      <MessageList
        messages={[
          createMessage({
            text: 'garbled payload with no sender prefix',
            sender_name: 'Alice',
            sender_key: 'ab'.repeat(32),
          }),
        ]}
        contacts={[]}
        loading={false}
        onOpenContactInfo={() => {}}
      />
    );

    expect(
      screen.getByRole('button', { name: i18n.t('messageList.viewInfoFor', { name: 'Alice' }) })
    ).toBeInTheDocument();
  });

  it('renders valid channel references as clickable links and ignores invalid ones', async () => {
    const user = userEvent.setup();
    const onChannelReferenceClick = vi.fn();

    render(
      <MessageList
        messages={[
          createMessage({
            text: 'Alice: Join #mesh-room now skip #bad--room and visit https://example.com/#also-skip',
          }),
        ]}
        contacts={[]}
        loading={false}
        onChannelReferenceClick={onChannelReferenceClick}
      />
    );

    const linkedChannel = screen.getByRole('button', { name: '#mesh-room' });
    expect(linkedChannel).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: '#bad--room' })).not.toBeInTheDocument();
    expect(
      screen.getByRole('link', { name: 'https://example.com/#also-skip' })
    ).toBeInTheDocument();

    await user.click(linkedChannel);

    expect(onChannelReferenceClick).toHaveBeenCalledWith('#mesh-room');
  });

  it('links valid channel references when followed by clause punctuation', async () => {
    const user = userEvent.setup();
    const onChannelReferenceClick = vi.fn();

    render(
      <MessageList
        messages={[
          createMessage({
            text: 'Alice: Check #mesh-room, then #ops-room; then #alpha-room.',
          }),
        ]}
        contacts={[]}
        loading={false}
        onChannelReferenceClick={onChannelReferenceClick}
      />
    );

    await user.click(screen.getByRole('button', { name: '#mesh-room' }));
    await user.click(screen.getByRole('button', { name: '#ops-room' }));
    await user.click(screen.getByRole('button', { name: '#alpha-room' }));

    expect(onChannelReferenceClick).toHaveBeenNthCalledWith(1, '#mesh-room');
    expect(onChannelReferenceClick).toHaveBeenNthCalledWith(2, '#ops-room');
    expect(onChannelReferenceClick).toHaveBeenNthCalledWith(3, '#alpha-room');
  });

  it('links valid channel references in direct messages too', async () => {
    const user = userEvent.setup();
    const onChannelReferenceClick = vi.fn();

    render(
      <MessageList
        messages={[
          createMessage({
            type: 'PRIV',
            text: 'check #ops-room',
            conversation_key: 'ab'.repeat(32),
          }),
        ]}
        contacts={[]}
        loading={false}
        onChannelReferenceClick={onChannelReferenceClick}
      />
    );

    await user.click(screen.getByRole('button', { name: '#ops-room' }));

    expect(onChannelReferenceClick).toHaveBeenCalledWith('#ops-room');
  });

  it('does not strip colon-prefixed text in direct messages (issue #198)', () => {
    render(
      <MessageList
        messages={[
          createMessage({
            type: 'PRIV',
            conversation_key: 'ab'.repeat(32),
            text: 'TEST1: TEST2',
          }),
        ]}
        contacts={[]}
        loading={false}
      />
    );

    expect(screen.getByText('TEST1: TEST2')).toBeInTheDocument();
  });

  it('offers a jump instead of a divider when the unread boundary is not loaded', async () => {
    const user = userEvent.setup();
    const onNavigateToUnread = vi.fn();
    // Boundary id 999 is not among the loaded messages: the real first-unread is
    // further back than this window. The divider must not be invented at the top.
    render(
      <MessageList
        messages={[
          createMessage({ id: 1, received_at: 1700000001, text: 'Alice: older' }),
          createMessage({ id: 2, received_at: 1700000010, text: 'Alice: newer' }),
        ]}
        contacts={[]}
        loading={false}
        unreadMarkerMessageId={999}
        onNavigateToUnread={onNavigateToUnread}
      />
    );

    expect(screen.queryByText(i18n.t('messageList.unread'))).not.toBeInTheDocument();

    const jump = await screen.findByRole('button', { name: i18n.t('messageList.jumpToUnread') });
    await user.click(jump);

    // Hands off to the jump-to-message path rather than scrolling to a wrong row.
    expect(onNavigateToUnread).toHaveBeenCalledWith(999);
  });

  it('shows no unread affordance at all when nothing is unread', () => {
    render(
      <MessageList
        messages={[createMessage({ id: 1, received_at: 1700000001, text: 'Alice: hi' })]}
        contacts={[]}
        loading={false}
        unreadMarkerMessageId={null}
      />
    );

    expect(screen.queryByText(i18n.t('messageList.unread'))).not.toBeInTheDocument();
    expect(
      screen.queryByRole('button', { name: i18n.t('messageList.jumpToUnread') })
    ).not.toBeInTheDocument();
  });

  it('renders and dismisses an unread marker at the first unread message boundary', async () => {
    const user = userEvent.setup();
    const messages = [
      createMessage({ id: 1, received_at: 1700000001, text: 'Alice: older' }),
      createMessage({ id: 2, received_at: 1700000010, text: 'Alice: newer' }),
    ];

    function DismissibleUnreadMarkerList() {
      const [unreadMarkerMessageId, setUnreadMarkerMessageId] = useState<number | undefined>(2);

      return (
        <MessageList
          messages={messages}
          contacts={[]}
          loading={false}
          unreadMarkerMessageId={unreadMarkerMessageId}
          onDismissUnreadMarker={() => setUnreadMarkerMessageId(undefined)}
        />
      );
    }

    render(<DismissibleUnreadMarkerList />);

    const marker = screen.getByRole('button', {
      name: new RegExp(i18n.t('messageList.unread'), 'i'),
    });
    expect(marker).toBeInTheDocument();
    expect(screen.getByText('older')).toBeInTheDocument();
    expect(screen.getByText('newer')).toBeInTheDocument();

    await user.click(marker);

    expect(
      screen.queryByRole('button', { name: new RegExp(i18n.t('messageList.unread'), 'i') })
    ).not.toBeInTheDocument();
  });

  it('shows a jump-to-unread button and dismisses it after use without hiding the marker', async () => {
    const user = userEvent.setup();
    const messages = [
      createMessage({ id: 1, received_at: 1700000001, text: 'Alice: older' }),
      createMessage({ id: 2, received_at: 1700000010, text: 'Alice: newer' }),
    ];

    render(
      <MessageList messages={messages} contacts={[]} loading={false} unreadMarkerMessageId={2} />
    );

    const jumpButton = screen.getByRole('button', { name: i18n.t('messageList.jumpToUnread') });
    expect(jumpButton).toBeInTheDocument();
    expect(screen.getByText(i18n.t('messageList.unread'))).toBeInTheDocument();

    await user.click(jumpButton);

    expect(
      screen.queryByRole('button', { name: i18n.t('messageList.jumpToUnread') })
    ).not.toBeInTheDocument();
    expect(screen.getByText(i18n.t('messageList.unread'))).toBeInTheDocument();
    expect(scrollIntoViewMock).toHaveBeenCalled();
  });

  it('lets the user dismiss the jump-to-unread button without scrolling or hiding the marker', async () => {
    const user = userEvent.setup();
    const messages = [
      createMessage({ id: 1, received_at: 1700000001, text: 'Alice: older' }),
      createMessage({ id: 2, received_at: 1700000010, text: 'Alice: newer' }),
    ];

    render(
      <MessageList messages={messages} contacts={[]} loading={false} unreadMarkerMessageId={2} />
    );

    await user.click(screen.getByRole('button', { name: i18n.t('messageList.dismissJump') }));

    expect(
      screen.queryByRole('button', { name: i18n.t('messageList.jumpToUnread') })
    ).not.toBeInTheDocument();
    expect(screen.getByText(i18n.t('messageList.unread'))).toBeInTheDocument();
    expect(scrollIntoViewMock).not.toHaveBeenCalled();
  });

  it('hides the jump-to-unread button when the unread marker is already visible', () => {
    Object.defineProperty(HTMLElement.prototype, 'getBoundingClientRect', {
      configurable: true,
      writable: true,
      value: function () {
        const element = this as HTMLElement;
        if (element.textContent?.includes(i18n.t('messageList.unread'))) {
          return {
            top: 200,
            bottom: 240,
            left: 0,
            right: 300,
            width: 300,
            height: 40,
            x: 0,
            y: 200,
            toJSON: () => '',
          };
        }
        if (element.className.includes('overflow-y-auto')) {
          return {
            top: 100,
            bottom: 500,
            left: 0,
            right: 400,
            width: 400,
            height: 400,
            x: 0,
            y: 100,
            toJSON: () => '',
          };
        }
        return {
          top: 0,
          bottom: 0,
          left: 0,
          right: 0,
          width: 0,
          height: 0,
          x: 0,
          y: 0,
          toJSON: () => '',
        };
      },
    });

    const messages = [
      createMessage({ id: 1, received_at: 1700000001, text: 'Alice: older' }),
      createMessage({ id: 2, received_at: 1700000010, text: 'Alice: newer' }),
    ];

    render(
      <MessageList messages={messages} contacts={[]} loading={false} unreadMarkerMessageId={2} />
    );

    expect(screen.getByText(i18n.t('messageList.unread'))).toBeInTheDocument();
    expect(
      screen.queryByRole('button', { name: i18n.t('messageList.jumpToUnread') })
    ).not.toBeInTheDocument();
  });
  it('mounts only a window of rows for a long history', () => {
    const messages = Array.from({ length: 500 }, (_, i) =>
      createMessage({
        id: i + 1,
        text: `Alice: message ${i}`,
        sender_timestamp: 1700000000 + i,
        received_at: 1700000001 + i,
      })
    );

    const { container } = render(<MessageList messages={messages} contacts={[]} loading={false} />);

    // jsdom reports no layout, so the list falls back to a nominal viewport. The point
    // is that the window is bounded: a 500-message history must not mount 500 rows.
    const mounted = container.querySelectorAll('[data-message-id]').length;
    expect(mounted).toBeGreaterThan(0);
    expect(mounted).toBeLessThan(100);
  });
});

describe('MessageList Open reactions and replies', () => {
  beforeEach(() => {
    Object.defineProperty(HTMLElement.prototype, 'scrollIntoView', {
      configurable: true,
      value: scrollIntoViewMock,
      writable: true,
    });
  });

  it('renders an inbound Open GIF even when rich reactions are off', () => {
    render(
      <MessageList
        messages={[
          createMessage({
            id: 1,
            text: 'Alice: g:abc123',
            sender_name: 'Alice',
          }),
        ]}
        contacts={[]}
        loading={false}
      />
    );

    const gif = screen.getByTestId('message-gif');
    expect(gif).toHaveAttribute('src', 'https://media.giphy.com/media/abc123/giphy.gif');
    expect(screen.queryByText('g:abc123')).not.toBeInTheDocument();
  });

  it('renders an inbound Open location pin even when rich reactions are off', () => {
    render(
      <MessageList
        messages={[
          createMessage({
            id: 1,
            text: 'Alice: m:43.580000,7.120000|FR-06-PSCL-Base|loc',
            sender_name: 'Alice',
          }),
        ]}
        contacts={[]}
        loading={false}
      />
    );

    const pin = screen.getByTestId('message-location');
    expect(pin).toHaveAttribute(
      'href',
      'https://www.openstreetmap.org/?mlat=43.58&mlon=7.12#map=16/43.58/7.12'
    );
    expect(screen.getByText('FR-06-PSCL-Base')).toBeInTheDocument();
    expect(screen.queryByText(/m:43\.580000/)).not.toBeInTheDocument();
  });

  it('attaches a matching Open reaction as a badge on the target, not a standalone row', () => {
    const wire = formatOpenReaction(1700000000, 'Alice', 'hello world', '🔥');
    render(
      <MessageList
        messages={[
          createMessage({ id: 1, text: 'Alice: hello world', sender_name: 'Alice' }),
          createMessage({
            id: 2,
            text: `Bob: ${wire}`,
            sender_name: 'Bob',
            sender_timestamp: 1700000002,
            received_at: 1700000003,
          }),
        ]}
        contacts={[]}
        loading={false}
      />
    );

    expect(screen.getByText('hello world')).toBeInTheDocument();
    expect(screen.getByTestId('message-reactions')).toHaveTextContent('🔥');
    expect(screen.queryByText(i18n.t('messageList.reacted'))).not.toBeInTheDocument();
    expect(screen.queryByText(wire!)).not.toBeInTheDocument();
  });

  it('keeps an unmatched Open reaction as a standalone reacted row when rich payloads are on', () => {
    render(
      <RichPayloadProvider renderRichPayloads setRenderRichPayloads={() => {}}>
        <MessageList
          messages={[
            createMessage({
              id: 1,
              text: 'Bob: r:ffff:00',
              sender_name: 'Bob',
            }),
          ]}
          contacts={[]}
          loading={false}
        />
      </RichPayloadProvider>
    );

    expect(screen.getByText(i18n.t('messageList.reacted'))).toBeInTheDocument();
    expect(screen.getByText('👍')).toBeInTheDocument();
  });

  it('keeps parsing MeshCore One inbound as a standalone reacted row', () => {
    render(
      <RichPayloadProvider renderRichPayloads setRenderRichPayloads={() => {}}>
        <MessageList
          messages={[
            createMessage({
              id: 1,
              text: 'Bob: \u{1F44D}@[Alice]\nb45pc4ek',
              sender_name: 'Bob',
            }),
          ]}
          contacts={[]}
          loading={false}
        />
      </RichPayloadProvider>
    );

    expect(
      screen.getByText(i18n.t('messageList.reactedTo', { sender: 'Alice' }))
    ).toBeInTheDocument();
  });

  it('sends an Open reaction wire through onSendMessage', async () => {
    const user = userEvent.setup();
    const onSendMessage = vi.fn().mockResolvedValue(undefined);
    const expected = formatOpenReaction(1700000000, 'Alice', 'hello world', '👍');

    render(
      <MessageList
        messages={[createMessage({ sender_name: 'Alice' })]}
        contacts={[]}
        loading={false}
        onSendMessage={onSendMessage}
      />
    );

    expect(screen.queryByTestId('message-quick-reactions')).toBeNull();

    await user.click(screen.getByTestId('message-react-trigger'));
    await user.click(
      screen.getAllByRole('button', { name: i18n.t('messageList.reactWith', { emoji: '👍' }) })[0]
    );

    expect(onSendMessage).toHaveBeenCalledWith(expected);
  });

  it('keeps the quick reaction bar closed until the emoji trigger is clicked', async () => {
    const user = userEvent.setup();

    render(
      <MessageList
        messages={[createMessage({ sender_name: 'Alice' })]}
        contacts={[]}
        loading={false}
        onSendMessage={vi.fn()}
      />
    );

    expect(screen.getByTestId('message-react-trigger')).toBeInTheDocument();
    expect(screen.queryByTestId('message-quick-reactions')).toBeNull();

    await user.click(screen.getByTestId('message-react-trigger'));

    const picker = screen.getByTestId('message-quick-reactions');
    expect(picker).toBeInTheDocument();
    expect(picker).toHaveClass('right-0');
    expect(picker).not.toHaveClass('left-0');
  });

  it('does not emit a keyless tapback when reacting', async () => {
    const user = userEvent.setup();
    const onSendMessage = vi.fn().mockResolvedValue(undefined);

    render(
      <MessageList
        messages={[createMessage({ sender_name: 'Alice' })]}
        contacts={[]}
        loading={false}
        onSendMessage={onSendMessage}
      />
    );

    await user.click(screen.getByTestId('message-react-trigger'));
    await user.click(
      screen.getAllByRole('button', { name: i18n.t('messageList.reactWith', { emoji: '👍' }) })[0]
    );

    expect(onSendMessage).not.toHaveBeenCalledWith(expect.stringMatching(/^@\[Alice\]/));
  });

  it('replies by quoting the message and mentioning the sender', async () => {
    const user = userEvent.setup();
    const onSenderClick = vi.fn();

    render(
      <MessageList
        messages={[createMessage({ sender_name: 'Alice' })]}
        contacts={[]}
        loading={false}
        onSenderClick={onSenderClick}
      />
    );

    expect(screen.queryByRole('menuitem', { name: i18n.t('messageList.reply') })).toBeNull();
    expect(screen.queryByRole('button', { name: i18n.t('messageList.reply') })).toBeNull();

    await user.click(screen.getByRole('button', { name: i18n.t('messageList.actions') }));
    await user.click(screen.getByRole('menuitem', { name: i18n.t('messageList.reply') }));

    expect(onSenderClick).toHaveBeenCalledWith('Alice', 'hello world');
  });

  it('copies the message text from the action menu', async () => {
    const user = userEvent.setup();
    const writeText = vi.fn().mockResolvedValue(undefined);
    Object.defineProperty(navigator, 'clipboard', {
      configurable: true,
      value: { writeText },
    });

    render(
      <MessageList
        messages={[createMessage({ sender_name: 'Alice' })]}
        contacts={[]}
        loading={false}
      />
    );

    await user.click(screen.getByRole('button', { name: i18n.t('messageList.actions') }));
    await user.click(screen.getByRole('menuitem', { name: i18n.t('messageList.copy') }));

    expect(writeText).toHaveBeenCalledWith('hello world');
  });

  it('does not offer react or reply on outgoing messages', async () => {
    const user = userEvent.setup();
    render(
      <MessageList
        messages={[createMessage({ outgoing: true, text: 'hello world' })]}
        contacts={[]}
        loading={false}
        onSendMessage={vi.fn()}
        onSenderClick={vi.fn()}
        onOpenContactInfo={vi.fn()}
      />
    );

    expect(screen.queryByRole('button', { name: i18n.t('messageList.react') })).toBeNull();
    expect(screen.queryByTestId('message-react-trigger')).toBeNull();
    expect(screen.queryByTestId('message-quick-reactions')).toBeNull();

    await user.click(screen.getByRole('button', { name: i18n.t('messageList.actions') }));

    expect(screen.queryByRole('menuitem', { name: i18n.t('messageList.reply') })).toBeNull();
    expect(screen.queryByRole('menuitem', { name: i18n.t('messageList.contactInfo') })).toBeNull();
    expect(
      screen.getByRole('menuitem', { name: i18n.t('messageList.messageDetails') })
    ).toBeInTheDocument();
    expect(
      screen.getByRole('menuitem', { name: i18n.t('messageList.delete') })
    ).toBeInTheDocument();
  });

  it('opens contact info from the message menu', async () => {
    const user = userEvent.setup();
    const onOpenContactInfo = vi.fn();
    const publicKey = 'ab'.repeat(32);

    render(
      <MessageList
        messages={[
          createMessage({
            type: 'PRIV',
            conversation_key: publicKey,
            text: 'hello world',
            sender_name: 'Alice',
          }),
        ]}
        contacts={[]}
        loading={false}
        onOpenContactInfo={onOpenContactInfo}
      />
    );

    await user.click(screen.getByRole('button', { name: i18n.t('messageList.actions') }));
    await user.click(screen.getByRole('menuitem', { name: i18n.t('messageList.contactInfo') }));

    expect(onOpenContactInfo).toHaveBeenCalledWith(publicKey, false);
  });

  it('opens message details from the menu', async () => {
    const user = userEvent.setup();

    render(
      <MessageList
        messages={[createMessage({ sender_name: 'Alice' })]}
        contacts={[]}
        loading={false}
      />
    );

    await user.click(screen.getByRole('button', { name: i18n.t('messageList.actions') }));
    await user.click(screen.getByRole('menuitem', { name: i18n.t('messageList.messageDetails') }));

    expect(screen.getByText(i18n.t('messageList.messageStatus'))).toBeInTheDocument();
  });

  it('deletes a message after confirmation', async () => {
    const user = userEvent.setup();
    const onMessageDeleted = vi.fn();

    render(
      <MessageList
        messages={[createMessage({ sender_name: 'Alice' })]}
        contacts={[]}
        loading={false}
        onMessageDeleted={onMessageDeleted}
      />
    );

    await user.click(screen.getByRole('button', { name: i18n.t('messageList.actions') }));
    await user.click(screen.getByRole('menuitem', { name: i18n.t('messageList.delete') }));
    await user.click(
      screen.getByRole('button', { name: i18n.t('messageList.deleteConfirmAction') })
    );

    await waitFor(() => {
      expect(apiMocks.deleteMessage).toHaveBeenCalledWith(1);
      expect(onMessageDeleted).toHaveBeenCalledWith(1);
    });
  });

  it('shows the observer ear on flood channel messages when the directory is on', async () => {
    const user = userEvent.setup();
    render(
      <MessageList
        messages={[
          createMessage({
            sender_name: 'Alice',
            packet_hash: 'AABBCCDDEEFF0011',
            observer_reach_eligible: true,
            received_at: Math.floor(Date.now() / 1000) - 60,
          }),
        ]}
        contacts={[]}
        loading={false}
        directoryEnabled
        conversationKey="C3B889530D4F02DB5662EA13C417F530"
      />
    );

    const badge = await screen.findByTestId('observer-reach-badge');
    expect(badge).toBeInTheDocument();
    await waitFor(() => {
      expect(apiMocks.getPacketObserverReachCounts).toHaveBeenCalled();
    });

    await user.click(badge);
    expect(
      await screen.findByRole('heading', { name: i18n.t('messageList.observerReachTitle') })
    ).toBeInTheDocument();
    expect(screen.getByText(i18n.t('messageList.observerReachCaveat'))).toBeInTheDocument();
    expect(apiMocks.getPacketObserverReach).toHaveBeenCalledWith('AABBCCDDEEFF0011');
  });

  it('does not show the ear on direct routed DMs', () => {
    render(
      <MessageList
        messages={[
          createMessage({
            type: 'PRIV',
            conversation_key: 'ab'.repeat(32),
            packet_hash: 'AABBCCDDEEFF0011',
            observer_reach_eligible: false,
            sender_name: 'Alice',
          }),
        ]}
        contacts={[]}
        loading={false}
        directoryEnabled
        conversationKey={'ab'.repeat(32)}
      />
    );

    expect(screen.queryByTestId('observer-reach-badge')).not.toBeInTheDocument();
  });

  it('makes no observer network call when the directory is off', async () => {
    render(
      <MessageList
        messages={[
          createMessage({
            sender_name: 'Alice',
            packet_hash: 'AABBCCDDEEFF0011',
            observer_reach_eligible: true,
          }),
        ]}
        contacts={[]}
        loading={false}
        directoryEnabled={false}
        conversationKey="C3B889530D4F02DB5662EA13C417F530"
      />
    );

    expect(screen.queryByTestId('observer-reach-badge')).not.toBeInTheDocument();
    apiMocks.getPacketObserverReachCounts.mockClear();
    await new Promise((resolve) => setTimeout(resolve, 300));
    expect(apiMocks.getPacketObserverReachCounts).not.toHaveBeenCalled();
  });

  it('hides the ear when the observer count is zero', async () => {
    apiMocks.getPacketObserverReachCounts.mockResolvedValue({
      directory_enabled: true,
      counts: { BBBBCCDDEEFF0011: 0 },
    });
    render(
      <MessageList
        messages={[
          createMessage({
            sender_name: 'Alice',
            packet_hash: 'BBBBCCDDEEFF0011',
            observer_reach_eligible: true,
            received_at: Math.floor(Date.now() / 1000) - 120,
          }),
        ]}
        contacts={[]}
        loading={false}
        directoryEnabled
        conversationKey="C3B889530D4F02DB5662EA13C417F530"
      />
    );

    await waitFor(() => {
      expect(apiMocks.getPacketObserverReachCounts).toHaveBeenCalled();
    });
    expect(screen.queryByTestId('observer-reach-badge')).not.toBeInTheDocument();
  });

  it('shows a single ear on an outgoing flood message', async () => {
    render(
      <MessageList
        messages={[
          createMessage({
            outgoing: true,
            acked: 1,
            sender_name: 'Me',
            text: 'hello',
            packet_hash: 'AABBCCDDEEFF0011',
            observer_reach_eligible: true,
            received_at: Math.floor(Date.now() / 1000) - 60,
          }),
        ]}
        contacts={[]}
        loading={false}
        directoryEnabled
        conversationKey="C3B889530D4F02DB5662EA13C417F530"
      />
    );

    const badges = await screen.findAllByTestId('observer-reach-badge');
    expect(badges).toHaveLength(1);
    expect(badges[0]).toHaveTextContent('3');
  });
});
