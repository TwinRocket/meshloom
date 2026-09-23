import type { Contact } from '../types';
import {
  directoryHopLocation,
  findContactsByPrefix,
  isValidLocation,
  resolveHopDisplay,
  resolveLocalHopDisplay,
  type DirectoryHopHit,
  type PathHop,
} from './pathUtils';

/**
 * Turning an observer's raw path prefix into something nameable and placeable.
 *
 * Two surfaces read the same reach payload — the per-message modal and the radio
 * test page — and a hop resolved differently in each would be the same relay under
 * two names on two screens. They share these three functions instead.
 */

export function toPathHop(prefix: string, contacts: Contact[]): PathHop {
  const normalized = prefix.toUpperCase();
  return {
    prefix: normalized,
    matches: findContactsByPrefix(normalized, contacts, true),
    distanceFromPrev: null,
  };
}

export function hopMapLocation(
  hop: PathHop,
  directory?: DirectoryHopHit | null
): { lat: number; lon: number; name: string } | null {
  const local = resolveLocalHopDisplay(hop);
  if (local.kind === 'known' && isValidLocation(local.contact.lat, local.contact.lon)) {
    return {
      lat: local.contact.lat!,
      lon: local.contact.lon!,
      name: local.contact.name || hop.prefix,
    };
  }
  return directoryHopLocation(hop, directory);
}

export function hopDisplayName(hop: PathHop, directory?: DirectoryHopHit | null): string | null {
  const display = resolveHopDisplay(hop, directory);
  if (display.kind === 'known') {
    return display.contact.name || hop.prefix;
  }
  if (display.kind === 'directory') {
    return display.name;
  }
  return null;
}
