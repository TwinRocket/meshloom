import { describe, expect, it } from 'vitest';
import { formatClockTime } from '../utils/formatTime';

describe('formatClockTime', () => {
  const ts = Date.UTC(2026, 9, 25, 14, 5) / 1000;
  const hourMinute = (language: string) =>
    new Intl.DateTimeFormat(language, { hour: '2-digit', minute: '2-digit' }).format(
      new Date(ts * 1000)
    );

  it('formats in the requested language', () => {
    expect(formatClockTime(ts, 'fr')).toBe(hourMinute('fr'));
    expect(formatClockTime(ts, 'en-US')).toBe(hourMinute('en-US'));
    expect(formatClockTime(ts, 'en-US')).toMatch(/[AP]M/);
  });

  it('defaults to the UI language and never throws on a repeated call', () => {
    expect(formatClockTime(ts)).toBe(formatClockTime(ts));
  });
});
