import { beforeEach, describe, expect, it, vi } from 'vitest';
import { MeshCoreDecoder } from '@michaelhart/meshcore-decoder';

import {
  buildJournalEntries,
  canonicalControlJournalType,
  clearControlJournalCache,
  destMatchesPublicKey,
  filterControlJournalPackets,
  getControlJournalCacheSize,
  isControlJournalPayloadType,
  pairRequestsAndResponses,
  type JournalEntry,
} from '../utils/controlJournal';
import { CONTACT_TYPE_REPEATER, type Contact, type RawPacket } from '../types';

const GROUP_DATA_PACKET = '19006ed356e5b542d4bceab6dc9bc995d8225492b0';
const LOCAL_KEY = 'aa'.repeat(32);
const REPEATER_KEY = 'bb'.repeat(32);

function floodPacketHex(payloadType: number, payloadHex: string): string {
  const header = ((payloadType & 0x0f) << 2) | 0x01;
  return `${header.toString(16).padStart(2, '0')}00${payloadHex}`;
}

function requestHex(dest: string, src: string): string {
  return floodPacketHex(0x00, `${dest}${src}aabb${'11'.repeat(16)}`);
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

beforeEach(() => {
  clearControlJournalCache();
  vi.restoreAllMocks();
});

describe('control journal helpers', () => {
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

describe('canonicalControlJournalType', () => {
  it('folds aliases and separators', () => {
    expect(canonicalControlJournalType('anon_req')).toBe('AnonRequest');
    expect(canonicalControlJournalType('GRP-DATA')).toBe('GroupData');
    expect(canonicalControlJournalType('Advert')).toBeNull();
  });
});

describe('pairRequestsAndResponses', () => {
  const base = buildJournalEntries([makePacket()], [makeContact()], [], LOCAL_KEY)[0];
  const entry = (key: string, type: 'Request' | 'Response', tag: string | null): JournalEntry => ({
    ...base,
    observationKey: key,
    journalType: type,
    correlationTag: tag,
  });

  it('pairs a request and a response sharing a tag, without mutating its input', () => {
    const input = [entry('obs-1', 'Request', 'ab'), entry('obs-2', 'Response', 'ab')];
    const paired = pairRequestsAndResponses(input);
    expect(paired.map((e) => e.pairedKey)).toEqual(['obs-2', 'obs-1']);
    expect(input.map((e) => e.pairedKey)).toEqual([null, null]);
  });

  it('leaves unmatched and untagged entries unpaired', () => {
    const paired = pairRequestsAndResponses([
      entry('obs-1', 'Request', 'ab'),
      entry('obs-2', 'Response', 'cd'),
      entry('obs-3', 'Request', null),
    ]);
    expect(paired.every((e) => e.pairedKey === null)).toBe(true);
  });
});

describe('decode cache', () => {
  const packets = Array.from({ length: 50 }, (_, i) =>
    makePacket({
      id: 100 + i,
      observation_id: 100 + i,
      timestamp: 1_700_000_000 + i,
      data: requestHex('bb', (i % 200).toString(16).padStart(2, '0')),
    })
  );

  it('decodes each observation once across 20 successive frames', () => {
    const spy = vi.spyOn(MeshCoreDecoder, 'decode');
    const contacts = [makeContact()];
    for (let frame = 1; frame <= 20; frame += 1) {
      const buffer = packets.slice(0, Math.ceil((frame / 20) * packets.length));
      const entries = buildJournalEntries(
        filterControlJournalPackets(buffer),
        contacts,
        [],
        LOCAL_KEY
      );
      expect(entries).toHaveLength(buffer.length);
    }
    expect(spy).toHaveBeenCalledTimes(50);
  });

  it('does not decode again when contacts change, and re-resolves them', () => {
    const spy = vi.spyOn(MeshCoreDecoder, 'decode');
    const journal = filterControlJournalPackets(packets.slice(0, 5));
    expect(buildJournalEntries(journal, [], [], LOCAL_KEY)[0].threadKind).toBe('unresolved');
    expect(buildJournalEntries(journal, [makeContact()], [], LOCAL_KEY)[0].threadKind).toBe(
      'repeater'
    );
    expect(spy).toHaveBeenCalledTimes(5);
  });

  it('decodes again when an observation key is reused with other bytes', () => {
    const spy = vi.spyOn(MeshCoreDecoder, 'decode');
    filterControlJournalPackets([makePacket()]);
    filterControlJournalPackets([makePacket({ data: requestHex('cc', 'dd') })]);
    expect(spy).toHaveBeenCalledTimes(2);
  });

  it('follows the buffer: observations that left it are evicted', () => {
    const journal = filterControlJournalPackets(packets);
    buildJournalEntries(journal, [makeContact()], [], LOCAL_KEY);
    expect(getControlJournalCacheSize()).toEqual({ decoded: 50, entries: 50 });
    const tail = filterControlJournalPackets(packets.slice(40));
    buildJournalEntries(tail, [makeContact()], [], LOCAL_KEY);
    expect(getControlJournalCacheSize()).toEqual({ decoded: 10, entries: 10 });
  });

  it('keeps the non-journal types out of the entries but decodes them once', () => {
    const spy = vi.spyOn(MeshCoreDecoder, 'decode');
    const advert = makePacket({
      id: 9,
      observation_id: 9,
      payload_type: 'Advert',
      data: floodPacketHex(0x04, 'aa'),
    });
    const buffer = [advert, makePacket({ id: 10, observation_id: 10 })];
    for (let i = 0; i < 3; i += 1) {
      expect(filterControlJournalPackets(buffer).map((p) => p.id)).toEqual([10]);
    }
    expect(spy).toHaveBeenCalledTimes(2);
  });
});
