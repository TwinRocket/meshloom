import { render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import { NeighborsMiniMap } from '../components/NeighborsMiniMap';
import i18n from '../i18n';

vi.mock('react-leaflet', () => ({
  MapContainer: ({ children }: { children: React.ReactNode }) => (
    <div data-testid="map-container">{children}</div>
  ),
  TileLayer: () => null,
  CircleMarker: ({ children }: { children: React.ReactNode }) => <div>{children}</div>,
  Popup: ({ children }: { children: React.ReactNode }) => (
    <div data-testid="neighbor-popup">{children}</div>
  ),
  Tooltip: ({ children }: { children: React.ReactNode }) => (
    <div data-testid="neighbor-tooltip">{children}</div>
  ),
  Polyline: () => null,
  useMap: () => ({
    getContainer: () => ({
      getBoundingClientRect: () => ({ width: 400, height: 300 }),
    }),
    invalidateSize: vi.fn(),
    fitBounds: vi.fn(),
  }),
}));

const neighbor = {
  lat: -31.94,
  lon: 115.87,
  name: 'Neighbor',
  pubkey_prefix: 'bbbbbbbbbbbb',
  snr: 7.2,
  distance: '1.2 km',
};

describe('NeighborsMiniMap labels', () => {
  it('shows only the name in the click popup by default', () => {
    render(
      <NeighborsMiniMap
        neighbors={[neighbor]}
        radioLat={-31.95}
        radioLon={115.86}
        radioName="TestRepeater"
      />
    );

    const popups = screen.getAllByTestId('neighbor-popup');
    expect(popups.some((el) => el.textContent?.includes('Neighbor'))).toBe(true);
    expect(screen.queryByText(`${i18n.t('repeater.snr')}: +7.2 dB`)).toBeNull();
    expect(screen.queryByText(`${i18n.t('repeater.dist')}: 1.2 km`)).toBeNull();
    expect(screen.queryByText(/-31\.94000/)).toBeNull();
    expect(screen.queryByTestId('neighbor-tooltip')).toBeNull();
  });

  it('adds SNR, distance, and GPS to the click popup when detailed', () => {
    render(
      <NeighborsMiniMap
        neighbors={[neighbor]}
        radioLat={-31.95}
        radioLon={115.86}
        radioName="TestRepeater"
        detailed
      />
    );

    expect(screen.getByText(`${i18n.t('repeater.snr')}: +7.2 dB`)).toBeInTheDocument();
    expect(screen.getByText(`${i18n.t('repeater.dist')}: 1.2 km`)).toBeInTheDocument();
    expect(
      screen.getByText(`${i18n.t('repeater.gps')}: -31.94000, 115.87000`)
    ).toBeInTheDocument();
    expect(screen.queryByTestId('neighbor-tooltip')).toBeNull();
  });

  it('keeps a name label on every point when permanent', () => {
    render(
      <NeighborsMiniMap
        neighbors={[neighbor]}
        radioLat={-31.95}
        radioLon={115.86}
        radioName="TestRepeater"
        permanent
      />
    );

    const tooltips = screen.getAllByTestId('neighbor-tooltip');
    expect(tooltips).toHaveLength(2);
    expect(tooltips.some((el) => el.textContent === 'Neighbor')).toBe(true);
    expect(tooltips.some((el) => el.textContent === 'TestRepeater')).toBe(true);
    expect(screen.queryByText(`${i18n.t('repeater.snr')}: +7.2 dB`)).toBeNull();
  });

  it('fills permanent labels with the extra fields when both toggles are on', () => {
    render(
      <NeighborsMiniMap
        neighbors={[neighbor]}
        radioLat={-31.95}
        radioLon={115.86}
        radioName="TestRepeater"
        detailed
        permanent
      />
    );

    expect(screen.getAllByText(`${i18n.t('repeater.snr')}: +7.2 dB`).length).toBeGreaterThan(0);
    expect(
      screen.getAllByText(`${i18n.t('repeater.gps')}: -31.95000, 115.86000`).length
    ).toBeGreaterThan(0);
  });
});
