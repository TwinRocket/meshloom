import { useEffect, useMemo, useState, type FormEvent } from 'react';
import { useTranslation } from 'react-i18next';
import { Crosshair } from 'lucide-react';

import { api, ApiError } from '../api';
import type {
  Contact,
  Conversation,
  LocateAnchor,
  LocateCandidate,
  LocateResponse,
} from '../types';
import { CONTACT_TYPE_REPEATER } from '../types';
import { calculateDistance, isValidLocation } from '../utils/pathUtils';
import { calibrateRadiusKm } from '../utils/locateZone';
import { Button } from './ui/button';
import { Input } from './ui/input';
import { LocateZoneMap } from './LocateZoneMap';
import { ToolPaneHeader } from './ToolPaneHeader';

interface LocatePaneProps {
  /** Leaves this sub-screen for the Tools screen. Phones only. */
  onBackToTools?: () => void;
  contacts: Contact[];
  locateKey?: string;
  directoryEnabled?: boolean;
  onSelectLocate: (query: string) => void;
  onOpenDirectorySettings?: () => void;
}

function isAmbiguousDetail(
  detail: unknown
): detail is { reason: 'ambiguous'; candidates: LocateCandidate[] } {
  return (
    typeof detail === 'object' &&
    detail !== null &&
    'reason' in detail &&
    (detail as { reason?: string }).reason === 'ambiguous' &&
    Array.isArray((detail as { candidates?: unknown }).candidates)
  );
}

function sourceLabel(source: LocateResponse['source'], t: (key: string) => string): string {
  if (source === 'local') return t('locate.sourceLocal');
  if (source === 'corescope') return t('locate.sourceCorescope');
  if (source === 'mixte') return t('locate.sourceMixte');
  return t('locate.sourceUnknown');
}

export function LocatePane({
  onBackToTools,
  contacts,
  locateKey,
  directoryEnabled = false,
  onSelectLocate,
  onOpenDirectorySettings,
}: LocatePaneProps) {
  const { t } = useTranslation();
  const [query, setQuery] = useState(locateKey ?? '');
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [result, setResult] = useState<LocateResponse | null>(null);
  const [candidates, setCandidates] = useState<LocateCandidate[]>([]);
  const [showDisks, setShowDisks] = useState(true);
  const [radiusOverrides, setRadiusOverrides] = useState<Record<string, number>>({});
  const [calibratingKey, setCalibratingKey] = useState<string | null>(null);

  useEffect(() => {
    setQuery(locateKey ?? '');
  }, [locateKey]);

  useEffect(() => {
    if (!locateKey) {
      setResult(null);
      setCandidates([]);
      setError(null);
      return;
    }
    let cancelled = false;
    setLoading(true);
    setError(null);
    setCandidates([]);
    setRadiusOverrides({});
    api
      .locate(locateKey)
      .then((payload) => {
        if (!cancelled) setResult(payload);
      })
      .catch((err: unknown) => {
        if (cancelled) return;
        if (err instanceof ApiError && err.status === 409 && isAmbiguousDetail(err.detail)) {
          setResult(null);
          setCandidates(err.detail.candidates);
          return;
        }
        if (err instanceof ApiError && err.status === 400) {
          setResult(null);
          setError(typeof err.detail === 'string' ? err.detail : err.message);
          return;
        }
        setResult(null);
        setError(err instanceof Error ? err.message : t('locate.loadFailed'));
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [locateKey, t]);

  const anchors = useMemo(() => {
    if (!result) return [];
    return result.anchors.map((anchor) => {
      const key = anchor.public_key ?? `${anchor.kind}:${anchor.lat}:${anchor.lon}`;
      const override = radiusOverrides[key];
      return override == null ? anchor : { ...anchor, radius_km: override };
    });
  }, [result, radiusOverrides]);

  const handleSubmit = (event: FormEvent) => {
    event.preventDefault();
    const next = query.trim();
    if (!next) return;
    onSelectLocate(next);
  };

  const calibrateAnchor = async (anchor: LocateAnchor, mode: 'directory' | 'radio') => {
    if (!anchor.public_key) return;
    setCalibratingKey(anchor.public_key);
    try {
      const distances: number[] = [];
      if (mode === 'directory') {
        const neighbors = await api.getDirectoryNodeNeighbors(anchor.public_key);
        for (const neighbor of neighbors.neighbors) {
          if (neighbor.ambiguous || !isValidLocation(neighbor.lat ?? null, neighbor.lon ?? null)) {
            continue;
          }
          const distance = calculateDistance(
            anchor.lat,
            anchor.lon,
            neighbor.lat ?? null,
            neighbor.lon ?? null
          );
          if (distance != null) distances.push(distance);
        }
      } else {
        const neighbors = await api.repeaterNeighbors(anchor.public_key);
        for (const neighbor of neighbors.neighbors) {
          const match = contacts.find(
            (contact) =>
              contact.public_key.toLowerCase().startsWith(neighbor.pubkey_prefix.toLowerCase()) &&
              isValidLocation(contact.lat ?? null, contact.lon ?? null)
          );
          if (!match || match.lat == null || match.lon == null) continue;
          const distance = calculateDistance(anchor.lat, anchor.lon, match.lat, match.lon);
          if (distance != null) distances.push(distance);
        }
      }
      const nextRadius = calibrateRadiusKm(distances);
      if (nextRadius == null) {
        setError(t('locate.calibrateEmpty'));
        return;
      }
      setRadiusOverrides((prev) => ({ ...prev, [anchor.public_key!]: nextRadius }));
      setError(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : t('locate.calibrateFailed'));
    } finally {
      setCalibratingKey(null);
    }
  };

  const identityLabel = result?.identity
    ? result.identity.name || result.identity.public_key.slice(0, 12)
    : null;

  return (
    <div className="flex h-full min-h-0 flex-col overflow-y-auto" data-testid="locate-pane">
      <ToolPaneHeader
        title={
          <span className="flex items-center justify-center gap-2 md:justify-start">
            <Crosshair className="h-4 w-4 shrink-0" aria-hidden="true" />
            {t('locate.title')}
          </span>
        }
        onBack={onBackToTools}
        subtitle={<p className="max-w-3xl text-[0.8125rem]">{t('locate.help')}</p>}
      />

      <form
        className="flex shrink-0 gap-2 border-b border-border px-4 py-3"
        onSubmit={handleSubmit}
      >
        <Input
          value={query}
          onChange={(event) => setQuery(event.target.value)}
          placeholder={t('locate.searchPlaceholder')}
          aria-label={t('locate.searchPlaceholder')}
        />
        <Button type="submit" disabled={!query.trim() || loading}>
          {t('locate.search')}
        </Button>
      </form>

      <div className="flex flex-1 flex-col gap-4 p-4 lg:min-h-0 lg:flex-row">
        <section className="flex w-full flex-col gap-3 lg:max-w-[24rem]">
          {loading && <p className="text-sm text-muted-foreground">{t('locate.loading')}</p>}
          {error && (
            <p className="text-sm text-destructive" data-testid="locate-error">
              {error}
            </p>
          )}

          {candidates.length > 0 && (
            <div data-testid="locate-candidates">
              <h3 className="text-sm font-semibold">{t('locate.pickIdentity')}</h3>
              <p className="mt-1 text-xs text-muted-foreground">{t('locate.ambiguousHelp')}</p>
              <ul className="mt-2 space-y-1">
                {candidates.map((candidate) => (
                  <li key={candidate.public_key}>
                    <button
                      type="button"
                      className="w-full rounded border border-border px-3 py-2 text-left text-sm hover:bg-accent"
                      onClick={() => onSelectLocate(candidate.public_key)}
                    >
                      <span className="font-medium">
                        {candidate.name || candidate.public_key.slice(0, 12)}
                      </span>
                      <span className="ml-2 text-xs text-muted-foreground">
                        {candidate.public_key.slice(0, 12)}
                      </span>
                    </button>
                  </li>
                ))}
              </ul>
            </div>
          )}

          {result?.empty_reason === 'insufficient_identity' && (
            <p className="text-sm text-muted-foreground" data-testid="locate-insufficient">
              {t('locate.insufficient')}
            </p>
          )}

          {result?.empty_reason === 'directory_off' && (
            <div data-testid="locate-directory-cta">
              <p className="text-sm text-muted-foreground">{t('locate.directoryOff')}</p>
              {onOpenDirectorySettings && (
                <Button
                  type="button"
                  variant="outline"
                  size="sm"
                  className="mt-2"
                  onClick={onOpenDirectorySettings}
                >
                  {t('locate.enableDirectory')}
                </Button>
              )}
            </div>
          )}

          {result?.empty_reason === 'no_anchors' && (
            <p className="text-sm text-muted-foreground" data-testid="locate-empty">
              {result.heard_locally_0hop && !result.radio_has_gps
                ? t('locate.heardNoGps')
                : t('locate.noObservers')}
            </p>
          )}

          {result?.identity && (
            <div>
              <div className="flex flex-wrap items-center gap-2">
                <h3 className="text-sm font-semibold">{identityLabel}</h3>
                {result.source && (
                  <span
                    className="rounded bg-primary/10 px-1.5 py-0.5 text-[0.625rem] font-medium uppercase tracking-wider"
                    data-testid="locate-source-badge"
                  >
                    {sourceLabel(result.source, t)}
                  </span>
                )}
                {result.identity.inferred && (
                  <span className="rounded bg-muted px-1.5 py-0.5 text-[0.625rem] uppercase tracking-wider text-muted-foreground">
                    {t('locate.inferred')}
                  </span>
                )}
              </div>
              <p className="mt-1 font-mono text-xs text-muted-foreground">
                {result.identity.public_key}
              </p>
              <p className="mt-2 text-xs text-muted-foreground">
                {t('locate.anchorCount', { count: anchors.length })}
              </p>
            </div>
          )}

          {anchors.length > 0 && (
            <ul className="space-y-2" data-testid="locate-anchor-list">
              {anchors.map((anchor) => {
                const contact = anchor.public_key
                  ? contacts.find((item) => item.public_key === anchor.public_key)
                  : undefined;
                const canRadioPoll =
                  Boolean(anchor.calibratable && anchor.public_key) &&
                  (contact?.type === CONTACT_TYPE_REPEATER || !contact);
                return (
                  <li
                    key={`${anchor.kind}-${anchor.public_key ?? anchor.name}`}
                    className="rounded border border-border px-3 py-2 text-sm"
                  >
                    <div className="font-medium">{anchor.name}</div>
                    <div className="text-xs text-muted-foreground">
                      {t(`locate.kind.${anchor.kind}`)} · {anchor.radius_km.toFixed(0)} km
                      {anchor.snr != null ? ` · SNR ${anchor.snr.toFixed(1)}` : ''}
                    </div>
                    {anchor.calibratable && anchor.public_key && (
                      <div className="mt-2 flex flex-wrap gap-2">
                        {directoryEnabled && (
                          <Button
                            type="button"
                            size="sm"
                            variant="outline"
                            disabled={calibratingKey === anchor.public_key}
                            onClick={() => void calibrateAnchor(anchor, 'directory')}
                          >
                            {t('locate.calibrateDirectory')}
                          </Button>
                        )}
                        {canRadioPoll && (
                          <Button
                            type="button"
                            size="sm"
                            variant="outline"
                            disabled={calibratingKey === anchor.public_key}
                            onClick={() => void calibrateAnchor(anchor, 'radio')}
                          >
                            {t('locate.calibrateRadio')}
                          </Button>
                        )}
                      </div>
                    )}
                  </li>
                );
              })}
            </ul>
          )}

          {result && result.unresolved_hops.length > 0 && (
            <div>
              <h4 className="text-xs font-medium uppercase tracking-wider text-muted-foreground">
                {t('locate.unresolved')}
              </h4>
              <ul className="mt-1 space-y-1 text-xs text-muted-foreground">
                {result.unresolved_hops.map((hop) => (
                  <li key={`${hop.prefix}-${hop.reason}`}>
                    {hop.prefix} — {t(`locate.unresolvedReason.${hop.reason}`)}
                    {hop.candidates.map((candidate) => (
                      <button
                        key={candidate.public_key}
                        type="button"
                        className="ml-2 text-primary hover:underline"
                        onClick={() => onSelectLocate(candidate.public_key)}
                      >
                        {candidate.name || candidate.public_key.slice(0, 12)}
                      </button>
                    ))}
                  </li>
                ))}
              </ul>
            </div>
          )}
        </section>

        <section className="flex min-h-72 flex-1 flex-col gap-2">
          {anchors.length > 0 || result?.declared_gps ? (
            <>
              <label className="flex items-center gap-2 text-xs text-muted-foreground">
                <input
                  type="checkbox"
                  checked={showDisks}
                  onChange={(event) => setShowDisks(event.target.checked)}
                />
                {t('locate.showDisks')}
              </label>
              <LocateZoneMap
                anchors={anchors}
                declaredGps={result?.declared_gps ?? null}
                showDisks={showDisks}
              />
            </>
          ) : (
            <div className="flex min-h-72 flex-1 items-center justify-center rounded border border-dashed border-border text-sm text-muted-foreground">
              {t('locate.mapEmpty')}
            </div>
          )}
        </section>
      </div>
    </div>
  );
}

export function locateConversation(query?: string): Conversation {
  const trimmed = query?.trim() ?? '';
  return {
    type: 'locate',
    id: 'locate',
    name: 'RF Locate',
    ...(trimmed ? { locateKey: trimmed } : {}),
  };
}
