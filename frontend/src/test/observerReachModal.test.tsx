import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import type { DirectoryResolveHopsResponse, PacketObserverReachResponse } from '../types';

const apiMocks = vi.hoisted(() => ({
  getPacketObserverReach: vi.fn<(hash: string) => Promise<PacketObserverReachResponse>>(
    async () => ({
      directory_enabled: true,
      observer_count: 0,
      observers: [],
      origin_available: false,
    })
  ),
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

vi.mock('react-leaflet', () => ({
  MapContainer: () => null,
  TileLayer: () => null,
  CircleMarker: () => null,
  Popup: () => null,
  Tooltip: () => null,
  Polyline: () => null,
  useMap: () => ({
    getContainer: () => document.createElement('div'),
    invalidateSize: () => undefined,
    setView: () => undefined,
    fitBounds: () => undefined,
  }),
}));

import { ObserverReachModal } from '../components/ObserverReachModal';
import i18n from '../i18n';

describe('ObserverReachModal hop path', () => {
  beforeEach(() => {
    apiMocks.getPacketObserverReach.mockReset();
    apiMocks.resolveDirectoryHops.mockReset();
    apiMocks.resolveDirectoryHops.mockResolvedValue({ resolved: {} });
    apiMocks.getPacketObserverReach.mockResolvedValue({
      directory_enabled: true,
      packet_hash: 'AABBCCDDEEFF0011',
      observer_count: 2,
      observers: [
        {
          name: 'Lyon',
          hops: 2,
          snr: -3,
          path: ['ab', 'cd'],
          lat: 45.75,
          lon: 4.85,
          isMLC: true,
        },
        {
          name: 'Paris',
          hops: 0,
          path: [],
          lat: 48.85,
          lon: 2.35,
        },
      ],
      max_hops: 2,
      max_distance_km: 12.4,
      origin_available: true,
      origin_lat: 45.76,
      origin_lon: 4.83,
    });
  });

  it('expands hop prefixes under an observer and leaves a direct observer collapsed', async () => {
    const user = userEvent.setup();
    render(<ObserverReachModal packetHash="AABBCCDDEEFF0011" open onOpenChange={() => {}} />);

    expect(await screen.findByText('Lyon')).toBeInTheDocument();
    expect(
      screen.getByText(new RegExp(i18n.t('messageList.observerReachHops', { count: 2 })))
    ).toBeInTheDocument();
    expect(screen.getByText(i18n.t('messageList.observerReachDirect'))).toBeInTheDocument();
    expect(screen.getByTestId('observer-mlc-mark')).toHaveAttribute(
      'alt',
      i18n.t('messageList.observerMlcMark')
    );
    expect(screen.getAllByTestId('observer-mlc-mark')).toHaveLength(1);

    expect(screen.queryByText('AB')).not.toBeInTheDocument();
    await user.click(
      screen.getByRole('button', {
        name: i18n.t('messageList.observerReachExpandPath', { name: 'Lyon' }),
      })
    );
    expect(screen.getByText('AB')).toBeInTheDocument();
    expect(screen.getByText('CD')).toBeInTheDocument();
    expect(screen.getByText(i18n.t('path.hop', { n: 1 }))).toBeInTheDocument();
    expect(
      screen.queryByRole('button', {
        name: i18n.t('messageList.observerReachExpandPath', { name: 'Paris' }),
      })
    ).not.toBeInTheDocument();
  });

  it('resolves 2-byte hops through the directory when the radio does not know them', async () => {
    const user = userEvent.setup();
    apiMocks.getPacketObserverReach.mockResolvedValue({
      directory_enabled: true,
      packet_hash: 'AABBCCDDEEFF0011',
      observer_count: 1,
      observers: [
        {
          name: 'Lyon',
          hops: 1,
          path: ['1a2b'],
          lat: 45.75,
          lon: 4.85,
        },
      ],
      max_hops: 1,
      origin_available: false,
    });
    apiMocks.resolveDirectoryHops.mockResolvedValue({
      resolved: { '1A2B': { name: 'Relay-East', source: 'corescope', hash_width: 2 } },
    });

    render(<ObserverReachModal packetHash="AABBCCDDEEFF0011" open onOpenChange={() => {}} />);
    await waitFor(() => {
      expect(apiMocks.resolveDirectoryHops).toHaveBeenCalledWith(['1A2B']);
    });
    await user.click(
      screen.getByRole('button', {
        name: i18n.t('messageList.observerReachExpandPath', { name: 'Lyon' }),
      })
    );
    expect(screen.getByText('1A2B')).toBeInTheDocument();
    expect(screen.getByText(/Relay-East/)).toBeInTheDocument();
  });
});
