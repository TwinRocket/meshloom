import { act, cleanup, fireEvent, render, screen, within } from '@testing-library/react';
import { MeshCoreDecoder } from '@michaelhart/meshcore-decoder';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import './qSlices';
import './eSlices';
import './wSlices';

const mocks = vi.hoisted(() => ({ getMessages: vi.fn() }));

vi.mock('../api', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../api')>();
  return { ...actual, api: { ...actual.api, getMessages: mocks.getMessages } };
});

import { FeedView } from '../components/feed/FeedView';
import { FEED_DOM_MAX } from '../components/feed/feedConversation';
import i18n from '../i18n';
import wEn from '../i18n/locales/slices/w.en.json';
import wFr from '../i18n/locales/slices/w.fr.json';
import { activateFeedStore, recordFeedControlPacket, resetFeedStore } from '../stores/feedStore';
import { recordRawPacket, resetRawPacketStore } from '../stores/rawPacketStore';
import { clearControlJournalCache } from '../utils/controlJournal';
import {
  CONTACT_TYPE_REPEATER,
  type Channel,
  type Contact,
  type Message,
  type RawPacket,
} from '../types';

const CHANNEL_KEY = 'AABBCCDDEEFF00112233445566778899';
const GROUP_DATA_PACKET = '19006ed356e5b542d4bceab6dc9bc995d8225492b0';
const LOCAL_KEY = 'aa'.repeat(32);
const REPEATER_KEY = 'bb'.repeat(32);
const ANON_SENDER_KEY = 'cc'.repeat(32);
const BASE = 1_700_000_000;

function floodPacketHex(payloadType: number, payloadHex: string): string {
  const header = ((payloadType & 0x0f) << 2) | 0x01;
  return `${header.toString(16).padStart(2, '0')}00${payloadHex}`;
}
const requestHex = (dest: string, src: string, tag = '11'.repeat(16)) =>
  floodPacketHex(0x00, `${dest}${src}aabb${tag}`);
const responseHex = (dest: string, src: string, tag = '22'.repeat(16)) =>
  floodPacketHex(0x01, `${dest}${src}aabb${tag}`);
const anonRequestHex = (dest: string, senderKey: string) =>
  floodPacketHex(0x07, `${dest}${senderKey}${'ee'.repeat(2)}${'33'.repeat(16)}`);

function packet(id: number, overrides: Partial<RawPacket> = {}): RawPacket {
  return {
    id,
    observation_id: id,
    timestamp: BASE + id,
    data: requestHex('bb', 'aa'),
    payload_type: 'Request',
    snr: null,
    rssi: null,
    decrypted: false,
    decrypted_info: null,
    ...overrides,
  };
}

function contact(overrides: Partial<Contact> = {}): Contact {
  return {
    public_key: REPEATER_KEY,
    name: 'Tower',
    type: CONTACT_TYPE_REPEATER,
    flags: 0,
    direct_path: null,
    direct_path_len: 0,
    direct_path_hash_mode: 0,
    last_advert: BASE,
    lat: null,
    lon: null,
    last_seen: null,
    on_radio: false,
    favorite: false,
    last_contacted: null,
    last_read_at: null,
    first_seen: null,
    ...overrides,
  };
}

const channel = {
  key: CHANNEL_KEY,
  name: '#gd',
  is_hashtag: true,
  on_radio: false,
  last_read_at: null,
  favorite: false,
  muted: false,
} as unknown as Channel;

const groupDataPacket = (id: number) =>
  packet(id, {
    data: GROUP_DATA_PACKET,
    payload_type: 'GroupData',
    decrypted_info: {
      channel_name: '#gd',
      sender: null,
      channel_key: CHANNEL_KEY,
      contact_key: null,
      sender_timestamp: null,
      message: null,
      group_data: { data_type: 0x00ab, data_len: 9, data_hex: '73', data_text: 'sensor-ok' },
    },
  });

function msg(id: number, receivedAt: number, key = CHANNEL_KEY): Message {
  return {
    id,
    type: 'CHAN',
    conversation_key: key,
    text: `Alice: hello ${id}`,
    sender_timestamp: receivedAt,
    received_at: receivedAt,
    paths: null,
    txt_type: 0,
    signature: null,
    sender_key: null,
    outgoing: false,
    acked: 0,
    sender_name: null,
  };
}

function renderFeed(
  props: Partial<React.ComponentProps<typeof FeedView>> = {},
  channels: Channel[] = [channel]
) {
  return render(
    <FeedView
      contacts={[contact()]}
      channels={channels}
      publicKey={LOCAL_KEY}
      onBackToTools={vi.fn()}
      {...props}
    />
  );
}

/** Seed through the live path: the store is active, as after the first FeedView mount. */
function feedControl(...packets: RawPacket[]) {
  activateFeedStore();
  for (const p of packets) recordFeedControlPacket(p);
}

/**
 * A third party's Request / Response stays encrypted, so the decoder yields no correlation
 * tag for it: give both the same one, as a decrypted exchange would have.
 */
function withCorrelationTag(tag: number) {
  const real = MeshCoreDecoder.decode.bind(MeshCoreDecoder);
  return vi.spyOn(MeshCoreDecoder, 'decode').mockImplementation((...args) => {
    const decoded = real(...args);
    const payload = decoded.payload.decoded as { decrypted?: { tag: number } } | null;
    if (payload) payload.decrypted = { tag };
    return decoded;
  });
}

const rowsOf = async () =>
  within(await screen.findByRole('list', { name: i18n.t('feed.listLabel') })).getAllByRole(
    'listitem'
  );

beforeEach(() => {
  mocks.getMessages.mockReset();
  mocks.getMessages.mockResolvedValue([]);
  resetFeedStore();
  resetRawPacketStore();
  clearControlJournalCache();
  localStorage.clear();
});

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

describe('feed control rows', () => {
  it('keeps the four control types only: GroupText and Advert are ignored', async () => {
    feedControl(
      packet(10, { payload_type: 'GroupText', data: floodPacketHex(0x05, 'aabbccddeeff') }),
      packet(11, { payload_type: 'Advert', data: floodPacketHex(0x04, '77'.repeat(4)) }),
      packet(12),
      packet(13, { data: responseHex('aa', 'bb'), payload_type: 'Response' }),
      packet(14, { data: anonRequestHex('bb', ANON_SENDER_KEY), payload_type: 'AnonRequest' }),
      groupDataPacket(15)
    );
    renderFeed();
    await rowsOf();
    expect(screen.getAllByTestId('feed-control-row-Request')).toHaveLength(1);
    expect(screen.getAllByTestId('feed-control-row-Response')).toHaveLength(1);
    expect(screen.getAllByTestId('feed-control-row-AnonRequest')).toHaveLength(1);
    expect(screen.getAllByTestId('feed-control-row-GroupData')).toHaveLength(1);
    expect(screen.queryByTestId('feed-control-row-GroupText')).not.toBeInTheDocument();
    expect(screen.queryByText(/aabbccddeeff/i)).not.toBeInTheDocument();
  });

  it('shows the decrypted GroupData text, its type and a channel link', async () => {
    const onSelectConversation = vi.fn();
    feedControl(groupDataPacket(20));
    renderFeed({
      onSelectConversation,
      onOpenContactInfo: vi.fn(),
      onNavigateToMessage: vi.fn(),
    });
    await rowsOf();
    expect(screen.getByTestId('feed-control-data-type')).toHaveTextContent('0x00ab');
    expect(screen.getByText('sensor-ok')).toBeInTheDocument();
    expect(screen.queryByText(i18n.t('controlJournal.locked'))).not.toBeInTheDocument();
    const link = screen.getByTestId('feed-control-channel');
    expect(link).toHaveTextContent(i18n.t('controlJournal.channel', { name: '#gd' }));
    fireEvent.click(link);
    expect(onSelectConversation).toHaveBeenCalledWith({
      type: 'channel',
      id: CHANNEL_KEY,
      name: '#gd',
    });
    // The link is not a click on the row: no inspector.
    expect(screen.queryByText(i18n.t('rawPacket.details'))).not.toBeInTheDocument();
  });

  it('keeps a locked Request useful with dest, src and the repeater name', async () => {
    feedControl(packet(30));
    renderFeed();
    await rowsOf();
    expect(screen.getByText(i18n.t('controlJournal.locked'))).toBeInTheDocument();
    expect(screen.getByTestId('feed-control-envelope')).toHaveTextContent(/Dest BB/i);
    expect(screen.getByTestId('feed-control-envelope')).toHaveTextContent(/Src AA/i);
    expect(screen.getAllByText('Tower').length).toBeGreaterThan(0);
  });

  it('puts a dest matching our key prefix on the Us node', async () => {
    feedControl(packet(40, { data: requestHex('aa', 'dd') }));
    renderFeed({}, []);
    await rowsOf();
    expect(screen.getAllByTestId('feed-node-us')[0]).toHaveTextContent(
      i18n.t('controlJournal.usThread')
    );
    expect(screen.getByTestId('feed-control-envelope')).toHaveTextContent(/Dest AA/i);
  });

  it('resolves the AnonRequest sender on the from side', async () => {
    feedControl(
      packet(50, { data: anonRequestHex('bb', ANON_SENDER_KEY), payload_type: 'AnonRequest' })
    );
    renderFeed({
      contacts: [contact(), contact({ public_key: ANON_SENDER_KEY, name: 'Walker', type: 1 })],
    });
    await rowsOf();
    expect(screen.getByText('Walker')).toBeInTheDocument();
    expect(screen.getByText(i18n.t('controlJournal.subtypeLogin'))).toBeInTheDocument();
  });

  it('opens the packet inspector from a row click', async () => {
    feedControl(packet(60));
    renderFeed();
    fireEvent.click((await rowsOf())[0]);
    expect(screen.getByText(i18n.t('rawPacket.details'))).toBeInTheDocument();
  });

  it('opens the contact sheet from a unique peer, without opening the inspector', async () => {
    const onOpenContactInfo = vi.fn();
    feedControl(packet(61));
    renderFeed({
      onOpenContactInfo,
      onSelectConversation: vi.fn(),
      onNavigateToMessage: vi.fn(),
    });
    await rowsOf();
    fireEvent.click(screen.getByRole('link', { name: 'Tower' }));
    expect(onOpenContactInfo).toHaveBeenCalledWith(REPEATER_KEY);
    expect(screen.queryByText(i18n.t('rawPacket.details'))).not.toBeInTheDocument();
  });

  it('pairs a Request with its Response and scrolls to the twin', async () => {
    const tag = '44'.repeat(4);
    withCorrelationTag(0x1234);
    feedControl(
      packet(70, { data: requestHex('bb', 'aa', tag) }),
      packet(71, { data: responseHex('aa', 'bb', tag), payload_type: 'Response' })
    );
    renderFeed();
    await rowsOf();
    const badges = screen.getAllByTestId('feed-control-paired');
    expect(badges).toHaveLength(2);
    fireEvent.click(badges[0]);
    // The twin is highlighted for a moment.
    const response = screen.getByTestId('feed-control-row-Response');
    expect(response.className).toMatch(/bg-primary\/10/);
  });

  it('shows the Paired badge as plain text when the twin is filtered out by the node', async () => {
    const tag = '55'.repeat(4);
    withCorrelationTag(0x5678);
    feedControl(
      packet(72, { data: requestHex('bb', 'aa', tag) }),
      packet(73, { data: responseHex('aa', 'bb', tag), payload_type: 'Response' })
    );
    renderFeed({}, []);
    await rowsOf();
    // Request is in the repeater thread, Response in "Us": pick the repeater.
    fireEvent.click(screen.getAllByTestId('feed-node-repeater')[0]);
    await rowsOf();
    expect(screen.queryByTestId('feed-control-paired')).not.toBeInTheDocument();
    expect(screen.getByText(i18n.t('controlJournal.paired'))).toBeInTheDocument();
  });
});

describe('feed Nodes facet', () => {
  const phone = (matches: boolean) =>
    vi.stubGlobal(
      'matchMedia',
      vi.fn().mockReturnValue({
        matches,
        addEventListener: vi.fn(),
        removeEventListener: vi.fn(),
      })
    );
  afterEach(() => vi.unstubAllGlobals());

  it('opens the Nodes sheet with the Control preset on a phone only', async () => {
    feedControl(packet(90));
    phone(true);
    renderFeed({ preset: 'control' });
    await screen.findAllByTestId('feed-nodes-open');
    expect(await screen.findByRole('dialog')).toBeInTheDocument();
    cleanup();

    renderFeed();
    await rowsOf();
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
    cleanup();

    phone(false);
    renderFeed({ preset: 'control' });
    await screen.findAllByTestId('feed-nodes-open');
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
  });

  it('filters control rows to the selected node and clears with "All nodes"', async () => {
    feedControl(packet(80), packet(81, { data: requestHex('aa', 'dd') }), groupDataPacket(82));
    renderFeed();
    await rowsOf();
    expect(await rowsOf()).toHaveLength(3);
    fireEvent.click(screen.getAllByTestId('feed-node-us')[0]);
    expect(await rowsOf()).toHaveLength(1);
    expect(screen.getByTestId('feed-control-row-Request')).toBeInTheDocument();
    fireEvent.click(screen.getAllByTestId('feed-node-all')[0]);
    expect(await rowsOf()).toHaveLength(3);
  });

  it('keeps the node per browser', async () => {
    feedControl(packet(83), packet(84, { data: requestHex('aa', 'dd') }));
    const first = renderFeed();
    await rowsOf();
    fireEvent.click(screen.getAllByTestId('feed-node-us')[0]);
    first.unmount();
    renderFeed();
    expect(await rowsOf()).toHaveLength(1);
  });

  it('ignores a stored node that no longer exists', async () => {
    localStorage.setItem('meshloom-feed-filters', JSON.stringify({ node: 'contact:gone' }));
    feedControl(packet(85));
    renderFeed();
    expect(await rowsOf()).toHaveLength(1);
  });

  it('a channel node also narrows the messages to that channel', async () => {
    const other = 'FF'.repeat(16);
    mocks.getMessages.mockResolvedValue([msg(2, BASE + 200, other), msg(1, BASE + 100)]);
    feedControl(groupDataPacket(90));
    renderFeed();
    await screen.findByText(/hello 1/);
    expect(screen.getByText(/hello 2/)).toBeInTheDocument();
    fireEvent.click(screen.getAllByTestId('feed-node-channel')[0]);
    expect(screen.getByText(/hello 1/)).toBeInTheDocument();
    expect(screen.queryByText(/hello 2/)).not.toBeInTheDocument();
  });

  it('a repeater node hides the messages', async () => {
    mocks.getMessages.mockResolvedValue([msg(1, BASE + 100)]);
    feedControl(packet(91));
    renderFeed();
    await screen.findByText(/hello 1/);
    fireEvent.click(screen.getAllByTestId('feed-node-repeater')[0]);
    expect(screen.queryByText(/hello 1/)).not.toBeInTheDocument();
    expect(screen.getByTestId('feed-control-row-Request')).toBeInTheDocument();
  });

  it('turning Control off hides the control rows and the facet; Messages off keeps them', async () => {
    mocks.getMessages.mockResolvedValue([msg(1, BASE + 100)]);
    feedControl(packet(92));
    renderFeed();
    await screen.findByText(/hello 1/);
    const chip = (name: string) => screen.getByRole('button', { name });
    fireEvent.click(chip(i18n.t('feed.filters.control')));
    expect(screen.queryByTestId('feed-control-row-Request')).not.toBeInTheDocument();
    expect(screen.queryByTestId('feed-node-all')).not.toBeInTheDocument();
    fireEvent.click(chip(i18n.t('feed.filters.control')));
    mocks.getMessages.mockClear();
    fireEvent.click(chip(i18n.t('feed.filters.messages')));
    expect(screen.getByTestId('feed-control-row-Request')).toBeInTheDocument();
    expect(screen.queryByText(/hello 1/)).not.toBeInTheDocument();
    expect(mocks.getMessages).not.toHaveBeenCalled();
  });
});

describe('feed node filter is never a hidden filter', () => {
  it('ignores a stored repeater node while Control is off, so messages show', async () => {
    mocks.getMessages.mockResolvedValue([msg(1, BASE + 100)]);
    feedControl(packet(93));
    renderFeed();
    await screen.findByText(/hello 1/);
    fireEvent.click(screen.getAllByTestId('feed-node-repeater')[0]);
    expect(screen.queryByText(/hello 1/)).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: i18n.t('feed.filters.control') }));
    expect(screen.getByText(/hello 1/)).toBeInTheDocument();
    expect(screen.queryByTestId('feed-node-all')).not.toBeInTheDocument();
  });

  it('does the same after a reload from localStorage, for a repeater and a channel node', async () => {
    mocks.getMessages.mockResolvedValue([msg(1, BASE + 100), msg(2, BASE + 200, 'FF'.repeat(16))]);
    feedControl(packet(94), groupDataPacket(95));
    for (const node of ['contact:' + REPEATER_KEY, `channel:${CHANNEL_KEY}`]) {
      localStorage.setItem(
        'meshloom-feed-filters',
        JSON.stringify({ node, sources: { messages: true, control: false, events: false } })
      );
      const view = renderFeed();
      await screen.findByText(/hello 1/);
      expect(screen.getByText(/hello 2/)).toBeInTheDocument();
      view.unmount();
    }
  });

  it('shows an active node with a one-click clear, and clears it', async () => {
    mocks.getMessages.mockResolvedValue([msg(1, BASE + 100)]);
    feedControl(packet(96));
    renderFeed();
    await screen.findByText(/hello 1/);
    expect(screen.queryByTestId('feed-node-clear')).not.toBeInTheDocument();
    fireEvent.click(screen.getAllByTestId('feed-node-repeater')[0]);
    fireEvent.click(screen.getByTestId('feed-node-clear'));
    expect(screen.queryByTestId('feed-node-clear')).not.toBeInTheDocument();
    expect(screen.getByText(/hello 1/)).toBeInTheDocument();
  });

  it('the node is back when Control is turned on again', async () => {
    feedControl(packet(97), packet(98, { data: requestHex('aa', 'dd') }));
    localStorage.setItem(
      'meshloom-feed-filters',
      JSON.stringify({ node: 'us', sources: { messages: true, control: false, events: false } })
    );
    renderFeed();
    await screen.findByText(i18n.t('feed.emptyFiltered'));
    fireEvent.click(screen.getByRole('button', { name: i18n.t('feed.filters.control') }));
    expect(await rowsOf()).toHaveLength(1);
  });
});

describe('feed control volume', () => {
  it('decodes each observation once, however often the view renders', async () => {
    const spy = vi.spyOn(MeshCoreDecoder, 'decode');
    const view = renderFeed();
    // Active store, packets arrive live, one at a time.
    activateFeedStore();
    for (let i = 0; i < 40; i += 1) {
      act(() => {
        recordFeedControlPacket(
          packet(100 + i, { data: requestHex('bb', (i % 256).toString(16).padStart(2, '0')) })
        );
      });
    }
    await rowsOf();
    view.rerender(<FeedView contacts={[contact()]} channels={[channel]} publicKey={LOCAL_KEY} />);
    view.rerender(
      <FeedView
        contacts={[contact(), contact({ public_key: 'dd'.repeat(32), name: 'X' })]}
        channels={[channel]}
        publicKey={LOCAL_KEY}
      />
    );
    expect(spy.mock.calls.length).toBe(40);
  });

  it('renders 500 control packets and 200 messages under the DOM cap, interleaved by time', async () => {
    const messages: Message[] = [];
    for (let i = 0; i < 200; i += 1) messages.push(msg(i + 1, BASE + 100 + i * 2));
    mocks.getMessages.mockResolvedValue([...messages].reverse());
    // Control packets are older at first, then interleave with the messages.
    const packets: RawPacket[] = [];
    for (let i = 0; i < 500; i += 1) {
      packets.push(
        packet(1000 + i, {
          timestamp: BASE + 101 + i * 2,
          data: requestHex(
            'bb',
            (i % 256).toString(16).padStart(2, '0'),
            'ab'.repeat(15) + (i % 256).toString(16).padStart(2, '0')
          ),
        })
      );
    }
    feedControl(...packets);
    renderFeed();
    await screen.findByText(/hello 1$/);
    const items = await rowsOf();
    expect(items.length).toBeGreaterThan(0);
    expect(items.length).toBeLessThanOrEqual(FEED_DOM_MAX);
    const kinds = items.map((el) =>
      (el.getAttribute('data-testid') ?? '').startsWith('feed-control') ? 'c' : 'm'
    );
    expect(kinds).toContain('c');
    expect(kinds).toContain('m');
    // 700 rows in total: the bound of 500 control packets holds.
    activateFeedStore();
  });
});

describe('feed control seeding', () => {
  it('starts from the packets the raw store holds when the view mounts', async () => {
    recordRawPacket(packet(200));
    recordRawPacket(
      packet(201, { payload_type: 'GroupText', data: floodPacketHex(0x05, 'aabbccddeeff') })
    );
    renderFeed();
    expect(await rowsOf()).toHaveLength(1);
  });
});

describe('control rows in display mode', () => {
  it('are not focusable and open nothing', async () => {
    feedControl(packet(12), groupDataPacket(15));
    renderFeed({ initialQuery: 'display=1', onSelectConversation: vi.fn() });
    await rowsOf();
    const rows = [
      screen.getByTestId('feed-control-row-Request'),
      screen.getByTestId('feed-control-row-GroupData'),
    ];
    for (const row of rows) {
      expect(row.hasAttribute('tabindex')).toBe(false);
      fireEvent.click(row);
    }
    expect(screen.queryByRole('dialog')).toBeNull();
    expect(screen.queryAllByRole('button').map((b) => b.textContent)).toEqual([
      i18n.t('feed.display.exit'),
    ]);
  });
});

describe('feed i18n', () => {
  const flatten = (obj: unknown, prefix = ''): string[] =>
    obj && typeof obj === 'object'
      ? Object.entries(obj as Record<string, unknown>).flatMap(([k, v]) =>
          flatten(v, prefix ? `${prefix}.${k}` : k)
        )
      : [prefix];

  it('keeps the w slice in parity between English and French', () => {
    expect(flatten(wEn).sort()).toEqual(flatten(wFr).sort());
    expect(flatten(wEn).length).toBeGreaterThan(2);
  });
});
