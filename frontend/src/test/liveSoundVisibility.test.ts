import { describe, expect, it } from 'vitest';

import {
  laserEdgeVisible,
  pointInViewport,
  segmentIntersectsViewport,
} from '../utils/liveSoundVisibility';

describe('liveSoundVisibility', () => {
  it('treats points inside the canvas as visible', () => {
    expect(pointInViewport({ x: 0, y: 0 }, 200, 100)).toBe(true);
    expect(pointInViewport({ x: 200, y: 100 }, 200, 100)).toBe(true);
    expect(pointInViewport({ x: 10, y: 10 }, 200, 100)).toBe(true);
  });

  it('rejects points outside the canvas', () => {
    expect(pointInViewport({ x: -1, y: 10 }, 200, 100)).toBe(false);
    expect(pointInViewport({ x: 10, y: 101 }, 200, 100)).toBe(false);
    expect(pointInViewport({ x: 10, y: 10 }, 0, 100)).toBe(false);
  });

  it('detects a segment that crosses the canvas', () => {
    expect(segmentIntersectsViewport({ x: -40, y: 50 }, { x: 240, y: 50 }, 200, 100)).toBe(true);
    expect(segmentIntersectsViewport({ x: 100, y: -20 }, { x: 100, y: 140 }, 200, 100)).toBe(true);
  });

  it('rejects a segment that stays outside the canvas', () => {
    expect(segmentIntersectsViewport({ x: -40, y: -20 }, { x: -10, y: -5 }, 200, 100)).toBe(false);
    expect(segmentIntersectsViewport({ x: 300, y: 0 }, { x: 400, y: 80 }, 200, 100)).toBe(false);
  });

  it('treats a 1-point pulse as its projected point', () => {
    expect(laserEdgeVisible({ x: 20, y: 20 }, null, 200, 100)).toBe(true);
    expect(laserEdgeVisible({ x: 400, y: 20 }, null, 200, 100)).toBe(false);
    expect(laserEdgeVisible({ x: 20, y: 20 }, { x: 20, y: 20 }, 200, 100)).toBe(true);
  });
});
