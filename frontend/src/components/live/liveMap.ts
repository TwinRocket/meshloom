import { MapboxOverlay } from '@deck.gl/mapbox';
import { PathLayer, ScatterplotLayer } from '@deck.gl/layers';
import maplibregl, { LngLatBounds, Map as MapLibreMap, NavigationControl } from 'maplibre-gl';

import type { DirectoryMapNode, DirectoryNodeRole, RadioConfig } from '../../types';
import { isValidLocation } from '../../utils/pathUtils';
import {
  LIVE_REMANENCE_MS,
  LIVE_SEGMENT_MS,
  LIVE_STAGGER_MS,
  MAX_LIVE_SHOTS,
  buildLaserPolyline,
  dashLonLat,
  earPulse,
  earVisual,
  hexToRgba,
  interpolatePolyline,
  laserTravel,
  laserWidth,
  mappableDirectoryNodes,
  nodeRoleStyle,
  observationColor,
  observationDrawOpacity,
  observationEarSource,
  observationPassesFilters,
  readLiveCamera,
  remanenceOpacity,
  selectCatchup,
  shouldAutoFitCamera,
  shouldSpawnLaser,
  strokeStyleForConfidence,
  visibleEdgeSlices,
  writeLiveCamera,
  type LiveEarSource,
  type LiveViewFilters,
  type LonLat,
  type Rgba,
} from './liveRender';
import type { LiveObservation } from '../../utils/livePackets';
import { isStaleLiveTime, snrWeight } from '../../utils/livePackets';

export const CARTO_DARK_MATTER_STYLE =
  'https://basemaps.cartocdn.com/gl/dark-matter-gl-style/style.json';

export const LIVE_MAP_ATTRIBUTION = [
  '<a href="https://www.openstreetmap.org/copyright">© OpenStreetMap</a>',
  '<a href="https://carto.com/">© CARTO</a>',
];

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
  | { kind: 'node'; name: string; role: DirectoryNodeRole; x: number; y: number }
  | { kind: 'probable'; reason: string; label?: string; x: number; y: number }
  | { kind: 'exact'; label?: string; x: number; y: number }
  | { kind: 'ear'; source: LiveEarSource; iata: string | null; x: number; y: number };

type HoverHandler = (hover: LiveHoverPayload | null) => void;

interface PathSprite {
  id: string;
  path: LonLat[];
  color: Rgba;
  width: number;
  pick: LiveHoverPayload | null;
}

interface PointSprite {
  id: string;
  position: LonLat;
  fill: Rgba;
  line: Rgba;
  radius: number;
  pick: LiveHoverPayload | null;
}

interface LaserShot {
  id: string;
  obs: LiveObservation;
  poly: ReturnType<typeof buildLaserPolyline>;
  colorHex: string;
  opacity: number;
  width: number;
  startedAt: number;
  finishedAt: number | null;
}

interface EarState {
  id: string;
  lon: number;
  lat: number;
  source: LiveEarSource;
  iata: string | null;
  lastAt: number;
}

export interface LiveMapOptions {
  onHover?: HoverHandler;
}

export class LiveMapController {
  private readonly onHover: HoverHandler;
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
  private hasSavedCamera: boolean;
  private fitted = false;
  private lastHoverKey = '';
  private resizeObserver: ResizeObserver | null = null;

  private filters: LiveViewFilters = { iata: '', hiddenTypes: new Set(), exactOnly: false };
  private localHash8 = new Set<string>();
  private seen = new Set<string>();
  private shots: LaserShot[] = [];
  private ears = new Map<string, EarState>();
  private nodes: DirectoryMapNode[] = [];
  private localEar: EarState | null = null;

  private glowSprites: PathSprite[] = [];
  private coreSprites: PathSprite[] = [];
  private nodeSprites: PointSprite[] = [];
  private earSprites: PointSprite[] = [];
  private pulseSprites: PointSprite[] = [];
  private headSprites: PointSprite[] = [];

  constructor(container: HTMLElement, options: LiveMapOptions = {}) {
    this.onHover = options.onHover ?? (() => {});
    const saved = readLiveCamera();
    this.hasSavedCamera = saved != null;
    if (saved) this.userMoved = true;
    this.map = new maplibregl.Map({
      container,
      style: CARTO_DARK_MATTER_STYLE,
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
      interleaved: true,
      layers: [],
      pickingRadius: 10,
      getCursor: ({ isHovering }: { isHovering: boolean }) => (isHovering ? 'pointer' : 'grab'),
      onHover: (info: { object?: { pick?: LiveHoverPayload | null }; x: number; y: number }) => {
        this.handleHover(info);
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
    const now = performance.now();
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

  setLocalEar(config: RadioConfig | null): void {
    if (config && isValidLocation(config.lat, config.lon)) {
      this.localEar = {
        id: (config.public_key || 'local-ear').slice(0, 16),
        lon: config.lon,
        lat: config.lat,
        source: 'local',
        iata: null,
        lastAt: 0,
      };
    } else {
      this.localEar = null;
    }
    this.draw();
  }

  setDirectoryNodes(nodes: DirectoryMapNode[]): void {
    this.nodes = mappableDirectoryNodes(nodes);
    this.maybeFitNodes();
    this.draw();
  }

  syncObservations(observations: LiveObservation[], localHash8: Set<string>): void {
    this.localHash8 = localHash8;
    const newcomers: LiveObservation[] = [];
    for (const obs of observations) {
      if (this.seen.has(obs.id)) continue;
      this.seen.add(obs.id);
      newcomers.push(obs);
    }
    if (newcomers.length === 0) {
      this.draw();
      return;
    }

    const spawnIds = selectCatchup(
      newcomers.filter((obs) => shouldSpawnLaser(obs)).map((obs) => obs.id)
    );
    const now = this.now();
    let stagger = 0;
    for (const obs of newcomers) {
      this.touchEar(obs, now);
      if (!this.playing || !spawnIds.has(obs.id)) continue;
      const poly = buildLaserPolyline(obs);
      if (poly.points.length < 2) continue;
      const twin = obs.source === 'community' && this.localHash8.has(obs.hash8);
      this.shots.push({
        id: obs.id,
        obs,
        poly,
        colorHex: observationColor(obs),
        opacity: observationDrawOpacity(obs, twin),
        width: laserWidth(snrWeight(obs.snr)),
        startedAt: now + stagger * LIVE_STAGGER_MS,
        finishedAt: poly.points.length < 2 ? now : null,
      });
      stagger += 1;
    }
    if (this.shots.length > MAX_LIVE_SHOTS) {
      this.shots = this.shots.slice(this.shots.length - MAX_LIVE_SHOTS);
    }
    this.draw();
    if (this.playing) this.startLoop();
  }

  private now(): number {
    if (this.pausedAt != null) return this.pausedAt - this.clockOffset;
    return performance.now() - this.clockOffset;
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
    if (
      this.shots.some(
        (shot) => shot.finishedAt == null || now - shot.finishedAt < LIVE_REMANENCE_MS
      )
    ) {
      return true;
    }
    for (const ear of this.ears.values()) {
      if (now - ear.lastAt < 1600) return true;
    }
    if (this.localEar && now - this.localEar.lastAt < 1600) return true;
    return false;
  }

  private touchEar(obs: LiveObservation, now: number): void {
    if (!obs.ear) return;
    const source = observationEarSource(obs) ?? 'iata';
    this.ears.set(obs.earId, {
      id: obs.earId,
      lon: obs.ear.lon,
      lat: obs.ear.lat,
      source,
      iata: obs.iata,
      lastAt: now,
    });
    if (this.localEar && source === 'local') {
      this.localEar.lastAt = now;
    }
  }

  private persistCamera(): void {
    if (!this.map || !this.userMoved) return;
    const center = this.map.getCenter();
    writeLiveCamera({ lat: center.lat, lon: center.lng, zoom: this.map.getZoom() });
  }

  private maybeFitNodes(): void {
    if (
      !this.map ||
      this.fitted ||
      !shouldAutoFitCamera(this.hasSavedCamera, this.userMoved, this.nodes.length)
    ) {
      return;
    }
    const bounds = new LngLatBounds();
    for (const node of this.nodes) bounds.extend([node.lon, node.lat]);
    this.map.fitBounds(bounds, { padding: 56, maxZoom: 4.6, duration: 0 });
    this.fitted = true;
  }

  private handleHover(info: {
    object?: { pick?: LiveHoverPayload | null };
    x: number;
    y: number;
  }): void {
    const pick = info.object?.pick ?? null;
    const key = pick
      ? `${pick.kind}:${'reason' in pick ? pick.reason : ''}:${'name' in pick ? pick.name : ''}:${'source' in pick ? pick.source : ''}`
      : '';
    if (key === this.lastHoverKey) return;
    this.lastHoverKey = key;
    if (!pick) {
      this.onHover(null);
      return;
    }
    this.onHover({ ...pick, x: info.x, y: info.y });
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
    sprite.path.length = 0;
    for (const point of path) sprite.path.push(point);
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
    pick: LiveHoverPayload | null
  ): number {
    const sprite = this.takeSprite(pool, index, () => ({
      id,
      position: [0, 0] as LonLat,
      fill: [0, 0, 0, 0] as Rgba,
      line: [0, 0, 0, 0] as Rgba,
      radius,
      pick: null,
    }));
    sprite.id = id;
    sprite.position[0] = position[0];
    sprite.position[1] = position[1];
    this.resetColor(sprite.fill, fill);
    this.resetColor(sprite.line, line);
    sprite.radius = radius;
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
    label?: string
  ): [number, number] {
    if (path.length < 2 || alpha <= 0.01) return [glowCount, coreCount];
    const style = strokeStyleForConfidence(confidence, width);
    const pieces = style.dashed ? dashLonLat(path[0], path[path.length - 1]) : [path];
    const pick = this.pickForSlice(confidence, reason, label);
    const glow = hexToRgba(colorHex, alpha * style.opacityScale * 0.22);
    const core = hexToRgba(colorHex, alpha * style.opacityScale);
    for (let i = 0; i < pieces.length; i++) {
      const piece = pieces[i];
      glowCount = this.emitPath(
        this.glowSprites,
        glowCount,
        `${id}:g:${i}`,
        piece,
        glow,
        style.width * 6.2,
        pick
      );
      coreCount = this.emitPath(
        this.coreSprites,
        coreCount,
        `${id}:c:${i}`,
        piece,
        core,
        style.width,
        pick
      );
    }
    return [glowCount, coreCount];
  }

  private draw(): void {
    if (!this.ready || !this.overlay) return;
    this.frame += 1;
    const now = this.now();
    const wall = Date.now();
    let glowCount = 0;
    let coreCount = 0;
    let headCount = 0;

    const nextShots: LaserShot[] = [];
    for (const shot of this.shots) {
      if (!observationPassesFilters(shot.obs, this.filters)) {
        nextShots.push(shot);
        continue;
      }
      const elapsed = now - shot.startedAt;
      if (elapsed < 0) {
        nextShots.push(shot);
        continue;
      }
      const travel = laserTravel(shot.poly.points.length, elapsed, LIVE_SEGMENT_MS);
      if (travel.finished && shot.finishedAt == null) shot.finishedAt = now;
      if (shot.finishedAt != null && now - shot.finishedAt >= LIVE_REMANENCE_MS) continue;
      nextShots.push(shot);

      const fade =
        shot.finishedAt == null ? 1 : remanenceOpacity(now - shot.finishedAt, LIVE_REMANENCE_MS);
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
          slice.label
        );
      }

      if (shot.finishedAt == null && shot.poly.points.length > 0) {
        const head =
          shot.poly.points.length === 1
            ? shot.poly.points[0]
            : interpolatePolyline(shot.poly.points, travel.headT);
        if (head) {
          const halo = hexToRgba(shot.colorHex, shot.opacity * 0.35);
          const core = hexToRgba(shot.colorHex, Math.min(1, shot.opacity + 0.15));
          headCount = this.emitPoint(
            this.headSprites,
            headCount,
            `${shot.id}:halo`,
            head,
            halo,
            [255, 255, 255, 0],
            11 + shot.width,
            null
          );
          headCount = this.emitPoint(
            this.headSprites,
            headCount,
            `${shot.id}:core`,
            head,
            core,
            [255, 255, 255, 220],
            3.4 + shot.width * 0.35,
            null
          );
        }
      }
    }
    this.shots = nextShots;

    let nodeCount = 0;
    for (const node of this.nodes) {
      const style = nodeRoleStyle(node.role);
      const fill = hexToRgba(style.color, 0.72);
      const line = hexToRgba('#020617', 0.7);
      nodeCount = this.emitPoint(
        this.nodeSprites,
        nodeCount,
        node.public_key,
        [node.lon, node.lat],
        fill,
        line,
        style.radius,
        { kind: 'node', name: node.name, role: node.role, x: 0, y: 0 }
      );
    }

    const earList: EarState[] = [];
    if (this.localEar) earList.push(this.localEar);
    for (const ear of this.ears.values()) {
      if (this.localEar && ear.id === this.localEar.id) continue;
      earList.push(ear);
    }

    let earCount = 0;
    let pulseCount = 0;
    for (const ear of earList) {
      const visual = earVisual(ear.source);
      const stale = ear.lastAt > 0 && isStaleLiveTime(ear.lastAt, wall);
      const fill = hexToRgba(visual.color, stale ? visual.opacity * 0.28 : visual.opacity);
      const line = hexToRgba(visual.ring, stale ? 0.25 : 0.85);
      const pick: LiveHoverPayload = {
        kind: 'ear',
        source: ear.source,
        iata: ear.iata,
        x: 0,
        y: 0,
      };
      earCount = this.emitPoint(
        this.earSprites,
        earCount,
        ear.id,
        [ear.lon, ear.lat],
        fill,
        line,
        visual.radius,
        pick
      );
      const pulse = earPulse(now - ear.lastAt);
      if (pulse > 0) {
        pulseCount = this.emitPoint(
          this.pulseSprites,
          pulseCount,
          `${ear.id}:pulse`,
          [ear.lon, ear.lat],
          hexToRgba(visual.color, pulse * 0.28),
          hexToRgba(visual.ring, pulse * 0.5),
          visual.radius * (1 + (visual.pulseScale - 1) * (1 - pulse)),
          pick
        );
      }
    }

    this.glowSprites.length = glowCount;
    this.coreSprites.length = coreCount;
    this.nodeSprites.length = nodeCount;
    this.earSprites.length = earCount;
    this.pulseSprites.length = pulseCount;
    this.headSprites.length = headCount;

    const trigger = this.frame;
    this.overlay.setProps({
      layers: [
        new ScatterplotLayer<PointSprite>({
          id: 'live-nodes',
          data: this.nodeSprites,
          pickable: true,
          opacity: 1,
          stroked: true,
          filled: true,
          radiusUnits: 'pixels',
          lineWidthUnits: 'pixels',
          getPosition: (d) => d.position,
          getFillColor: (d) => d.fill,
          getLineColor: (d) => d.line,
          getRadius: (d) => d.radius,
          getLineWidth: 1,
          updateTriggers: { getPosition: trigger, getFillColor: trigger, getRadius: trigger },
        }),
        new PathLayer<PathSprite>({
          id: 'live-laser-glow',
          data: this.glowSprites,
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
          data: this.coreSprites,
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
          id: 'live-ears',
          data: this.earSprites,
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
          updateTriggers: { getPosition: trigger, getFillColor: trigger, getRadius: trigger },
        }),
        new ScatterplotLayer<PointSprite>({
          id: 'live-ear-pulses',
          data: this.pulseSprites,
          pickable: false,
          stroked: true,
          filled: true,
          radiusUnits: 'pixels',
          lineWidthUnits: 'pixels',
          parameters: ADDITIVE,
          getPosition: (d) => d.position,
          getFillColor: (d) => d.fill,
          getLineColor: (d) => d.line,
          getRadius: (d) => d.radius,
          getLineWidth: 1.2,
          updateTriggers: { getPosition: trigger, getFillColor: trigger, getRadius: trigger },
        }),
        new ScatterplotLayer<PointSprite>({
          id: 'live-laser-heads',
          data: this.headSprites,
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

export function liveHoverKey(hover: LiveHoverPayload | null): string {
  if (!hover) return '';
  if (hover.kind === 'node') return `node:${hover.name}`;
  if (hover.kind === 'probable') return `probable:${hover.reason}`;
  if (hover.kind === 'exact') return `exact:${hover.label ?? ''}`;
  return `ear:${hover.source}:${hover.iata ?? ''}`;
}
