import ianaBackward from '../data/iana-backward.txt?raw';
import zone1970Tab from '../data/zone1970.tab?raw';

export type TimezoneLocation = {
  lat: number;
  lon: number;
  city: string;
};

const ISO_6709 = /^([+-])(\d{2})(\d{2})(\d{2})?([+-])(\d{3})(\d{2})(\d{2})?$/;

/** IANA zone1970.tab principal location, ISO 6709 ±DDMM±DDDMM or ±DDMMSS±DDDMMSS. */
export function parseIso6709(raw: string): { lat: number; lon: number } | null {
  const match = raw.trim().match(ISO_6709);
  if (!match) return null;
  const latSign = match[1] === '-' ? -1 : 1;
  const lonSign = match[5] === '-' ? -1 : 1;
  const lat = latSign * (Number(match[2]) + Number(match[3]) / 60 + Number(match[4] ?? 0) / 3600);
  const lon = lonSign * (Number(match[6]) + Number(match[7]) / 60 + Number(match[8] ?? 0) / 3600);
  if (!Number.isFinite(lat) || !Number.isFinite(lon)) return null;
  if (lat < -90 || lat > 90 || lon < -180 || lon > 180) return null;
  return { lat, lon };
}

export function timezoneCityLabel(timeZone: string): string {
  const parts = timeZone.split('/').filter(Boolean);
  const leaf = parts[parts.length - 1] ?? timeZone;
  return leaf.replace(/_/g, ' ');
}

function parseZoneTable(source: string): Map<string, { lat: number; lon: number }> {
  const zones = new Map<string, { lat: number; lon: number }>();
  for (const line of source.split(/\r?\n/)) {
    if (!line || line.startsWith('#')) continue;
    const cols = line.split('\t');
    if (cols.length < 3) continue;
    const coords = parseIso6709(cols[1] ?? '');
    const name = (cols[2] ?? '').trim();
    if (!coords || !name) continue;
    zones.set(name, coords);
  }
  return zones;
}

function parseBackwardAliases(source: string): Map<string, string> {
  const aliases = new Map<string, string>();
  for (const line of source.split(/\r?\n/)) {
    if (!line.startsWith('Link')) continue;
    const parts = line.split(/\s+/).filter(Boolean);
    if (parts.length < 3) continue;
    const target = parts[1];
    const name = parts[2];
    if (!target || !name || name.startsWith('#')) continue;
    aliases.set(name, target);
  }
  return aliases;
}

const ZONE_COORDS = parseZoneTable(zone1970Tab);
const ZONE_ALIASES = parseBackwardAliases(ianaBackward);

function canonicalZoneName(timeZone: string): string {
  let current = timeZone;
  const seen = new Set<string>();
  while (ZONE_ALIASES.has(current) && !seen.has(current)) {
    seen.add(current);
    current = ZONE_ALIASES.get(current) ?? current;
  }
  return current;
}

export function getBrowserTimeZone(): string {
  try {
    return Intl.DateTimeFormat().resolvedOptions().timeZone ?? '';
  } catch {
    return '';
  }
}

export function locationFromTimeZone(timeZone: string): TimezoneLocation | null {
  const trimmed = timeZone.trim();
  if (!trimmed || trimmed === 'UTC' || trimmed === 'GMT' || trimmed.startsWith('Etc/')) {
    return null;
  }
  const coords = ZONE_COORDS.get(trimmed) ?? ZONE_COORDS.get(canonicalZoneName(trimmed));
  if (!coords) return null;
  return { ...coords, city: timezoneCityLabel(trimmed) };
}
