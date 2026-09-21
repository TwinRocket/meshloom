import { act, render, screen } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { api } from '../api';
import {
  clearReplayPackets,
  getReplayPacketKey,
  getReplayPacketKeys,
  getReplayPackets,
  isReplayActive,
  loadReplayPackets,
  resetRawPacketReplayStore,
  toReplayPacket,
  useReplayActive,
  useReplayPackets,
} from '../stores/rawPacketReplayStore';
import {
  clearRawPackets,
  getRawPackets,
  recordRawPacket,
  resetRawPacketStore,
} from '../stores/rawPacketStore';
import { getRawPacketReplayCacheKey } from '../utils/rawPacketDerivedCache';
import type { RawPacket } from '../types';

function createPacket(overrides: Partial<RawPacket> = {}): RawPacket {
  return {
    id: 11,
    observation_id: 99,
    timestamp: 1_700_000_000,
    data: 'aabb',
    payload_type: 'GroupData',
    snr: 8,
    rssi: -70,
    decrypted: false,
    decrypted_info: null,
    ...overrides,
  };
}

describe('rawPacketReplayStore', () => {
  beforeEach(() => {
    resetRawPacketStore();
    resetRawPacketReplayStore();
  });

  it('keys every replay packet as hist-{dbId}', () => {
    const packet = createPacket({ id: 42, observation_id: 7 });
    act(() => loadReplayPackets([packet]));

    expect(getReplayPacketKey(packet)).toBe('hist-42');
    expect(getReplayPacketKeys()).toEqual(['hist-42']);
    expect(getRawPacketReplayCacheKey(42)).toBe('hist-42');
    expect(getReplayPackets()[0].observation_id).toBeUndefined();
  });

  it('never invents RSSI or SNR on history rows', () => {
    const normalized = toReplayPacket(
      createPacket({ rssi: -55, snr: 12, observation_id: 3, packet_hash: 'deadbeef' })
    );

    expect(normalized.rssi).toBeNull();
    expect(normalized.snr).toBeNull();
    expect(normalized.observation_id).toBeUndefined();
    expect(normalized.packet_hash).toBe('deadbeef');
  });

  it('leaves the live store untouched when loading replay', () => {
    act(() => recordRawPacket(createPacket({ id: 1, observation_id: 1, data: 'aa11' })));
    const liveBefore = getRawPackets();

    act(() => loadReplayPackets([createPacket({ id: 88, data: 'bb22' })]));

    expect(getRawPackets()).toBe(liveBefore);
    expect(getRawPackets()).toHaveLength(1);
    expect(getRawPackets()[0].data).toBe('aa11');
    expect(getReplayPackets()).toHaveLength(1);
    expect(getReplayPackets()[0].data).toBe('bb22');
    expect(isReplayActive()).toBe(true);
  });

  it('does not wipe replay when clearRawPackets runs', () => {
    act(() => {
      recordRawPacket(createPacket({ id: 1, observation_id: 1 }));
      loadReplayPackets([createPacket({ id: 77, data: 'cc33' })]);
      clearRawPackets();
    });

    expect(getRawPackets()).toEqual([]);
    expect(getReplayPackets()).toHaveLength(1);
    expect(getReplayPacketKeys()).toEqual(['hist-77']);
    expect(isReplayActive()).toBe(true);
  });

  it('notifies subscribers and clears replay independently of live', () => {
    function ReplayCount() {
      const active = useReplayActive();
      const replayPackets = useReplayPackets();
      return <span data-testid="replay">{active ? replayPackets.length : 'off'}</span>;
    }
    render(<ReplayCount />);
    expect(screen.getByTestId('replay').textContent).toBe('off');

    act(() => loadReplayPackets([createPacket({ id: 5 })]));
    expect(screen.getByTestId('replay').textContent).toBe('1');

    act(() => clearReplayPackets());
    expect(screen.getByTestId('replay').textContent).toBe('off');
    expect(getRawPackets()).toEqual([]);
  });
});

describe('getPacketsHistory client', () => {
  afterEach(() => {
    vi.restoreAllMocks();
  });

  it('GETs /packets/history and never /packets/{id}', async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      json: () =>
        Promise.resolve({
          items: [],
          total: 0,
          truncated: false,
          scanned: 0,
        }),
    });
    global.fetch = fetchMock;

    await api.getPacketsHistory({
      payload_type: 'GROUP_DATA',
      since: 10,
      until: 20,
      limit: 100,
      after_id: 7,
    });

    expect(fetchMock).toHaveBeenCalledTimes(1);
    const url = String(fetchMock.mock.calls[0][0]);
    expect(url).toContain('/packets/history?');
    expect(url).toContain('payload_type=GROUP_DATA');
    expect(url).not.toMatch(/\/packets\/history\/\d/);
    expect(url).not.toMatch(/\/packets\/(?!history)\d/);
  });
});
