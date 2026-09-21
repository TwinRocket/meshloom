import { renderHook } from '@testing-library/react';
import { afterEach, describe, expect, it } from 'vitest';

import type { Channel, RawPacket } from '../types';
import {
  clearRawPacketDerivedCache,
  computeRawPacketDerived,
  getPacketRepeatKey,
  getRawPacketDerived,
  getRawPacketDerivedCacheKey,
  getRawPacketDerivedCacheStats,
  getRawPacketReplayCacheKey,
  mapRawPacketsDerived,
  peekRawPacketDerived,
  serializeRawPacketDerivedNamespace,
  useRawPacketDerivedCache,
} from './rawPacketDerivedCache';

const CHANNEL_KEY = 'aabbccddeeff00112233445566778899';
const GROUP_DATA_PACKET = '19006ed356e5b542d4bceab6dc9bc995d8225492b0';
const GROUP_TEXT_PACKET =
  '1500d9b5d4330c3bfc80e2114278944c79dad5760f3b1baa407d7786765eabdf97f90d9c9d';

function createPacket(overrides: Partial<RawPacket> = {}): RawPacket {
  return {
    id: 1,
    observation_id: 1,
    timestamp: 1_700_000_000,
    data: GROUP_DATA_PACKET,
    payload_type: 'GroupData',
    snr: null,
    rssi: null,
    decrypted: false,
    decrypted_info: null,
    ...overrides,
  };
}

function createChannel(key: string, name = '#sensors'): Channel {
  return {
    key,
    name,
    is_hashtag: true,
    on_radio: false,
    last_read_at: null,
    favorite: false,
    muted: false,
  };
}

afterEach(() => {
  clearRawPacketDerivedCache();
});

describe('getRawPacketDerivedCacheKey', () => {
  it('uses obs-* for live packets with observation_id', () => {
    expect(getRawPacketDerivedCacheKey(createPacket({ id: 99, observation_id: 7 }))).toBe('obs-7');
  });

  it('falls back to db-* when live observation_id is missing', () => {
    expect(getRawPacketDerivedCacheKey(createPacket({ id: 42, observation_id: undefined }))).toBe(
      'db-42'
    );
  });

  it('uses hist-{dbId} for replay, isolated from obs-*', () => {
    const packet = createPacket({ id: 5, observation_id: 5 });
    expect(getRawPacketDerivedCacheKey(packet, 'replay')).toBe('hist-5');
    expect(getRawPacketReplayCacheKey(5)).toBe('hist-5');
    expect(getRawPacketDerivedCacheKey(packet, 'live')).toBe('obs-5');
  });
});

describe('getPacketRepeatKey', () => {
  it('prefers packet_hash and falls back to storage id', () => {
    expect(getPacketRepeatKey(createPacket({ id: 9, packet_hash: 'deadbeef' }))).toBe('deadbeef');
    expect(getPacketRepeatKey(createPacket({ id: 9, packet_hash: null }))).toBe('9');
  });
});

describe('rawPacketDerivedCache', () => {
  it('returns the same object on a cache hit', () => {
    const packet = createPacket();
    const first = getRawPacketDerived(packet);
    const second = getRawPacketDerived(packet);
    const stats = getRawPacketDerivedCacheStats();

    expect(second).toBe(first);
    expect(stats.misses).toBe(1);
    expect(stats.hits).toBe(1);
    expect(stats.size).toBe(1);
    expect(first.payloadType).toBe('GroupData');
    expect(first.repeatKey).toBe('1');
  });

  it('invalidates when channel keys change', () => {
    const packet = createPacket();
    const closed = getRawPacketDerived(packet, { channelKeys: [] });
    expect(closed.clientDecoded).toBe(false);
    expect(peekRawPacketDerived('obs-1', { channelKeys: [] })).toBe(closed);

    const opened = getRawPacketDerived(packet, { channelKeys: [CHANNEL_KEY] });
    expect(opened).not.toBe(closed);
    expect(opened.clientDecoded).toBe(true);
    expect(opened.summary).toContain('0x00ab');
    expect(peekRawPacketDerived('obs-1', { channelKeys: [] })).toBeUndefined();
    expect(peekRawPacketDerived('obs-1', { channelKeys: [CHANNEL_KEY] })).toBe(opened);

    const again = getRawPacketDerived(packet, { channels: [createChannel(CHANNEL_KEY)] });
    expect(again).toBe(opened);
    expect(getRawPacketDerivedCacheStats().hits).toBe(1);
  });

  it('invalidates when the community name list identity changes', () => {
    const packet = createPacket({
      data: GROUP_TEXT_PACKET,
      payload_type: 'GroupText',
    });
    const without = getRawPacketDerived(packet, { communityNames: [] });
    expect(without.clientDecoded).toBe(false);

    const withNames = getRawPacketDerived(packet, { communityNames: ['test'] });
    expect(withNames).not.toBe(without);
    expect(withNames.clientDecoded).toBe(true);
    expect(withNames.payloadType).toBe('GroupText');
    expect(peekRawPacketDerived('obs-1', { communityNames: [] })).toBeUndefined();

    const renamed = getRawPacketDerived(packet, { communityNames: ['other'] });
    expect(renamed).not.toBe(withNames);
    expect(renamed.clientDecoded).toBe(false);
    expect(serializeRawPacketDerivedNamespace({ communityNames: ['test'] })).not.toBe(
      serializeRawPacketDerivedNamespace({ communityNames: ['other'] })
    );
  });

  it('invalidates when a generation token changes', () => {
    const packet = createPacket();
    const first = getRawPacketDerived(packet, { generation: 1, channelKeys: [CHANNEL_KEY] });
    const same = getRawPacketDerived(packet, { generation: 1, channelKeys: [CHANNEL_KEY] });
    const next = getRawPacketDerived(packet, { generation: 2, channelKeys: [CHANNEL_KEY] });

    expect(same).toBe(first);
    expect(next).not.toBe(first);
    expect(next.clientDecoded).toBe(true);
    expect(serializeRawPacketDerivedNamespace({ generation: 1, channelKeys: [CHANNEL_KEY] })).toBe(
      `gen:1|k:${CHANNEL_KEY}#n:#x:`
    );
  });

  it('does not return a stale closed hit when generation stays 1 and channelKeys appear', () => {
    const packet = createPacket();
    const closed = getRawPacketDerived(packet, { generation: 1, channelKeys: [] });
    expect(closed.clientDecoded).toBe(false);

    const opened = getRawPacketDerived(packet, { generation: 1, channelKeys: [CHANNEL_KEY] });
    expect(opened).not.toBe(closed);
    expect(opened.clientDecoded).toBe(true);
    expect(peekRawPacketDerived('obs-1', { generation: 1, channelKeys: [] })).toBeUndefined();
    expect(peekRawPacketDerived('obs-1', { generation: 1, channelKeys: [CHANNEL_KEY] })).toBe(
      opened
    );
  });

  it('sets clientDecoded without mutating packet.decrypted', () => {
    const packet = createPacket({ decrypted: false, decrypted_info: null });
    Object.freeze(packet);

    const derived = getRawPacketDerived(packet, { channelKeys: [CHANNEL_KEY] });

    expect(derived.clientDecoded).toBe(true);
    expect(derived.isOpen).toBe(true);
    expect(packet.decrypted).toBe(false);
    expect(packet.decrypted_info).toBeNull();
  });

  it('treats server group_data as clientDecoded without writing decrypted', () => {
    const packet = createPacket({
      decrypted: false,
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
    Object.freeze(packet);
    Object.freeze(packet.decrypted_info);

    const derived = computeRawPacketDerived(packet);

    expect(derived.clientDecoded).toBe(true);
    expect(packet.decrypted).toBe(false);
    expect(packet.decrypted_info?.message).toBeNull();
  });

  it('keeps hist- and obs- keys isolated', () => {
    const live = createPacket({ id: 5, observation_id: 5, packet_hash: 'aa' });
    const replay = createPacket({
      id: 5,
      observation_id: undefined,
      packet_hash: 'bb',
    });

    const liveDerived = getRawPacketDerived(live, { scope: 'live' });
    const replayDerived = getRawPacketDerived(replay, { scope: 'replay' });

    expect(liveDerived.cacheKey).toBe('obs-5');
    expect(replayDerived.cacheKey).toBe('hist-5');
    expect(liveDerived).not.toBe(replayDerived);
    expect(liveDerived.repeatKey).toBe('aa');
    expect(replayDerived.repeatKey).toBe('bb');
    expect(peekRawPacketDerived('obs-5')).toBe(liveDerived);
    expect(peekRawPacketDerived('hist-5')).toBe(replayDerived);

    expect(getRawPacketDerived(live, { scope: 'live' })).toBe(liveDerived);
    expect(getRawPacketDerived(replay, { scope: 'replay' })).toBe(replayDerived);
    expect(getRawPacketDerivedCacheStats().hits).toBe(2);
    expect(getRawPacketDerivedCacheStats().size).toBe(2);
  });

  it('retains only keys present in a mapped list', () => {
    const kept = createPacket({ id: 1, observation_id: 1 });
    const dropped = createPacket({ id: 2, observation_id: 2 });
    getRawPacketDerived(dropped);
    mapRawPacketsDerived([kept]);

    expect(peekRawPacketDerived('obs-1')).toBeDefined();
    expect(peekRawPacketDerived('obs-2')).toBeUndefined();
    expect(getRawPacketDerivedCacheStats().size).toBe(1);
  });
});

describe('useRawPacketDerivedCache', () => {
  it('maps packets through the cache without rewriting them', () => {
    const packet = createPacket({ decrypted: false });
    const { result, rerender } = renderHook(
      ({ packets, channelKeys }: { packets: RawPacket[]; channelKeys: string[] }) =>
        useRawPacketDerivedCache(packets, { channelKeys }),
      { initialProps: { packets: [packet], channelKeys: [CHANNEL_KEY] } }
    );

    expect(result.current).toHaveLength(1);
    expect(result.current[0].packet).toBe(packet);
    expect(result.current[0].clientDecoded).toBe(true);
    expect(result.current[0].cacheKey).toBe('obs-1');
    expect(packet.decrypted).toBe(false);

    const firstRow = result.current[0];
    rerender({ packets: [packet], channelKeys: [CHANNEL_KEY] });
    expect(result.current[0]).toMatchObject({
      cacheKey: firstRow.cacheKey,
      clientDecoded: true,
      payloadType: 'GroupData',
    });
    expect(result.current[0].clientDecoded).toBe(firstRow.clientDecoded);
    expect(getRawPacketDerived(packet, { channelKeys: [CHANNEL_KEY] })).toBe(
      peekRawPacketDerived('obs-1', { channelKeys: [CHANNEL_KEY] })
    );
  });
});
