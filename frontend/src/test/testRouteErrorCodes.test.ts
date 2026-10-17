/**
 * The push and notification test routes answer coded details only (no network
 * error text). Each code has a translated message in every language.
 */

import { describe, expect, it } from 'vitest';

import { ApiError, formatApiError } from '../api';
import i18n from '../i18n';
import kEn from '../i18n/locales/slices/k.en.json';
import kFr from '../i18n/locales/slices/k.fr.json';

i18n.addResourceBundle('en', 'translation', kEn, true, true);
i18n.addResourceBundle('fr', 'translation', kFr, true, true);

const CODES = [
  'push_delivery_failed',
  'push_stale',
  'push_timeout',
  'notification_test_failed',
  'notification_destination_invalid',
] as const;

describe('test route error codes', () => {
  it.each(['en', 'fr'] as const)('translates every code in %s', async (lang) => {
    const t = i18n.getFixedT(lang);
    for (const code of CODES) {
      const err = new ApiError('backend message', 502, { code, message: 'backend message' });
      const text = formatApiError(err, t);
      expect(text).toBe(t(`errors.${code}`));
      expect(text).not.toBe('backend message');
      expect(text).not.toBe(`errors.${code}`);
    }
  });

  it('has different copy in French and English', () => {
    for (const code of CODES) {
      expect(kFr.errors[code]).not.toBe(kEn.errors[code]);
    }
  });
});
