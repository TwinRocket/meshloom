import { render, screen, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import type { Contact, DirectoryResolveHopsResponse, PacketObserverReachResponse } from '../types';
import { CONTACT_TYPE_REPEATER } from '../types';
import { MESH_TEST_RUN_KEY } from '../utils/meshTest';
import i18n from '../i18n';

const apiMocks = vi.hoisted(() => ({
  getPacketObserverReach: vi.fn<(hash: string) => Promise<PacketObserverReachResponse>>(),
  resolveDirectoryHops: vi.fn<(hops: string[]) => Promise<DirectoryResolveHopsResponse>>(
    async () => ({ resolved: {} })
  ),
}));

vi.mock('../api', () => ({
  api: {
    getPacketObserverReach: (hash: string) => apiMocks.getPacketObserverReach(hash),
    resolveDirectoryHops: (hops: string[]) => apiMocks.resolveDirectoryHops(hops),
  },
  formatApiError: (err: unknown) => (err instanceof Error ? err.message : String(err)),
}));

vi.mock('../components/MeshTestMap', () => ({
  MeshTestMap: ({
    observers,
  }: {
    observers: { path: { prefix: string; lat: number | null }[] }[];
  }) => <div data-testid="mesh-test-map" data-hop-count={observers[0]?.path.length ?? 0} />,
}));

import { MeshTestView } from '../components/MeshTestView';

describe('MeshTestView', () => {
  beforeEach(() => {
    sessionStorage.clear();
    apiMocks.getPacketObserverReach.mockReset();
    apiMocks.resolveDirectoryHops.mockClear();
  });

  it('lists a hop that has no GPS, marked as having no position', async () => {
    sessionStorage.setItem(
      MESH_TEST_RUN_KEY,
      JSON.stringify({
        packetHash: 'abc',
        sentAt: Math.floor(Date.now() / 1000),
        floodScope: 'nl-gr',
        originLat: 48.85,
        originLon: 2.35,
      })
    );
    apiMocks.getPacketObserverReach.mockResolvedValue({
      directory_enabled: true,
      observer_count: 1,
      observers: [
        {
          name: 'Lyon',
          lat: 45.75,
          lon: 4.85,
          hops: 2,
          snr: 4.5,
          rssi: -90,
          role: 'repeater',
          path: ['aa11', 'bb22'],
        },
      ],
      origin_available: false,
      sealed: false,
    });

    render(<MeshTestView contacts={[]} knownRegions={['nl-gr']} radioConnected />);

    expect(await screen.findByText('BB22')).toBeInTheDocument();
    expect(screen.getByText('AA11')).toBeInTheDocument();
    await waitFor(() => {
      expect(screen.getAllByText(new RegExp(i18n.t('meshTest.noPosition'))).length).toBeGreaterThan(
        0
      );
    });
  });

  it('places a named repeater from the directory when the local contact has no GPS', async () => {
    sessionStorage.setItem(
      MESH_TEST_RUN_KEY,
      JSON.stringify({
        packetHash: 'abc',
        sentAt: Math.floor(Date.now() / 1000),
        floodScope: 'fr',
        originLat: 43.55,
        originLon: 7.02,
      })
    );
    const contact: Contact = {
      public_key: 'aa11' + 'ab'.repeat(30),
      name: 'FR06-CARROS-Village',
      type: CONTACT_TYPE_REPEATER,
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
      on_radio: true,
      favorite: false,
      last_contacted: null,
      last_read_at: null,
      first_seen: null,
    };
    apiMocks.resolveDirectoryHops.mockResolvedValue({
      resolved: {
        AA11: { name: 'Directory', source: 'corescope', hash_width: 2, lat: 43.7, lon: 7.26 },
      },
    });
    apiMocks.getPacketObserverReach.mockResolvedValue({
      directory_enabled: true,
      observer_count: 1,
      observers: [
        {
          name: 'Ear',
          public_key: contact.public_key,
          lat: null,
          lon: null,
          hops: 1,
          path: ['aa11'],
        },
      ],
      origin_available: false,
      sealed: false,
    });

    render(<MeshTestView contacts={[contact]} knownRegions={['fr']} radioConnected />);

    expect(await screen.findByText(/FR06-CARROS-Village/)).toBeInTheDocument();
    await waitFor(() => {
      expect(apiMocks.resolveDirectoryHops).toHaveBeenCalledWith(['AA11']);
    });
    const hop = screen.getByText(/FR06-CARROS-Village/).closest('li');
    expect(hop?.textContent).not.toContain(i18n.t('meshTest.noPosition'));
  });
});
