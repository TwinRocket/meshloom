/**
 * Live region filter, remembered per browser.
 *
 * Stored value: `"all"` (every region) or a JSON array of IATA codes.
 * Nothing stored means "the radio's IATA" (see `effectiveLiveIataSelection`).
 */
export const LIVE_IATA_STORAGE_KEY = 'meshloom-live-iata';
/** Not a region: Community files unplaced observations under it. */
export const LIVE_PLACEHOLDER_IATA = 'XXX';

/** `null` = every region. A non-empty list = these regions only. */
export type LiveIataSelection = string[] | null;

const IATA_RE = /^[A-Z]{3}$/;

export function normalizeIataCode(raw: unknown): string | null {
  if (typeof raw !== 'string') return null;
  const code = raw.trim().toUpperCase();
  if (!IATA_RE.test(code) || code === LIVE_PLACEHOLDER_IATA) return null;
  return code;
}

/** Sorted, deduplicated, valid codes; an empty result means every region. */
export function normalizeLiveIataSelection(raw: unknown): LiveIataSelection {
  if (!Array.isArray(raw)) return null;
  const codes = new Set<string>();
  for (const item of raw) {
    const code = normalizeIataCode(item);
    if (code) codes.add(code);
  }
  return codes.size ? [...codes].sort() : null;
}

/** `undefined` when nothing was saved (or storage is unavailable). */
export function getSavedLiveIataSelection(): LiveIataSelection | undefined {
  try {
    const raw = localStorage.getItem(LIVE_IATA_STORAGE_KEY);
    if (raw == null) return undefined;
    if (raw === 'all') return null;
    const parsed: unknown = JSON.parse(raw);
    if (!Array.isArray(parsed)) return undefined;
    return normalizeLiveIataSelection(parsed);
  } catch {
    return undefined;
  }
}

export function setSavedLiveIataSelection(selection: LiveIataSelection): void {
  try {
    const codes = normalizeLiveIataSelection(selection);
    localStorage.setItem(LIVE_IATA_STORAGE_KEY, codes === null ? 'all' : JSON.stringify(codes));
  } catch {
    /* localStorage may be unavailable */
  }
}

/** The saved choice, else the radio's IATA alone, else every region. */
export function effectiveLiveIataSelection(
  saved: LiveIataSelection | undefined,
  radioIata: string | undefined
): LiveIataSelection {
  if (saved !== undefined) return saved;
  const radio = normalizeIataCode(radioIata);
  return radio ? [radio] : null;
}
