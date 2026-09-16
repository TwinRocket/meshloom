import { fireEvent, render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';

import { RadioSetupBanner, radioSetupBannerVisible } from '../components/RadioSetupBanner';
import type { HealthStatus } from '../types';

function health(overrides: Partial<HealthStatus> = {}): HealthStatus {
  return {
    status: 'ok',
    radio_connected: false,
    radio_initializing: false,
    connection_info: null,
    database_size_mb: 0,
    oldest_undecrypted_timestamp: null,
    fanout_statuses: {},
    bots_disabled: false,
    ...overrides,
  };
}

describe('RadioSetupBanner', () => {
  it('appears only when the server says no transport is configured', () => {
    expect(radioSetupBannerVisible(health({ transport_configured: false }))).toBe(true);
    expect(radioSetupBannerVisible(health({ transport_configured: true }))).toBe(false);
  });

  it('stays hidden when the server does not report the field at all', () => {
    // An older server omits it. Silence is not an answer, and treating it as one
    // would put a setup banner in front of a working installation.
    expect(radioSetupBannerVisible(health())).toBe(false);
    expect(radioSetupBannerVisible(null)).toBe(false);
  });

  it('does not render when a radio is already set up', () => {
    render(
      <RadioSetupBanner
        health={health({ transport_configured: true })}
        onOpenRadioSettings={vi.fn()}
      />
    );
    expect(screen.queryByTestId('radio-setup-banner')).toBeNull();
  });

  it('opens the radio settings from its action', () => {
    const onOpenRadioSettings = vi.fn();
    render(
      <RadioSetupBanner
        health={health({ transport_configured: false })}
        onOpenRadioSettings={onOpenRadioSettings}
      />
    );
    fireEvent.click(screen.getByRole('button'));
    expect(onOpenRadioSettings).toHaveBeenCalledTimes(1);
  });
});
