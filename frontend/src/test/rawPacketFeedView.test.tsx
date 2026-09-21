import { act, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import './eSlices';
import { RawPacketFeedView } from '../components/RawPacketFeedView';
import i18n from '../i18n';
import { resetRawPacketStore, seedRawPacketStore } from '../stores/rawPacketStore';
import type { RawPacketStatsSessionState } from '../utils/rawPacketStats';
import type { Channel, Contact, RawPacket } from '../types';

const GROUP_TEXT_PACKET_HEX =
  '1500E69C7A89DD0AF6A2D69F5823B88F9720731E4B887C56932BF889255D8D926D99195927144323A42DD8A158F878B518B8304DF55E80501C7D02A9FFD578D3518283156BBA257BF8413E80A237393B2E4149BBBC864371140A9BBC4E23EB9BF203EF0D029214B3E3AAC3C0295690ACDB89A28619E7E5F22C83E16073AD679D25FA904D07E5ACF1DB5A7C77D7E1719FB9AE5BF55541EE0D7F59ED890E12CF0FEED6700818';

const TEST_CHANNEL: Channel = {
  key: '7ABA109EDCF304A84433CB71D0F3AB73',
  name: '#six77',
  is_hashtag: true,
  on_radio: false,
  last_read_at: null,
  favorite: false,
  muted: false,
};

const COLLIDING_TEST_CHANNEL: Channel = {
  ...TEST_CHANNEL,
  name: '#collision',
};

function createSession(
  overrides: Partial<RawPacketStatsSessionState> = {}
): RawPacketStatsSessionState {
  return {
    sessionStartedAt: 1_700_000_000_000,
    totalObservedPackets: 3,
    trimmedObservationCount: 0,
    observations: [
      {
        observationKey: 'obs-1',
        timestamp: 1_700_000_000,
        payloadType: 'Advert',
        routeType: 'Flood',
        decrypted: false,
        rssi: -70,
        snr: 6,
        sourceKey: 'AA11',
        sourceLabel: 'AA11',
        pathTokenCount: 1,
        pathSignature: '01',
      },
      {
        observationKey: 'obs-2',
        timestamp: 1_700_000_030,
        payloadType: 'TextMessage',
        routeType: 'Direct',
        decrypted: true,
        rssi: -66,
        snr: 7,
        sourceKey: 'BB22',
        sourceLabel: 'BB22',
        pathTokenCount: 0,
        pathSignature: null,
      },
      {
        observationKey: 'obs-3',
        timestamp: 1_700_000_050,
        payloadType: 'Ack',
        routeType: 'Direct',
        decrypted: true,
        rssi: -80,
        snr: 4,
        sourceKey: 'BB22',
        sourceLabel: 'BB22',
        pathTokenCount: 0,
        pathSignature: null,
      },
    ],
    ...overrides,
  };
}

function createContact(overrides: Partial<Contact> = {}): Contact {
  return {
    public_key: 'aa11bb22cc33' + '0'.repeat(52),
    name: 'Alpha',
    type: 1,
    flags: 0,
    direct_path: null,
    direct_path_len: 0,
    direct_path_hash_mode: 0,
    last_advert: 1_700_000_000,
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

function renderView({
  packets = [],
  contacts = [],
  channels = [],
  rawPacketStatsSession = createSession(),
}: {
  packets?: RawPacket[];
  contacts?: Contact[];
  channels?: Channel[];
  rawPacketStatsSession?: RawPacketStatsSessionState;
} = {}) {
  seedRawPacketStore({ packets, statsSession: rawPacketStatsSession });
  return render(<RawPacketFeedView contacts={contacts} channels={channels} />);
}

describe('RawPacketFeedView', () => {
  beforeEach(() => {
    resetRawPacketStore();
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it('opens a stats drawer with window controls and grouped summaries', () => {
    renderView();

    expect(screen.getByText(i18n.t('rawPacket.title'))).toBeInTheDocument();
    expect(screen.queryByText(i18n.t('rawPacket.packetTypes'))).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: i18n.t('rawPacket.showStats') }));

    expect(screen.getByLabelText(i18n.t('rawPacket.windowAria'))).toBeInTheDocument();
    expect(screen.getByText(i18n.t('rawPacket.packetTypes'))).toBeInTheDocument();
    expect(screen.getByText(i18n.t('rawPacket.hopByteWidth'))).toBeInTheDocument();
    expect(screen.getByText(i18n.t('rawPacket.mostHeard'))).toBeInTheDocument();
    expect(screen.getByText(i18n.t('rawPacket.timeline'))).toBeInTheDocument();
  });

  it('shows an Autoscroll toggle that is ticked by default and can be unchecked', () => {
    renderView();

    const autoscroll = screen.getByLabelText(i18n.t('rawPacket.autoscroll')) as HTMLInputElement;
    expect(autoscroll.checked).toBe(true);

    fireEvent.click(autoscroll);
    expect(
      (screen.getByLabelText(i18n.t('rawPacket.autoscroll')) as HTMLInputElement).checked
    ).toBe(false);
  });

  it('analyzes a pasted raw packet without adding it to the live feed', () => {
    renderView({ channels: [TEST_CHANNEL] });

    fireEvent.click(screen.getByRole('button', { name: i18n.t('rawPacket.analyze') }));

    expect(screen.getByRole('heading', { name: i18n.t('rawPacket.analyze') })).toBeInTheDocument();

    fireEvent.change(screen.getByLabelText(i18n.t('rawPacket.pasteHex')), {
      target: { value: GROUP_TEXT_PACKET_HEX },
    });

    expect(screen.getByText(i18n.t('rawPacket.fullHex'))).toBeInTheDocument();
    expect(screen.getByText(i18n.t('rawPacket.packetFields'))).toBeInTheDocument();
    expect(screen.getByText(i18n.t('rawPacket.payloadFields'))).toBeInTheDocument();
  });

  it('shows stats by default on desktop', () => {
    vi.stubGlobal(
      'matchMedia',
      vi.fn().mockImplementation((query: string) => ({
        matches: query === '(min-width: 768px)',
        media: query,
        onchange: null,
        addListener: vi.fn(),
        removeListener: vi.fn(),
        addEventListener: vi.fn(),
        removeEventListener: vi.fn(),
        dispatchEvent: vi.fn(),
      }))
    );

    renderView();

    expect(screen.getByText(i18n.t('rawPacket.packetTypes'))).toBeInTheDocument();
    expect(screen.getByText(i18n.t('rawPacket.hopByteWidth'))).toBeInTheDocument();
    expect(screen.getByRole('button', { name: i18n.t('rawPacket.hideStats') })).toBeInTheDocument();
  });

  it('refreshes coverage when packet or session props update without counter deltas', () => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date('2024-01-01T00:00:30Z'));

    const initialPackets: RawPacket[] = [];
    const nextPackets: RawPacket[] = [
      {
        id: 1,
        timestamp: 1_704_067_255,
        data: '00',
        decrypted: false,
        payload_type: 'Unknown',
        rssi: null,
        snr: null,
        observation_id: 1,
        decrypted_info: null,
      },
    ];
    const initialSession = createSession({
      sessionStartedAt: Date.parse('2024-01-01T00:00:00Z'),
      totalObservedPackets: 10,
      trimmedObservationCount: 1,
      observations: [
        {
          observationKey: 'obs-1',
          timestamp: 1_704_067_220,
          payloadType: 'Advert',
          routeType: 'Flood',
          decrypted: false,
          rssi: -70,
          snr: 6,
          sourceKey: 'AA11',
          sourceLabel: 'AA11',
          pathTokenCount: 1,
          pathSignature: '01',
        },
      ],
    });

    renderView({
      packets: initialPackets,
      rawPacketStatsSession: initialSession,
      contacts: [],
    });

    fireEvent.click(screen.getByRole('button', { name: i18n.t('rawPacket.showStats') }));
    fireEvent.change(screen.getByLabelText(i18n.t('rawPacket.windowAria')), {
      target: { value: '1m' },
    });
    expect(
      screen.getByText(
        i18n.t('rawPacket.coveragePartial', {
          duration: i18n.t('rawPacket.durationSec', { count: 10 }),
        })
      )
    ).toBeInTheDocument();

    vi.setSystemTime(new Date('2024-01-01T00:01:10Z'));
    act(() => seedRawPacketStore({ packets: nextPackets, statsSession: initialSession }));
    expect(
      screen.getByText(
        i18n.t('rawPacket.coveragePartial', {
          duration: i18n.t('rawPacket.durationSec', { count: 50 }),
        })
      )
    ).toBeInTheDocument();

    vi.setSystemTime(new Date('2024-01-01T00:01:30Z'));
    const nextSession = {
      ...initialSession,
      sessionStartedAt: Date.parse('2024-01-01T00:01:00Z'),
      observations: [
        {
          ...initialSession.observations[0],
          timestamp: 1_704_067_280,
        },
      ],
    };
    act(() => seedRawPacketStore({ packets: nextPackets, statsSession: nextSession }));
    expect(
      screen.getByText(
        i18n.t('rawPacket.coveragePartial', {
          duration: i18n.t('rawPacket.durationSec', { count: 10 }),
        })
      )
    ).toBeInTheDocument();

    vi.useRealTimers();
  });

  it('resolves neighbor labels from matching contacts when identity is available', () => {
    renderView({
      rawPacketStatsSession: createSession({
        totalObservedPackets: 1,
        observations: [
          {
            observationKey: 'obs-1',
            timestamp: 1_700_000_000,
            payloadType: 'Advert',
            routeType: 'Flood',
            decrypted: false,
            rssi: -70,
            snr: 6,
            sourceKey: 'AA11BB22CC33',
            sourceLabel: 'AA11BB22CC33',
            pathTokenCount: 1,
            pathSignature: '01',
          },
        ],
      }),
      contacts: [createContact()],
    });

    fireEvent.click(screen.getByRole('button', { name: i18n.t('rawPacket.showStats') }));
    fireEvent.change(screen.getByLabelText(i18n.t('rawPacket.windowAria')), {
      target: { value: 'session' },
    });
    expect(screen.getAllByText('Alpha').length).toBeGreaterThan(0);
    expect(screen.getByText(i18n.t('rawPacket.strongestNeighbor'))).toBeInTheDocument();
    expect(
      screen.getByText(i18n.t('rawPacket.bestHeard', { rssi: '-70 dBm' }))
    ).toBeInTheDocument();
  });

  it('marks unresolved neighbor identities explicitly', () => {
    renderView({
      rawPacketStatsSession: createSession({
        totalObservedPackets: 1,
        observations: [
          {
            observationKey: 'obs-1',
            timestamp: 1_700_000_000,
            payloadType: 'Advert',
            routeType: 'Flood',
            decrypted: false,
            rssi: -70,
            snr: 6,
            sourceKey: 'DEADBEEF1234',
            sourceLabel: 'DEADBEEF1234',
            pathTokenCount: 1,
            pathSignature: '01',
          },
        ],
      }),
      contacts: [],
    });

    fireEvent.click(screen.getByRole('button', { name: i18n.t('rawPacket.showStats') }));
    fireEvent.change(screen.getByLabelText(i18n.t('rawPacket.windowAria')), {
      target: { value: 'session' },
    });
    expect(screen.getAllByText(i18n.t('rawPacket.identityUnresolved')).length).toBeGreaterThan(0);
  });

  it('collapses uniquely resolved hash buckets into the same visible contact row', () => {
    const alphaContact = createContact({
      public_key: 'aa11bb22cc33' + '0'.repeat(52),
      name: 'Alpha',
    });

    renderView({
      rawPacketStatsSession: createSession({
        totalObservedPackets: 2,
        observations: [
          {
            observationKey: 'obs-1',
            timestamp: 1_700_000_000,
            payloadType: 'TextMessage',
            routeType: 'Direct',
            decrypted: true,
            rssi: -70,
            snr: 6,
            sourceKey: 'hash1:AA',
            sourceLabel: 'AA',
            pathTokenCount: 0,
            pathSignature: null,
          },
          {
            observationKey: 'obs-2',
            timestamp: 1_700_000_030,
            payloadType: 'TextMessage',
            routeType: 'Direct',
            decrypted: true,
            rssi: -67,
            snr: 7,
            sourceKey: alphaContact.public_key.toUpperCase(),
            sourceLabel: alphaContact.public_key.slice(0, 12).toUpperCase(),
            pathTokenCount: 0,
            pathSignature: null,
          },
        ],
      }),
      contacts: [alphaContact],
    });

    fireEvent.click(screen.getByRole('button', { name: i18n.t('rawPacket.showStats') }));
    fireEvent.change(screen.getByLabelText(i18n.t('rawPacket.windowAria')), {
      target: { value: 'session' },
    });

    expect(screen.getAllByText('Alpha').length).toBeGreaterThan(0);
    expect(screen.queryByText(i18n.t('rawPacket.identityUnresolved'))).not.toBeInTheDocument();
  });

  describe('hex filter', () => {
    function makePacket(id: number, data: string): RawPacket {
      return {
        id,
        observation_id: id,
        timestamp: 1_700_000_000 + id,
        data,
        decrypted: false,
        payload_type: 'Unknown',
        rssi: null,
        snr: null,
        decrypted_info: null,
      };
    }

    const HEX_FILTER_LABEL = i18n.t('rawPacket.hexAria');

    it('filters the feed to packets whose raw hex contains the query', () => {
      renderView({ packets: [makePacket(1, 'aa11bb22'), makePacket(2, 'cc33dd44')] });

      expect(screen.getByText('AA11BB22')).toBeInTheDocument();
      expect(screen.getByText('CC33DD44')).toBeInTheDocument();

      fireEvent.change(screen.getByLabelText(HEX_FILTER_LABEL), { target: { value: 'aa11' } });

      expect(screen.getByText('AA11BB22')).toBeInTheDocument();
      expect(screen.queryByText('CC33DD44')).not.toBeInTheDocument();
      expect(screen.getByText(/1\s*\/\s*2/)).toBeInTheDocument();
    });

    it('normalizes 0x prefix, colons, whitespace, and case in the query', () => {
      renderView({ packets: [makePacket(1, 'aabbcc'), makePacket(2, 'ddeeff')] });

      fireEvent.change(screen.getByLabelText(HEX_FILTER_LABEL), {
        target: { value: '0xAA:BB CC' },
      });

      expect(screen.getByText('AABBCC')).toBeInTheDocument();
      expect(screen.queryByText('DDEEFF')).not.toBeInTheDocument();
    });

    it('shows a hint and matches nothing for a non-hex query', () => {
      renderView({ packets: [makePacket(1, 'aabbcc')] });

      fireEvent.change(screen.getByLabelText(HEX_FILTER_LABEL), { target: { value: 'zzz' } });

      expect(screen.getByText(i18n.t('rawPacket.hexOnly'))).toBeInTheDocument();
      expect(screen.queryByText('AABBCC')).not.toBeInTheDocument();
      expect(screen.getByText(i18n.t('rawPacket.empty'))).toBeInTheDocument();
    });

    it('restores the full feed when the filter is cleared', () => {
      renderView({ packets: [makePacket(1, 'aabbcc'), makePacket(2, 'ddeeff')] });

      fireEvent.change(screen.getByLabelText(HEX_FILTER_LABEL), { target: { value: 'aa' } });
      expect(screen.queryByText('DDEEFF')).not.toBeInTheDocument();

      fireEvent.click(screen.getByRole('button', { name: i18n.t('rawPacket.clearHex') }));

      expect(screen.getByText('AABBCC')).toBeInTheDocument();
      expect(screen.getByText('DDEEFF')).toBeInTheDocument();
    });
  });

  it('labels new payload types instead of collapsing them to Unknown', () => {
    renderView();

    expect(screen.getByLabelText(i18n.t('rawPacket.type.groupData'))).toBeInTheDocument();
    expect(screen.getByLabelText(i18n.t('rawPacket.type.anonRequest'))).toBeInTheDocument();
    expect(screen.getByLabelText(i18n.t('rawPacket.type.multipart'))).toBeInTheDocument();
    expect(screen.getByLabelText(i18n.t('rawPacket.type.rawCustom'))).toBeInTheDocument();
    expect(screen.queryByLabelText('Unknown')).not.toBeInTheDocument();
    expect(screen.getByLabelText(i18n.t('rawPacket.type.unknown'))).toBeInTheDocument();
  });

  it('filters the feed by route and text', () => {
    const floodAck = '0D00AABBCCDD';
    const directAck = '0E00AABBCCDD';
    renderView({
      packets: [
        {
          id: 1,
          observation_id: 1,
          timestamp: 1_700_000_001,
          data: floodAck,
          decrypted: false,
          payload_type: 'Ack',
          rssi: null,
          snr: null,
          decrypted_info: {
            channel_name: '#alpha',
            sender: 'Alpha',
            channel_key: 'aa',
            contact_key: null,
            sender_timestamp: null,
            message: null,
            group_data: null,
          },
        },
        {
          id: 2,
          observation_id: 2,
          timestamp: 1_700_000_002,
          data: directAck,
          decrypted: true,
          payload_type: 'Ack',
          rssi: null,
          snr: null,
          decrypted_info: {
            channel_name: '#beta',
            sender: 'Beta',
            channel_key: 'bb',
            contact_key: null,
            sender_timestamp: null,
            message: 'hello',
            group_data: null,
          },
        },
      ],
    });

    expect(screen.getByText(floodAck)).toBeInTheDocument();
    expect(screen.getByText(directAck)).toBeInTheDocument();

    fireEvent.click(screen.getByLabelText(i18n.t('rawPacket.route.flood')));
    expect(screen.queryByText(floodAck)).not.toBeInTheDocument();
    expect(screen.getByText(directAck)).toBeInTheDocument();

    fireEvent.click(screen.getByLabelText(i18n.t('rawPacket.route.flood')));
    fireEvent.change(screen.getByLabelText(i18n.t('rawPacket.textAria')), {
      target: { value: 'alpha' },
    });
    expect(screen.getByText(floodAck)).toBeInTheDocument();
    expect(screen.queryByText(directAck)).not.toBeInTheDocument();
  });

  it('filters crypto state using GroupText and GroupData, not cleartext Acks', () => {
    const groupDataPacket = '19006ed356e5b542d4bceab6dc9bc995d8225492b0';
    renderView({
      packets: [
        {
          id: 1,
          observation_id: 1,
          timestamp: 1_700_000_001,
          data: GROUP_TEXT_PACKET_HEX,
          decrypted: false,
          payload_type: 'GroupText',
          rssi: null,
          snr: null,
          decrypted_info: null,
        },
        {
          id: 2,
          observation_id: 2,
          timestamp: 1_700_000_002,
          data: groupDataPacket,
          decrypted: false,
          payload_type: 'GroupData',
          rssi: null,
          snr: null,
          decrypted_info: {
            channel_name: '#gd',
            sender: null,
            channel_key: 'aabbccddeeff00112233445566778899',
            contact_key: null,
            sender_timestamp: null,
            message: null,
            group_data: {
              data_type: 0x00ab,
              data_len: 9,
              data_hex: '73656e736f722d6f6b',
              data_text: 'sensor-ok',
            },
          },
        },
      ],
    });

    expect(screen.getByText(GROUP_TEXT_PACKET_HEX.toUpperCase())).toBeInTheDocument();
    expect(screen.getByText(groupDataPacket.toUpperCase())).toBeInTheDocument();

    fireEvent.change(screen.getByLabelText(i18n.t('rawPacket.cryptoAria')), {
      target: { value: 'decrypted' },
    });
    expect(screen.queryByText(GROUP_TEXT_PACKET_HEX.toUpperCase())).not.toBeInTheDocument();
    expect(screen.getByText(groupDataPacket.toUpperCase())).toBeInTheDocument();

    fireEvent.change(screen.getByLabelText(i18n.t('rawPacket.cryptoAria')), {
      target: { value: 'encrypted' },
    });
    expect(screen.getByText(GROUP_TEXT_PACKET_HEX.toUpperCase())).toBeInTheDocument();
    expect(screen.queryByText(groupDataPacket.toUpperCase())).not.toBeInTheDocument();
  });

  it('opens a packet detail modal from the raw feed and decrypts channel messages when a key is loaded', () => {
    renderView({
      packets: [
        {
          id: 1,
          observation_id: 10,
          timestamp: 1_700_000_000,
          data: GROUP_TEXT_PACKET_HEX,
          decrypted: false,
          payload_type: 'GroupText',
          rssi: -72,
          snr: 5.5,
          decrypted_info: null,
        },
      ],
      channels: [TEST_CHANNEL],
    });

    fireEvent.click(
      screen.getByRole('button', {
        name: new RegExp(i18n.t('rawPacket.gtFrom', { sender: 'Flightless🥝', path: '' }), 'i'),
      })
    );

    expect(screen.getByText(i18n.t('rawPacket.details'))).toBeInTheDocument();
    expect(screen.getByText(i18n.t('rawPacket.payloadFields'))).toBeInTheDocument();
    expect(screen.getByText(i18n.t('rawPacket.fullHex'))).toBeInTheDocument();
    expect(screen.getByText('#six77')).toBeInTheDocument();
    expect(
      screen.getByText(
        i18n.t('rawPacket.packetPrimary', {
          bytes: GROUP_TEXT_PACKET_HEX.length / 2,
          state: i18n.t('rawPacket.decrypted'),
        })
      )
    ).toBeInTheDocument();
    expect(
      screen.getByText(i18n.t('rawPacket.sender', { name: 'Flightless🥝' }))
    ).toBeInTheDocument();
    expect(
      screen.getByText(/hello there; this hashtag room is essentially public/i)
    ).toBeInTheDocument();
  });

  it('does not guess a channel name when multiple loaded channels collide on the group hash', () => {
    renderView({
      packets: [
        {
          id: 1,
          observation_id: 10,
          timestamp: 1_700_000_000,
          data: GROUP_TEXT_PACKET_HEX,
          decrypted: false,
          payload_type: 'GroupText',
          rssi: -72,
          snr: 5.5,
          decrypted_info: null,
        },
      ],
      channels: [TEST_CHANNEL, COLLIDING_TEST_CHANNEL],
    });

    fireEvent.click(
      screen.getByRole('button', {
        name: new RegExp(i18n.t('rawPacket.gtFrom', { sender: 'Flightless🥝', path: '' }), 'i'),
      })
    );

    expect(screen.getByText(i18n.t('rawPacket.channelHash', { hash: 'E6' }))).toBeInTheDocument();
    expect(screen.queryByText('#six77')).not.toBeInTheDocument();
    expect(screen.queryByText('#collision')).not.toBeInTheDocument();
  });
});
