import { fireEvent, render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';

import { ConvertedBotsBanner, convertedBotsBannerVisible } from '../components/ConvertedBotsBanner';
import i18n from '../i18n';
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
    ...overrides,
  };
}

describe('ConvertedBotsBanner', () => {
  it('shows while the notice is pending and only to administrators', () => {
    const pending = health({ converted_bots: { converted: 2, attention: 2 } });
    expect(convertedBotsBannerVisible(pending, true)).toBe(true);
    expect(convertedBotsBannerVisible(pending, false)).toBe(false);
    expect(convertedBotsBannerVisible(health({ converted_bots: null }), true)).toBe(false);
    expect(convertedBotsBannerVisible(health(), true)).toBe(false);
    expect(convertedBotsBannerVisible(null, true)).toBe(false);
  });

  it('says how many bots were converted and opens the extensions section', () => {
    const onOpenExtensions = vi.fn();
    render(
      <ConvertedBotsBanner
        health={health({ converted_bots: { converted: 2, attention: 1 } })}
        isAdmin
        onOpenExtensions={onOpenExtensions}
      />
    );
    expect(screen.getByTestId('converted-bots-banner').textContent).toContain(
      i18n.t('settings.extensions.migration.bannerText', { count: 2 })
    );
    fireEvent.click(screen.getByRole('button'));
    expect(onOpenExtensions).toHaveBeenCalledTimes(1);
  });

  it('renders nothing once acknowledged', () => {
    render(
      <ConvertedBotsBanner
        health={health({ converted_bots: null })}
        isAdmin
        onOpenExtensions={vi.fn()}
      />
    );
    expect(screen.queryByTestId('converted-bots-banner')).toBeNull();
  });
});
