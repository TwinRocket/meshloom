import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';

import type { ComponentProps } from 'react';
import { fireEvent, render, screen } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import './eSlices';
import i18n from '../i18n';
import eEn from '../i18n/locales/slices/e.en.json';
import eFr from '../i18n/locales/slices/e.fr.json';
import {
  ControlJournalView,
  destMatchesPublicKey,
  filterControlJournalPackets,
  isControlJournalPayloadType,
} from '../components/ControlJournalView';
import { resetRawPacketStore, seedRawPacketStore } from '../stores/rawPacketStore';
import { CONTACT_TYPE_REPEATER, type Channel, type Contact, type RawPacket } from '../types';

const CHANNEL_KEY = 'aabbccddeeff00112233445566778899';
const GROUP_DATA_PACKET = '19006ed356e5b542d4bceab6dc9bc995d8225492b0';
const LOCAL_KEY = 'aa'.repeat(32);
const REPEATER_KEY = 'bb'.repeat(32);
const ANON_SENDER_KEY = 'cc'.repeat(32);

function floodPacketHex(payloadType: number, payloadHex: string): string {
  const header = ((payloadType & 0x0f) << 2) | 0x01;
  return `${header.toString(16).padStart(2, '0')}00${payloadHex}`;
}

function requestHex(dest: string, src: string): string {
  return floodPacketHex(0x00, `${dest}${src}aabb${'11'.repeat(16)}`);
}

function anonRequestHex(dest: string, senderKey: string): string {
  return floodPacketHex(0x07, `${dest}${senderKey}${'ee'.repeat(2)}${'33'.repeat(16)}`);
}

function flattenKeys(obj: unknown, prefix = ''): string[] {
  if (!obj || typeof obj !== 'object' || Array.isArray(obj)) {
    return prefix ? [prefix] : [];
  }
  return Object.entries(obj as Record<string, unknown>).flatMap(([key, value]) =>
    flattenKeys(value, prefix ? `${prefix}.${key}` : key)
  );
}

function makePacket(overrides: Partial<RawPacket> = {}): RawPacket {
  return {
    id: 1,
    observation_id: 1,
    timestamp: 1_700_000_000,
    data: requestHex('bb', 'aa'),
    payload_type: 'Request',
    snr: null,
    rssi: null,
    decrypted: false,
    decrypted_info: null,
    ...overrides,
  };
}

function makeContact(overrides: Partial<Contact> = {}): Contact {
  return {
    public_key: REPEATER_KEY,
    name: 'Tower',
    type: CONTACT_TYPE_REPEATER,
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

const channel: Channel = {
  key: CHANNEL_KEY,
  name: '#gd',
  is_hashtag: true,
  on_radio: false,
  last_read_at: null,
  favorite: false,
  muted: false,
};

function renderJournal(overrides: Partial<ComponentProps<typeof ControlJournalView>> = {}) {
  return render(
    <ControlJournalView
      contacts={[makeContact()]}
      channels={[channel]}
      onOpenContactInfo={vi.fn()}
      onSelectConversation={vi.fn()}
      publicKey={LOCAL_KEY}
      {...overrides}
    />
  );
}

describe('ControlJournalView helpers', () => {
  it('accepts only Request, Response, AnonRequest, and GroupData', () => {
    expect(isControlJournalPayloadType('Request')).toBe(true);
    expect(isControlJournalPayloadType('REQ')).toBe(true);
    expect(isControlJournalPayloadType('Response')).toBe(true);
    expect(isControlJournalPayloadType('AnonRequest')).toBe(true);
    expect(isControlJournalPayloadType('GroupData')).toBe(true);
    expect(isControlJournalPayloadType('GroupText')).toBe(false);
    expect(isControlJournalPayloadType('Advert')).toBe(false);
    expect(isControlJournalPayloadType('Ack')).toBe(false);
  });

  it('filters the store down to those four types', () => {
    const kept = filterControlJournalPackets([
      makePacket({ id: 1, payload_type: 'Request' }),
      makePacket({ id: 2, payload_type: 'GROUP_TEXT', data: '1500aabb' }),
      makePacket({
        id: 3,
        payload_type: 'GroupData',
        data: GROUP_DATA_PACKET,
      }),
      makePacket({ id: 4, payload_type: 'Advert', data: floodPacketHex(0x04, 'aa') }),
    ]);

    expect(kept.map((packet) => packet.id)).toEqual([1, 3]);
  });

  it('matches Us from the public-key prefix or first byte', () => {
    expect(destMatchesPublicKey('aa', LOCAL_KEY)).toBe(true);
    expect(destMatchesPublicKey('aaaaaaaaaaaa', LOCAL_KEY)).toBe(true);
    expect(destMatchesPublicKey('bb', LOCAL_KEY)).toBe(false);
  });
});

describe('ControlJournalView', () => {
  beforeEach(() => {
    resetRawPacketStore();
  });

  it('subscribes itself; ConversationPane and App ancestors do not', () => {
    const read = (rel: string) => readFileSync(resolve(__dirname, rel), 'utf8');
    expect(read('../components/ControlJournalView.tsx')).toMatch(/useRawPackets/);
    expect(read('../components/ConversationPane.tsx')).not.toMatch(/useRawPackets/);
    expect(read('../App.tsx')).not.toMatch(/useRawPackets/);
    expect(read('../components/AppShell.tsx')).not.toMatch(/useRawPackets/);
  });

  it('ignores GroupText and other aquarium types in the journal', () => {
    seedRawPacketStore({
      packets: [
        makePacket({
          id: 10,
          observation_id: 10,
          payload_type: 'GroupText',
          data: floodPacketHex(0x05, 'aabbccddeeff'),
        }),
        makePacket({
          id: 11,
          observation_id: 11,
          payload_type: 'Request',
          data: requestHex('bb', 'aa'),
        }),
      ],
    });

    renderJournal();

    expect(screen.getByTestId('control-journal-card-Request')).toBeInTheDocument();
    expect(screen.queryByTestId('control-journal-card-GroupText')).not.toBeInTheDocument();
    expect(screen.queryByText(/aabbccddeeff/i)).not.toBeInTheDocument();
  });

  it('shows channel and data_type on a decrypted GroupData card', () => {
    seedRawPacketStore({
      packets: [
        makePacket({
          id: 20,
          observation_id: 20,
          data: GROUP_DATA_PACKET,
          payload_type: 'GroupData',
          decrypted_info: {
            channel_name: '#gd',
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
        }),
      ],
    });

    renderJournal();

    expect(screen.getByTestId('control-journal-thread-channel')).toBeInTheDocument();
    expect(screen.getByTestId('control-journal-channel')).toHaveTextContent(
      i18n.t('controlJournal.channel', { name: '#gd' })
    );
    expect(screen.getByTestId('control-journal-data-type')).toHaveTextContent('0x00ab');
    expect(screen.getByText('sensor-ok')).toBeInTheDocument();
    expect(screen.queryByText(i18n.t('controlJournal.locked'))).not.toBeInTheDocument();
  });

  it('keeps a locked Request card useful with dest and src', () => {
    seedRawPacketStore({
      packets: [
        makePacket({
          id: 30,
          observation_id: 30,
          data: requestHex('bb', 'aa'),
          payload_type: 'Request',
        }),
      ],
    });

    renderJournal();

    expect(screen.getByTestId('control-journal-card-Request')).toBeInTheDocument();
    expect(screen.getByText(i18n.t('controlJournal.locked'))).toBeInTheDocument();
    expect(screen.getByTestId('control-journal-envelope')).toHaveTextContent(/Dest BB/i);
    expect(screen.getByTestId('control-journal-envelope')).toHaveTextContent(/Src AA/i);
    expect(screen.getAllByText('Tower').length).toBeGreaterThan(0);
  });

  it('puts dest matching our public-key prefix on the Us thread', () => {
    seedRawPacketStore({
      packets: [
        makePacket({
          id: 40,
          observation_id: 40,
          data: requestHex('aa', 'dd'),
          payload_type: 'Request',
        }),
      ],
    });

    renderJournal({ contacts: [] });

    expect(screen.getByTestId('control-journal-thread-us')).toHaveTextContent(
      i18n.t('controlJournal.usThread')
    );
    expect(screen.getByTestId('control-journal-card-Request')).toBeInTheDocument();
    expect(screen.getByTestId('control-journal-envelope')).toHaveTextContent(/Dest AA/i);
  });

  it('resolves AnonRequest sender pubkeys onto the from side', () => {
    seedRawPacketStore({
      packets: [
        makePacket({
          id: 50,
          observation_id: 50,
          data: anonRequestHex('bb', ANON_SENDER_KEY),
          payload_type: 'AnonRequest',
        }),
      ],
    });

    renderJournal({
      contacts: [
        makeContact(),
        makeContact({
          public_key: ANON_SENDER_KEY,
          name: 'Walker',
          type: 1,
        }),
      ],
    });

    expect(screen.getByTestId('control-journal-card-AnonRequest')).toBeInTheDocument();
    expect(screen.getByText('Walker')).toBeInTheDocument();
    expect(screen.getByText(i18n.t('controlJournal.subtypeLogin'))).toBeInTheDocument();
  });

  it('opens the existing inspector from a card click', () => {
    seedRawPacketStore({
      packets: [makePacket({ id: 60, observation_id: 60 })],
    });

    renderJournal();
    fireEvent.click(screen.getByTestId('control-journal-card-Request'));
    expect(screen.getByText(i18n.t('rawPacket.details'))).toBeInTheDocument();
  });

  it('keeps English and French controlJournal keys in parity', () => {
    const enKeys = flattenKeys(eEn.controlJournal).sort();
    const frKeys = flattenKeys(eFr.controlJournal).sort();
    expect(enKeys).toEqual(frKeys);
    expect(enKeys.length).toBeGreaterThan(10);
  });
});
