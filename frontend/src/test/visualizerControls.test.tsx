import { fireEvent, render, screen } from '@testing-library/react';
import type { ComponentProps } from 'react';
import { describe, expect, it, vi } from 'vitest';
import './eSlices';
import { VisualizerControls } from '../components/visualizer/VisualizerControls';
import i18n from '../i18n';

type ControlsProps = ComponentProps<typeof VisualizerControls>;

function renderControls(overrides: Partial<ControlsProps> = {}) {
  const props: ControlsProps = {
    showControls: true,
    setShowControls: vi.fn(),
    showAmbiguousPaths: false,
    setShowAmbiguousPaths: vi.fn(),
    showAmbiguousNodes: false,
    setShowAmbiguousNodes: vi.fn(),
    useAdvertPathHints: false,
    setUseAdvertPathHints: vi.fn(),
    collapseLikelyKnownSiblingRepeaters: false,
    setCollapseLikelyKnownSiblingRepeaters: vi.fn(),
    splitAmbiguousByTraffic: false,
    setSplitAmbiguousByTraffic: vi.fn(),
    observationWindowSec: 5,
    setObservationWindowSec: vi.fn(),
    pruneStaleNodes: true,
    setPruneStaleNodes: vi.fn(),
    pruneStaleMinutes: 10,
    setPruneStaleMinutes: vi.fn(),
    letEmDrift: false,
    setLetEmDrift: vi.fn(),
    autoOrbit: false,
    setAutoOrbit: vi.fn(),
    chargeStrength: -100,
    setChargeStrength: vi.fn(),
    particleSpeedMultiplier: 1,
    setParticleSpeedMultiplier: vi.fn(),
    nodeCount: 0,
    linkCount: 0,
    onExpandContract: vi.fn(),
    onClearAndReset: vi.fn(),
    ...overrides,
  };
  render(<VisualizerControls {...props} />);
  return props;
}

function openPanel(key: string) {
  fireEvent.click(screen.getByRole('button', { name: i18n.t(key) }));
}

describe('VisualizerControls', () => {
  it('keeps controls out of the graph until a group is opened', () => {
    renderControls();

    expect(screen.queryByRole('group')).not.toBeInTheDocument();
    expect(screen.queryByLabelText(i18n.t('visualizer.ackWindow'))).not.toBeInTheDocument();

    openPanel('visualizer.groupFilters');
    expect(screen.getByRole('group', { name: i18n.t('visualizer.groupFilters') })).toBeVisible();
  });

  it('shows only one group at a time', () => {
    renderControls();

    openPanel('visualizer.groupFilters');
    openPanel('visualizer.groupLegend');

    const groups = screen.getAllByRole('group');
    expect(groups).toHaveLength(1);
    expect(groups[0]).toHaveAccessibleName(i18n.t('visualizer.groupLegend'));
  });

  it('closes the open group on Escape', () => {
    renderControls();

    openPanel('visualizer.groupLayout');
    expect(screen.getByRole('group')).toBeInTheDocument();

    fireEvent.keyDown(document, { key: 'Escape' });
    expect(screen.queryByRole('group')).not.toBeInTheDocument();
  });

  it('collapses the whole toolbar so the graph gets the full surface', () => {
    const props = renderControls();

    fireEvent.click(screen.getByRole('button', { name: i18n.t('visualizer.hideToolbar') }));
    expect(props.setShowControls).toHaveBeenCalledWith(false);
  });

  it('offers a way back when the toolbar is collapsed', () => {
    const props = renderControls({ showControls: false });

    const restore = screen.getByRole('button', { name: i18n.t('visualizer.showToolbar') });
    expect(screen.queryByRole('button', { name: i18n.t('visualizer.groupFilters') })).toBeNull();

    fireEvent.click(restore);
    expect(props.setShowControls).toHaveBeenCalledWith(true);
  });

  it('explains the Community globe in the node legend', () => {
    renderControls();
    openPanel('visualizer.groupLegend');

    expect(screen.getByText(i18n.t('path.directoryGlobe'))).toBeInTheDocument();
    expect(screen.getByTestId('directory-globe-icon')).toBeInTheDocument();
  });

  it('allows clearing numeric inputs while editing', () => {
    renderControls();
    openPanel('visualizer.groupFilters');

    const observationInput = screen.getByLabelText(
      i18n.t('visualizer.ackWindow')
    ) as HTMLInputElement;
    const pruneInput = screen.getByLabelText(i18n.t('visualizer.window')) as HTMLInputElement;

    fireEvent.change(observationInput, { target: { value: '' } });
    fireEvent.change(pruneInput, { target: { value: '' } });

    expect(observationInput.value).toBe('');
    expect(pruneInput.value).toBe('');
  });
});
