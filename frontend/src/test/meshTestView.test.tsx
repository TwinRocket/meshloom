import { render, screen, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import type { DirectoryResolveHopsResponse, PacketObserverReachResponse } from '../types';
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
});
