import type { ShareStatus, ShareTermsFields } from '../types';

/** True when the owner turned sharing on before and the terms changed since. */
export function shareTermsStale(status: ShareStatus): boolean {
  return (status as Partial<ShareTermsFields>).terms_stale === true;
}
