import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { LiveView } from '../components/LiveView';
import { LiveMapController } from '../components/live/liveMap';
import { DIRECTORY_NODES_REFRESH_MS } from '../components/live/liveRender';
import { api } from '../api';
import type { Contact } from '../types';
import i18n from '../i18n';
import {
  applyLiveStatus,
  recordCommunityPacket,
  resetLivePacketStore,
} from '../stores/livePacketStore';
import { resetRawPacketStore } from '../stores/rawPacketStore';
import { LIVE_PACKET_LEGEND_OPEN_KEY } from '../utils/liveLegendPreference';
import { LIVE_IATA_STORAGE_KEY } from '../utils/liveIataPreference';
import { resetLocatedLiveRegions } from '../utils/liveRegionCenters';
import { LiveSoundEngine } from '../utils/liveSound';

vi.mock('../api', () => ({
  api: {
    subscribeCommunityLive: vi.fn(),
    unsubscribeCommunityLive: vi.fn(),
    relancerCommunityLive: vi.fn(),
    getLiveDirectoryMapNodes: vi.fn(),
    getCommunityActiveIata: vi.fn(),
    searchCommunityAirports: vi.fn(async () => []),
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
  const setWorkerUrl = () => {};
  const maplibregl = { Map: FakeMap, NavigationControl, LngLatBounds, setWorkerUrl };
  return { default: maplibregl, Map: FakeMap, NavigationControl, LngLatBounds, setWorkerUrl };
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
    localStorage.removeItem(LIVE_PACKET_LEGEND_OPEN_KEY);
    localStorage.removeItem(LIVE_IATA_STORAGE_KEY);
    vi.mocked(api.subscribeCommunityLive).mockReset();
    vi.mocked(api.getCommunityActiveIata).mockReset();
    vi.mocked(api.getCommunityActiveIata).mockResolvedValue({
      available: true,
      codes: [
        { code: 'NCE', unique_hashes_24h: 40 },
        { code: 'TLN', unique_hashes_24h: 12 },
      ],
    });
    localStorage.setItem('meshloom-live-sound-theme', 'laser');
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
    resetLivePacketStore();
    overlays.length = 0;
  });

  it('does not show retired Relancer or slot-busy banners', () => {
    vi.mocked(api.subscribeCommunityLive).mockReturnValue(new Promise(() => {}));
    applyLiveStatus({ close_code: 4001, opted_out: false, connected: false });
    const { rerender } = render(
      <LiveView contacts={[]} config={null} communityEnabled communityIata="LYS" />
    );
    expect(screen.queryByText(i18n.t('live.bannerExpired'))).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: i18n.t('live.relancer') })).not.toBeInTheDocument();

    applyLiveStatus({ close_code: 4003, opted_out: false, connected: false });
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
    expect(api.getCommunityActiveIata).not.toHaveBeenCalled();
  });

  it('asks Community for nothing while it is off', async () => {
    vi.mocked(api.searchCommunityAirports).mockClear();
    render(<LiveView contacts={[]} config={null} communityEnabled={false} communityIata="LYS" />);
    await new Promise((resolve) => setTimeout(resolve, 20));
    expect(api.subscribeCommunityLive).not.toHaveBeenCalled();
    expect(api.getCommunityActiveIata).not.toHaveBeenCalled();
    expect(api.searchCommunityAirports).not.toHaveBeenCalled();
  });

  it('frames the regions again when Community comes back on, without a reload', async () => {
    resetLocatedLiveRegions();
    vi.mocked(api.searchCommunityAirports).mockClear();
    vi.mocked(api.searchCommunityAirports).mockResolvedValue([
      {
        iata: 'LYS',
        name: 'Lyon',
        city: 'Lyon',
        country: 'FR',
        label: 'LYS',
        lat: 45.72,
        lon: 5.08,
      },
    ]);
    const { rerender } = render(
      <LiveView contacts={[]} config={null} communityEnabled={false} communityIata="LYS" />
    );
    await new Promise((resolve) => setTimeout(resolve, 20));
    expect(api.searchCommunityAirports).not.toHaveBeenCalled();

    rerender(<LiveView contacts={[]} config={null} communityEnabled communityIata="LYS" />);
    await waitFor(() => expect(api.searchCommunityAirports).toHaveBeenCalledWith('LYS'));
    vi.mocked(api.searchCommunityAirports).mockResolvedValue([]);
  });

  it('does not show a 24h inactive banner or Relancer when 4002 is set', () => {
    vi.mocked(api.subscribeCommunityLive).mockReturnValue(new Promise(() => {}));
    applyLiveStatus({ close_code: 4002, opted_out: false, connected: false });
    render(<LiveView contacts={[]} config={null} communityEnabled communityIata="LYS" />);
    expect(screen.queryByText(i18n.t('live.bannerOptOut'))).not.toBeInTheDocument();
    expect(i18n.exists('live.bannerInactive')).toBe(false);
    expect(screen.queryByRole('button', { name: i18n.t('live.relancer') })).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Inactif 24 h' })).not.toBeInTheDocument();
  });

  it('explains a given-up clock-skew refusal and retries through Relancer', async () => {
    vi.mocked(api.subscribeCommunityLive).mockResolvedValue({
      session_id: 'live-session',
      close_code: null,
      opted_out: false,
      connected: false,
      state: 'auth_rejected',
      auth_error: 'clock_skew',
      auth_code: 'clock_skew',
      clock_skew_s: -600,
    });
    render(<LiveView contacts={[]} config={null} communityEnabled communityIata="LYS" />);
    expect(await screen.findByText(i18n.t('live.bannerClockSkew'))).toBeInTheDocument();
    expect(screen.getByText(i18n.t('live.clockAheadBy', { count: 10 }))).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: i18n.t('live.retry') }));
    expect(api.relancerCommunityLive).toHaveBeenCalledTimes(1);
    await waitFor(() =>
      expect(screen.queryByText(i18n.t('live.bannerClockSkew'))).not.toBeInTheDocument()
    );
  });

  it('shows a generic refusal banner when the relay gives up without a reason', async () => {
    vi.mocked(api.subscribeCommunityLive).mockResolvedValue({
      session_id: 'live-session',
      close_code: null,
      opted_out: false,
      connected: false,
      state: 'auth_rejected',
    });
    render(<LiveView contacts={[]} config={null} communityEnabled communityIata="LYS" />);
    expect(await screen.findByText(i18n.t('live.bannerTokenRejected'))).toBeInTheDocument();
    expect(screen.getByRole('button', { name: i18n.t('live.retry') })).toBeInTheDocument();
  });

  it('has no play/pause control', () => {
    render(<LiveView contacts={[]} config={null} communityEnabled communityIata="LYS" />);
    expect(i18n.exists('live.playPause')).toBe(false);
    expect(screen.queryByRole('button', { name: /play|pause|lecture/i })).not.toBeInTheDocument();
  });

  it('filters on the radio IATA by default and subscribes with it', async () => {
    render(<LiveView contacts={[]} config={null} communityEnabled communityIata="LYS" />);
    expect(screen.getByTestId('live-iata-label')).toHaveTextContent('LYS');
    await waitFor(() =>
      expect(api.subscribeCommunityLive).toHaveBeenCalledWith(undefined, ['LYS'])
    );
    // The region picker is no longer a <select>.
    expect(screen.queryByRole('option', { name: i18n.t('live.iataAll') })).not.toBeInTheDocument();
  });

  it('offers active, seen and radio regions as checkboxes, and remembers the choice', async () => {
    render(<LiveView contacts={[]} config={null} communityEnabled communityIata="LYS" />);
    await waitFor(() => expect(api.getCommunityActiveIata).toHaveBeenCalled());
    await waitFor(() =>
      expect(api.subscribeCommunityLive).toHaveBeenCalledWith(undefined, ['LYS'])
    );
    // The button's name is its visible text: the label and the selection.
    const toggle = screen.getByRole('button', {
      name: new RegExp(`^${i18n.t('live.iataFilter')}\\s*LYS$`),
    });
    fireEvent.click(toggle);
    const panel = await screen.findByRole('group', { name: i18n.t('live.iataMenu') });
    expect(panel).toBeInTheDocument();
    expect(toggle).toHaveAttribute('aria-expanded', 'true');
    expect(toggle).toHaveAttribute('aria-controls', panel.id);
    expect(await screen.findByRole('checkbox', { name: /NCE/ })).not.toBeChecked();
    expect(screen.getByRole('checkbox', { name: /TLN/ })).not.toBeChecked();
    expect(screen.getByRole('checkbox', { name: /LYS/ })).toBeChecked();
    expect(screen.getByRole('checkbox', { name: i18n.t('live.iataAll') })).not.toBeChecked();

    fireEvent.click(screen.getByRole('checkbox', { name: /NCE/ }));
    expect(screen.getByTestId('live-iata-label')).toHaveTextContent('LYS, NCE');
    expect(localStorage.getItem(LIVE_IATA_STORAGE_KEY)).toBe(JSON.stringify(['LYS', 'NCE']));
    await waitFor(() =>
      expect(api.subscribeCommunityLive).toHaveBeenCalledWith('live-session', ['LYS', 'NCE'])
    );

    fireEvent.click(screen.getByRole('checkbox', { name: i18n.t('live.iataAll') }));
    expect(screen.getByTestId('live-iata-label')).toHaveTextContent(i18n.t('live.iataAll'));
    expect(localStorage.getItem(LIVE_IATA_STORAGE_KEY)).toBe('all');
    await waitFor(() =>
      expect(api.subscribeCommunityLive).toHaveBeenLastCalledWith('live-session', null)
    );
    fireEvent.keyDown(document, { key: 'Escape' });
    expect(screen.queryByRole('group', { name: i18n.t('live.iataMenu') })).not.toBeInTheDocument();
    expect(toggle).toHaveFocus();
  });

  it('keeps the session id the relay answers with', async () => {
    render(<LiveView contacts={[]} config={null} communityEnabled communityIata="LYS" />);
    await waitFor(() =>
      expect(api.subscribeCommunityLive).toHaveBeenCalledWith(undefined, ['LYS'])
    );
    // The relay forgot 'live-session' (TTL, restart) and minted a new one.
    vi.mocked(api.subscribeCommunityLive).mockResolvedValue({
      session_id: 'renewed',
      close_code: null,
      opted_out: false,
      connected: false,
    });
    fireEvent.click(screen.getByRole('button', { name: new RegExp(i18n.t('live.iataFilter')) }));
    fireEvent.click(await screen.findByRole('checkbox', { name: /NCE/ }));
    await waitFor(() =>
      expect(api.subscribeCommunityLive).toHaveBeenCalledWith('live-session', ['LYS', 'NCE'])
    );
    fireEvent.click(screen.getByRole('checkbox', { name: /TLN/ }));
    await waitFor(() =>
      expect(api.subscribeCommunityLive).toHaveBeenLastCalledWith('renewed', ['LYS', 'NCE', 'TLN'])
    );
  });

  it('restores the saved regions', async () => {
    localStorage.setItem(LIVE_IATA_STORAGE_KEY, JSON.stringify(['tln', 'NCE']));
    render(<LiveView contacts={[]} config={null} communityEnabled communityIata="LYS" />);
    expect(screen.getByTestId('live-iata-label')).toHaveTextContent('NCE, TLN');
    await waitFor(() =>
      expect(api.subscribeCommunityLive).toHaveBeenCalledWith(undefined, ['NCE', 'TLN'])
    );
  });

  it('falls back to the radio IATA when storage is unreadable', async () => {
    const getItem = vi.spyOn(Storage.prototype, 'getItem').mockImplementation(() => {
      throw new Error('denied');
    });
    try {
      render(<LiveView contacts={[]} config={null} communityEnabled communityIata="LYS" />);
      expect(screen.getByTestId('live-iata-label')).toHaveTextContent('LYS');
    } finally {
      getItem.mockRestore();
    }
  });

  it('keeps the seen regions when Community has no active list yet', async () => {
    vi.mocked(api.getCommunityActiveIata).mockRejectedValue(new Error('404'));
    recordCommunityPacket({
      v: 2,
      event_id: 'mrs-1',
      hash8: 'abcdef01',
      type: 'advert',
      path: [],
      hop_count: 0,
      hops: [],
      ear: { lat: 43.3, lon: 5.4, source: 'advert' },
      iata: 'MRS',
      t: Date.now(),
      ear_id: 'ear-mrs',
    });
    render(<LiveView contacts={[]} config={null} communityEnabled communityIata="LYS" />);
    await waitFor(() => expect(api.getCommunityActiveIata).toHaveBeenCalled());
    fireEvent.click(screen.getByRole('button', { name: new RegExp(i18n.t('live.iataFilter')) }));
    expect(await screen.findByRole('checkbox', { name: /MRS/ })).toBeInTheDocument();
    expect(screen.getByRole('checkbox', { name: /LYS/ })).toBeInTheDocument();
    expect(screen.queryByRole('checkbox', { name: /NCE/ })).not.toBeInTheDocument();
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

  it('exposes a Sound select that starts Off even if a theme was stored', () => {
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
    resume.mockRestore();
  });

  it('collapses the entire legend from the chevron and restores it on click', () => {
    render(<LiveView contacts={[]} config={null} communityEnabled communityIata="LYS" />);
    const toggle = screen.getByRole('button', { name: i18n.t('live.packetLegendToggle') });
    expect(toggle).toHaveAttribute('aria-expanded', 'true');
    expect(screen.getByRole('group', { name: i18n.t('live.packetLegend') })).toBeInTheDocument();
    expect(screen.getByRole('group', { name: i18n.t('live.roleLegend') })).toBeInTheDocument();
    expect(screen.getByText(i18n.t('live.nodes.companion'))).toBeInTheDocument();

    fireEvent.click(toggle);
    expect(toggle).toHaveAttribute('aria-expanded', 'false');
    expect(
      screen.queryByRole('group', { name: i18n.t('live.packetLegend') })
    ).not.toBeInTheDocument();
    expect(
      screen.queryByRole('group', { name: i18n.t('live.roleLegend') })
    ).not.toBeInTheDocument();
    expect(screen.queryByText(i18n.t('live.nodes.companion'))).not.toBeInTheDocument();
    expect(localStorage.getItem(LIVE_PACKET_LEGEND_OPEN_KEY)).toBe('false');

    fireEvent.click(toggle);
    expect(toggle).toHaveAttribute('aria-expanded', 'true');
    expect(screen.getByRole('group', { name: i18n.t('live.packetLegend') })).toBeInTheDocument();
    expect(screen.getByRole('group', { name: i18n.t('live.roleLegend') })).toBeInTheDocument();
    expect(localStorage.getItem(LIVE_PACKET_LEGEND_OPEN_KEY)).toBe('true');
  });

  it('refetches the live directory every 10 minutes', async () => {
    vi.useFakeTimers();
    try {
      render(<LiveView contacts={[]} config={null} communityEnabled communityIata="LYS" />);
      await vi.waitFor(() => expect(api.getLiveDirectoryMapNodes).toHaveBeenCalledTimes(1));
      await vi.advanceTimersByTimeAsync(DIRECTORY_NODES_REFRESH_MS);
      expect(api.getLiveDirectoryMapNodes).toHaveBeenCalledTimes(2);
    } finally {
      vi.useRealTimers();
    }
  });

  it('adds a pin when a community frame names an exact origin with GPS', async () => {
    const originKey = 'ab'.repeat(32);
    const spy = vi.spyOn(LiveMapController.prototype, 'setDirectoryNodes');
    render(<LiveView contacts={[]} config={null} communityEnabled communityIata="LYS" />);
    await waitFor(() => expect(api.getLiveDirectoryMapNodes).toHaveBeenCalled());
    recordCommunityPacket({
      v: 2,
      event_id: 'heard-origin',
      hash8: 'aabbccdd',
      type: 'advert',
      path: [],
      hop_count: 0,
      hops: [],
      ear: { lat: 45.7256, lon: 5.0811, source: 'advert' },
      origin: {
        token: 'ab12',
        confidence: 'exact',
        lat: 43.7,
        lon: 7.25,
        pubkey: originKey,
        name: 'OnAir',
      },
      iata: 'LYS',
      t: Date.now(),
      ear_id: 'ear-heard',
    });
    await waitFor(() => {
      const last = spy.mock.calls[spy.mock.calls.length - 1]?.[0] ?? [];
      expect(last.some((node) => node.public_key === originKey && node.name === 'OnAir')).toBe(
        true
      );
    });
    spy.mockRestore();
  });
});
