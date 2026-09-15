import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { LiveView } from '../components/LiveView';
import { api } from '../api';
import i18n from '../i18n';
import {
  resetLivePacketStore,
  setLiveCloseCode,
  setLiveInactiveObserver,
} from '../stores/livePacketStore';
import { resetRawPacketStore } from '../stores/rawPacketStore';
import { stopLivePacketFixtures } from '../fixtures/livePacketFixtures';

vi.mock('../api', () => ({
  api: {
    subscribeCommunityLive: vi.fn(),
    unsubscribeCommunityLive: vi.fn(),
    relancerCommunityLive: vi.fn(),
    getDirectoryMapNodes: vi.fn(),
  },
}));

const { FakeMap } = vi.hoisted(() => {
  class FakeMap {
    handlers = new Map<string, Array<(...args: unknown[]) => void>>();
    addControl = vi.fn();
    removeControl = vi.fn();
    remove = vi.fn();
    resize = vi.fn();
    fitBounds = vi.fn();
    getCenter = () => ({ lat: 46.2, lng: 5.2 });
    getZoom = () => 6;
    on(event: string, cb: (...args: unknown[]) => void) {
      const list = this.handlers.get(event) ?? [];
      list.push(cb);
      this.handlers.set(event, list);
      if (event === 'load') queueMicrotask(() => cb());
    }
    off() {}
  }
  return { FakeMap };
});

vi.mock('maplibre-gl', () => {
  class LngLatBounds {
    extend() {
      return this;
    }
  }
  class NavigationControl {}
  const maplibregl = { Map: FakeMap, NavigationControl, LngLatBounds };
  return { default: maplibregl, Map: FakeMap, NavigationControl, LngLatBounds };
});

vi.mock('maplibre-gl/dist/maplibre-gl.css', () => ({}));

vi.mock('@deck.gl/mapbox', () => ({
  MapboxOverlay: class {
    setProps = vi.fn();
    constructor(_props: unknown) {}
  },
}));

vi.mock('@deck.gl/layers', () => ({
  PathLayer: class {
    constructor(public props: unknown) {}
  },
  ScatterplotLayer: class {
    constructor(public props: unknown) {}
  },
  IconLayer: class {
    constructor(public props: unknown) {}
  },
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
    vi.mocked(api.getDirectoryMapNodes).mockResolvedValue({
      nodes: [
        {
          public_key: 'aa',
          name: 'Lyon Repeater',
          role: 'repeater',
          lat: 45.76,
          lon: 4.84,
          source: 'corescope',
        },
      ],
      total: 1,
    });
  });

  afterEach(() => {
    stopLivePacketFixtures();
    resetLivePacketStore();
  });

  it('does not show retired Relancer or slot-busy banners', () => {
    vi.mocked(api.subscribeCommunityLive).mockReturnValue(new Promise(() => {}));
    setLiveCloseCode(4001);
    const { rerender } = render(<LiveView contacts={[]} config={null} communityEnabled />);
    expect(screen.queryByText(i18n.t('live.bannerExpired'))).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: i18n.t('live.relancer') })).not.toBeInTheDocument();

    setLiveCloseCode(4003);
    rerender(<LiveView contacts={[]} config={null} communityEnabled />);
    expect(screen.queryByText(i18n.t('live.bannerSlotBusy'))).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: i18n.t('live.relancer') })).not.toBeInTheDocument();
  });

  it('shows the opt-out banner without debug toggles', () => {
    render(<LiveView contacts={[]} config={null} communityEnabled={false} />);
    expect(screen.getByText(i18n.t('live.bannerOptOut'))).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Inactif 24 h' })).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'JWT expiré' })).not.toBeInTheDocument();
  });

  it('shows the inactive-observer banner from store state', () => {
    vi.mocked(api.subscribeCommunityLive).mockReturnValue(new Promise(() => {}));
    setLiveInactiveObserver(true);
    render(<LiveView contacts={[]} config={null} communityEnabled />);
    expect(screen.getByText(i18n.t('live.bannerInactive'))).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Inactif 24 h' })).not.toBeInTheDocument();
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

  it('exposes packet-type chips and an exact-only toggle', () => {
    render(<LiveView contacts={[]} config={null} communityEnabled />);
    expect(screen.getByRole('group', { name: i18n.t('live.typeFilter') })).toBeInTheDocument();
    const textChip = screen.getByRole('button', { name: i18n.t('live.legend.text') });
    expect(textChip).toHaveAttribute('aria-pressed', 'true');
    fireEvent.click(textChip);
    expect(textChip).toHaveAttribute('aria-pressed', 'false');
    const exact = screen.getByLabelText(i18n.t('live.certainOnly'));
    expect(exact).not.toBeChecked();
    fireEvent.click(exact);
    expect(exact).toBeChecked();
  });

  it('shows a dual legend for packet types and roles, and no packet log', () => {
    render(<LiveView contacts={[]} config={null} communityEnabled />);
    expect(screen.getByLabelText(i18n.t('live.legendTitle'))).toBeInTheDocument();
    expect(screen.getByRole('group', { name: i18n.t('live.packetLegend') })).toBeInTheDocument();
    expect(screen.getByRole('group', { name: i18n.t('live.roleLegend') })).toBeInTheDocument();
    expect(screen.getByText(i18n.t('live.nodes.companion'))).toBeInTheDocument();
    expect(screen.getByText(i18n.t('live.nodes.repeater'))).toBeInTheDocument();
    expect(screen.queryByRole('log')).not.toBeInTheDocument();
    expect(screen.queryByTestId('live-packet-log')).not.toBeInTheDocument();
  });

  it('loads community directory nodes as the permanent map layer', async () => {
    render(<LiveView contacts={[]} config={null} communityEnabled />);
    await waitFor(() => {
      expect(api.getDirectoryMapNodes).toHaveBeenCalled();
    });
  });

  it('does not mount a Leaflet tile layer', () => {
    const { container } = render(<LiveView contacts={[]} config={null} communityEnabled />);
    expect(container.querySelector('.leaflet-container')).toBeNull();
    expect(screen.queryByTestId('tile-layer')).not.toBeInTheDocument();
    expect(container.querySelector('.live-map-osm')).not.toBeNull();
  });
});
