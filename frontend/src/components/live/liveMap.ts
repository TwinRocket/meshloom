import { MapboxOverlay } from '@deck.gl/mapbox';
import { IconLayer, PathLayer, ScatterplotLayer } from '@deck.gl/layers';
import maplibregl, { LngLatBounds, Map as MapLibreMap, NavigationControl } from 'maplibre-gl';

import type { DirectoryMapNode, DirectoryNodeRole, RadioConfig } from '../../types';
import { osmDarkRasterStyle } from '../../utils/mapTiles';
import { isValidLocation } from '../../utils/pathUtils';
import {
  LASER_GLOW_ALPHA,
  LIVE_HOLD_MS,
  LIVE_RIPPLE_MS,
  LIVE_STAGGER_MS,
  LOCAL_RADIO_VISUAL,
  MAX_CONCURRENT_ANIMS,
  MAX_LIVE_SHOTS,
  MAX_PENDING_ANIMS,
  buildRoleIconAtlas,
  cloneLonLat,
  cloneLonLatPath,
  dashLonLat,
  drawableLaserPolyline,
  findPinnedHop,
  geometryDirectoryNodes,
  hexToRgba,
  hopVertexT,
  interpolatePolyline,
  laserGlowWidth,
  laserHeadRadii,
  laserPolylineOriginToHop,
  laserRemanenceMs,
  laserTravel,
  laserTravelMs,
  laserWidth,
  liveHoverKey,
  mappableDirectoryNodes,
  nearerEndpointLabel,
  nodeRoleStyle,
  normalizeDirectoryRole,
  observationColor,
  observationDrawOpacity,
  observationPassesFilters,
  placeableEdges,
  readLiveCamera,
  remanenceOpacity,
  rippleRadii,
  selectCatchup,
  shouldAutoFitCamera,
  shouldSpawnLaser,
  strokeStyleForConfidence,
  visibleEdgeSlices,
  writeLiveCamera,
  type LaserPolyline,
  type LiveRoleShape,
  type LiveViewFilters,
  type LonLat,
  type Rgba,
} from './liveRender';
import type {
  FanoutPlan,
  LiveObservation,
  LiveOriginPin,
  LiveRouteKind,
  LiveWaypoint,
} from '../../utils/livePackets';
import {
  fanoutFromOrigin,
  firstHopWaypoint,
  inferFloodForAdvert,
  mergeRouteKind,
  observationCoalesceKey,
  observationHasLocalTwin,
  pinsIncludingLocalRadio,
  prependOrigin,
  resolveOriginPin,
  snrWeight,
} from '../../utils/livePackets';

/** As close in as framing the nodes is allowed to go — a lone node must not put
 *  the camera in a street. */
const LIVE_FIT_MAX_ZOOM = 11;

export const LIVE_MAP_STYLE = osmDarkRasterStyle();

export const LIVE_MAP_ATTRIBUTION = [
  '<a href="https://www.openstreetmap.org/copyright">© OpenStreetMap</a>',
];

const builtRoleIcons = buildRoleIconAtlas();
const ROLE_ICONS = {
  atlas: roleIconAtlasUrl(builtRoleIcons.atlas),
  mapping: builtRoleIcons.mapping,
};

function roleIconAtlasUrl(canvas: HTMLCanvasElement): string {
  try {
    return canvas.toDataURL('image/png');
  } catch {
    return '';
  }
}

const ADDITIVE = {
  depthWriteEnabled: false,
  blend: true,
  blendColorOperation: 'add',
  blendColorSrcFactor: 'src-alpha',
  blendColorDstFactor: 'one',
  blendAlphaOperation: 'add',
  blendAlphaSrcFactor: 'one',
  blendAlphaDstFactor: 'one',
} as const;

export type LiveHoverPayload =
  | { kind: 'node'; name: string; role: DirectoryNodeRole; publicKey: string; x: number; y: number }
  | { kind: 'probable'; reason: string; label?: string; x: number; y: number }
  | { kind: 'exact'; label?: string; x: number; y: number }
  | { kind: 'local'; x: number; y: number };

type HoverHandler = (hover: LiveHoverPayload | null) => void;
type NodeClickHandler = (publicKey: string) => void;

interface PathSprite {
  id: string;
  path: LonLat[];
  color: Rgba;
  width: number;
  pick: LiveHoverPayload | null;
  fromLabel?: string;
  toLabel?: string;
}

interface PointSprite {
  id: string;
  position: LonLat;
  fill: Rgba;
  line: Rgba;
  radius: number;
  shape: LiveRoleShape;
  pick: LiveHoverPayload | null;
}

interface LaserShot {
  id: string;
  obs: LiveObservation;
  poly: LaserPolyline;
  colorHex: string;
  opacity: number;
  width: number;
  startedAt: number;
  finishedAt: number | null;
  travelMs: number;
  remanenceMs: number;
  rippledHops: Set<number>;
}

interface HoldBucket {
  key: string;
  firstSeenAt: number;
  observations: LiveObservation[];
}

interface ReleasedBucket {
  routeKind: LiveRouteKind;
  originSpawned: boolean;
  origin: LiveOriginPin | null;
  spawnedKeys: Set<string>;
}

interface RippleSprite {
  id: string;
  position: LonLat;
  colorHex: string;
  startedAt: number;
  pinRadius: number;
}

interface LocalRadioMarker {
  id: string;
  lon: number;
  lat: number;
}

interface PendingShot {
  id: string;
  obs: LiveObservation;
  poly: LaserPolyline;
}

export interface LiveMapOptions {
  onHover?: HoverHandler;
  onNodeClick?: NodeClickHandler;
  now?: () => number;
}

export interface LiveShotSnapshot {
  id: string;
  pointCount: number;
  firstPoint?: LonLat;
  lastPoint?: LonLat;
  vertexKinds?: Array<LiveWaypoint['kind']>;
  finishedAt: number | null;
  startedAt: number;
  travelMs: number;
  remanenceMs: number;
}

export interface LiveRippleSnapshot {
  id: string;
  startedAt: number;
}

export class LiveMapController {
  private readonly onHover: HoverHandler;
  private readonly onNodeClick: NodeClickHandler;
  private readonly wallClock: () => number;
  private map: MapLibreMap | null = null;
  private overlay: MapboxOverlay | null = null;
  private raf = 0;
  private ready = false;
  private destroyed = false;
  private playing = true;
  private pausedAt: number | null = null;
  private clockOffset = 0;
  private frame = 0;
  private userMoved = false;
  private fitted = false;
  private lastHoverKey = '';
  private resizeObserver: ResizeObserver | null = null;

  private filters: LiveViewFilters = { iata: '', hiddenTypes: new Set(), exactOnly: false };
  private localHash8 = new Set<string>();
  private seen = new Set<string>();
  private shots: LaserShot[] = [];
  private pendingShots: PendingShot[] = [];
  private droppedPending = 0;
  private droppedCatchup = 0;
  private buckets = new Map<string, HoldBucket>();
  private released = new Map<string, ReleasedBucket>();
  private ripples: RippleSprite[] = [];
  private nextFreeStartAt = 0;
  private nodes: DirectoryMapNode[] = [];
  private geometryNodes: DirectoryMapNode[] = [];
  private localRadio: LocalRadioMarker | null = null;
  private localRadioConfig: RadioConfig | null = null;
  /** last_seen is omitted so telemetry churn does not re-upload static pins. */
  private nodeFingerprint = '';
  private localFingerprint = '';
  private nodeRevision = 0;
  private localRevision = 0;

  private glowSprites: PathSprite[] = [];
  private coreSprites: PathSprite[] = [];
  private nodeSprites: PointSprite[] = [];
  private localSprites: PointSprite[] = [];
  private headSprites: PointSprite[] = [];
  private rippleSprites: PointSprite[] = [];

  constructor(container: HTMLElement, options: LiveMapOptions = {}) {
    this.onHover = options.onHover ?? (() => {});
    this.onNodeClick = options.onNodeClick ?? (() => {});
    this.wallClock = options.now ?? (() => performance.now());
    const saved = readLiveCamera();
    container.classList.add('live-map-osm');
    this.map = new maplibregl.Map({
      container,
      style: LIVE_MAP_STYLE,
      center: [saved?.lon ?? 8, saved?.lat ?? 24],
      zoom: saved?.zoom ?? 2.15,
      attributionControl: {
        compact: true,
        customAttribution: LIVE_MAP_ATTRIBUTION,
      },
      fadeDuration: 0,
      maxPitch: 55,
    });
    this.map.addControl(new NavigationControl({ visualizePitch: true }), 'bottom-right');
    this.overlay = new MapboxOverlay({
      interleaved: false,
      layers: [],
      pickingRadius: 10,
      getCursor: ({ isHovering }: { isHovering: boolean }) => (isHovering ? 'pointer' : 'grab'),
      onHover: (info: {
        object?: {
          pick?: LiveHoverPayload | null;
          path?: LonLat[];
          fromLabel?: string;
          toLabel?: string;
        };
        x: number;
        y: number;
        coordinate?: number[];
      }) => {
        this.handleHover(info);
      },
      onClick: (info: { object?: { pick?: LiveHoverPayload | null } }) => {
        const pick = info.object?.pick;
        if (pick?.kind === 'node' && pick.publicKey) this.onNodeClick(pick.publicKey);
      },
    });

    this.map.on('load', () => {
      if (this.destroyed || !this.map || !this.overlay) return;
      this.map.addControl(this.overlay);
      this.ready = true;
      this.draw();
      this.startLoop();
    });
    this.map.on('movestart', (event: { originalEvent?: Event }) => {
      if (event.originalEvent) this.userMoved = true;
    });
    this.map.on('moveend', () => this.persistCamera());
    this.map.on('zoomend', () => this.persistCamera());

    this.resizeObserver = new ResizeObserver(() => {
      this.map?.resize();
    });
    this.resizeObserver.observe(container);
  }

  destroy(): void {
    this.destroyed = true;
    cancelAnimationFrame(this.raf);
    this.resizeObserver?.disconnect();
    this.resizeObserver = null;
    if (this.map && this.overlay) {
      try {
        this.map.removeControl(this.overlay);
      } catch {
        /* overlay may already be gone */
      }
    }
    this.overlay = null;
    this.map?.remove();
    this.map = null;
  }

  setPlaying(playing: boolean): void {
    if (playing === this.playing) return;
    const now = this.wallClock();
    if (!playing) {
      this.pausedAt = now;
      this.playing = false;
      cancelAnimationFrame(this.raf);
      this.raf = 0;
      this.draw();
      return;
    }
    if (this.pausedAt != null) {
      this.clockOffset += now - this.pausedAt;
      this.pausedAt = null;
    }
    this.playing = true;
    this.startLoop();
  }

  setFilters(filters: LiveViewFilters): void {
    this.filters = filters;
    this.draw();
  }

  setLocalRadio(config: RadioConfig | null): void {
    if (config && isValidLocation(config.lat, config.lon)) {
      this.localRadioConfig = config;
      this.localRadio = {
        id: (config.public_key || 'local-radio').slice(0, 16),
        lon: config.lon,
        lat: config.lat,
      };
    } else {
      this.localRadioConfig = null;
      this.localRadio = null;
    }
    this.syncLocalSprites();
    this.draw();
  }

  setDirectoryNodes(nodes: DirectoryMapNode[]): void {
    this.geometryNodes = geometryDirectoryNodes(nodes);
    this.nodes = mappableDirectoryNodes(this.geometryNodes);
    this.syncNodeSprites();
    this.maybeFitNodes();
    this.draw();
  }

  syncObservations(observations: LiveObservation[], localHash8: Set<string>): void {
    this.localHash8 = localHash8;
    const now = this.now();
    for (const obs of observations) {
      if (this.seen.has(obs.id)) continue;
      this.seen.add(obs.id);
      if (!shouldSpawnLaser(obs)) continue;
      this.enqueueObservation(obs, now);
    }
    this.releaseDueBuckets(now);
    this.draw();
    if (this.playing) this.startLoop();
  }

  getShotSnapshots(): LiveShotSnapshot[] {
    return this.shots.map((shot) => ({
      id: shot.id,
      pointCount: shot.poly.points.length,
      firstPoint: shot.poly.points[0],
      lastPoint: shot.poly.points[shot.poly.points.length - 1],
      vertexKinds: shot.poly.vertexKind.slice(),
      finishedAt: shot.finishedAt,
      startedAt: shot.startedAt,
      travelMs: shot.travelMs,
      remanenceMs: shot.remanenceMs,
    }));
  }

  drawnPublicKeys(): string[] {
    return this.nodes.map((node) => node.public_key);
  }

  geometryPublicKeys(): string[] {
    return this.geometryNodes.map((node) => node.public_key);
  }

  getRippleSnapshots(): LiveRippleSnapshot[] {
    return this.ripples.map((ripple) => ({ id: ripple.id, startedAt: ripple.startedAt }));
  }

  pendingBucketCount(): number {
    return this.buckets.size;
  }

  pendingAnimCount(): number {
    return this.pendingShots.length;
  }

  droppedPendingCount(): number {
    return this.droppedPending;
  }

  droppedCatchupCount(): number {
    return this.droppedCatchup;
  }

  private now(): number {
    if (this.pausedAt != null) return this.pausedAt - this.clockOffset;
    return this.wallClock() - this.clockOffset;
  }

  private geometryPins(): LiveOriginPin[] {
    return pinsIncludingLocalRadio(this.geometryNodes, this.localRadioConfig);
  }

  private enqueueObservation(obs: LiveObservation, now: number): void {
    const key = observationCoalesceKey(obs);
    const released = this.released.get(key);
    if (released) {
      released.routeKind = mergeRouteKind(released.routeKind, obs.routeKind);
      const plan = fanoutFromOrigin(
        { observations: [obs] },
        this.geometryPins(),
        released.spawnedKeys
      );
      this.applyFanout(plan, now, false);
      this.rememberFanoutKeys(released, plan);
      return;
    }
    const existing = this.buckets.get(key);
    if (existing) {
      existing.observations.push(obs);
      return;
    }
    this.buckets.set(key, { key, firstSeenAt: now, observations: [obs] });
  }

  private releaseDueBuckets(now: number): void {
    const due: HoldBucket[] = [];
    for (const bucket of this.buckets.values()) {
      if (now - bucket.firstSeenAt >= LIVE_HOLD_MS) due.push(bucket);
    }
    due.sort((a, b) => a.firstSeenAt - b.firstSeenAt || a.key.localeCompare(b.key));
    for (const bucket of due) {
      this.buckets.delete(bucket.key);
      this.releaseBucket(bucket, now);
    }
  }

  private releaseBucket(bucket: HoldBucket, now: number): void {
    const spawnIds = selectCatchup(bucket.observations.map((obs) => obs.id));
    const trimmed = bucket.observations.length - spawnIds.size;
    if (trimmed > 0) this.droppedCatchup += trimmed;
    const kept = bucket.observations.filter((obs) => spawnIds.has(obs.id));
    let routeKind: LiveRouteKind = 'unknown';
    for (const obs of kept) routeKind = mergeRouteKind(routeKind, obs.routeKind);
    const startedAt =
      this.inFlightCount() >= MAX_CONCURRENT_ANIMS ? null : this.allocateStartTime(now);
    const pins = this.geometryPins();
    const representative = kept[kept.length - 1];
    const origin = representative ? resolveOriginPin(representative, pins) : null;
    const released: ReleasedBucket = {
      routeKind,
      originSpawned: origin != null,
      origin,
      spawnedKeys: new Set(),
    };
    this.released.set(bucket.key, released);
    if (!representative) return;
    if (startedAt != null) {
      this.spawnOriginRipple([representative], routeKind, startedAt);
    }
    this.spawnShot(representative, routeKind, startedAt, { originRipple: false });
    const first = firstHopWaypoint(representative);
    if (first)
      released.spawnedKeys.add(`${first.token.trim().toLowerCase()}@${first.lat},${first.lon}`);
    if (first?.pubkey) released.spawnedKeys.add(first.pubkey.trim().toLowerCase());
    const plan = fanoutFromOrigin({ observations: kept }, pins, released.spawnedKeys);
    this.applyFanout(plan, startedAt, false);
    this.rememberFanoutKeys(released, plan);
  }

  private rememberFanoutKeys(released: ReleasedBucket, plan: FanoutPlan): void {
    for (const laser of plan.lasers) released.spawnedKeys.add(laser.key);
    for (const flash of plan.hopFlashes) released.spawnedKeys.add(flash.key);
    for (const flash of plan.earFlashes) released.spawnedKeys.add(flash.key);
  }

  private applyFanout(plan: FanoutPlan, startedAt: number | null, originRipple: boolean): void {
    const sample =
      plan.lasers[0]?.obs ?? plan.hopFlashes[0]?.obs ?? plan.earFlashes[0]?.obs ?? null;
    if (originRipple && plan.origin && sample && startedAt != null) {
      this.pushRipple(
        `origin:${plan.origin.public_key}`,
        [plan.origin.lon, plan.origin.lat],
        sample,
        startedAt,
        plan.origin
      );
    }
    for (const laser of plan.lasers) {
      this.pushPolylineShot(
        `fanout:${laser.key}`,
        laser.obs,
        laserPolylineOriginToHop(laser.origin, laser.hop),
        startedAt
      );
    }
    for (const flash of plan.hopFlashes) {
      this.flashPoint(`hopflash:${flash.key}`, flash.obs, flash.lat, flash.lon, 'hop', startedAt);
    }
    for (const flash of plan.earFlashes) {
      this.flashPoint(`ear:${flash.key}`, flash.obs, flash.lat, flash.lon, 'ear', startedAt);
    }
  }

  private flashPoint(
    id: string,
    obs: LiveObservation,
    lat: number,
    lon: number,
    kind: LiveWaypoint['kind'],
    startedAt: number | null
  ): void {
    this.pushPolylineShot(
      id,
      obs,
      {
        points: [[lon, lat]],
        vertexLabel: [undefined],
        vertexKind: [kind],
        vertexPubkey: [undefined],
        vertexToken: [undefined],
        edgeConfidence: [],
        edgeReason: [],
        edgeLabel: [],
      },
      startedAt
    );
  }

  private spawnOriginRipple(
    observations: LiveObservation[],
    _routeKind: LiveRouteKind,
    startedAt: number
  ): void {
    const pins = this.geometryPins();
    for (const obs of observations) {
      const origin = resolveOriginPin(obs, pins);
      if (!origin) continue;
      const id = `origin:${origin.public_key}`;
      if (this.ripples.some((ripple) => ripple.id === id)) return;
      this.pushRipple(id, [origin.lon, origin.lat], obs, startedAt, origin);
      return;
    }
  }

  private spawnShot(
    obs: LiveObservation,
    routeKind: LiveRouteKind,
    startedAt: number | null,
    opts: { originRipple: boolean }
  ): void {
    const withOrigin: LiveObservation = {
      ...obs,
      waypoints: prependOrigin(obs, this.geometryPins()),
    };
    const poly = drawableLaserPolyline(withOrigin, routeKind);
    this.pushPolylineShot(obs.id, withOrigin, poly, startedAt);
    if (opts.originRipple && startedAt != null) {
      this.spawnOriginRipple([obs], routeKind, startedAt);
    }
  }

  private maxStaggerAheadMs(): number {
    return (MAX_CONCURRENT_ANIMS - 1) * LIVE_STAGGER_MS;
  }

  /** Space one wave of releases. A gap longer than LIVE_STAGGER_MS snaps back to now. */
  private allocateStartTime(now: number): number | null {
    const startedAt = Math.max(now, this.nextFreeStartAt);
    if (startedAt - now > this.maxStaggerAheadMs()) return null;
    this.nextFreeStartAt = startedAt + LIVE_STAGGER_MS;
    return startedAt;
  }

  private inFlightCount(): number {
    const now = this.now();
    return this.shots.filter((shot) => shot.finishedAt == null && shot.startedAt <= now).length;
  }

  private enqueuePendingShot(id: string, obs: LiveObservation, poly: LaserPolyline): void {
    this.pendingShots.push({ id, obs, poly });
    while (this.pendingShots.length > MAX_PENDING_ANIMS) {
      this.pendingShots.shift();
      this.droppedPending += 1;
    }
  }

  private drainPendingShots(now: number): void {
    while (this.inFlightCount() < MAX_CONCURRENT_ANIMS && this.pendingShots.length > 0) {
      const startedAt = this.allocateStartTime(now);
      if (startedAt == null) break;
      const item = this.pendingShots.shift();
      if (!item) break;
      this.startPolylineShot(item.id, item.obs, item.poly, startedAt);
    }
  }

  private pushPolylineShot(
    id: string,
    obs: LiveObservation,
    poly: LaserPolyline,
    startedAt: number | null
  ): void {
    if (poly.points.length < 1) return;
    if (startedAt == null || this.inFlightCount() >= MAX_CONCURRENT_ANIMS) {
      this.enqueuePendingShot(id, obs, poly);
      return;
    }
    this.startPolylineShot(id, obs, poly, startedAt);
  }

  private startPolylineShot(
    id: string,
    obs: LiveObservation,
    poly: LaserPolyline,
    startedAt: number
  ): void {
    const twin = obs.source === 'community' && observationHasLocalTwin(obs, this.localHash8);
    const edges = placeableEdges(poly.points.length);
    this.shots.push({
      id,
      obs,
      poly,
      colorHex: observationColor(obs),
      opacity: observationDrawOpacity(obs, twin),
      width: laserWidth(snrWeight(obs.snr)),
      startedAt,
      finishedAt: null,
      travelMs: laserTravelMs(edges),
      remanenceMs: laserRemanenceMs(edges),
      rippledHops: new Set(),
    });
    if (this.shots.length > MAX_LIVE_SHOTS) {
      this.shots = this.shots.slice(this.shots.length - MAX_LIVE_SHOTS);
    }
  }

  private pushRipple(
    id: string,
    position: LonLat,
    obs: LiveObservation,
    startedAt: number,
    pin: { lat: number; lon: number; public_key?: string }
  ): void {
    const node = pin.public_key
      ? this.geometryNodes.find(
          (item) => item.public_key.toLowerCase() === pin.public_key!.toLowerCase()
        )
      : undefined;
    const style = nodeRoleStyle(node?.role);
    this.ripples.push({
      id,
      position,
      colorHex: observationColor(obs),
      startedAt,
      pinRadius: style.radius,
    });
  }

  private maybeRippleHops(shot: LaserShot, headT: number, now: number): void {
    if (!this.isFloodShot(shot)) return;
    const count = shot.poly.points.length;
    for (let i = 0; i < count; i++) {
      if (shot.poly.vertexKind[i] !== 'hop') continue;
      if (shot.rippledHops.has(i)) continue;
      if (headT + 1e-6 < hopVertexT(count, i)) continue;
      const pin = findPinnedHop(
        shot.poly.vertexToken[i],
        shot.poly.vertexPubkey[i],
        this.geometryNodes
      );
      shot.rippledHops.add(i);
      if (!pin) continue;
      this.pushRipple(`${shot.id}:hop:${i}`, [pin.lon, pin.lat], shot.obs, now, pin);
    }
  }

  private isFloodShot(shot: LaserShot): boolean {
    const released = this.released.get(observationCoalesceKey(shot.obs));
    return (released?.routeKind ?? inferFloodForAdvert(shot.obs)) === 'flood';
  }

  private startLoop(): void {
    if (this.destroyed || !this.playing || this.raf) return;
    const tick = () => {
      this.raf = 0;
      if (this.destroyed || !this.playing) return;
      this.draw();
      if (this.hasMovingWork()) {
        this.raf = requestAnimationFrame(tick);
      }
    };
    this.raf = requestAnimationFrame(tick);
  }

  private hasMovingWork(): boolean {
    const now = this.now();
    if (this.buckets.size > 0) return true;
    if (this.pendingShots.length > 0) return true;
    if (this.ripples.some((ripple) => now - ripple.startedAt < LIVE_RIPPLE_MS)) return true;
    return this.shots.some(
      (shot) => shot.finishedAt == null || now - shot.finishedAt < shot.remanenceMs
    );
  }

  private persistCamera(): void {
    if (!this.map || !this.userMoved) return;
    const center = this.map.getCenter();
    writeLiveCamera({ lat: center.lat, lon: center.lng, zoom: this.map.getZoom() });
  }

  private directoryPinFingerprint(nodes: readonly DirectoryMapNode[]): string {
    return nodes
      .map((node) => `${node.public_key}|${node.lat}|${node.lon}|${node.role}|${node.name}`)
      .sort()
      .join('\n');
  }

  private localRadioPinFingerprint(radio: LocalRadioMarker | null): string {
    return radio ? `${radio.id}|${radio.lat}|${radio.lon}` : '';
  }

  private syncNodeSprites(): void {
    const fingerprint = this.directoryPinFingerprint(this.nodes);
    if (fingerprint === this.nodeFingerprint) return;
    this.nodeFingerprint = fingerprint;
    let nodeCount = 0;
    for (const node of this.nodes) {
      const style = nodeRoleStyle(node.role);
      nodeCount = this.emitPoint(
        this.nodeSprites,
        nodeCount,
        node.public_key,
        [node.lon, node.lat],
        hexToRgba(style.color, 0.88),
        hexToRgba('#020617', 0.7),
        style.radius,
        {
          kind: 'node',
          name: node.name,
          role: normalizeDirectoryRole(node.role),
          publicKey: node.public_key,
          x: 0,
          y: 0,
        },
        style.shape
      );
    }
    this.nodeSprites.length = nodeCount;
    this.nodeRevision += 1;
  }

  private syncLocalSprites(): void {
    const fingerprint = this.localRadioPinFingerprint(this.localRadio);
    if (fingerprint === this.localFingerprint) return;
    this.localFingerprint = fingerprint;
    if (!this.localRadio) {
      this.localSprites.length = 0;
    } else {
      this.emitPoint(
        this.localSprites,
        0,
        this.localRadio.id,
        [this.localRadio.lon, this.localRadio.lat],
        hexToRgba(LOCAL_RADIO_VISUAL.color, LOCAL_RADIO_VISUAL.opacity),
        hexToRgba(LOCAL_RADIO_VISUAL.ring, 0.85),
        LOCAL_RADIO_VISUAL.radius,
        { kind: 'local', x: 0, y: 0 }
      );
      this.localSprites.length = 1;
    }
    this.localRevision += 1;
  }

  private maybeFitNodes(): void {
    if (!this.map || this.fitted || !shouldAutoFitCamera(this.userMoved, this.nodes.length)) {
      return;
    }
    const bounds = new LngLatBounds();
    for (const node of this.nodes) bounds.extend([node.lon, node.lat]);
    this.map.fitBounds(bounds, { padding: 56, maxZoom: LIVE_FIT_MAX_ZOOM, duration: 0 });
    this.fitted = true;
  }

  private handleHover(info: {
    object?: {
      pick?: LiveHoverPayload | null;
      path?: LonLat[];
      fromLabel?: string;
      toLabel?: string;
    };
    x: number;
    y: number;
    coordinate?: number[];
  }): void {
    const raw = info.object?.pick ?? null;
    const pick = this.resolvePathPick(raw, info.object, info.coordinate);
    const key = pick ? `${liveHoverKey(pick)}:${info.x}:${info.y}` : '';
    if (key === this.lastHoverKey) return;
    this.lastHoverKey = key;
    if (!pick) {
      this.onHover(null);
      return;
    }
    this.onHover({ ...pick, x: info.x, y: info.y });
  }

  private resolvePathPick(
    pick: LiveHoverPayload | null,
    object: { path?: LonLat[]; fromLabel?: string; toLabel?: string } | undefined,
    coordinate: number[] | undefined
  ): LiveHoverPayload | null {
    if (!pick || (pick.kind !== 'exact' && pick.kind !== 'probable')) return pick;
    const path = object?.path;
    if (!path || path.length < 2 || !coordinate || coordinate.length < 2) return pick;
    const label = nearerEndpointLabel(path, object.fromLabel, object.toLabel, [
      coordinate[0],
      coordinate[1],
    ]);
    return label === pick.label ? pick : { ...pick, label };
  }

  private takeSprite<T>(pool: T[], index: number, factory: () => T): T {
    const existing = pool[index];
    if (existing) return existing;
    const created = factory();
    pool[index] = created;
    return created;
  }

  private resetColor(color: Rgba, next: Rgba): void {
    color[0] = next[0];
    color[1] = next[1];
    color[2] = next[2];
    color[3] = next[3];
  }

  private emitPath(
    pool: PathSprite[],
    index: number,
    id: string,
    path: LonLat[],
    color: Rgba,
    width: number,
    pick: LiveHoverPayload | null
  ): number {
    const sprite = this.takeSprite(pool, index, () => ({
      id,
      path: [],
      color: [0, 0, 0, 0] as Rgba,
      width,
      pick: null,
    }));
    sprite.id = id;
    sprite.path = cloneLonLatPath(path);
    this.resetColor(sprite.color, color);
    sprite.width = width;
    sprite.pick = pick;
    return index + 1;
  }

  private emitPoint(
    pool: PointSprite[],
    index: number,
    id: string,
    position: LonLat,
    fill: Rgba,
    line: Rgba,
    radius: number,
    pick: LiveHoverPayload | null,
    shape: LiveRoleShape = 'circle'
  ): number {
    const sprite = this.takeSprite(pool, index, () => ({
      id,
      position: [0, 0] as LonLat,
      fill: [0, 0, 0, 0] as Rgba,
      line: [0, 0, 0, 0] as Rgba,
      radius,
      shape,
      pick: null,
    }));
    sprite.id = id;
    sprite.position = cloneLonLat(position);
    this.resetColor(sprite.fill, fill);
    this.resetColor(sprite.line, line);
    sprite.radius = radius;
    sprite.shape = shape;
    sprite.pick = pick;
    return index + 1;
  }

  private pickForSlice(
    confidence: 'exact' | 'probable',
    reason: string | undefined,
    label: string | undefined
  ): LiveHoverPayload | null {
    if (confidence === 'probable') {
      return { kind: 'probable', reason: reason ?? '', label, x: 0, y: 0 };
    }
    if (label) return { kind: 'exact', label, x: 0, y: 0 };
    return null;
  }

  private emitStyledPath(
    glowCount: number,
    coreCount: number,
    id: string,
    path: LonLat[],
    colorHex: string,
    width: number,
    alpha: number,
    confidence: 'exact' | 'probable',
    reason?: string,
    fromLabel?: string,
    toLabel?: string
  ): [number, number] {
    if (path.length < 2 || alpha <= 0.01) return [glowCount, coreCount];
    const style = strokeStyleForConfidence(confidence, width);
    const pieces = style.dashed ? dashLonLat(path[0], path[path.length - 1]) : [path];
    const pick = this.pickForSlice(confidence, reason, toLabel ?? fromLabel);
    const glow = hexToRgba(colorHex, alpha * style.opacityScale * LASER_GLOW_ALPHA);
    const core = hexToRgba(colorHex, alpha * style.opacityScale);
    for (let i = 0; i < pieces.length; i++) {
      const piece = pieces[i];
      glowCount = this.emitLabeledPath(
        this.glowSprites,
        glowCount,
        `${id}:g:${i}`,
        piece,
        glow,
        laserGlowWidth(style.width),
        pick,
        fromLabel,
        toLabel
      );
      coreCount = this.emitLabeledPath(
        this.coreSprites,
        coreCount,
        `${id}:c:${i}`,
        piece,
        core,
        style.width,
        pick,
        fromLabel,
        toLabel
      );
    }
    return [glowCount, coreCount];
  }

  private emitLabeledPath(
    pool: PathSprite[],
    index: number,
    id: string,
    path: LonLat[],
    color: Rgba,
    width: number,
    pick: LiveHoverPayload | null,
    fromLabel?: string,
    toLabel?: string
  ): number {
    const next = this.emitPath(pool, index, id, path, color, width, pick);
    const sprite = pool[index];
    sprite.fromLabel = fromLabel;
    sprite.toLabel = toLabel;
    return next;
  }

  private draw(): void {
    if (!this.ready || !this.overlay) return;
    this.frame += 1;
    const now = this.now();
    this.releaseDueBuckets(now);
    let glowCount = 0;
    let coreCount = 0;
    let headCount = 0;
    let rippleCount = 0;

    const nextShots: LaserShot[] = [];
    for (const shot of this.shots) {
      const elapsed = now - shot.startedAt;
      if (elapsed < 0) {
        nextShots.push(shot);
        continue;
      }
      const travel = laserTravel(shot.poly.points.length, elapsed, shot.travelMs);
      if (travel.finished && shot.finishedAt == null) shot.finishedAt = now;
      if (shot.finishedAt != null && now - shot.finishedAt >= shot.remanenceMs) continue;
      nextShots.push(shot);
      if (!observationPassesFilters(shot.obs, this.filters)) continue;
      this.maybeRippleHops(shot, travel.headT, now);

      const fade =
        shot.finishedAt == null ? 1 : remanenceOpacity(now - shot.finishedAt, shot.remanenceMs);
      const t0 = shot.finishedAt == null ? travel.trailStartT : 0;
      const t1 = shot.finishedAt == null ? travel.headT : 1;
      const slices = visibleEdgeSlices(shot.poly, t0, t1, this.filters.exactOnly);
      for (let i = 0; i < slices.length; i++) {
        const slice = slices[i];
        [glowCount, coreCount] = this.emitStyledPath(
          glowCount,
          coreCount,
          `${shot.id}:${i}`,
          slice.path,
          shot.colorHex,
          shot.width,
          shot.opacity * fade,
          slice.confidence,
          slice.reason,
          slice.fromLabel,
          slice.toLabel
        );
      }

      if (shot.finishedAt == null && shot.poly.points.length > 0) {
        const head =
          shot.poly.points.length === 1
            ? shot.poly.points[0]
            : interpolatePolyline(shot.poly.points, travel.headT);
        if (head) {
          const halo = hexToRgba(shot.colorHex, shot.opacity * 0.28);
          const core = hexToRgba(shot.colorHex, Math.min(1, shot.opacity + 0.15));
          const headSize = laserHeadRadii(shot.width);
          headCount = this.emitPoint(
            this.headSprites,
            headCount,
            `${shot.id}:halo`,
            head,
            halo,
            [255, 255, 255, 0],
            headSize.halo,
            null
          );
          headCount = this.emitPoint(
            this.headSprites,
            headCount,
            `${shot.id}:core`,
            head,
            core,
            [255, 255, 255, 220],
            headSize.core,
            null
          );
        }
      }
    }
    this.shots = nextShots;
    this.drainPendingShots(now);

    const nextRipples: RippleSprite[] = [];
    for (const ripple of this.ripples) {
      const age = now - ripple.startedAt;
      if (age < 0 || age >= LIVE_RIPPLE_MS) continue;
      nextRipples.push(ripple);
      const wave = rippleRadii(ripple.pinRadius, age / LIVE_RIPPLE_MS);
      rippleCount = this.emitPoint(
        this.rippleSprites,
        rippleCount,
        ripple.id,
        ripple.position,
        [0, 0, 0, 0],
        hexToRgba(ripple.colorHex, wave.lineAlpha),
        wave.radius,
        null
      );
    }
    this.ripples = nextRipples;

    this.glowSprites.length = glowCount;
    this.coreSprites.length = coreCount;
    this.headSprites.length = headCount;
    this.rippleSprites.length = rippleCount;

    const trigger = this.frame;
    this.overlay.setProps({
      layers: [
        new IconLayer<PointSprite>({
          id: 'live-nodes',
          data: this.nodeSprites,
          pickable: true,
          opacity: 1,
          iconAtlas: ROLE_ICONS.atlas,
          iconMapping: ROLE_ICONS.mapping,
          getIcon: (d) => d.shape,
          getPosition: (d) => d.position,
          getColor: (d) => d.fill,
          getSize: (d) => d.radius * 2.4,
          sizeUnits: 'pixels',
          updateTriggers: {
            getPosition: this.nodeRevision,
            getColor: this.nodeRevision,
            getSize: this.nodeRevision,
            getIcon: this.nodeRevision,
          },
        }),
        new ScatterplotLayer<PointSprite>({
          id: 'live-ripples',
          data: this.rippleSprites.slice(),
          pickable: false,
          stroked: true,
          filled: false,
          radiusUnits: 'pixels',
          lineWidthUnits: 'pixels',
          parameters: ADDITIVE,
          getPosition: (d) => d.position,
          getLineColor: (d) => d.line,
          getRadius: (d) => d.radius,
          getLineWidth: 2,
          updateTriggers: { getPosition: trigger, getLineColor: trigger, getRadius: trigger },
        }),
        new PathLayer<PathSprite>({
          id: 'live-laser-glow',
          data: this.glowSprites.slice(),
          pickable: true,
          widthUnits: 'pixels',
          jointRounded: true,
          capRounded: true,
          antialiasing: true,
          parameters: ADDITIVE,
          getPath: (d) => d.path,
          getColor: (d) => d.color,
          getWidth: (d) => d.width,
          updateTriggers: { getPath: trigger, getColor: trigger, getWidth: trigger },
        }),
        new PathLayer<PathSprite>({
          id: 'live-laser-core',
          data: this.coreSprites.slice(),
          pickable: true,
          widthUnits: 'pixels',
          jointRounded: true,
          capRounded: true,
          antialiasing: true,
          parameters: ADDITIVE,
          getPath: (d) => d.path,
          getColor: (d) => d.color,
          getWidth: (d) => d.width,
          updateTriggers: { getPath: trigger, getColor: trigger, getWidth: trigger },
        }),
        new ScatterplotLayer<PointSprite>({
          id: 'live-local-radio',
          data: this.localSprites,
          pickable: true,
          stroked: true,
          filled: true,
          radiusUnits: 'pixels',
          lineWidthUnits: 'pixels',
          getPosition: (d) => d.position,
          getFillColor: (d) => d.fill,
          getLineColor: (d) => d.line,
          getRadius: (d) => d.radius,
          getLineWidth: 1.4,
          updateTriggers: {
            getPosition: this.localRevision,
            getFillColor: this.localRevision,
            getRadius: this.localRevision,
          },
        }),
        new ScatterplotLayer<PointSprite>({
          id: 'live-laser-heads',
          data: this.headSprites.slice(),
          pickable: false,
          stroked: false,
          filled: true,
          radiusUnits: 'pixels',
          parameters: ADDITIVE,
          getPosition: (d) => d.position,
          getFillColor: (d) => d.fill,
          getRadius: (d) => d.radius,
          updateTriggers: { getPosition: trigger, getFillColor: trigger, getRadius: trigger },
        }),
      ],
    });
  }
}

export { liveHoverKey } from './liveRender';
