import { describe, expect, it } from 'vitest';

import {
  MAX_GIF_BYTES,
  MAX_SIDE,
  chooseOutputType,
  classifySource,
  fitWithin,
  isHeic,
  maxBytesFor,
} from '../utils/imageDownscale';

describe('fitWithin', () => {
  it('brings a 4000x3000 photo to 1600 px on its longest side', () => {
    expect(fitWithin({ width: 4000, height: 3000 })).toEqual({ width: 1600, height: 1200 });
  });
  it('handles portrait and keeps the ratio', () => {
    expect(fitWithin({ width: 3000, height: 4000 })).toEqual({ width: 1200, height: 1600 });
  });
  it('never upscales and leaves an exact fit alone', () => {
    expect(fitWithin({ width: 800, height: 600 })).toEqual({ width: 800, height: 600 });
    expect(fitWithin({ width: MAX_SIDE, height: 10 })).toEqual({ width: MAX_SIDE, height: 10 });
  });
  it('keeps at least one pixel on a very thin image', () => {
    expect(fitWithin({ width: 100000, height: 1 })).toEqual({ width: 1600, height: 1 });
  });
});

describe('classifySource', () => {
  it('refuses HEIC by type or by extension', () => {
    expect(classifySource({ type: 'image/heic', name: 'a.heic' })).toBe('heic');
    expect(classifySource({ type: '', name: 'IMG_0001.HEIC' })).toBe('heic');
    expect(isHeic({ type: 'image/heif', name: 'x' })).toBe(true);
  });
  it('refuses other types', () => {
    expect(classifySource({ type: 'application/pdf', name: 'a.pdf' })).toBe('unsupported');
    expect(classifySource({ type: 'image/svg+xml', name: 'a.svg' })).toBe('unsupported');
  });
  it('accepts the allowlist, with the extension filling an empty type', () => {
    expect(classifySource({ type: 'image/jpeg', name: 'a' })).toBe('image/jpeg');
    expect(classifySource({ type: '', name: 'a.png' })).toBe('image/png');
    expect(classifySource({ type: 'image/gif', name: 'a.gif' })).toBe('image/gif');
  });
});

describe('chooseOutputType', () => {
  it('keeps GIF, and PNG only with transparency', () => {
    expect(chooseOutputType('image/gif', false, true)).toBe('image/gif');
    expect(chooseOutputType('image/png', true, true)).toBe('image/png');
    expect(chooseOutputType('image/png', false, true)).toBe('image/jpeg');
  });
  it('keeps WebP when the browser encodes it, else falls back', () => {
    expect(chooseOutputType('image/webp', false, true)).toBe('image/webp');
    expect(chooseOutputType('image/webp', false, false)).toBe('image/jpeg');
    expect(chooseOutputType('image/webp', true, false)).toBe('image/png');
  });
  it('sends everything else as JPEG', () => {
    expect(chooseOutputType('image/jpeg', false, true)).toBe('image/jpeg');
  });
});

describe('maxBytesFor', () => {
  it('caps a GIF lower than other images', () => {
    expect(maxBytesFor('image/gif')).toBe(MAX_GIF_BYTES);
    expect(maxBytesFor('image/jpeg')).toBeGreaterThan(MAX_GIF_BYTES);
  });
});
