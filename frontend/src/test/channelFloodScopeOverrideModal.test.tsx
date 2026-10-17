/**
 * The region input is seeded from the current override whenever the dialog
 * opens, and again if the override changes while it is open.
 */

import { fireEvent, render } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';

import { ChannelFloodScopeOverrideModal } from '../components/ChannelFloodScopeOverrideModal';

function renderModal(open: boolean, currentOverride: string | null) {
  return (
    <ChannelFloodScopeOverrideModal
      open={open}
      onClose={vi.fn()}
      roomName="#test"
      currentOverride={currentOverride}
      onSetOverride={vi.fn()}
    />
  );
}

const regionInput = () => document.getElementById('channel-region-input') as HTMLInputElement;

describe('ChannelFloodScopeOverrideModal seeding', () => {
  it('seeds on open, keeps edits while open, and reseeds on reopen', () => {
    const view = render(renderModal(true, '#alpha'));
    expect(regionInput().value).toBe('alpha');

    fireEvent.change(regionInput(), { target: { value: 'edited' } });
    view.rerender(renderModal(true, '#alpha'));
    expect(regionInput().value).toBe('edited');

    view.rerender(renderModal(false, '#alpha'));
    view.rerender(renderModal(true, '#alpha'));
    expect(regionInput().value).toBe('alpha');
  });

  it('reseeds when the override changes while open', () => {
    const view = render(renderModal(true, '#alpha'));
    fireEvent.change(regionInput(), { target: { value: 'edited' } });
    view.rerender(renderModal(true, '#beta'));
    expect(regionInput().value).toBe('beta');
  });
});
