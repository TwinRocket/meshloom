import { laserTravelMs } from '../components/live/liveRender';
import type { CommunityPacketType, KnownCommunityPacketType } from '../types';
import type { LiveSoundTheme } from './liveSoundPreference';

export const MAX_LIVE_SOUND_VOICES = 4;

export interface TypeVoice {
  pitch: number;
  durScale: number;
}

export const TYPE_VOICE: Record<KnownCommunityPacketType, TypeVoice> = {
  ack: { pitch: 1.28, durScale: 0.82 },
  text: { pitch: 1.0, durScale: 1.0 },
  grp_txt: { pitch: 0.97, durScale: 1.0 },
  grp_data: { pitch: 0.93, durScale: 1.0 },
  req: { pitch: 1.12, durScale: 0.92 },
  response: { pitch: 1.07, durScale: 0.95 },
  anon_req: { pitch: 1.1, durScale: 0.92 },
  advert: { pitch: 0.72, durScale: 1.18 },
  path: { pitch: 0.88, durScale: 1.0 },
  trace: { pitch: 0.84, durScale: 1.04 },
  multipart: { pitch: 0.9, durScale: 1.0 },
  control: { pitch: 0.78, durScale: 1.06 },
  raw_custom: { pitch: 0.8, durScale: 1.04 },
  other: { pitch: 0.74, durScale: 1.0 },
};

export function typeVoice(type: string): TypeVoice {
  return TYPE_VOICE[type as KnownCommunityPacketType] ?? TYPE_VOICE.other;
}

export function segmentDurationMs(edges: number): number {
  return laserTravelMs(edges) / Math.max(edges, 1);
}

export function laserSegmentSoundMs(edges: number, type: CommunityPacketType): number {
  return segmentDurationMs(edges) * typeVoice(type).durScale;
}

interface LiveSoundHandle {
  stop: () => void;
}

function createAudioContext(): AudioContext | null {
  const Ctor =
    typeof AudioContext !== 'undefined'
      ? AudioContext
      : (globalThis as { webkitAudioContext?: typeof AudioContext }).webkitAudioContext;
  if (!Ctor) return null;
  try {
    return new Ctor();
  } catch {
    return null;
  }
}

function crushCurve(): Float32Array {
  const curve = new Float32Array(256);
  for (let i = 0; i < 256; i += 1) curve[i] = i / 127.5 - 1;
  return curve;
}

function startThemeVoice(
  ctx: AudioContext,
  theme: Exclude<LiveSoundTheme, 'off'>,
  type: CommunityPacketType,
  durationMs: number
): LiveSoundHandle {
  const start = ctx.currentTime;
  const dur = Math.max(0.08, durationMs / 1000);
  const fade = Math.min(0.12, dur * 0.18);
  const { pitch } = typeVoice(type);
  const osc = ctx.createOscillator();
  const gain = ctx.createGain();
  gain.gain.setValueAtTime(0.0001, start);
  gain.gain.exponentialRampToValueAtTime(theme === 'bit8' ? 0.07 : 0.1, start + 0.01);
  gain.gain.setValueAtTime(theme === 'bit8' ? 0.07 : 0.1, start + Math.max(0.02, dur - fade));
  gain.gain.exponentialRampToValueAtTime(0.0001, start + dur);

  if (theme === 'bubbles') {
    osc.type = 'sine';
    osc.frequency.setValueAtTime(420 * pitch, start);
    osc.frequency.exponentialRampToValueAtTime(1180 * pitch, start + dur);
    osc.connect(gain);
  } else if (theme === 'bit8') {
    osc.type = 'square';
    const steps = Math.max(8, Math.round(dur * 12));
    for (let i = 0; i < steps; i += 1) {
      const progress = i / Math.max(steps - 1, 1);
      osc.frequency.setValueAtTime(
        1900 * pitch * (240 / 1900) ** progress,
        start + (dur * i) / steps
      );
    }
    const shaper = ctx.createWaveShaper();
    shaper.curve = crushCurve();
    osc.connect(shaper);
    shaper.connect(gain);
  } else {
    osc.type = 'sawtooth';
    osc.frequency.setValueAtTime(1680 * pitch, start);
    osc.frequency.exponentialRampToValueAtTime(Math.max(80, 210 * pitch), start + dur);
    const filter = ctx.createBiquadFilter();
    filter.type = 'lowpass';
    filter.frequency.setValueAtTime(3600 * pitch, start);
    filter.frequency.exponentialRampToValueAtTime(Math.max(180, 480 * pitch), start + dur);
    osc.connect(filter);
    filter.connect(gain);
  }
  gain.connect(ctx.destination);
  osc.start(start);
  osc.stop(start + dur + 0.03);
  return {
    stop: () => {
      try {
        osc.stop();
      } catch {
        /* already stopped */
      }
    },
  };
}

export class LiveSoundEngine {
  private ctx: AudioContext | null = null;
  private voices: LiveSoundHandle[] = [];
  private muted = false;
  private readonly onVisibility: () => void;

  constructor() {
    this.onVisibility = () => {
      this.muted = typeof document !== 'undefined' && document.hidden;
      if (this.muted) this.stopAll();
    };
    if (typeof document !== 'undefined') {
      this.muted = document.hidden;
      document.addEventListener('visibilitychange', this.onVisibility);
    }
  }

  async resume(): Promise<void> {
    this.ctx ??= createAudioContext();
    if (this.ctx?.state === 'suspended') await this.ctx.resume();
  }

  play(theme: LiveSoundTheme, type: CommunityPacketType, durationMs: number): boolean {
    if (theme === 'off' || this.muted || durationMs <= 0) return false;
    while (this.voices.length >= MAX_LIVE_SOUND_VOICES) {
      this.voices.shift()?.stop();
    }
    const handle = this.startVoice(theme, type, durationMs);
    this.voices.push(handle);
    return true;
  }

  stopAll(): void {
    for (const voice of this.voices) voice.stop();
    this.voices = [];
  }

  destroy(): void {
    this.stopAll();
    if (typeof document !== 'undefined') {
      document.removeEventListener('visibilitychange', this.onVisibility);
    }
    if (this.ctx) {
      void this.ctx.close();
      this.ctx = null;
    }
  }

  activeVoiceCount(): number {
    return this.voices.length;
  }

  private startVoice(
    theme: Exclude<LiveSoundTheme, 'off'>,
    type: CommunityPacketType,
    durationMs: number
  ): LiveSoundHandle {
    this.ctx ??= createAudioContext();
    if (!this.ctx) {
      return { stop: () => {} };
    }
    return startThemeVoice(this.ctx, theme, type, durationMs);
  }
}
