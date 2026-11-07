import type { TFunction } from 'i18next';

import { ApiError, formatApiError, getApiErrorCode } from '../api';
import { PASSWORD_MIN_LENGTH } from './types';

/**
 * The text for a failed Users & Access call. Coded errors (`errors.auth_*`, such as
 * `auth_last_admin` or `auth_password_policy`) are translated by `formatApiError`;
 * a 429 or 503 without a code (a proxy in front, a busy server) gets a generic line
 * instead of the raw status text.
 */
export function formatAccessError(err: unknown, t: TFunction): string {
  if (err instanceof ApiError) {
    const parsed = getApiErrorCode(err.detail);
    if (parsed?.code === 'auth_password_policy') {
      const minLength = Number(parsed.params.min_length) || PASSWORD_MIN_LENGTH;
      return `${formatApiError(err, t)} ${t('errors.auth_password_policyDetail', {
        min_length: minLength,
      })}`;
    }
    if (!parsed && err.status === 429) {
      return t('errors.access_rate_limited', { retry_after: err.retryAfter ?? 30 });
    }
    if (!parsed && err.status === 503) return t('errors.access_unavailable');
  }
  return formatApiError(err, t);
}
