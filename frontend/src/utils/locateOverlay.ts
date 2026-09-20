import type {
  DirectoryHopHit,
  DirectoryReachResponse,
  DirectoryResolveHopsResponse,
  LocateAnchor,
  LocateDeclaredGps,
  LocateResponse,
  LocateSource,
  LocateUnresolvedHop,
} from '../types';

const HOP_HEX_LENS = new Set([4, 6]);

function isValidLocation(lat: number | null | undefined, lon: number | null | undefined): boolean {
  return (
    lat != null &&
    lon != null &&
    Number.isFinite(lat) &&
    Number.isFinite(lon) &&
    Math.abs(lat) <= 90 &&
    Math.abs(lon) <= 180 &&
    !(lat === 0 && lon === 0)
  );
}

function dedupeAnchors(anchors: LocateAnchor[]): LocateAnchor[] {
  const seen = new Set<string>();
  const out: LocateAnchor[] = [];
  for (const anchor of anchors) {
    const key = `${anchor.kind}:${anchor.public_key ?? ''}:${anchor.lat.toFixed(5)}:${anchor.lon.toFixed(5)}`;
    if (seen.has(key)) continue;
    seen.add(key);
    out.push(anchor);
  }
  return out;
}

function sourceBadge(anchors: LocateAnchor[]): LocateSource | null {
  if (!anchors.length) return null;
  const sources = new Set(anchors.map((anchor) => anchor.source));
  if (sources.size === 1 && sources.has('local')) return 'local';
  if (sources.size === 1 && sources.has('corescope')) return 'corescope';
  return 'mixte';
}

export function mergeReachOverlay(
  result: LocateResponse,
  reach: DirectoryReachResponse | null
): LocateResponse {
  if (!reach) return result;
  const extra: LocateAnchor[] = [];
  for (const observer of reach.observers) {
    if (!isValidLocation(observer.lat, observer.lon)) continue;
    extra.push({
      kind: 'corescope_0hop',
      source: 'corescope',
      name: observer.name,
      public_key: observer.public_key,
      lat: observer.lat as number,
      lon: observer.lon as number,
      radius_km: result.default_radius_km,
      snr: observer.avg_snr ?? null,
      heard_count: observer.count || null,
      calibratable: false,
    });
  }
  let declaredGps: LocateDeclaredGps | null = result.declared_gps;
  if (declaredGps == null && reach.node && isValidLocation(reach.node.lat, reach.node.lon)) {
    declaredGps = {
      lat: reach.node.lat as number,
      lon: reach.node.lon as number,
      source: 'corescope',
    };
  }
  let identity = result.identity;
  if (identity && !identity.name && reach.node?.name) {
    identity = { ...identity, name: reach.node.name };
  }
  const anchors = dedupeAnchors([...result.anchors, ...extra]);
  const emptyReason =
    anchors.length > 0
      ? null
      : result.empty_reason === 'directory_off'
        ? 'directory_off'
        : 'no_anchors';
  return {
    ...result,
    identity,
    anchors,
    declared_gps: declaredGps,
    source: sourceBadge(anchors),
    empty_reason: emptyReason,
  };
}

export function firstHopPrefixes(
  unresolved: LocateUnresolvedHop[],
  anchors: LocateAnchor[]
): string[] {
  const prefixes = new Set<string>();
  for (const hop of unresolved) {
    if (HOP_HEX_LENS.has(hop.prefix.length)) prefixes.add(hop.prefix.toUpperCase());
  }
  for (const anchor of anchors) {
    if (
      anchor.kind === 'first_hop' &&
      anchor.hop_prefix &&
      HOP_HEX_LENS.has(anchor.hop_prefix.length)
    ) {
      prefixes.add(anchor.hop_prefix.toUpperCase());
    }
  }
  return [...prefixes];
}

export function applyHopOverlay(
  result: LocateResponse,
  resolved: DirectoryResolveHopsResponse,
  requestedPrefixes: string[]
): LocateResponse {
  const hits = resolved.resolved ?? {};
  const requested = new Set(requestedPrefixes.map((item) => item.toUpperCase()));
  const keepLocal: LocateAnchor[] = [];
  const replaced: LocateAnchor[] = [];
  const unresolved: LocateUnresolvedHop[] = [];

  for (const anchor of result.anchors) {
    if (anchor.kind !== 'first_hop' || !anchor.hop_prefix) {
      keepLocal.push(anchor);
      continue;
    }
    const prefix = anchor.hop_prefix.toUpperCase();
    if (!HOP_HEX_LENS.has(prefix.length) || !requested.has(prefix)) {
      keepLocal.push(anchor);
      continue;
    }
    const hit = hits[prefix] as DirectoryHopHit | undefined;
    if (!hit) {
      unresolved.push({ prefix, reason: 'unmatched', candidates: [] });
      continue;
    }
    if (!isValidLocation(hit.lat, hit.lon)) {
      unresolved.push({ prefix, reason: 'no_gps', candidates: [] });
      continue;
    }
    replaced.push({
      kind: 'first_hop',
      source: 'corescope',
      name: hit.name || hit.public_key?.slice(0, 12) || prefix,
      public_key: hit.public_key ?? null,
      hop_prefix: prefix.toLowerCase(),
      lat: hit.lat as number,
      lon: hit.lon as number,
      radius_km: result.default_radius_km,
      heard_count: anchor.heard_count,
      last_seen: anchor.last_seen,
      calibratable: Boolean(hit.public_key),
    });
  }

  for (const hop of result.unresolved_hops) {
    const prefix = hop.prefix.toUpperCase();
    const hit = hits[prefix];
    if (!hit || !HOP_HEX_LENS.has(prefix.length)) {
      unresolved.push(hop);
      continue;
    }
    if (!isValidLocation(hit.lat, hit.lon)) {
      unresolved.push({ prefix, reason: 'no_gps', candidates: hop.candidates });
      continue;
    }
    replaced.push({
      kind: 'first_hop',
      source: 'corescope',
      name: hit.name || hit.public_key?.slice(0, 12) || prefix,
      public_key: hit.public_key ?? null,
      hop_prefix: prefix.toLowerCase(),
      lat: hit.lat as number,
      lon: hit.lon as number,
      radius_km: result.default_radius_km,
      calibratable: Boolean(hit.public_key),
    });
  }

  const anchors = dedupeAnchors([...keepLocal, ...replaced]);
  const emptyReason =
    anchors.length > 0
      ? null
      : result.empty_reason === 'directory_off'
        ? 'directory_off'
        : 'no_anchors';
  return {
    ...result,
    anchors,
    unresolved_hops: unresolved,
    source: sourceBadge(anchors),
    empty_reason: emptyReason,
  };
}

export function diskBounds(
  anchors: LocateAnchor[],
  declaredGps: LocateDeclaredGps | null
): [number, number][] {
  const points: [number, number][] = [];
  const kmPerDegLat = 111;
  for (const anchor of anchors) {
    const latDelta = anchor.radius_km / kmPerDegLat;
    const lonScale = Math.max(0.2, Math.cos((anchor.lat * Math.PI) / 180));
    const lonDelta = anchor.radius_km / (kmPerDegLat * lonScale);
    points.push([anchor.lat - latDelta, anchor.lon - lonDelta]);
    points.push([anchor.lat + latDelta, anchor.lon + lonDelta]);
  }
  if (declaredGps) points.push([declaredGps.lat, declaredGps.lon]);
  return points;
}
