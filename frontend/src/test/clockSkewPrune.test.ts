import { describe, expect, it, vi } from 'vitest';
import { PayloadType } from '@michaelhart/meshcore-decoder';

import {
  buildPacketNetworkContext,
  createPacketNetworkState,
  ingestPacketIntoPacketNetwork,
  prunePacketNetworkState,
} from '../networkGraph/packetNetworkGraph';
import type { Contact, RadioConfig, RawPacket } from '../types';
import { getRawPackets, recordRawPacket, resetRawPacketStore } from '../stores/rawPacketStore';

const { packetFixtures } = vi.hoisted(() => ({
  packetFixtures: new Map<string, unknown>(),
}));

vi.mock('../utils/visualizerUtils', async () => {
  const actual = await vi.importActual<typeof import('../utils/visualizerUtils')>(
    '../utils/visualizerUtils'
  );

  return {
    ...actual,
    parsePacket: vi.fn(
      (hexData: string) => packetFixtures.get(hexData) ?? actual.parsePacket(hexData)
    ),
  };
});

function createConfig(publicKey: string): RadioConfig {
  return {
    public_key: publicKey,
    name: 'Me',
    lat: 0,
    lon: 0,
    tx_power: 0,
    max_tx_power: 0,
    radio: { freq: 0, bw: 0, sf: 0, cr: 0 },
    path_hash_mode: 0,
    path_hash_mode_supported: true,
    advert_location_source: 'off',
  };
}

function createContact(publicKey: string, name: string, type = 1): Contact {
  return {
    public_key: publicKey,
    name,
    type,
    flags: 0,
    direct_path: null,
    direct_path_len: 0,
    direct_path_hash_mode: 0,
    route_override_path: null,
    route_override_len: null,
    route_override_hash_mode: null,
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
}

function createPacket(data: string): RawPacket {
  return {
    id: 1,
    observation_id: 1,
    timestamp: 1_700_000_000,
    data,
    payload_type: 'TEXT',
    snr: null,
    rssi: null,
    decrypted: false,
    decrypted_info: null,
  };
}

const SERVER_NOW_S = 1_791_000_000; // server clock
const PRUNE_MS = 5 * 60 * 1000; // default pruneStaleMinutes

/** browserAheadMin > 0: the server clock is that many minutes behind the browser. */
function scenario(browserAheadMin: number, viaStore: boolean) {
  const selfKey = 'ffffffffffff0000000000000000000000000000000000000000000000000000';
  const aliceKey = 'aaaaaaaaaaaa0000000000000000000000000000000000000000000000000000';
  packetFixtures.set('skew', {
    payloadType: PayloadType.TextMessage,
    messageHash: 'skew',
    pathBytes: ['32'],
    srcHash: 'aaaaaaaaaaaa',
    dstHash: 'ffffffffffff',
    advertPubkey: null,
    groupTextSender: null,
    anonRequestPubkey: null,
  });
  const browserNow = SERVER_NOW_S * 1000 + browserAheadMin * 60_000;
  vi.useFakeTimers();
  vi.setSystemTime(browserNow);
  resetRawPacketStore();
  const state = createPacketNetworkState('Me');
  const context = buildPacketNetworkContext({
    contacts: [createContact(aliceKey, 'Alice')],
    config: createConfig(selfKey),
    repeaterAdvertPaths: [],
    splitAmbiguousByTraffic: false,
    useAdvertPathHints: false,
  });
  let packet: RawPacket = { ...createPacket('skew'), timestamp: SERVER_NOW_S };
  if (viaStore) {
    recordRawPacket(packet); // real WS path (useRealtimeAppState.onRawPacket)
    packet = getRawPackets()[getRawPackets().length - 1];
  }
  ingestPacketIntoPacketNetwork(state, context, packet);
  const before = state.nodes.size;
  const sizeAt = (afterMs: number) => {
    vi.setSystemTime(browserNow + afterMs);
    prunePacketNetworkState(state, Date.now() - PRUNE_MS);
    return state.nodes.size;
  };
  const after1s = sizeAt(1000);
  const after4min = sizeAt(4 * 60_000);
  const after6min = sizeAt(6 * 60_000);
  vi.useRealTimers();
  return { before, after1s, after4min, after6min };
}

describe('visualizer prune vs server clock', () => {
  it('server 8 min behind: graph wiped within 1 s (no receipt time: server timestamp only)', () => {
    expect(scenario(8, false)).toEqual({ before: 3, after1s: 1, after4min: 1, after6min: 1 });
  });
  it('server 8 min ahead: nodes survive 13 min instead of 5 (no receipt time: server timestamp only)', () => {
    expect(scenario(-8, false)).toEqual({ before: 3, after1s: 3, after4min: 3, after6min: 3 });
  });
  it('clocks in sync: nodes kept 5 min then pruned', () => {
    expect(scenario(0, false)).toEqual({ before: 3, after1s: 3, after4min: 3, after6min: 1 });
  });
  for (const skew of [8, -8, 30, -30]) {
    it(`fix: receipt time via store, server skew ${skew} min behaves like in sync`, () => {
      expect(scenario(skew, true)).toEqual({ before: 3, after1s: 3, after4min: 3, after6min: 1 });
    });
  }
});
