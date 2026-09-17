import { api } from '../api';
import { getBrowserTimeZone, locationFromTimeZone } from './ianaTimezoneLocation';
import { isValidLocation } from './pathUtils';

export type LocationPickerOriginSource = 'current' | 'iata' | 'timezone' | 'fallback';

export type LocationPickerPoint = {
  lat: number;
  lon: number;
};

export type LocationPickerOrigin = LocationPickerPoint & {
  zoom: number;
  source: LocationPickerOriginSource;
  iata?: string;
  city?: string;
  timeZone?: string;
};

export const LOCATION_PICKER_FALLBACK: LocationPickerOrigin = {
  lat: 20,
  lon: 0,
  zoom: 2,
  source: 'fallback',
};

const IATA_RE = /^[A-Z]{3}$/;

export function parseLocationPair(
  latRaw: string | number | null | undefined,
  lonRaw: string | number | null | undefined
): LocationPickerPoint | null {
  const lat = typeof latRaw === 'number' ? latRaw : Number.parseFloat(String(latRaw ?? ''));
  const lon = typeof lonRaw === 'number' ? lonRaw : Number.parseFloat(String(lonRaw ?? ''));
  if (!Number.isFinite(lat) || !Number.isFinite(lon) || !isValidLocation(lat, lon)) return null;
  return { lat, lon };
}

export function formatPickedCoordinate(value: number): string {
  return value.toFixed(6);
}

export function resolveLocationPickerOrigin(
  current: LocationPickerPoint | null,
  iata: (LocationPickerPoint & { iata?: string }) | null,
  timezone: (LocationPickerPoint & { city?: string; timeZone?: string }) | null = null
): LocationPickerOrigin {
  if (current && isValidLocation(current.lat, current.lon)) {
    return { lat: current.lat, lon: current.lon, zoom: 13, source: 'current' };
  }
  if (iata && isValidLocation(iata.lat, iata.lon)) {
    return {
      lat: iata.lat,
      lon: iata.lon,
      zoom: 11,
      source: 'iata',
      ...(iata.iata ? { iata: iata.iata } : {}),
    };
  }
  if (timezone && isValidLocation(timezone.lat, timezone.lon)) {
    return {
      lat: timezone.lat,
      lon: timezone.lon,
      zoom: 11,
      source: 'timezone',
      ...(timezone.city ? { city: timezone.city } : {}),
      ...(timezone.timeZone ? { timeZone: timezone.timeZone } : {}),
    };
  }
  return LOCATION_PICKER_FALLBACK;
}

export function resolveBrowserTimezoneLocation(
  timeZone = getBrowserTimeZone()
): (LocationPickerPoint & { city: string; timeZone: string }) | null {
  const found = locationFromTimeZone(timeZone);
  if (!found) return null;
  return { ...found, timeZone };
}

export async function lookupCommunityIataLocation(): Promise<
  (LocationPickerPoint & { iata: string }) | null
> {
  try {
    const community = await api.getCommunity();
    const iata = (community.iata ?? '').trim().toUpperCase();
    if (!IATA_RE.test(iata)) return null;
    const hits = await api.searchCommunityAirports(iata);
    const hit = hits.find((row) => row.iata.toUpperCase() === iata) ?? null;
    if (!hit) return null;
    const point = parseLocationPair(hit.lat, hit.lon);
    if (!point) return null;
    return { ...point, iata };
  } catch {
    return null;
  }
}
