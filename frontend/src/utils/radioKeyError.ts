import type { TFunction } from 'i18next';

import { ApiError, getApiErrorCode } from '../api';
import { userDocsUrl } from './userDocsUrl';

/**
 * The radio key is not on this instance (`app/keystore.py`, `RADIO_KEY_ERRORS`):
 * `errors.<code>` says why, and `docs/user/<lang>/deep/radio.md` what needs it.
 * `share_radio_key_unavailable` is the same failure for an encrypted DM file.
 */
const RADIO_KEY_CODE_RE =
  /^(radio_key_(behind_proxy|export_disabled|unavailable)|share_radio_key_unavailable)$/;

/** Also true for a fanout module's `last_error`, which is the bare code. */
export function isRadioKeyCode(code: string | null | undefined): boolean {
  return !!code && RADIO_KEY_CODE_RE.test(code);
}

export function isRadioKeyError(err: unknown): boolean {
  return err instanceof ApiError && isRadioKeyCode(getApiErrorCode(err.detail)?.code);
}

interface DocsAction {
  action: { label: string; onClick: () => void };
}

/** Toast options with the docs link. */
export function radioKeyDocsAction(t: TFunction, language: string | undefined): DocsAction {
  return {
    action: {
      label: t('settings.common.learnMore'),
      onClick: () => window.open(userDocsUrl(language, 'deep/radio'), '_blank', 'noreferrer'),
    },
  };
}

/** The same, only when `err` is a missing radio key. */
export function radioKeyToastAction(
  err: unknown,
  t: TFunction,
  language: string | undefined
): DocsAction | undefined {
  return isRadioKeyError(err) ? radioKeyDocsAction(t, language) : undefined;
}
