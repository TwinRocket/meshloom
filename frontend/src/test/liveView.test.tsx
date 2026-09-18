import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { LiveView } from '../components/LiveView';
import { LiveMapController } from '../components/live/liveMap';
import { api } from '../api';
import type { Contact } from '../types';
import i18n from '../i18n';
import { resetLivePacketStore, setLiveCloseCode } from '../stores/livePacketStore';
import { resetRawPacketStore } from '../stores/rawPacketStore';
import { stopLivePacketFixtures } from '../fixtures/livePacketFixtures';
import { LIVE_PACKET_LEGEND_OPEN_KEY } from '../utils/liveLegendPreference';
import { LiveSoundEngine } from '../utils/liveSound';
import { LIVE_SOUND_THEME_KEY } from '../utils/liveSoundPreference';

vi.mock('../api', () => ({
  api: {
    subscribeCommunityLive: vi.fn(),
    unsubscribeCommunityLive: vi.fn(),
    relancerCommunityLive: vi.fn(),
    getLiveDirectoryMapNodes: vi.fn(),
  },
}));

const { FakeMap, overlays } = vi.hoisted(() => {
  const overlays: Array<{ onClick?: (info: unknown) => void }> = [];
  class FakeMap {
    handlers = new Map<string, Array<(...args: unknown[]) => void>>();
    addControl = vi.fn();
    removeControl = vi.fn();
    remove = vi.fn();
    resize = vi.fn();
    fitBounds = vi.fn();
    getCenter = () => ({ lat: 46.2, lng: 5.2 });
    getZoom = () => 6;
    project = () => ({ x: 20, y: 20 });
    getCanvas = () => ({ width: 200, height: 200, clientWidth: 200, clientHeight: 200 });
    on(event: string, cb: (...args: unknown[]) => void) {
      const list = this.handlers.get(event) ?? [];
      list.push(cb);
      this.handlers.set(event, list);
      if (event === 'load') queueMicrotask(() => cb());
    }
    off() {}
  }
  return { FakeMap, overlays };
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
    onClick?: (info: unknown) => void;
    constructor(props: { onClick?: (info: unknown) => void } = {}) {
      this.onClick = props.onClick;
      overlays.push(this);
    }
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
    localStorage.removeItem(LIVE_SOUND_THEME_KEY);
    localStorage.removeItem(LIVE_PACKET_LEGEND_OPEN_KEY);
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
    vi.mocked(api.getLiveDirectoryMapNodes).mockResolvedValue({
      nodes: [
        {
          public_key: 'aa',
          name: 'Lyon Repeater',
          role: 'repeater',
          lat: 45.76,
          lon: 4.84,
          source: 'community-db',
        },
      ],
      total: 1,
    });
  });

  afterEach(() => {
    stopLivePacketFixtures();
    resetLivePacketStore();
    overlays.length = 0;
  });

  it('does not show retired Relancer or slot-busy banners', () => {
    vi.mocked(api.subscribeCommunityLive).mockReturnValue(new Promise(() => {}));
    setLiveCloseCode(4001);
    const { rerender } = render(
      <LiveView contacts={[]} config={null} communityEnabled communityIata="LYS" />
    );
    expect(screen.queryByText(i18n.t('live.bannerExpired'))).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: i18n.t('live.relancer') })).not.toBeInTheDocument();

    setLiveCloseCode(4003);
    rerender(<LiveView contacts={[]} config={null} communityEnabled communityIata="LYS" />);
    expect(screen.queryByText(i18n.t('live.bannerSlotBusy'))).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: i18n.t('live.relancer') })).not.toBeInTheDocument();
  });

  it('shows one Community+IATA banner with a Settings link when opted out', () => {
    const onOpenCommunitySettings = vi.fn();
    render(
      <LiveView
        contacts={[]}
        config={null}
        communityEnabled={false}
        communityIata=""
        onOpenCommunitySettings={onOpenCommunitySettings}
      />
    );
    expect(screen.getByText(i18n.t('live.bannerOptOut'))).toBeInTheDocument();
    fireEvent.click(
      screen.getByRole('button', { name: i18n.t('settings.community.bannerOpenSettings') })
    );
    expect(onOpenCommunitySettings).toHaveBeenCalled();
    expect(screen.queryByRole('button', { name: 'Inactif 24 h' })).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'JWT expiré' })).not.toBeInTheDocument();
  });

  it('shows the same banner when Community is on but IATA is empty', () => {
    const onOpenCommunitySettings = vi.fn();
    render(
      <LiveView
        contacts={[]}
        config={null}
        communityEnabled
        communityIata=""
        onOpenCommunitySettings={onOpenCommunitySettings}
      />
    );
    expect(screen.getByText(i18n.t('live.bannerOptOut'))).toBeInTheDocument();
    expect(
      screen.getByRole('button', { name: i18n.t('settings.community.bannerOpenSettings') })
    ).toBeInTheDocument();
    expect(api.subscribeCommunityLive).not.toHaveBeenCalled();
  });

  it('does not show a 24h inactive banner or Relancer when 4002 is set', () => {
    vi.mocked(api.subscribeCommunityLive).mockReturnValue(new Promise(() => {}));
    setLiveCloseCode(4002);
    render(<LiveView contacts={[]} config={null} communityEnabled communityIata="LYS" />);
    expect(screen.queryByText(i18n.t('live.bannerOptOut'))).not.toBeInTheDocument();
    expect(i18n.exists('live.bannerInactive')).toBe(false);
    expect(screen.queryByRole('button', { name: i18n.t('live.relancer') })).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Inactif 24 h' })).not.toBeInTheDocument();
  });

  it('has no play/pause control', () => {
    render(<LiveView contacts={[]} config={null} communityEnabled communityIata="LYS" />);
    expect(i18n.exists('live.playPause')).toBe(false);
    expect(screen.queryByRole('button', { name: /play|pause|lecture/i })).not.toBeInTheDocument();
  });

  it('exposes an IATA-only filter', () => {
    render(<LiveView contacts={[]} config={null} communityEnabled communityIata="LYS" />);
    expect(screen.getByLabelText(i18n.t('live.iataFilter'))).toBeInTheDocument();
    expect(screen.getByRole('option', { name: i18n.t('live.iataAll') })).toBeInTheDocument();
  });

  it('exposes packet-type chips and an exact-only toggle', () => {
    render(<LiveView contacts={[]} config={null} communityEnabled communityIata="LYS" />);
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
    render(<LiveView contacts={[]} config={null} communityEnabled communityIata="LYS" />);
    expect(screen.getByLabelText(i18n.t('live.legendTitle'))).toBeInTheDocument();
    expect(screen.getByRole('group', { name: i18n.t('live.packetLegend') })).toBeInTheDocument();
    expect(screen.getByRole('group', { name: i18n.t('live.roleLegend') })).toBeInTheDocument();
    expect(screen.getByText(i18n.t('live.nodes.companion'))).toBeInTheDocument();
    expect(screen.getByText(i18n.t('live.nodes.repeater'))).toBeInTheDocument();
    expect(screen.queryByRole('log')).not.toBeInTheDocument();
    expect(screen.queryByTestId('live-packet-log')).not.toBeInTheDocument();
  });

  it('loads the live directory as the permanent map layer', async () => {
    render(<LiveView contacts={[]} config={null} communityEnabled communityIata="LYS" />);
    await waitFor(() => {
      expect(api.getLiveDirectoryMapNodes).toHaveBeenCalled();
    });
  });

  it('keeps observer GPS in the directory pin set and has no observer legend', async () => {
    vi.mocked(api.getLiveDirectoryMapNodes).mockResolvedValue({
      nodes: [
        {
          public_key: 'aa',
          name: 'Lyon Repeater',
          role: 'repeater',
          lat: 45.76,
          lon: 4.84,
          source: 'community-db',
        },
        {
          public_key: 'ee',
          name: 'Community ear',
          role: 'observer',
          lat: 45.8,
          lon: 4.9,
          source: 'community-db',
        },
      ],
      total: 2,
    });
    const spy = vi.spyOn(LiveMapController.prototype, 'setDirectoryNodes');
    render(<LiveView contacts={[]} config={null} communityEnabled communityIata="LYS" />);
    await waitFor(() => {
      const last = spy.mock.calls[spy.mock.calls.length - 1]?.[0] ?? [];
      expect(last.some((node) => node.role === 'observer' && node.public_key === 'ee')).toBe(true);
    });
    const legend = screen.getByRole('group', { name: i18n.t('live.roleLegend') });
    expect(legend.textContent ?? '').not.toMatch(/observ/i);
    spy.mockRestore();
  });

  it('has no observer entry in the role legend', () => {
    render(<LiveView contacts={[]} config={null} communityEnabled communityIata="LYS" />);
    const legend = screen.getByRole('group', { name: i18n.t('live.roleLegend') });
    expect(legend.textContent ?? '').not.toMatch(/observ/i);
  });

  it('does not mount a Leaflet tile layer', () => {
    const { container } = render(
      <LiveView contacts={[]} config={null} communityEnabled communityIata="LYS" />
    );
    expect(container.querySelector('.leaflet-container')).toBeNull();
    expect(screen.queryByTestId('tile-layer')).not.toBeInTheDocument();
    expect(container.querySelector('.live-map-osm')).not.toBeNull();
  });

  it('overlays a GPS contact and tombstones it when the contact leaves', async () => {
    const gpsContact: Contact = {
      public_key: 'dd'.repeat(32),
      name: 'NewPin',
      type: 1,
      flags: 0,
      direct_path: null,
      direct_path_len: -1,
      direct_path_hash_mode: 0,
      last_advert: null,
      lat: 45.11,
      lon: 4.11,
      last_seen: null,
      on_radio: false,
      favorite: false,
      last_contacted: null,
      last_read_at: null,
      first_seen: null,
    };
    const spy = vi.spyOn(LiveMapController.prototype, 'setDirectoryNodes');
    const { rerender } = render(
      <LiveView contacts={[]} config={null} communityEnabled communityIata="LYS" />
    );
    await waitFor(() => expect(api.getLiveDirectoryMapNodes).toHaveBeenCalled());
    rerender(
      <LiveView contacts={[gpsContact]} config={null} communityEnabled communityIata="LYS" />
    );
    await waitFor(() => {
      const last = spy.mock.calls[spy.mock.calls.length - 1]?.[0] ?? [];
      expect(
        last.some((node) => node.public_key === 'dd'.repeat(32) && node.name === 'NewPin')
      ).toBe(true);
    });
    rerender(<LiveView contacts={[]} config={null} communityEnabled communityIata="LYS" />);
    await waitFor(() => {
      const last = spy.mock.calls[spy.mock.calls.length - 1]?.[0] ?? [];
      expect(last.some((node) => node.public_key === 'dd'.repeat(32))).toBe(false);
    });
    spy.mockRestore();
  });

  it('opens contact info for a known companion and the conversation for a known repeater', async () => {
    const companion: Contact = {
      public_key: '11'.repeat(32),
      name: 'Alice',
      type: 1,
      flags: 0,
      direct_path: null,
      direct_path_len: -1,
      direct_path_hash_mode: 0,
      last_advert: null,
      lat: 45.11,
      lon: 4.11,
      last_seen: null,
      on_radio: false,
      favorite: false,
      last_contacted: null,
      last_read_at: null,
      first_seen: null,
    };
    const repeater: Contact = {
      ...companion,
      public_key: '22'.repeat(32),
      name: 'FR83-RPT',
      type: 2,
    };
    const onOpenContactInfo = vi.fn();
    const onSelectConversation = vi.fn();
    render(
      <LiveView
        contacts={[companion, repeater]}
        config={null}
        communityEnabled
        communityIata="LYS"
        onOpenContactInfo={onOpenContactInfo}
        onSelectConversation={onSelectConversation}
      />
    );
    await waitFor(() => expect(overlays.length).toBeGreaterThan(0));
    const click = overlays[overlays.length - 1]?.onClick;
    click?.({
      object: {
        pick: {
          kind: 'node',
          name: 'Alice',
          role: 'companion',
          publicKey: companion.public_key,
          x: 0,
          y: 0,
        },
      },
    });
    click?.({
      object: {
        pick: {
          kind: 'node',
          name: 'FR83-RPT',
          role: 'repeater',
          publicKey: repeater.public_key,
          x: 0,
          y: 0,
        },
      },
    });
    click?.({
      object: {
        pick: {
          kind: 'node',
          name: 'Stranger',
          role: 'repeater',
          publicKey: 'ff'.repeat(32),
          x: 0,
          y: 0,
        },
      },
    });
    expect(onOpenContactInfo).toHaveBeenCalledTimes(1);
    expect(onOpenContactInfo).toHaveBeenCalledWith(companion.public_key);
    expect(onSelectConversation).toHaveBeenCalledTimes(1);
    expect(onSelectConversation).toHaveBeenCalledWith({
      type: 'contact',
      id: repeater.public_key,
      name: 'FR83-RPT',
    });
  });

  it('exposes a Sound select defaulting to Off', () => {
    const resume = vi.spyOn(LiveSoundEngine.prototype, 'resume').mockResolvedValue();
    render(<LiveView contacts={[]} config={null} communityEnabled communityIata="LYS" />);
    const select = screen.getByLabelText(i18n.t('live.soundTheme'));
    expect(select).toHaveValue('off');
    expect(screen.getByRole('option', { name: i18n.t('live.soundOff') })).toBeInTheDocument();
    expect(screen.getByRole('option', { name: i18n.t('live.soundBubbles') })).toBeInTheDocument();
    expect(screen.getByRole('option', { name: i18n.t('live.soundLaser') })).toBeInTheDocument();
    expect(screen.getByRole('option', { name: i18n.t('live.soundBit8') })).toBeInTheDocument();
    fireEvent.change(select, { target: { value: 'laser' } });
    expect(select).toHaveValue('laser');
    expect(resume).toHaveBeenCalled();
    expect(localStorage.getItem(LIVE_SOUND_THEME_KEY)).toBe('laser');
    resume.mockRestore();
  });

  it('collapses packet types from the legend chevron and leaves roles open', () => {
    render(<LiveView contacts={[]} config={null} communityEnabled communityIata="LYS" />);
    const toggle = screen.getByRole('button', { name: i18n.t('live.packetLegendToggle') });
    expect(toggle).toHaveAttribute('aria-expanded', 'true');
    expect(screen.getByRole('group', { name: i18n.t('live.packetLegend') })).toBeInTheDocument();
    fireEvent.click(toggle);
    expect(toggle).toHaveAttribute('aria-expanded', 'false');
    expect(screen.queryByRole('group', { name: i18n.t('live.packetLegend') })).not.toBeInTheDocument();
    expect(screen.getByRole('group', { name: i18n.t('live.roleLegend') })).toBeInTheDocument();
    expect(screen.getByText(i18n.t('live.nodes.companion'))).toBeInTheDocument();
    expect(localStorage.getItem(LIVE_PACKET_LEGEND_OPEN_KEY)).toBe('false');
  });
});
