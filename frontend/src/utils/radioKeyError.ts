import type { TFunction } from 'i18next';

import { ApiError, getApiErrorCode } from '../api';
import { userDocsUrl } from './userDocsUrl';

/**
 * The radio key is not on this instance (`app/keystore.py`, `RADIO_KEY_ERRORS`):
 * `errors.<code>` says why, and `docs/user/<lang>/deep/radio.md` what needs it.
 */
const RADIO_KEY_CODE_RE = /^radio_key_(behind_proxy|export_disabled|unavailable)$/;

/** Also true for a fanout module's `last_error`, which is the bare code. */
export function isRadioKeyCode(code: string | null | undefined): boolean {
  return !!code && RADIO_KEY_CODE_RE.test(code);
}

export function isRadioKeyError(err: unknown): boolean {
  return err instanceof ApiError && isRadioKeyCode(getApiErrorCode(err.detail)?.code);
}

/** Toast options with the docs link, for an error shown as a toast. */
export function radioKeyToastAction(
  err: unknown,
  t: TFunction,
  language: string | undefined
): { action: { label: string; onClick: () => void } } | undefined {
  if (!isRadioKeyError(err)) return undefined;
  return {
    action: {
      label: t('settings.common.learnMore'),
      onClick: () => window.open(userDocsUrl(language, 'deep/radio'), '_blank', 'noreferrer'),
    },
  };
}
