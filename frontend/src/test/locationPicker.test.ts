import { beforeEach, describe, expect, it, vi } from 'vitest';

import { api } from '../api';
import {
  LOCATION_PICKER_FALLBACK,
  formatPickedCoordinate,
  lookupCommunityIataLocation,
  parseLocationPair,
  resolveLocationPickerOrigin,
} from '../utils/locationPicker';

vi.mock('../api', () => ({
  api: {
    getCommunity: vi.fn(),
    searchCommunityAirports: vi.fn(),
  },
}));

describe('parseLocationPair', () => {
  it('accepts a valid radio location', () => {
    expect(parseLocationPair('45.764043', '4.835659')).toEqual({
      lat: 45.764043,
      lon: 4.835659,
    });
  });

  it('rejects the unset 0,0 sentinel and empty fields', () => {
    expect(parseLocationPair('0', '0')).toBeNull();
    expect(parseLocationPair('', '')).toBeNull();
    expect(parseLocationPair('91', '2')).toBeNull();
  });
});

describe('resolveLocationPickerOrigin', () => {
  it('prefers the current radio location over IATA', () => {
    expect(
      resolveLocationPickerOrigin({ lat: 45.76, lon: 4.83 }, { lat: 49.01, lon: 2.55, iata: 'CDG' })
    ).toEqual({ lat: 45.76, lon: 4.83, zoom: 13, source: 'current' });
  });

  it('falls back to the Community airport', () => {
    expect(resolveLocationPickerOrigin(null, { lat: 49.01278, lon: 2.55, iata: 'CDG' })).toEqual({
      lat: 49.01278,
      lon: 2.55,
      zoom: 11,
      source: 'iata',
      iata: 'CDG',
    });
  });

  it('falls back to the timezone city before the world view', () => {
    expect(
      resolveLocationPickerOrigin(null, null, {
        lat: 48 + 52 / 60,
        lon: 2 + 20 / 60,
        city: 'Paris',
        timeZone: 'Europe/Paris',
      })
    ).toEqual({
      lat: 48 + 52 / 60,
      lon: 2 + 20 / 60,
      zoom: 11,
      source: 'timezone',
      city: 'Paris',
      timeZone: 'Europe/Paris',
    });
  });

  it('prefers IATA over the timezone city', () => {
    expect(
      resolveLocationPickerOrigin(
        null,
        { lat: 49.01278, lon: 2.55, iata: 'CDG' },
        { lat: 48.8667, lon: 2.3333, city: 'Paris', timeZone: 'Europe/Paris' }
      )
    ).toMatchObject({ source: 'iata', iata: 'CDG' });
  });

  it('uses the world view when nothing is known', () => {
    expect(resolveLocationPickerOrigin(null, null, null)).toEqual(LOCATION_PICKER_FALLBACK);
  });
});

describe('formatPickedCoordinate', () => {
  it('keeps six decimals like the geolocation helper', () => {
    expect(formatPickedCoordinate(45.7640432)).toBe('45.764043');
  });
});

describe('lookupCommunityIataLocation', () => {
  beforeEach(() => {
    vi.mocked(api.getCommunity).mockReset();
    vi.mocked(api.searchCommunityAirports).mockReset();
  });

  it('returns the matching airport coordinates', async () => {
    vi.mocked(api.getCommunity).mockResolvedValue({
      enabled: true,
      locked: false,
      iata: 'lys',
      broker_host: '',
      api_base: '',
      publisher_configured: false,
      publisher_connected: false,
      env_seeded: false,
    });
    vi.mocked(api.searchCommunityAirports).mockResolvedValue([
      {
        iata: 'LYN',
        name: 'Lyon-Bron',
        city: 'Lyon',
        country: 'France',
        label: 'Lyon (LYN)',
        lat: 45.72,
        lon: 4.94,
      },
      {
        iata: 'LYS',
        name: 'Lyon-Saint-Exupéry',
        city: 'Lyon',
        country: 'France',
        label: 'Lyon (LYS)',
        lat: 45.7256,
        lon: 5.0811,
      },
    ]);

    await expect(lookupCommunityIataLocation()).resolves.toEqual({
      lat: 45.7256,
      lon: 5.0811,
      iata: 'LYS',
    });
  });

  it('returns null when Community has no IATA or the airport has no coords', async () => {
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
    await expect(lookupCommunityIataLocation()).resolves.toBeNull();

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
      },
    ]);
    await expect(lookupCommunityIataLocation()).resolves.toBeNull();
  });
});
