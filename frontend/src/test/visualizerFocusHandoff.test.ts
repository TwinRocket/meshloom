import { afterEach, describe, expect, it } from 'vitest';

import type { RawPacket } from '../types';
import {
  consumeVisualizerFocusHandoff,
  findVisualizerFocusPacket,
  peekVisualizerFocusHandoff,
  resetVisualizerFocusHandoff,
  setVisualizerFocusHandoff,
  VISUALIZER_FOCUS_FILTER_IDS_KEY,
  VISUALIZER_FOCUS_OBSERVATION_KEY,
  VISUALIZER_FOCUS_PACKET_HASH_KEY,
} from '../utils/visualizerFocusHandoff';

function createPacket(overrides: Partial<RawPacket> = {}): RawPacket {
  return {
    id: 1,
    timestamp: 1700000000,
    data: 'aabbccdd',
    payload_type: 'TEXT',
    snr: null,
    rssi: null,
    decrypted: false,
    decrypted_info: null,
    ...overrides,
  };
}

afterEach(() => {
  resetVisualizerFocusHandoff();
  sessionStorage.clear();
});

describe('setVisualizerFocusHandoff', () => {
  it('stores observation and optional ids only — never the packet buffer', () => {
    setVisualizerFocusHandoff({
      observationKey: 'obs-12',
      packetHash: '19d68fe91e75c7de',
      filterIds: ['GROUP_TEXT', 'TEXT'],
    });

    expect(sessionStorage.getItem(VISUALIZER_FOCUS_OBSERVATION_KEY)).toBe('obs-12');
    expect(sessionStorage.getItem(VISUALIZER_FOCUS_PACKET_HASH_KEY)).toBe('19d68fe91e75c7de');
    expect(sessionStorage.getItem(VISUALIZER_FOCUS_FILTER_IDS_KEY)).toBe('["GROUP_TEXT","TEXT"]');

    const dumped = JSON.stringify({
      observation: sessionStorage.getItem(VISUALIZER_FOCUS_OBSERVATION_KEY),
      packetHash: sessionStorage.getItem(VISUALIZER_FOCUS_PACKET_HASH_KEY),
      filterIds: sessionStorage.getItem(VISUALIZER_FOCUS_FILTER_IDS_KEY),
    });
    expect(dumped).not.toContain('aabbccdd');
    expect(dumped).not.toContain('payload');
  });

  it('normalizes a bare observation id to obs-*', () => {
    setVisualizerFocusHandoff({ observationKey: '21' });
    expect(sessionStorage.getItem(VISUALIZER_FOCUS_OBSERVATION_KEY)).toBe('obs-21');
  });
});

describe('consumeVisualizerFocusHandoff', () => {
  it('returns the handoff and clears sessionStorage', () => {
    setVisualizerFocusHandoff({ observationKey: 'obs-3', packetHash: 'abcd' });

    const first = consumeVisualizerFocusHandoff();
    expect(first).toEqual({ observationKey: 'obs-3', packetHash: 'abcd' });
    expect(sessionStorage.getItem(VISUALIZER_FOCUS_OBSERVATION_KEY)).toBeNull();
    expect(sessionStorage.getItem(VISUALIZER_FOCUS_PACKET_HASH_KEY)).toBeNull();

    const second = consumeVisualizerFocusHandoff();
    expect(second).toEqual({ observationKey: 'obs-3', packetHash: 'abcd' });
  });

  it('returns null when nothing was handed off', () => {
    expect(consumeVisualizerFocusHandoff()).toBeNull();
    expect(peekVisualizerFocusHandoff()).toBeNull();
  });
});

describe('findVisualizerFocusPacket', () => {
  it('matches a live observation and ignores a missing one', () => {
    const live = createPacket({ id: 4, observation_id: 9, packet_hash: 'ff00' });
    const handoff = { observationKey: 'obs-9' };

    expect(findVisualizerFocusPacket([live], handoff)).toBe(live);
    expect(findVisualizerFocusPacket([], handoff)).toBeNull();
    expect(findVisualizerFocusPacket([live], { observationKey: 'obs-404' })).toBeNull();
  });
});
