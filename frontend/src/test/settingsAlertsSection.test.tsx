import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { SettingsAlertsSection } from '../components/settings/SettingsAlertsSection';
import { api } from '../api';
import i18n from '../i18n';
import {
  DEFAULT_TELEMETRY_ALERT_RULES,
  normalizeTelemetryAlertCatalog,
  resolveTelemetryAlertRules,
  type AppSettings,
  type AppSettingsUpdate,
} from '../types';
import { DEFAULT_LOCALE } from '../utils/languagePreference';

const pushMocks = vi.hoisted(() => ({
  patchPreferences: vi.fn(async () => {}),
  preferences: {
    defaults: {
      telemetry_alert: { push: true, email: false, webhook: false },
    },
    overrides: {},
    vapid_subject: '',
  },
}));

vi.mock('../contexts/PushSubscriptionContext', () => ({
  usePush: () => ({
    preferences: pushMocks.preferences,
    patchPreferences: pushMocks.patchPreferences,
  }),
}));

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
  tracked_telemetry_repeaters: ['aa'.repeat(32)],
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
    <SettingsAlertsSection
      appSettings={overrides?.appSettings ?? baseSettings}
      onSaveAppSettings={onSaveAppSettings}
      trackedTelemetryRepeaters={
        overrides?.appSettings?.tracked_telemetry_repeaters ??
        baseSettings.tracked_telemetry_repeaters
      }
    />
  );
  await waitFor(() => {
    expect(api.getTelemetryAlertCatalog).toHaveBeenCalled();
  });
  return { onSaveAppSettings };
}

describe('telemetry alert rule helpers', () => {
  it('defaults missing documents', () => {
    expect(resolveTelemetryAlertRules(undefined)).toEqual(DEFAULT_TELEMETRY_ALERT_RULES);
    expect(resolveTelemetryAlertRules(null)).toEqual(DEFAULT_TELEMETRY_ALERT_RULES);
  });

  it('migrates the legacy 3-field document', () => {
    const resolved = resolveTelemetryAlertRules({
      battery_volts_min: 3.7,
      noise_floor_max_dbm: -85,
      misses_before_alert: 3,
    });
    expect(resolved.rules.battery.threshold).toBe(3.7);
    expect(resolved.rules.noise.threshold).toBe(-85);
    expect(resolved.rules.silence.threshold).toBe(3);
    expect(resolved.channels.push).toBe(true);
  });

  it('keeps the new channels/rules/overrides shape', () => {
    const resolved = resolveTelemetryAlertRules({
      channels: { push: false, email: true, webhook: true },
      rules: { battery: { enabled: false, threshold: 3.1 } },
      overrides: { aabbcc: { alerting: false, rules: { battery: { threshold: 3.0 } } } },
    });
    expect(resolved.channels).toEqual({ push: false, email: true, webhook: true });
    expect(resolved.rules.battery.enabled).toBe(false);
    expect(resolved.rules.battery.threshold).toBe(3.1);
    expect(resolved.overrides.aabbcc.alerting).toBe(false);
    expect(resolved.overrides.aabbcc.rules?.battery.threshold).toBe(3.0);
  });

  it('accepts backend string metric ids', () => {
    const catalog = normalizeTelemetryAlertCatalog({
      metrics: ['battery', 'lpp:temperature'],
      latches: [],
      tracked: [],
    });
    expect(catalog.metrics.map((metric) => metric.id)).toEqual(['battery', 'lpp:temperature']);
  });

  it('accepts alternate catalog field names', () => {
    const catalog = normalizeTelemetryAlertCatalog({
      metrics: [{ metric_id: 'battery', unit: 'V' }],
      latched: [{ pubkey: 'aa', rule: 'battery', fired_at: 1_700_000_000, value: 3.2 }],
      nodes: [{ pubkey: 'aa', name: 'Hill', enabled: true }],
    });
    expect(catalog.metrics[0].id).toBe('battery');
    expect(catalog.latches[0]).toMatchObject({
      public_key: 'aa',
      rule_id: 'battery',
      last_value: 3.2,
    });
    expect(catalog.tracked[0]).toEqual({ public_key: 'aa', name: 'Hill', alerting: true });
  });
});

describe('SettingsAlertsSection', () => {
  beforeEach(() => {
    pushMocks.patchPreferences.mockClear();
    pushMocks.preferences.defaults.telemetry_alert = {
      push: true,
      email: false,
      webhook: false,
    };
    vi.spyOn(api, 'getTelemetryAlertCatalog').mockResolvedValue({
      metrics: [],
      latches: [],
      tracked: [],
    });
  });

  afterEach(() => {
    vi.restoreAllMocks();
    void i18n.changeLanguage(DEFAULT_LOCALE);
  });

  it('disables email and webhook voices until destinations are set', async () => {
    await renderSection();

    expect(screen.getByLabelText(i18n.t('settings.alerts.voicePush'))).toBeEnabled();
    expect(screen.getByLabelText(i18n.t('settings.alerts.voiceEmail'))).toBeDisabled();
    expect(screen.getByLabelText(i18n.t('settings.alerts.voiceWebhook'))).toBeDisabled();
    expect(
      screen.getAllByRole('link', { name: i18n.t('settings.alerts.configureDestinations') })
    ).toHaveLength(2);
    expect(
      screen.getAllByRole('link', { name: i18n.t('settings.alerts.configureDestinations') })[0]
    ).toHaveAttribute('href', '#settings/notifications');
  });

  it('saves the push voice through notification preferences', async () => {
    const { onSaveAppSettings } = await renderSection();

    fireEvent.click(screen.getByLabelText(i18n.t('settings.alerts.voicePush')));

    await waitFor(() => {
      expect(pushMocks.patchPreferences).toHaveBeenCalledWith({
        defaults: { telemetry_alert: { push: false } },
      });
      expect(onSaveAppSettings).not.toHaveBeenCalled();
    });
  });

  it('renders builtin battery and RSSI help about last heard packet', async () => {
    await renderSection();

    expect(
      screen.getByLabelText(i18n.t('settings.alerts.rules.battery.label'))
    ).toBeInTheDocument();
    expect(screen.getByText(i18n.t('settings.alerts.rules.rssi.help'))).toBeInTheDocument();
    expect(screen.getByText(i18n.t('settings.alerts.rules.snr.help'))).toBeInTheDocument();
  });
});
