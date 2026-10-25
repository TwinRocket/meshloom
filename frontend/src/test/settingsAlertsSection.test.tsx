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
  });

  it('keeps the rules/overrides shape and drops a stored channels field', () => {
    const resolved = resolveTelemetryAlertRules({
      channels: { push: false, email: true, webhook: true },
      rules: { battery: { enabled: false, threshold: 3.1 } },
      overrides: { aabbcc: { alerting: false, rules: { battery: { threshold: 3.0 } } } },
    });
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

const CHANNELS = [
  { id: 'builtin:push', type: 'web_push', name: 'Browser push', enabled: true },
  { id: 'ntfy', type: 'apprise', name: 'My ntfy', enabled: true },
];

function rulesWith(alertChannels: string[]) {
  return {
    version: 1,
    paused_until: null,
    events: { 'telemetry.alert': { channels: alertChannels } },
    conversations: {},
  };
}

describe('SettingsAlertsSection', () => {
  beforeEach(() => {
    vi.spyOn(api, 'getTelemetryAlertCatalog').mockResolvedValue({
      metrics: [],
      latches: [],
      tracked: [],
    });
    vi.spyOn(api, 'getNotificationChannels').mockResolvedValue(CHANNELS as never);
    vi.spyOn(api, 'getNotificationRules').mockResolvedValue(rulesWith(['builtin:push']) as never);
  });

  afterEach(() => {
    vi.restoreAllMocks();
    void i18n.changeLanguage(DEFAULT_LOCALE);
  });

  it('shows a chip per channel, pressed for those of the telemetry.alert row', async () => {
    await renderSection();

    expect(await screen.findByRole('button', { name: /Browser push/ })).toHaveAttribute(
      'aria-pressed',
      'true'
    );
    expect(screen.getByRole('button', { name: /My ntfy/ })).toHaveAttribute(
      'aria-pressed',
      'false'
    );
  });

  it('adds a channel to the telemetry.alert row through the rules, not the settings', async () => {
    const patch = vi
      .spyOn(api, 'patchNotificationRules')
      .mockResolvedValue(rulesWith(['builtin:push', 'ntfy']) as never);
    const { onSaveAppSettings } = await renderSection();

    fireEvent.click(await screen.findByRole('button', { name: /My ntfy/ }));

    await waitFor(() => {
      expect(patch).toHaveBeenCalledWith({
        events: { 'telemetry.alert': { channels: ['builtin:push', 'ntfy'] } },
      });
    });
    await waitFor(() => {
      expect(screen.getByRole('button', { name: /My ntfy/ })).toHaveAttribute(
        'aria-pressed',
        'true'
      );
    });
    expect(onSaveAppSettings).not.toHaveBeenCalled();
  });

  it('removes a channel from the row', async () => {
    const patch = vi.spyOn(api, 'patchNotificationRules').mockResolvedValue(rulesWith([]) as never);
    await renderSection();

    fireEvent.click(await screen.findByRole('button', { name: /Browser push/ }));

    await waitFor(() => {
      expect(patch).toHaveBeenCalledWith({ events: { 'telemetry.alert': { channels: [] } } });
    });
  });

  it('offers a retry when the channels cannot be loaded', async () => {
    vi.spyOn(api, 'getNotificationChannels').mockRejectedValueOnce(new Error('down'));
    await renderSection();

    expect(await screen.findByRole('alert')).toHaveTextContent(
      i18n.t('settings.alerts.channelsLoadFailed')
    );
    fireEvent.click(
      screen.getByRole('button', { name: i18n.t('settings.notifications.rulesRetry') })
    );
    expect(await screen.findByRole('button', { name: /Browser push/ })).toBeInTheDocument();
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
