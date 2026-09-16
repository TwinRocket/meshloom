import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { SettingsRadioAppSection } from '../components/settings/SettingsRadioAppSection';
import { api } from '../api';
import i18n from '../i18n';
import {
  DEFAULT_TELEMETRY_ALERT_RULES,
  MISSES_BEFORE_ALERT_MAX,
  MISSES_BEFORE_ALERT_MIN,
  clampMissesBeforeAlert,
  resolveTelemetryAlertRules,
  type AppSettings,
  type AppSettingsUpdate,
} from '../types';
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

describe('telemetry alert rule helpers', () => {
  it('defaults missing or non-finite fields', () => {
    expect(resolveTelemetryAlertRules(undefined)).toEqual(DEFAULT_TELEMETRY_ALERT_RULES);
    expect(resolveTelemetryAlertRules(null)).toEqual(DEFAULT_TELEMETRY_ALERT_RULES);
    expect(
      resolveTelemetryAlertRules({
        battery_volts_min: Number.NaN,
        noise_floor_max_dbm: Number.NaN,
        misses_before_alert: Number.NaN,
      })
    ).toEqual(DEFAULT_TELEMETRY_ALERT_RULES);
  });

  it('clamps misses_before_alert to 1–4', () => {
    expect(clampMissesBeforeAlert(0)).toBe(MISSES_BEFORE_ALERT_MIN);
    expect(clampMissesBeforeAlert(1)).toBe(1);
    expect(clampMissesBeforeAlert(4)).toBe(4);
    expect(clampMissesBeforeAlert(5)).toBe(MISSES_BEFORE_ALERT_MAX);
    expect(clampMissesBeforeAlert(2.4)).toBe(2);
    expect(clampMissesBeforeAlert(2.6)).toBe(3);
    expect(clampMissesBeforeAlert(Number.NaN)).toBe(
      DEFAULT_TELEMETRY_ALERT_RULES.misses_before_alert
    );
    expect(
      resolveTelemetryAlertRules({ ...DEFAULT_TELEMETRY_ALERT_RULES, misses_before_alert: 9 })
    ).toEqual({ ...DEFAULT_TELEMETRY_ALERT_RULES, misses_before_alert: 4 });
  });
});

describe('SettingsRadioAppSection telemetry alert rules', () => {
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

  it('renders default telemetry alert rules when settings omit the field', async () => {
    await renderSection();

    expect(screen.getByLabelText(i18n.t('settings.radioApp.batteryVoltsMin'))).toHaveValue(
      DEFAULT_TELEMETRY_ALERT_RULES.battery_volts_min
    );
    expect(screen.getByLabelText(i18n.t('settings.radioApp.noiseFloorMax'))).toHaveValue(
      DEFAULT_TELEMETRY_ALERT_RULES.noise_floor_max_dbm
    );
    expect(screen.getByLabelText(i18n.t('settings.radioApp.missesBeforeAlert'))).toHaveValue(
      String(DEFAULT_TELEMETRY_ALERT_RULES.misses_before_alert)
    );
    expect(screen.getByText(i18n.t('settings.radioApp.missesBeforeAlertHelp'))).toBeInTheDocument();
  });

  it('limits missed-poll options to 1–4', async () => {
    await renderSection();

    const select = screen.getByLabelText(i18n.t('settings.radioApp.missesBeforeAlert'));
    const values = Array.from(select.querySelectorAll('option')).map((option) => option.value);
    expect(values).toEqual(['1', '2', '3', '4']);
  });

  it('clamps an out-of-range stored misses value into 1–4', async () => {
    await renderSection({
      appSettings: {
        ...baseSettings,
        telemetry_alert_rules: {
          battery_volts_min: 3.5,
          noise_floor_max_dbm: -90,
          misses_before_alert: 9,
        },
      },
    });

    expect(screen.getByLabelText(i18n.t('settings.radioApp.missesBeforeAlert'))).toHaveValue('4');
  });

  it('saves telemetry_alert_rules when the battery threshold changes', async () => {
    const { onSaveAppSettings } = await renderSection();
    const input = screen.getByLabelText(i18n.t('settings.radioApp.batteryVoltsMin'));

    fireEvent.change(input, { target: { value: '3.7' } });
    fireEvent.blur(input);

    await waitFor(() => {
      expect(onSaveAppSettings).toHaveBeenCalledWith({
        telemetry_alert_rules: {
          battery_volts_min: 3.7,
          noise_floor_max_dbm: -90,
          misses_before_alert: 2,
        },
      });
    });
  });

  it('saves telemetry_alert_rules when missed polls changes', async () => {
    const { onSaveAppSettings } = await renderSection({
      appSettings: {
        ...baseSettings,
        telemetry_alert_rules: {
          battery_volts_min: 3.4,
          noise_floor_max_dbm: -85,
          misses_before_alert: 2,
        },
      },
    });

    fireEvent.change(screen.getByLabelText(i18n.t('settings.radioApp.missesBeforeAlert')), {
      target: { value: '3' },
    });

    await waitFor(() => {
      expect(onSaveAppSettings).toHaveBeenCalledWith({
        telemetry_alert_rules: {
          battery_volts_min: 3.4,
          noise_floor_max_dbm: -85,
          misses_before_alert: 3,
        },
      });
    });
  });

  it('does not save an invalid battery draft', async () => {
    const { onSaveAppSettings } = await renderSection();
    const input = screen.getByLabelText(i18n.t('settings.radioApp.batteryVoltsMin'));

    fireEvent.change(input, { target: { value: '' } });
    fireEvent.blur(input);

    expect(input).toHaveValue(DEFAULT_TELEMETRY_ALERT_RULES.battery_volts_min);
    expect(onSaveAppSettings).not.toHaveBeenCalled();
  });
});
