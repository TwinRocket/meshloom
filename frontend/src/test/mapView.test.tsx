import { forwardRef } from 'react';
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import './eSlices';
import { MapView } from '../components/MapView';
import { api } from '../api';
import i18n from '../i18n';
import type { Contact } from '../types';
import { CONTACT_TYPE_REPEATER, CONTACT_TYPE_SENSOR } from '../types';
import { NODE_ROLE_STYLE } from '../components/live/liveRender';

vi.mock('../api', () => ({
  api: {
    getDirectoryMapNodes: vi.fn(),
  },
}));

vi.mock('react-leaflet', () => {
  const BaseLayer = ({
    children,
  }: {
    children: React.ReactNode;
    name: string;
    checked?: boolean;
  }) => <div>{children}</div>;
  const LayersControlMock = ({ children }: { children: React.ReactNode }) => <div>{children}</div>;
  (LayersControlMock as unknown as { BaseLayer: typeof BaseLayer }).BaseLayer = BaseLayer;
  return {
    MapContainer: ({ children, className }: { children: React.ReactNode; className?: string }) => (
      <div data-testid="map-container" className={className}>
        {children}
      </div>
    ),
    TileLayer: ({ url }: { url: string }) => <div data-testid="tile-layer" data-url={url} />,
    CircleMarker: forwardRef<
      HTMLDivElement,
      { children: React.ReactNode; pathOptions?: { fillColor?: string; color?: string } }
    >(({ children, pathOptions }, ref) => (
      <div
        ref={ref}
        data-fill-color={pathOptions?.fillColor}
        data-stroke-color={pathOptions?.color}
      >
        {children}
      </div>
    )),
    Marker: ({
      children,
      icon,
    }: {
      children: React.ReactNode;
      icon?: { options?: { html?: string } };
    }) => {
      const html = icon?.options?.html ?? '';
      const fillMatch = html.match(/fill="(#[0-9a-fA-F]{3,8})"/);
      return (
        <div
          data-shape={html.includes('polygon') ? 'triangle' : undefined}
          data-fill-color={fillMatch?.[1]}
        >
          {children}
        </div>
      );
    },
    Popup: ({ children }: { children: React.ReactNode }) => <div>{children}</div>,
    Polyline: () => null,
    LayersControl: LayersControlMock,
    useMap: () => ({
      setView: vi.fn(),
      fitBounds: vi.fn(),
      getBounds: vi.fn(() => ({ contains: () => true })),
      invalidateSize: vi.fn(),
      setMaxZoom: vi.fn(),
      setZoom: vi.fn(),
      getZoom: vi.fn(() => 2),
      getCenter: vi.fn(() => ({ lat: 20, lng: 0 })),
      on: vi.fn(),
      off: vi.fn(),
    }),
    useMapEvents: () => null,
  };
});

describe('MapView', () => {
  beforeEach(() => {
    vi.mocked(api.getDirectoryMapNodes).mockReset();
    vi.mocked(api.getDirectoryMapNodes).mockResolvedValue({ nodes: [] });
    localStorage.clear();
  });

  it('serves the dark basemap from OpenStreetMap, with no keyed provider left', () => {
    // CARTO's keyless raster answers every tile past zoom 7 with a watermark, so
    // the dark layer must not be a second provider any more.
    render(<MapView contacts={[]} />);

    const urls = screen.getAllByTestId('tile-layer').map((el) => el.getAttribute('data-url'));
    expect(urls.some((url) => url?.includes('openstreetmap'))).toBe(true);
    expect(urls.some((url) => url?.includes('cartocdn'))).toBe(false);
  });

  it('inverts the tiles only when the dark basemap is the chosen one', () => {
    localStorage.setItem('meshloom-map-layer', 'dark');
    localStorage.setItem('meshloom-map-layer-chosen', 'true');
    const { unmount } = render(<MapView contacts={[]} />);
    expect(screen.getByTestId('map-container').className).toContain('basemap-inverted');
    unmount();

    localStorage.setItem('meshloom-map-layer', 'light');
    render(<MapView contacts={[]} />);
    expect(screen.getByTestId('map-container').className).not.toContain('basemap-inverted');
  });

  it('lets the theme pick the basemap when nobody chose one', () => {
    // A stored light/dark nobody asked for predates the themed default; honouring
    // it would leave a daylight map inside a dark app for good.
    localStorage.setItem('meshloom-map-layer', 'dark');
    const { unmount } = render(<MapView contacts={[]} />);
    expect(screen.getByTestId('map-container').className).not.toContain('basemap-inverted');
    unmount();

    // A styled basemap is never theme-derived, so it stands without the marker.
    localStorage.setItem('meshloom-map-layer', 'satellite');
    render(<MapView contacts={[]} />);
    const urls = screen.getAllByTestId('tile-layer').map((el) => el.getAttribute('data-url'));
    expect(urls.some((url) => url?.includes('arcgisonline'))).toBe(true);
  });

  it('renders a never-heard fallback for a focused contact without last_seen', () => {
    const contact: Contact = {
      public_key: 'aa'.repeat(32),
      name: 'Mystery Node',
      type: 1,
      flags: 0,
      direct_path: null,
      direct_path_len: -1,
      direct_path_hash_mode: -1,
      route_override_path: null,
      route_override_len: null,
      route_override_hash_mode: null,
      last_advert: null,
      lat: 40,
      lon: -74,
      last_seen: null,
      on_radio: false,
      favorite: false,
      last_contacted: null,
      last_read_at: null,
      first_seen: null,
    };

    render(<MapView contacts={[contact]} focusedKey={contact.public_key} />);

    expect(
      screen.getByText(
        i18n.t('map.showingHeardFocused', { count: 1, since: i18n.t('map.inLast7d') })
      )
    ).toBeInTheDocument();
    expect(
      screen.getByText(i18n.t('map.lastHeard', { time: i18n.t('map.neverHeard') }))
    ).toBeInTheDocument();
  });

  it('invokes onSelectContact when the popup name is clicked', () => {
    const contact: Contact = {
      public_key: 'cc'.repeat(32),
      name: 'Clickable',
      type: 1,
      flags: 0,
      direct_path: null,
      direct_path_len: -1,
      direct_path_hash_mode: -1,
      route_override_path: null,
      route_override_len: null,
      route_override_hash_mode: null,
      last_advert: null,
      lat: 42,
      lon: -72,
      last_seen: Math.floor(Date.now() / 1000),
      on_radio: false,
      favorite: false,
      last_contacted: null,
      last_read_at: null,
      first_seen: null,
    };
    const onSelectContact = vi.fn();

    render(<MapView contacts={[contact]} onSelectContact={onSelectContact} />);

    const link = screen.getByRole('button', { name: 'Clickable' });
    expect(link).toHaveAttribute('title', i18n.t('map.openConversation', { name: 'Clickable' }));
    fireEvent.click(link);

    expect(onSelectContact).toHaveBeenCalledWith(contact);
  });

  it('paints a type-4 GPS contact as a teal triangle, not a companion circle or repeater', () => {
    const gpsContact = (name: string, key: string, type: number): Contact => ({
      public_key: key.repeat(32),
      name,
      type,
      flags: 0,
      direct_path: null,
      direct_path_len: -1,
      direct_path_hash_mode: -1,
      route_override_path: null,
      route_override_len: null,
      route_override_hash_mode: null,
      last_advert: null,
      lat: 40 + type,
      lon: -74,
      last_seen: Math.floor(Date.now() / 1000),
      on_radio: false,
      favorite: false,
      last_contacted: null,
      last_read_at: null,
      first_seen: null,
    });
    const companion = gpsContact('Companion', 'aa', 1);
    const sensor = gpsContact('Weather', 'bb', CONTACT_TYPE_SENSOR);
    const repeater = gpsContact('Tower', 'cc', CONTACT_TYPE_REPEATER);

    render(<MapView contacts={[companion, sensor, repeater]} />);

    const sensorPopup = screen.getByTestId('map-sensor-marker');
    const sensorMarker = sensorPopup.closest('[data-shape]');
    expect(sensorMarker).toHaveAttribute('data-shape', 'triangle');
    expect(sensorMarker).toHaveAttribute('data-fill-color', NODE_ROLE_STYLE.sensor.color);
    expect(within(sensorPopup).getByTitle(i18n.t('map.sensorTitle'))).toBeInTheDocument();
    expect(within(sensorPopup).queryByTitle(i18n.t('map.repeaterTitle'))).toBeNull();

    const companionName = screen.getByText('Companion');
    expect(companionName.closest('[data-shape]')).toBeNull();
    expect(companionName.closest('[data-fill-color]')?.getAttribute('data-fill-color')).not.toBe(
      NODE_ROLE_STYLE.sensor.color
    );

    expect(screen.getByTitle(i18n.t('map.repeaterTitle'))).toBeInTheDocument();
    expect(screen.getByText('Tower').closest('[data-shape]')).toBeNull();
  });

  it('renders the popup name as plain text when no onSelectContact is provided', () => {
    const contact: Contact = {
      public_key: 'dd'.repeat(32),
      name: 'Static',
      type: 1,
      flags: 0,
      direct_path: null,
      direct_path_len: -1,
      direct_path_hash_mode: -1,
      route_override_path: null,
      route_override_len: null,
      route_override_hash_mode: null,
      last_advert: null,
      lat: 42,
      lon: -72,
      last_seen: Math.floor(Date.now() / 1000),
      on_radio: false,
      favorite: false,
      last_contacted: null,
      last_read_at: null,
      first_seen: null,
    };

    render(<MapView contacts={[contact]} />);

    expect(
      screen.queryByRole('button', { name: i18n.t('map.openConversation', { name: 'Static' }) })
    ).toBeNull();
    expect(screen.getByText('Static')).toBeInTheDocument();
  });

  it('keeps the relative cutoff stable across re-renders that do not advance the clock', () => {
    vi.useFakeTimers();
    try {
      vi.setSystemTime(new Date('2026-03-15T12:00:00Z'));

      const contact: Contact = {
        public_key: 'bb'.repeat(32),
        name: 'Almost Stale',
        type: 1,
        flags: 0,
        direct_path: null,
        direct_path_len: -1,
        direct_path_hash_mode: -1,
        route_override_path: null,
        route_override_len: null,
        route_override_hash_mode: null,
        last_advert: null,
        lat: 41,
        lon: -73,
        last_seen: Math.floor(Date.now() / 1000) - 7 * 24 * 60 * 60 + 60,
        on_radio: false,
        favorite: false,
        last_contacted: null,
        last_read_at: null,
        first_seen: null,
      };

      const { rerender } = render(<MapView contacts={[contact]} focusedKey={null} />);

      expect(
        screen.getByText(i18n.t('map.showingHeard', { count: 1, since: i18n.t('map.inLast7d') }))
      ).toBeInTheDocument();

      // Re-rendering alone must not recompute the cutoff — that was the memo
      // thrash this guards against (see "Reduce memo thrash on map update").
      rerender(<MapView contacts={[contact]} focusedKey={null} />);

      expect(
        screen.getByText(i18n.t('map.showingHeard', { count: 1, since: i18n.t('map.inLast7d') }))
      ).toBeInTheDocument();
      expect(screen.getByText('Almost Stale')).toBeInTheDocument();
    } finally {
      vi.useRealTimers();
    }
  });

  describe('"heard since" filter', () => {
    function contactLastSeen(name: string, key: string, lastSeen: number | null): Contact {
      return {
        public_key: key.repeat(32),
        name,
        type: 1,
        flags: 0,
        direct_path: null,
        direct_path_len: -1,
        direct_path_hash_mode: -1,
        route_override_path: null,
        route_override_len: null,
        route_override_hash_mode: null,
        last_advert: null,
        lat: 40,
        lon: -74,
        last_seen: lastSeen,
        on_radio: false,
        favorite: false,
        last_contacted: null,
        last_read_at: null,
        first_seen: null,
      };
    }

    const nowSec = () => Math.floor(Date.now() / 1000);

    it('narrows to a relative preset and restores on a wider one', () => {
      const fresh = contactLastSeen('Fresh Node', 'aa', nowSec() - 60);
      const older = contactLastSeen('Older Node', 'bb', nowSec() - 5 * 60 * 60);

      render(<MapView contacts={[fresh, older]} />);

      // Default window is 7 days, so both are visible.
      expect(screen.getByText('Fresh Node')).toBeInTheDocument();
      expect(screen.getByText('Older Node')).toBeInTheDocument();

      fireEvent.click(screen.getByRole('button', { name: '<1h' }));

      expect(screen.getByText('Fresh Node')).toBeInTheDocument();
      expect(screen.queryByText('Older Node')).toBeNull();
      expect(
        screen.getByText(i18n.t('map.showingHeard', { count: 1, since: i18n.t('map.inLast1h') }))
      ).toBeInTheDocument();

      fireEvent.click(screen.getByRole('button', { name: '<1d' }));

      expect(screen.getByText('Older Node')).toBeInTheDocument();
    });

    it('reveals contacts older than the previous 7-day ceiling under "All"', () => {
      const ancient = contactLastSeen('Ancient Node', 'cc', nowSec() - 30 * 24 * 60 * 60);

      render(<MapView contacts={[ancient]} />);

      // Previously the map capped at 7 days and this node was unreachable.
      expect(screen.queryByText('Ancient Node')).toBeNull();

      fireEvent.click(screen.getByRole('button', { name: i18n.t('map.sinceAll') }));

      expect(screen.getByText('Ancient Node')).toBeInTheDocument();
      expect(
        screen.getByText(i18n.t('map.showingHeard', { count: 1, since: i18n.t('map.atAnyTime') }))
      ).toBeInTheDocument();
    });

    it('treats a custom datetime as local wall-clock time', () => {
      vi.useFakeTimers();
      try {
        vi.setSystemTime(new Date('2026-03-15T12:00:00'));

        // 11:00 and 13:00 local, either side of a 12:30 local cutoff.
        const before = contactLastSeen(
          'Before Cutoff',
          'dd',
          Math.floor(new Date('2026-03-15T11:00:00').getTime() / 1000)
        );
        const after = contactLastSeen(
          'After Cutoff',
          'ee',
          Math.floor(new Date('2026-03-15T13:00:00').getTime() / 1000)
        );

        render(<MapView contacts={[before, after]} />);

        fireEvent.click(screen.getByRole('button', { name: i18n.t('map.sinceCustom') }));
        fireEvent.change(screen.getByLabelText(i18n.t('map.sinceCustomAria')), {
          target: { value: '2026-03-15T12:30' },
        });

        expect(screen.getByText('After Cutoff')).toBeInTheDocument();
        expect(screen.queryByText('Before Cutoff')).toBeNull();
      } finally {
        vi.useRealTimers();
      }
    });

    it('always shows the focused contact even when it falls outside the window', () => {
      const stale = contactLastSeen('Stale Focus', 'ff', nowSec() - 30 * 24 * 60 * 60);

      render(<MapView contacts={[stale]} focusedKey={stale.public_key} />);

      fireEvent.click(screen.getByRole('button', { name: '<1h' }));

      expect(screen.getByText('Stale Focus')).toBeInTheDocument();
      expect(
        screen.getByText(
          i18n.t('map.showingHeardFocused', { count: 1, since: i18n.t('map.inLast1h') })
        )
      ).toBeInTheDocument();
    });
  });

  it('excludes contacts whose public key is in blockedKeys', () => {
    const visible: Contact = {
      public_key: 'aa'.repeat(32),
      name: 'Visible',
      type: 1,
      flags: 0,
      direct_path: null,
      direct_path_len: -1,
      direct_path_hash_mode: -1,
      route_override_path: null,
      route_override_len: null,
      route_override_hash_mode: null,
      last_advert: null,
      lat: 40,
      lon: -74,
      last_seen: Math.floor(Date.now() / 1000),
      on_radio: false,
      favorite: false,
      last_contacted: null,
      last_read_at: null,
      first_seen: null,
    };
    const blocked: Contact = {
      public_key: 'bb'.repeat(32),
      name: 'Blocked',
      type: 2,
      flags: 0,
      direct_path: null,
      direct_path_len: -1,
      direct_path_hash_mode: -1,
      route_override_path: null,
      route_override_len: null,
      route_override_hash_mode: null,
      last_advert: null,
      lat: 41,
      lon: -73,
      last_seen: Math.floor(Date.now() / 1000),
      on_radio: false,
      favorite: false,
      last_contacted: null,
      last_read_at: null,
      first_seen: null,
    };

    render(<MapView contacts={[visible, blocked]} blockedKeys={['bb'.repeat(32)]} />);

    expect(screen.getByText('Visible')).toBeInTheDocument();
    expect(screen.queryByText('Blocked')).toBeNull();
  });

  it('excludes contacts whose name is in blockedNames', () => {
    const visible: Contact = {
      public_key: 'aa'.repeat(32),
      name: 'Visible',
      type: 1,
      flags: 0,
      direct_path: null,
      direct_path_len: -1,
      direct_path_hash_mode: -1,
      route_override_path: null,
      route_override_len: null,
      route_override_hash_mode: null,
      last_advert: null,
      lat: 40,
      lon: -74,
      last_seen: Math.floor(Date.now() / 1000),
      on_radio: false,
      favorite: false,
      last_contacted: null,
      last_read_at: null,
      first_seen: null,
    };
    const blocked: Contact = {
      public_key: 'cc'.repeat(32),
      name: 'BadActor',
      type: 2,
      flags: 0,
      direct_path: null,
      direct_path_len: -1,
      direct_path_hash_mode: -1,
      route_override_path: null,
      route_override_len: null,
      route_override_hash_mode: null,
      last_advert: null,
      lat: 41,
      lon: -73,
      last_seen: Math.floor(Date.now() / 1000),
      on_radio: false,
      favorite: false,
      last_contacted: null,
      last_read_at: null,
      first_seen: null,
    };

    render(<MapView contacts={[visible, blocked]} blockedNames={['BadActor']} />);

    expect(screen.getByText('Visible')).toBeInTheDocument();
    expect(screen.queryByText('BadActor')).toBeNull();
  });

  it('hides the internet-relays checkbox until CoreScope is enabled', () => {
    render(<MapView contacts={[]} />);
    expect(
      screen.queryByRole('checkbox', { name: i18n.t('map.internetRelays') })
    ).not.toBeInTheDocument();
  });

  it('does not offer Visualize packets; rain lives on Live', () => {
    render(<MapView contacts={[]} />);
    expect(screen.queryByText(i18n.t('map.visualizePackets'))).not.toBeInTheDocument();
  });

  it('keeps internet relays off by default and styles them blue with an orange ring', async () => {
    vi.mocked(api.getDirectoryMapNodes).mockResolvedValue({
      nodes: [
        {
          public_key: 'ee'.repeat(32),
          name: 'WebRelay',
          role: 'repeater',
          lat: 48.8,
          lon: 2.3,
          source: 'corescope',
        },
      ],
    });

    render(<MapView contacts={[]} directoryEnabled />);

    const toggle = screen.getByRole('checkbox', { name: i18n.t('map.internetRelays') });
    expect(toggle).not.toBeChecked();
    expect(screen.queryByText('WebRelay')).not.toBeInTheDocument();
    expect(api.getDirectoryMapNodes).not.toHaveBeenCalled();

    fireEvent.click(toggle);

    await waitFor(() => {
      expect(screen.getByText('WebRelay')).toBeInTheDocument();
    });
    const marker = screen.getByTestId('directory-map-marker').closest('[data-fill-color]');
    expect(marker).toHaveAttribute('data-fill-color', '#2563eb');
    expect(marker).toHaveAttribute('data-stroke-color', '#f97316');
  });

  it('does not show an internet pin when the repeater is already known by radio', async () => {
    const localKey = 'ee'.repeat(32);
    vi.mocked(api.getDirectoryMapNodes).mockResolvedValue({
      nodes: [
        {
          public_key: localKey,
          name: 'WebName',
          role: 'repeater',
          lat: 48.8,
          lon: 2.3,
          source: 'corescope',
        },
      ],
    });
    const local: Contact = {
      public_key: localKey,
      name: 'RadioName',
      type: 2,
      flags: 0,
      direct_path: null,
      direct_path_len: -1,
      direct_path_hash_mode: -1,
      route_override_path: null,
      route_override_len: null,
      route_override_hash_mode: null,
      last_advert: null,
      lat: 48.8,
      lon: 2.3,
      last_seen: Math.floor(Date.now() / 1000),
      on_radio: false,
      favorite: false,
      last_contacted: null,
      last_read_at: null,
      first_seen: null,
    };

    render(<MapView contacts={[local]} directoryEnabled />);
    fireEvent.click(screen.getByRole('checkbox', { name: i18n.t('map.internetRelays') }));

    await waitFor(() => {
      expect(api.getDirectoryMapNodes).toHaveBeenCalled();
    });
    expect(screen.getByText('RadioName')).toBeInTheDocument();
    expect(screen.queryByText('WebName')).not.toBeInTheDocument();
    expect(screen.queryByTestId('directory-map-marker')).not.toBeInTheDocument();
  });
});
