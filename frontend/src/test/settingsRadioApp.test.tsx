import { render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { SettingsRadioAppSection } from '../components/settings/SettingsRadioAppSection';
import { api } from '../api';
import i18n from '../i18n';
import { type AppSettings, type AppSettingsUpdate } from '../types';
import { DEFAULT_LOCALE } from '../utils/languagePreference';

const baseSettings: AppSettings = {
  ui_preferences: { nav_rail: [], theme: '' },
  max_radio_contacts: 200,
  auto_decrypt_dm_on_advert: false,
  last_message_times: {},
  advert_interval: 0,
  last_advert_time: 0,
  flood_scope: '',
  known_regions: [],
  blocked_keys: [],
  blocked_names: [],
  discovery_blocked_types: [],
  tracked_telemetry_repeaters: [],
  tracked_telemetry_contacts: [],
  auto_resend_channel: false,
  telemetry_interval_hours: 8,
  telemetry_routed_hourly: false,
};

async function renderSection(overrides?: {
  appSettings?: AppSettings;
  onSaveAppSettings?: (update: AppSettingsUpdate) => Promise<void>;
}) {
  const onSaveAppSettings = overrides?.onSaveAppSettings ?? vi.fn(async () => {});
  render(
    <SettingsRadioAppSection
      appSettings={overrides?.appSettings ?? baseSettings}
      onSaveAppSettings={onSaveAppSettings}
    />
  );
  await waitFor(() => {
    expect(api.getContactGroups).toHaveBeenCalled();
  });
  return { onSaveAppSettings };
}

describe('SettingsRadioAppSection telemetry alerts', () => {
  beforeEach(() => {
    vi.spyOn(api, 'getCommunity').mockResolvedValue({
      enabled: false,
      locked: false,
      iata: '',
      broker_host: '',
      api_base: '',
      publisher_configured: false,
      publisher_connected: false,
      env_seeded: false,
    });
    vi.spyOn(api, 'getContactGroups').mockResolvedValue([]);
    vi.spyOn(api, 'getTelemetrySchedule').mockResolvedValue({
      preferred_hours: 8,
      effective_hours: 8,
      options: [1, 2, 3, 4, 6, 8, 12, 24],
      tracked_count: 0,
      max_tracked: 8,
      next_run_at: null,
      routed_hourly: false,
      next_routed_run_at: null,
    });
  });

  afterEach(() => {
    vi.restoreAllMocks();
    void i18n.changeLanguage(DEFAULT_LOCALE);
  });

  it('does not render the old battery/noise/misses inputs', async () => {
    const { onSaveAppSettings } = await renderSection();

    expect(
      screen.queryByLabelText(i18n.t('settings.radioApp.batteryVoltsMin'))
    ).not.toBeInTheDocument();
    expect(
      screen.queryByLabelText(i18n.t('settings.radioApp.noiseFloorMax'))
    ).not.toBeInTheDocument();
    expect(
      screen.queryByLabelText(i18n.t('settings.radioApp.missesBeforeAlert'))
    ).not.toBeInTheDocument();
    expect(screen.getByRole('link', { name: i18n.t('settingsNav.alerts') })).toHaveAttribute(
      'href',
      '#settings/alerts'
    );
    expect(onSaveAppSettings).not.toHaveBeenCalled();
  });
});
