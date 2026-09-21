import { render, screen } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import './eSlices';
import type { VisualizerData3D } from '../components/visualizer/useVisualizerData3D';
import type { VisualizerFocusHandoff } from '../utils/visualizerFocusHandoff';

const { lastSceneData, useVisualizerData3D } = vi.hoisted(() => {
  const lastSceneData: { current: VisualizerData3D | null } = { current: null };
  return {
    lastSceneData,
    useVisualizerData3D: vi.fn((options: { focusHandoff?: VisualizerFocusHandoff | null }) => {
      const focusHandoff = options.focusHandoff ?? null;
      return {
        nodes: new Map(),
        links: new Map(),
        canonicalNodes: new Map(),
        canonicalNeighborIds: new Map(),
        renderedNodeIds: new Set(),
        communityNames: new Map(),
        particles: [],
        stats: { processed: 0, animated: 0, nodes: 2, links: 1 },
        focusedObservationKey: focusHandoff?.observationKey ?? null,
        focusedNodeIds: focusHandoff ? new Set(['alice', 'self']) : new Set(),
        focusedLinkKeys: focusHandoff ? new Set(['alice->self']) : new Set(),
        expandContract: vi.fn(),
        clearAndReset: vi.fn(),
      } satisfies VisualizerData3D;
    }),
  };
});

vi.mock('../api', () => ({
  api: {
    getRepeaterAdvertPaths: vi.fn().mockResolvedValue([]),
  },
}));

vi.mock('../components/visualizer/useVisualizerData3D', () => ({
  useVisualizerData3D,
}));

vi.mock('../components/visualizer/useVisualizer3DScene', () => ({
  useVisualizer3DScene: ({ data }: { data: VisualizerData3D }) => {
    lastSceneData.current = data;
    return { hoveredNodeId: null, hoveredNeighborIds: [], pinnedNodeId: null };
  },
}));

import { PacketVisualizer3D } from '../components/PacketVisualizer3D';

describe('PacketVisualizer3D focus handoff', () => {
  beforeEach(() => {
    lastSceneData.current = null;
    useVisualizerData3D.mockClear();
  });

  it('passes the parent handoff into the data hook and the 3D scene', () => {
    const focusHandoff = { observationKey: 'obs-21', packetHash: 'aabbccddeeff0011' };

    render(
      <PacketVisualizer3D packets={[]} contacts={[]} config={null} focusHandoff={focusHandoff} />
    );

    expect(useVisualizerData3D).toHaveBeenCalledWith(expect.objectContaining({ focusHandoff }));
    expect(screen.getByRole('img')).toHaveAttribute('data-focused-observation', 'obs-21');
    expect(screen.getByRole('img')).toHaveAttribute('data-focused-node-count', '2');
    expect(screen.getByRole('img')).toHaveAttribute('data-focused-link-count', '1');
    expect(lastSceneData.current?.focusedObservationKey).toBe('obs-21');
    expect(lastSceneData.current?.focusedNodeIds).toEqual(new Set(['alice', 'self']));
    expect(lastSceneData.current?.focusedLinkKeys).toEqual(new Set(['alice->self']));
  });
});
