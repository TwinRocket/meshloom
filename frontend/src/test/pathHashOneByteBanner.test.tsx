import { fireEvent, render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';

import {
  PathHashOneByteBanner,
  pathHashOneByteBannerVisible,
} from '../components/PathHashOneByteBanner';
import type { RadioConfig } from '../types';

function config(overrides: Partial<RadioConfig> = {}): RadioConfig {
  return {
    public_key: 'aa'.repeat(32),
    name: 'Node',
    lat: 0,
    lon: 0,
    tx_power: 17,
    max_tx_power: 22,
    radio: { freq: 910.525, bw: 62.5, sf: 7, cr: 5 },
    path_hash_mode: 0,
    path_hash_mode_supported: true,
    ...overrides,
  };
}

describe('PathHashOneByteBanner', () => {
  it('appears only when firmware supports path hashing and the radio is on 1 byte', () => {
    expect(pathHashOneByteBannerVisible(config())).toBe(true);
    expect(pathHashOneByteBannerVisible(config({ path_hash_mode: 1 }))).toBe(false);
    expect(pathHashOneByteBannerVisible(config({ path_hash_mode_supported: false }))).toBe(false);
    expect(pathHashOneByteBannerVisible(null)).toBe(false);
  });

  it('does not render when the radio is already on 2-byte hops', () => {
    render(
      <PathHashOneByteBanner config={config({ path_hash_mode: 1 })} onOpenRadioSettings={vi.fn()} />
    );
    expect(screen.queryByTestId('path-hash-one-byte-banner')).toBeNull();
  });

  it('opens radio settings from its action', () => {
    const onOpenRadioSettings = vi.fn();
    render(<PathHashOneByteBanner config={config()} onOpenRadioSettings={onOpenRadioSettings} />);
    fireEvent.click(screen.getByRole('button'));
    expect(onOpenRadioSettings).toHaveBeenCalledTimes(1);
  });
});
