import type { ReactNode } from 'react';
import { fireEvent, render, screen } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { LiveView } from '../components/LiveView';
import { api } from '../api';
import i18n from '../i18n';
import { resetLivePacketStore, setLiveCloseCode } from '../stores/livePacketStore';
import { resetRawPacketStore } from '../stores/rawPacketStore';
import { stopLivePacketFixtures } from '../fixtures/livePacketFixtures';

vi.mock('../api', () => ({
  api: {
    subscribeCommunityLive: vi.fn(),
    unsubscribeCommunityLive: vi.fn(),
    relancerCommunityLive: vi.fn(),
  },
}));

vi.mock('react-leaflet', () => ({
  MapContainer: ({ children }: { children: ReactNode }) => <div>{children}</div>,
  TileLayer: () => <div data-testid="tile-layer" />,
  CircleMarker: () => null,
  Polyline: () => null,
  useMap: () => ({
    getContainer: () => document.createElement('div'),
    getSize: () => ({ x: 100, y: 100 }),
    on: vi.fn(),
    off: vi.fn(),
    fitBounds: vi.fn(),
    setView: vi.fn(),
    getCenter: () => ({ lat: 46.2, lng: 5.2 }),
    getZoom: () => 6,
    latLngToContainerPoint: () => ({ x: 0, y: 0 }),
  }),
}));

describe('LiveView', () => {
  beforeEach(() => {
    resetLivePacketStore();
    resetRawPacketStore();
    vi.mocked(api.subscribeCommunityLive).mockResolvedValue({
      session_id: 'live-session',
      close_code: null,
      opted_out: false,
      connected: false,
    });
    vi.mocked(api.unsubscribeCommunityLive).mockResolvedValue({
      session_id: 'live-session',
      close_code: null,
      opted_out: false,
      connected: false,
    });
    vi.mocked(api.relancerCommunityLive).mockResolvedValue({
      session_id: null,
      close_code: null,
      opted_out: false,
      connected: true,
    });
  });

  afterEach(() => {
    stopLivePacketFixtures();
    resetLivePacketStore();
  });

  it('shows Relancer for close 4001 and not for slot-busy', () => {
    vi.mocked(api.subscribeCommunityLive).mockReturnValue(new Promise(() => {}));
    setLiveCloseCode(4001);
    const { rerender } = render(<LiveView contacts={[]} config={null} communityEnabled />);
    expect(screen.getByText(i18n.t('live.bannerExpired'))).toBeInTheDocument();
    expect(screen.getByRole('button', { name: i18n.t('live.relancer') })).toBeInTheDocument();

    setLiveCloseCode(4003);
    rerender(<LiveView contacts={[]} config={null} communityEnabled />);
    expect(screen.getByText(i18n.t('live.bannerSlotBusy'))).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: i18n.t('live.relancer') })).not.toBeInTheDocument();

    setLiveCloseCode(4001);
    rerender(<LiveView contacts={[]} config={null} communityEnabled />);
    fireEvent.click(screen.getByRole('button', { name: i18n.t('live.relancer') }));
    expect(api.relancerCommunityLive).toHaveBeenCalled();
  });

  it('shows opt-out and inactive banners from fixture toggles', () => {
    render(<LiveView contacts={[]} config={null} communityEnabled={false} />);
    expect(screen.getByText(i18n.t('live.bannerOptOut'))).toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: i18n.t('live.simulateInactive') }));
    expect(screen.getByText(i18n.t('live.bannerInactive'))).toBeInTheDocument();
  });

  it('starts the rain playing and toggles to play when paused', () => {
    render(<LiveView contacts={[]} config={null} communityEnabled />);
    const toggle = screen.getByRole('button', { name: i18n.t('live.playPause') });
    expect(toggle).toHaveAttribute('aria-pressed', 'true');
    expect(toggle).toHaveTextContent(i18n.t('live.pause'));
    fireEvent.click(toggle);
    expect(toggle).toHaveAttribute('aria-pressed', 'false');
    expect(toggle).toHaveTextContent(i18n.t('live.play'));
  });

  it('exposes an IATA-only filter', () => {
    render(<LiveView contacts={[]} config={null} communityEnabled />);
    expect(screen.getByLabelText(i18n.t('live.iataFilter'))).toBeInTheDocument();
    expect(screen.getByRole('option', { name: i18n.t('live.iataAll') })).toBeInTheDocument();
  });
});
