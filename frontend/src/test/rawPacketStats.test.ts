import { describe, expect, it } from 'vitest';

import {
  KNOWN_PAYLOAD_TYPES,
  PAYLOAD_TYPE_COLORS,
  buildPayloadTypeColorMap,
  buildRawPacketStatsSnapshot,
  getPacketTypeName,
  summarizeRawPacketForStats,
  type RawPacketStatsSessionState,
} from '../utils/rawPacketStats';
import type { RawPacket } from '../types';

const TEXT_MESSAGE_PACKET = '09046F17C47ED00A13E16AB5B94B1CC2D1A5059C6E5A6253C60D';

function floodPacketHex(payloadType: number, payloadHex = 'AABBCCDDEE'): string {
  const header = ((payloadType & 0x0f) << 2) | 0x01;
  return `${header.toString(16).padStart(2, '0')}00${payloadHex}`;
}

function packetWithData(data: string, payloadType = 'Unknown'): RawPacket {
  return {
    id: 1,
    observation_id: 1,
    timestamp: 1_700_000_000,
    data,
    payload_type: payloadType,
    snr: null,
    rssi: null,
    decrypted: false,
    decrypted_info: null,
  };
}

function createSession(
  overrides: Partial<RawPacketStatsSessionState> = {}
): RawPacketStatsSessionState {
  return {
    sessionStartedAt: 700_000,
    totalObservedPackets: 4,
    trimmedObservationCount: 0,
    observations: [
      {
        observationKey: 'obs-1',
        timestamp: 850,
        payloadType: 'Advert',
        routeType: 'Flood',
        decrypted: false,
        rssi: -68,
        snr: 7,
        sourceKey: 'AA11',
        sourceLabel: 'AA11',
        pathTokenCount: 2,
        pathSignature: '01>02',
        hopByteWidth: 1,
      },
      {
        observationKey: 'obs-2',
        timestamp: 910,
        payloadType: 'TextMessage',
        routeType: 'Direct',
        decrypted: true,
        rssi: -74,
        snr: 5,
        sourceKey: 'BB22',
        sourceLabel: 'BB22',
        pathTokenCount: 0,
        pathSignature: null,
        hopByteWidth: null,
      },
      {
        observationKey: 'obs-3',
        timestamp: 960,
        payloadType: 'Advert',
        routeType: 'Flood',
        decrypted: false,
        rssi: -64,
        snr: 8,
        sourceKey: 'AA11',
        sourceLabel: 'AA11',
        pathTokenCount: 1,
        pathSignature: '02',
        hopByteWidth: 2,
      },
      {
        observationKey: 'obs-4',
        timestamp: 990,
        payloadType: 'Ack',
        routeType: 'Direct',
        decrypted: true,
        rssi: -88,
        snr: 3,
        sourceKey: null,
        sourceLabel: null,
        pathTokenCount: 0,
        pathSignature: null,
        hopByteWidth: null,
      },
    ],
    ...overrides,
  };
}

describe('buildRawPacketStatsSnapshot', () => {
  it('prefers decrypted contact identity over one-byte sourceHash for stats bucketing', () => {
    const packet: RawPacket = {
      id: 1,
      observation_id: 10,
      timestamp: 1_700_000_000,
      data: TEXT_MESSAGE_PACKET,
      payload_type: 'TextMessage',
      snr: 4,
      rssi: -72,
      decrypted: true,
      decrypted_info: {
        channel_name: null,
        sender: 'Alpha',
        channel_key: null,
        contact_key: '0a'.repeat(32),
        sender_timestamp: null,
        message: null,
        group_data: null,
      },
    };

    const summary = summarizeRawPacketForStats(packet);

    expect(summary.sourceKey).toBe('0A'.repeat(32));
    expect(summary.sourceLabel).toBe('Alpha');
  });

  it('tags unresolved one-byte source hashes so they do not collide with full contact keys', () => {
    const packet: RawPacket = {
      id: 2,
      observation_id: 11,
      timestamp: 1_700_000_000,
      data: TEXT_MESSAGE_PACKET,
      payload_type: 'TextMessage',
      snr: 4,
      rssi: -72,
      decrypted: false,
      decrypted_info: null,
    };

    const summary = summarizeRawPacketForStats(packet);

    expect(summary.sourceKey).toBe('hash1:0A');
    expect(summary.sourceLabel).toBe('0A');
  });

  it('computes counts, rankings, and rolling-window coverage from session observations', () => {
    const stats = buildRawPacketStatsSnapshot(createSession(), '5m', 1_000);

    expect(stats.packetCount).toBe(4);
    expect(stats.uniqueSources).toBe(2);
    expect(stats.pathBearingCount).toBe(2);
    expect(stats.payloadBreakdown.slice(0, 3).map((item) => item.label)).toEqual([
      'Advert',
      'Ack',
      'TextMessage',
    ]);
    expect(stats.payloadBreakdown).toEqual(
      expect.arrayContaining([
        expect.objectContaining({ label: 'GroupText', count: 0 }),
        expect.objectContaining({ label: 'Control', count: 0 }),
        expect.objectContaining({ label: 'GroupData', count: 0 }),
        expect.objectContaining({ label: 'AnonRequest', count: 0 }),
        expect.objectContaining({ label: 'Multipart', count: 0 }),
        expect.objectContaining({ label: 'RawCustom', count: 0 }),
      ])
    );
    expect(stats.hopProfile.map((item) => item.label)).toEqual([
      '0',
      '1',
      '2-5',
      '6-10',
      '11-15',
      '16-20',
      '21-31',
      '32+',
    ]);
    expect(stats.hopProfile).toEqual(
      expect.arrayContaining([
        expect.objectContaining({ label: '0', count: 2 }),
        expect.objectContaining({ label: '1', count: 1 }),
        expect.objectContaining({ label: '2-5', count: 1 }),
        expect.objectContaining({ label: '6-10', count: 0 }),
        expect.objectContaining({ label: '11-15', count: 0 }),
        expect.objectContaining({ label: '16-20', count: 0 }),
        expect.objectContaining({ label: '21-31', count: 0 }),
        expect.objectContaining({ label: '32+', count: 0 }),
      ])
    );
    expect(stats.hopByteWidthProfile).toEqual(
      expect.arrayContaining([
        expect.objectContaining({ label: 'No path', count: 2 }),
        expect.objectContaining({ label: '1 byte / hop', count: 1 }),
        expect.objectContaining({ label: '2 bytes / hop', count: 1 }),
      ])
    );
    expect(stats.strongestNeighbors[0]).toMatchObject({ label: 'AA11', bestRssi: -64 });
    expect(stats.mostActiveNeighbors[0]).toMatchObject({ label: 'AA11', count: 2 });
    expect(stats.windowFullyCovered).toBe(true);
  });

  it('flags incomplete session coverage when detailed history has been trimmed', () => {
    const stats = buildRawPacketStatsSnapshot(
      createSession({
        trimmedObservationCount: 25,
      }),
      'session',
      1_000
    );

    expect(stats.windowFullyCovered).toBe(false);
    expect(stats.packetCount).toBe(4);
  });
});

describe('KNOWN_PAYLOAD_TYPES', () => {
  it('includes the extended payload types plus Unknown', () => {
    expect(KNOWN_PAYLOAD_TYPES).toEqual(
      expect.arrayContaining([
        'Advert',
        'GroupText',
        'TextMessage',
        'Ack',
        'Request',
        'Response',
        'Trace',
        'Path',
        'Control',
        'GroupData',
        'AnonRequest',
        'Multipart',
        'RawCustom',
        'Unknown',
      ])
    );
    expect(KNOWN_PAYLOAD_TYPES[KNOWN_PAYLOAD_TYPES.length - 1]).toBe('Unknown');
    expect(new Set(KNOWN_PAYLOAD_TYPES).size).toBe(KNOWN_PAYLOAD_TYPES.length);
  });
});

describe('getPacketTypeName', () => {
  it('names GroupData, AnonRequest, Multipart, and RawCustom instead of collapsing them', () => {
    expect(getPacketTypeName(packetWithData(floodPacketHex(0x06)))).toBe('GroupData');
    expect(getPacketTypeName(packetWithData(floodPacketHex(0x07)))).toBe('AnonRequest');
    expect(getPacketTypeName(packetWithData(floodPacketHex(0x0a)))).toBe('Multipart');
    expect(getPacketTypeName(packetWithData(floodPacketHex(0x0f)))).toBe('RawCustom');
  });

  it('returns Unknown for reserved 0x0C–0x0E and invalid packets', () => {
    expect(getPacketTypeName(packetWithData(floodPacketHex(0x0c)))).toBe('Unknown');
    expect(getPacketTypeName(packetWithData(floodPacketHex(0x0d)))).toBe('Unknown');
    expect(getPacketTypeName(packetWithData(floodPacketHex(0x0e)))).toBe('Unknown');
    expect(getPacketTypeName(packetWithData('00'))).toBe('Unknown');
    expect(getPacketTypeName(packetWithData('not-hex'))).toBe('Unknown');
  });

  it('still names existing well-formed packets', () => {
    expect(getPacketTypeName(packetWithData(TEXT_MESSAGE_PACKET, 'TextMessage'))).toBe(
      'TextMessage'
    );
  });
});

describe('payload type colors', () => {
  it('covers every known payload type with a unique stable color', () => {
    const colorMap = buildPayloadTypeColorMap();
    const colors = KNOWN_PAYLOAD_TYPES.map((type) => PAYLOAD_TYPE_COLORS[type]);

    expect(Object.keys(PAYLOAD_TYPE_COLORS).sort()).toEqual([...KNOWN_PAYLOAD_TYPES].sort());
    for (const type of KNOWN_PAYLOAD_TYPES) {
      expect(PAYLOAD_TYPE_COLORS[type]).toMatch(/^#[0-9a-f]{6}$/);
      expect(colorMap.get(type)).toBe(PAYLOAD_TYPE_COLORS[type]);
    }
    expect(new Set(colors).size).toBe(KNOWN_PAYLOAD_TYPES.length);
    expect(colorMap.size).toBe(KNOWN_PAYLOAD_TYPES.length);
  });

  it('falls back to Unknown for names outside the known set', () => {
    const colorMap = buildPayloadTypeColorMap(['Advert', 'NotAType']);
    expect(colorMap.get('Advert')).toBe(PAYLOAD_TYPE_COLORS.Advert);
    expect(colorMap.get('NotAType')).toBe(PAYLOAD_TYPE_COLORS.Unknown);
  });
});
