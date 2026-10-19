import { describe, expect, it } from 'vitest';

import { DEFAULT_TELEMETRY_ALERT_RULES, resolveTelemetryAlertRules } from '../types';

describe('resolveTelemetryAlertRules without channels', () => {
  it('has no channels field: alerts go where the telemetry.alert rules row says', () => {
    expect(DEFAULT_TELEMETRY_ALERT_RULES).not.toHaveProperty('channels');
    expect(resolveTelemetryAlertRules({ rules: {}, overrides: {} })).not.toHaveProperty('channels');
  });

  it('ignores a stored legacy channels field but keeps the rest', () => {
    const resolved = resolveTelemetryAlertRules({
      channels: { push: false, email: true },
      rules: { battery: { enabled: false, threshold: 3.1 } },
    });
    expect(resolved).not.toHaveProperty('channels');
    expect(resolved.rules.battery.threshold).toBe(3.1);
  });
});
