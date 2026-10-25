import type { AppSettingsUpdate, ShareStatus } from '../types';

/**
 * Terms-acceptance fields of `GET /api/share/status` and `PATCH /api/settings` (MSHL-292).
 * Kept here rather than in `types.ts` until the next types consolidation; the backend
 * (`ShareStatus`, `AppSettingsUpdate`) is the source of truth.
 *
 * The terms version is a server-side constant: the consent dialog never sends it, only
 * `share_terms_accepted: true` with `share_enabled: true`, and the server records its
 * own current version and the time.
 */
export interface ShareTermsFields {
  /** Current version of the share terms (server-side constant). */
  terms_version: number;
  /** Version the owner accepted, 0 when never. */
  terms_accepted_version: number;
  /** Unix time of the recorded acceptance, null when never accepted. */
  terms_accepted_at: number | null;
  /** Sharing was turned on under older terms: it is off until accepted again. */
  terms_stale: boolean;
}

export type ShareStatusWithTerms = ShareStatus & ShareTermsFields;

export type ShareSettingsUpdate = AppSettingsUpdate & { share_terms_accepted?: boolean };

/** True when the owner turned sharing on before and the terms changed since. */
export function shareTermsStale(status: ShareStatus): boolean {
  return (status as Partial<ShareTermsFields>).terms_stale === true;
}
