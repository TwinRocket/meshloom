import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { LocationPickerModal } from '../components/LocationPickerModal';
import { api } from '../api';
import i18n from '../i18n';
import * as timezoneLocation from '../utils/ianaTimezoneLocation';

vi.mock('../api', () => ({
  api: {
    getCommunity: vi.fn(),
    searchCommunityAirports: vi.fn(),
  },
}));

const leafletClick = vi.hoisted(() => ({
  handler: null as ((event: { latlng: { lat: number; lng: number } }) => void) | null,
}));

vi.mock('react-leaflet', () => ({
  MapContainer: ({ children }: { children: React.ReactNode }) => (
    <div data-testid="picker-map">
      <button
        type="button"
        onClick={() => leafletClick.handler?.({ latlng: { lat: 48.8566, lng: 2.3522 } })}
      >
        map-click
      </button>
      {children}
    </div>
  ),
  TileLayer: () => null,
  CircleMarker: () => <div data-testid="picked-marker" />,
  useMap: () => ({
    getContainer: () => ({
      getBoundingClientRect: () => ({ width: 400, height: 300 }),
    }),
    invalidateSize: vi.fn(),
  }),
  useMapEvents: ({
    click,
  }: {
    click: (event: { latlng: { lat: number; lng: number } }) => void;
  }) => {
    leafletClick.handler = click;
    return null;
  },
}));

describe('LocationPickerModal', () => {
  beforeEach(() => {
    leafletClick.handler = null;
    vi.mocked(api.getCommunity).mockReset();
    vi.mocked(api.searchCommunityAirports).mockReset();
    vi.spyOn(timezoneLocation, 'getBrowserTimeZone').mockReturnValue('UTC');
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  it('seeds the pin from the current radio location and applies it', async () => {
    const onApply = vi.fn();
    const onOpenChange = vi.fn();
    render(
      <LocationPickerModal
        open
        onOpenChange={onOpenChange}
        initialLat="45.764043"
        initialLon="4.835659"
        onApply={onApply}
      />
    );

    expect(await screen.findByText('45.764043, 4.835659')).toBeInTheDocument();
    expect(screen.getByTestId('picked-marker')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: i18n.t('settings.radio.pickOnMapSave') }));
    expect(onApply).toHaveBeenCalledWith(45.764043, 4.835659);
    expect(onOpenChange).toHaveBeenCalledWith(false);
  });

  it('lets a map click replace the selected point', async () => {
    const onApply = vi.fn();
    render(
      <LocationPickerModal
        open
        onOpenChange={vi.fn()}
        initialLat="45.764043"
        initialLon="4.835659"
        onApply={onApply}
      />
    );

    fireEvent.click(await screen.findByRole('button', { name: 'map-click' }));
    expect(await screen.findByText('48.856600, 2.352200')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: i18n.t('settings.radio.pickOnMapSave') }));
    expect(onApply).toHaveBeenCalledWith(48.8566, 2.3522);
  });

  it('centers on the Community IATA airport when no radio location is set', async () => {
    vi.mocked(api.getCommunity).mockResolvedValue({
      enabled: true,
      locked: false,
      iata: 'CDG',
      broker_host: '',
      api_base: '',
      publisher_configured: false,
      publisher_connected: false,
      env_seeded: false,
    });
    vi.mocked(api.searchCommunityAirports).mockResolvedValue([
      {
        iata: 'CDG',
        name: 'Charles de Gaulle',
        city: 'Paris',
        country: 'France',
        label: 'Paris (CDG)',
        lat: 49.01278,
        lon: 2.55,
      },
    ]);

    render(
      <LocationPickerModal
        open
        onOpenChange={vi.fn()}
        initialLat="0"
        initialLon="0"
        onApply={vi.fn()}
      />
    );

    await waitFor(() => {
      expect(
        screen.getByText(i18n.t('settings.radio.pickOnMapCenteredIata', { iata: 'CDG' }))
      ).toBeInTheDocument();
    });
    expect(screen.getByText('49.012780, 2.550000')).toBeInTheDocument();
  });

  it('centers on the timezone city when radio location and IATA are missing', async () => {
    vi.mocked(api.getCommunity).mockResolvedValue({
      enabled: true,
      locked: false,
      iata: '',
      broker_host: '',
      api_base: '',
      publisher_configured: false,
      publisher_connected: false,
      env_seeded: false,
    });
    vi.mocked(timezoneLocation.getBrowserTimeZone).mockReturnValue('Europe/Paris');

    render(
      <LocationPickerModal
        open
        onOpenChange={vi.fn()}
        initialLat="0"
        initialLon="0"
        onApply={vi.fn()}
      />
    );

    await waitFor(() => {
      expect(
        screen.getByText(
          i18n.t('settings.radio.pickOnMapCenteredTimezone', {
            city: 'Paris',
            timeZone: 'Europe/Paris',
          })
        )
      ).toBeInTheDocument();
    });
    expect(screen.getByText('48.866667, 2.333333')).toBeInTheDocument();
  });
});
