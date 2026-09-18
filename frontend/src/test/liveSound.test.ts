import { afterEach, describe, expect, it } from 'vitest';

import { laserTravelMs } from '../components/live/liveRender';
import { LIVE_PACKET_TYPES } from '../utils/livePackets';
import {
  MAX_LIVE_SOUND_VOICES,
  LiveSoundEngine,
  laserSegmentSoundMs,
  segmentDurationMs,
  typeVoice,
} from '../utils/liveSound';

describe('liveSound timing', () => {
  it('uses travel time divided by at least one edge', () => {
    expect(segmentDurationMs(0)).toBe(laserTravelMs(0));
    expect(segmentDurationMs(1)).toBe(laserTravelMs(1));
    expect(segmentDurationMs(3)).toBe(laserTravelMs(3) / 3);
  });

  it('scales segment length by packet type', () => {
    expect(laserSegmentSoundMs(1, 'text')).toBe(laserTravelMs(1));
    expect(laserSegmentSoundMs(1, 'ack')).toBe(laserTravelMs(1) * typeVoice('ack').durScale);
    expect(laserSegmentSoundMs(1, 'unknown-type')).toBe(
      laserTravelMs(1) * typeVoice('other').durScale
    );
  });

  it('has a voice entry for every known live packet type', () => {
    for (const type of LIVE_PACKET_TYPES) {
      expect(typeVoice(type).pitch).toBeGreaterThan(0);
      expect(typeVoice(type).durScale).toBeGreaterThan(0);
    }
    expect(typeVoice('ack')).toEqual({ pitch: 1.28, durScale: 0.82 });
    expect(typeVoice('text')).toEqual({ pitch: 1.0, durScale: 1.0 });
    expect(typeVoice('advert')).toEqual({ pitch: 0.72, durScale: 1.18 });
  });
});

describe('LiveSoundEngine', () => {
  const engines: LiveSoundEngine[] = [];

  afterEach(() => {
    for (const engine of engines) engine.destroy();
    engines.length = 0;
  });

  it('does not start a voice when the theme is off', () => {
    const engine = new LiveSoundEngine();
    engines.push(engine);
    expect(engine.play('off', 'text', 1850)).toBe(false);
    expect(engine.activeVoiceCount()).toBe(0);
  });

  it('caps overlapping voices at MAX_LIVE_SOUND_VOICES', () => {
    const engine = new LiveSoundEngine();
    engines.push(engine);
    for (let i = 0; i < MAX_LIVE_SOUND_VOICES + 2; i += 1) {
      expect(engine.play('laser', 'text', 4000)).toBe(true);
    }
    expect(engine.activeVoiceCount()).toBe(MAX_LIVE_SOUND_VOICES);
  });
});
