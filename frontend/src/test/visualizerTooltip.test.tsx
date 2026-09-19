import { render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';

import './eSlices';
import { VisualizerTooltip } from '../components/visualizer/VisualizerTooltip';
import i18n from '../i18n';
import type { PacketNetworkNode } from '../networkGraph/packetNetworkGraph';

function createNode(
  overrides: Partial<PacketNetworkNode> & Pick<PacketNetworkNode, 'id' | 'type'>
): PacketNetworkNode {
  return {
    id: overrides.id,
    type: overrides.type,
    name: overrides.name ?? null,
    isAmbiguous: overrides.isAmbiguous ?? false,
    lastActivity: overrides.lastActivity ?? Date.now(),
    probableIdentity: overrides.probableIdentity,
    ambiguousNames: overrides.ambiguousNames,
    lastActivityReason: overrides.lastActivityReason,
  };
}

describe('VisualizerTooltip', () => {
  it('renders nothing without an active node', () => {
    const { container } = render(
      <VisualizerTooltip
        activeNodeId={null}
        canonicalNodes={new Map()}
        canonicalNeighborIds={new Map()}
        renderedNodeIds={new Set()}
      />
    );

    expect(container).toBeEmptyDOMElement();
  });

  it('renders ambiguous node details and neighbors', () => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date('2026-03-10T22:00:00Z'));

    const node = createNode({
      id: '?32',
      type: 'repeater',
      name: 'Likely Relay',
      isAmbiguous: true,
      probableIdentity: 'Likely Relay',
      ambiguousNames: ['Relay A', 'Relay B'],
      lastActivity: new Date('2026-03-10T21:58:30Z').getTime(),
      lastActivityReason: 'Relayed GT',
    });
    const neighbor = createNode({
      id: 'abcd1234ef56',
      type: 'client',
      name: 'Neighbor Node',
      ambiguousNames: ['Alt Neighbor'],
    });
    const hiddenRepeater = createNode({
      id: '?44',
      type: 'repeater',
      name: '44',
      isAmbiguous: true,
    });

    render(
      <VisualizerTooltip
        activeNodeId={node.id}
        canonicalNodes={
          new Map([
            [node.id, node],
            [neighbor.id, neighbor],
            [hiddenRepeater.id, hiddenRepeater],
          ])
        }
        canonicalNeighborIds={new Map([[node.id, [neighbor.id, hiddenRepeater.id]]])}
        renderedNodeIds={new Set([node.id, neighbor.id])}
      />
    );

    expect(screen.getByText('Likely Relay')).toBeInTheDocument();
    expect(screen.getByText(i18n.t('visualizer.id', { id: '?32' }))).toBeInTheDocument();
    expect(
      screen.getByText(
        i18n.t('visualizer.typeAmbiguous', { type: i18n.t('visualizer.nodeType.repeater') })
      )
    ).toBeInTheDocument();
    expect(
      screen.getByText(i18n.t('visualizer.probably', { name: 'Likely Relay' }))
    ).toBeInTheDocument();
    expect(
      screen.getByText(i18n.t('visualizer.otherPossible', { names: 'Relay A, Relay B' }))
    ).toBeInTheDocument();
    expect(
      screen.getByText(
        i18n.t('visualizer.lastActive', {
          time: i18n.t('visualizer.minutesSecondsAgo', { minutes: 1, seconds: 30 }),
        })
      )
    ).toBeInTheDocument();
    expect(
      screen.getByText(
        i18n.t('visualizer.reason', { reason: i18n.t('visualizer.reasonRelayed', { label: 'GT' }) })
      )
    ).toBeInTheDocument();
    expect(screen.getByText('Neighbor Node')).toBeInTheDocument();
    expect(screen.getByText('(Alt Neighbor)')).toBeInTheDocument();
    expect(screen.getByText('44')).toBeInTheDocument();
    expect(screen.getByText(i18n.t('visualizer.hidden'))).toBeInTheDocument();

    vi.useRealTimers();
  });

  it('marks Community-resolved names with the directory globe', () => {
    const node = createNode({
      id: '?aabb',
      type: 'repeater',
      name: 'AABB',
      isAmbiguous: true,
    });
    const neighbor = createNode({
      id: 'aabbccddeeff',
      type: 'client',
      name: null,
    });

    render(
      <VisualizerTooltip
        activeNodeId={node.id}
        canonicalNodes={
          new Map([
            [node.id, node],
            [neighbor.id, neighbor],
          ])
        }
        canonicalNeighborIds={new Map([[node.id, [neighbor.id]]])}
        renderedNodeIds={new Set([node.id, neighbor.id])}
        communityNames={
          new Map([
            [node.id, 'RemoteHill'],
            [neighbor.id, 'Companion'],
          ])
        }
      />
    );

    expect(screen.getByText('RemoteHill')).toBeInTheDocument();
    expect(screen.getByText(i18n.t('path.directoryGlobe'))).toBeInTheDocument();
    expect(screen.getAllByTestId('directory-globe-icon')).toHaveLength(2);
    expect(screen.getByText('Companion')).toBeInTheDocument();
    expect(screen.queryByText('AABB')).not.toBeInTheDocument();
  });
});
