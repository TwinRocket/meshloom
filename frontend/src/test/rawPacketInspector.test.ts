import { describe, expect, it } from 'vitest';
import { PayloadType } from '@michaelhart/meshcore-decoder';

import './eSlices';
import i18n from '../i18n';
import type { RawPacket } from '../types';
import {
  describeCiphertextStructure,
  decodePacketSummary,
  formatHexByHop,
  inspectRawPacketWithOptions,
  isCleartextPayloadType,
  isPacketOpen,
} from '../utils/rawPacketInspector';

const CHANNEL_KEY = 'aabbccddeeff00112233445566778899';
const GROUP_DATA_PACKET = '19006ed356e5b542d4bceab6dc9bc995d8225492b0';

function createPacket(overrides: Partial<RawPacket> = {}): RawPacket {
  return {
    id: 1,
    timestamp: 1700000000,
    data: GROUP_DATA_PACKET,
    payload_type: 'GroupData',
    snr: null,
    rssi: null,
    decrypted: false,
    decrypted_info: null,
    ...overrides,
  };
}

describe('rawPacketInspector helpers', () => {
  it('formats path hex as hop-delimited groups', () => {
    expect(formatHexByHop('A1B2C3D4E5F6', 2)).toBe('A1B2 → C3D4 → E5F6');
    expect(formatHexByHop('AABBCC', 1)).toBe('AA → BB → CC');
  });

  it('leaves non-hop-aligned hex unchanged', () => {
    expect(formatHexByHop('A1B2C3', 2)).toBe('A1B2C3');
    expect(formatHexByHop('A1B2', null)).toBe('A1B2');
  });

  it('describes undecryptable ciphertext with multiline bullets', () => {
    expect(describeCiphertextStructure(PayloadType.GroupText, 9, 'fallback')).toBe(
      i18n.t('rawPacket.ciphertextGroup', { bytes: 9 })
    );
    expect(describeCiphertextStructure(PayloadType.TextMessage, 12, 'fallback')).toBe(
      i18n.t('rawPacket.ciphertextDm', { bytes: 12 })
    );
    expect(describeCiphertextStructure(PayloadType.GroupData, 16, 'fallback')).toBe(
      i18n.t('rawPacket.ciphertextGroupData', { bytes: 16 })
    );
  });

  it('treats folded Advert/Ack/Control/Trace names as cleartext', () => {
    expect(isCleartextPayloadType('Advert')).toBe(true);
    expect(isCleartextPayloadType('ADVERT')).toBe(true);
    expect(isCleartextPayloadType('ACK')).toBe(true);
    expect(isCleartextPayloadType('Ack')).toBe(true);
    expect(isCleartextPayloadType('control')).toBe(true);
    expect(isCleartextPayloadType('Trace')).toBe(true);
    expect(isCleartextPayloadType('GroupData')).toBe(false);
    expect(isCleartextPayloadType('GROUP_TEXT')).toBe(false);
    expect(isPacketOpen('ACK', { decrypted: false }, false)).toBe(true);
    expect(isPacketOpen('GroupText', { decrypted: false }, false)).toBe(false);
    expect(isPacketOpen('GroupText', { decrypted: false }, true)).toBe(true);
  });

  it('does not mutate packet.decrypted when client-decoding GroupData', () => {
    const packet = createPacket();
    const summary = decodePacketSummary(packet, undefined, { channelKeys: [CHANNEL_KEY] });

    expect(packet.decrypted).toBe(false);
    expect(summary.clientDecoded).toBe(true);
    expect(summary.summary).toContain('0x00ab');
  });

  it('summarizes GroupData from decrypted_info.group_data without writing message', () => {
    const packet = createPacket({
      decrypted_info: {
        channel_name: '#sensors',
        sender: null,
        channel_key: CHANNEL_KEY,
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
    });

    const summary = decodePacketSummary(packet);
    expect(packet.decrypted).toBe(false);
    expect(packet.decrypted_info?.message).toBeNull();
    expect(summary.clientDecoded).toBe(true);
    expect(summary.summary).toContain('#sensors');
    expect(summary.summary).toContain('0x00ab');
  });

  it('enriches GroupData ciphertext from group_data or client parse', () => {
    const packet = createPacket({
      decrypted_info: {
        channel_name: '#sensors',
        sender: null,
        channel_key: CHANNEL_KEY,
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
    });
    const inspection = inspectRawPacketWithOptions(packet);
    const ciphertext = inspection.payloadFields.find(
      (field) => field.name === 'Ciphertext' || field.name === 'GroupData Payload'
    );

    expect(packet.decrypted_info?.message).toBeNull();
    expect(ciphertext?.decryptedMessage).toContain('#sensors');
    expect(ciphertext?.decryptedMessage).toContain('0x00ab');
    expect(ciphertext?.decryptedMessage).toContain('sensor-ok');
  });
});
