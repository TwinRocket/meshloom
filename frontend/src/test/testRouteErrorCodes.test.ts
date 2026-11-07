/**
 * The push and notification test routes answer coded details only (no network
 * error text). Each code has a translated message in every language.
 */

import { describe, expect, it } from 'vitest';

import { ApiError, formatApiError } from '../api';
import i18n from '../i18n';
import kEn from '../i18n/locales/slices/k.en.json';
import kFr from '../i18n/locales/slices/k.fr.json';
import mEn from '../i18n/locales/slices/n.en.json';
import mFr from '../i18n/locales/slices/n.fr.json';

for (const [lang, slice] of [
  ['en', kEn],
  ['fr', kFr],
  ['en', mEn],
  ['fr', mFr],
] as const) {
  i18n.addResourceBundle(lang, 'translation', slice, true, true);
}

const K_CODES = [
  'push_delivery_failed',
  'push_stale',
  'push_timeout',
  'notification_test_failed',
  'notification_destination_invalid',
] as const;
// The egress guard and the webhook test's failure classes.
const M_CODES = [
  'egress_scheme',
  'egress_private_address',
  'egress_unresolvable',
  'timeout',
  'tls_error',
  'service_error',
  'no_push_device',
] as const;
const CODES = [...K_CODES, ...M_CODES];

describe('test route error codes', () => {
  it.each(['en', 'fr'] as const)('translates every code in %s', async (lang) => {
    const t = i18n.getFixedT(lang);
    for (const code of CODES) {
      const params = { status: 503 };
      const err = new ApiError('backend message', 502, {
        code,
        message: 'backend message',
        params,
      });
      const text = formatApiError(err, t);
      expect(text).toBe(t(`errors.${code}`, params));
      expect(text).not.toBe('backend message');
      expect(text).not.toBe(`errors.${code}`);
    }
  });

  it('has different copy in French and English', () => {
    for (const code of K_CODES) {
      expect(kFr.errors[code]).not.toBe(kEn.errors[code]);
    }
    for (const code of M_CODES) {
      expect(mFr.errors[code]).not.toBe(mEn.errors[code]);
    }
  });

  it('shows the HTTP status a webhook answered', () => {
    const err = new ApiError('x', 502, {
      code: 'service_error',
      message: 'x',
      params: { status: 503 },
    });
    expect(formatApiError(err, i18n.getFixedT('en'))).toContain('503');
  });
});
