import { describe, expect, it } from 'vitest';

import { resolveTelemetryAlertRules } from '../types';

describe('resolveTelemetryAlertRules without channels', () => {
  it('tolerates a stored document that no longer has a channels field', () => {
    const resolved = resolveTelemetryAlertRules({ rules: {}, overrides: {} });
    expect(resolved.channels).toEqual({ push: true, email: false, webhook: false });
    expect(resolved.overrides).toEqual({});
  });

  it('keeps a legacy channels field readable', () => {
    const resolved = resolveTelemetryAlertRules({ channels: { push: false, email: true } });
    expect(resolved.channels.push).toBe(false);
    expect(resolved.channels.email).toBe(true);
  });
});
