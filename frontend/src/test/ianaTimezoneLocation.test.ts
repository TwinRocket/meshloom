import { describe, expect, it } from 'vitest';

import {
  locationFromTimeZone,
  parseIso6709,
  timezoneCityLabel,
} from '../utils/ianaTimezoneLocation';

describe('parseIso6709', () => {
  it('decodes the IANA Europe/Paris principal location', () => {
    expect(parseIso6709('+4852+00220')).toEqual({
      lat: 48 + 52 / 60,
      lon: 2 + 20 / 60,
    });
  });

  it('decodes seconds and a western longitude', () => {
    expect(parseIso6709('+513030-0000731')).toEqual({
      lat: 51 + 30 / 60 + 30 / 3600,
      lon: -(0 + 7 / 60 + 31 / 3600),
    });
  });
});

describe('timezoneCityLabel', () => {
  it('uses the last IANA path segment as the city', () => {
    expect(timezoneCityLabel('Europe/Paris')).toBe('Paris');
    expect(timezoneCityLabel('America/Argentina/Buenos_Aires')).toBe('Buenos Aires');
  });
});

describe('locationFromTimeZone', () => {
  it('maps Europe/Paris to Paris', () => {
    expect(locationFromTimeZone('Europe/Paris')).toEqual({
      lat: 48 + 52 / 60,
      lon: 2 + 20 / 60,
      city: 'Paris',
    });
  });

  it('follows IANA aliases and keeps the requested city name', () => {
    expect(locationFromTimeZone('Europe/Amsterdam')).toEqual({
      lat: 50 + 50 / 60,
      lon: 4 + 20 / 60,
      city: 'Amsterdam',
    });
  });

  it('ignores UTC-style zones with no city', () => {
    expect(locationFromTimeZone('UTC')).toBeNull();
    expect(locationFromTimeZone('Etc/UTC')).toBeNull();
    expect(locationFromTimeZone('')).toBeNull();
  });
});
