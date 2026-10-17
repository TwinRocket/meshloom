/**
 * The fanout routes answer coded details for a refused destination. Apprise
 * refusals carry params.context = "apprise" (i18next context: the
 * errors.<code>_apprise key) and the 1-based URL line.
 */

import { describe, expect, it } from 'vitest';

import { ApiError, formatApiError } from '../api';
import i18n from '../i18n';
import nEn from '../i18n/locales/slices/n.en.json';
import nFr from '../i18n/locales/slices/n.fr.json';
import oEn from '../i18n/locales/slices/o.en.json';
import oFr from '../i18n/locales/slices/o.fr.json';

for (const [lang, slice] of [
  ['en', nEn],
  ['fr', nFr],
  ['en', oEn],
  ['fr', oFr],
] as const) {
  i18n.addResourceBundle(lang, 'translation', slice, true, true);
}

const APPRISE_CODES = [
  'egress_scheme',
  'egress_private_address',
  'egress_unresolvable',
  'apprise_url_invalid',
  'apprise_chat_id_required',
] as const;

describe('Apprise URL error codes', () => {
  it.each(['en', 'fr'] as const)('translates every code with its line in %s', (lang) => {
    const t = i18n.getFixedT(lang);
    for (const code of APPRISE_CODES) {
      const err = new ApiError('backend message', 400, {
        code,
        message: 'backend message',
        params: { line: 3, context: 'apprise', scheme: 'dbus' },
      });
      const text = formatApiError(err, t);
      expect(text).not.toBe('backend message');
      expect(text).toContain('3');
    }
  });

  it('names the refused scheme', () => {
    const err = new ApiError('x', 400, {
      code: 'egress_scheme',
      message: 'x',
      params: { line: 1, context: 'apprise', scheme: 'dbus' },
    });
    expect(formatApiError(err, i18n.getFixedT('en'))).toContain('dbus://');
  });

  it('keeps the generic copy for the webhook (no context)', () => {
    const err = new ApiError('x', 400, { code: 'egress_scheme', message: 'x' });
    expect(formatApiError(err, i18n.getFixedT('en'))).toBe(nEn.errors.egress_scheme);
  });

  it('has different copy in French and English', () => {
    for (const key of Object.keys(oEn.errors) as (keyof typeof oEn.errors)[]) {
      expect(oFr.errors[key]).not.toBe(oEn.errors[key]);
    }
  });
});
