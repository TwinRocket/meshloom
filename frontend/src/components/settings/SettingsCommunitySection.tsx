import { useCallback, useEffect, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { toast } from '../ui/sonner';
import { api, ApiError, formatApiError } from '../../api';
import type {
  CommunityAirportHit,
  CommunityIataBindResult,
  CommunityMeStats,
  CommunityPublicStats,
  CommunityStatus,
} from '../../types';
import { Button } from '../ui/button';
import { Checkbox } from '../ui/checkbox';
import { Input } from '../ui/input';
import { Label } from '../ui/label';
import { Separator } from '../ui/separator';
import { SettingsGroupHeader } from './settingsPrimitives';

const IATA_RE = /^[A-Za-z]{3}$/;
const IATA_DIRECTORY_URL = 'https://www.iata.org/en/publications/directories/code-search/';

function normalizeIata(raw: string): string {
  return raw
    .toUpperCase()
    .replace(/[^A-Z]/g, '')
    .slice(0, 3);
}

function concordanceBucket(value: string): 'ok' | 'warn' | 'mismatch' | 'unknown' {
  const bucket = value.trim().toLowerCase();
  if (bucket === 'ok' || bucket === 'match' || bucket === 'consistent') return 'ok';
  if (bucket === 'warn') return 'warn';
  if (bucket === 'mismatch') return 'mismatch';
  return 'unknown';
}

function needsOverride(concordance: string): boolean {
  const bucket = concordanceBucket(concordance);
  return bucket === 'warn' || bucket === 'mismatch';
}

function concordanceMessageKey(
  concordance: string
):
  | 'settings.community.concordanceOk'
  | 'settings.community.concordanceWarn'
  | 'settings.community.concordanceMismatch'
  | 'settings.community.concordanceUnknown' {
  const bucket = concordanceBucket(concordance);
  if (bucket === 'ok') return 'settings.community.concordanceOk';
  if (bucket === 'warn') return 'settings.community.concordanceWarn';
  if (bucket === 'mismatch') return 'settings.community.concordanceMismatch';
  return 'settings.community.concordanceUnknown';
}

function IataHelp() {
  const { t } = useTranslation();
  return (
    <p className="text-[0.8125rem] text-muted-foreground">
      {t('settings.community.iataHelp')}{' '}
      <a
        href={IATA_DIRECTORY_URL}
        target="_blank"
        rel="noreferrer"
        className="cursor-pointer underline underline-offset-2 hover:text-primary transition-colors"
      >
        {t('settings.community.iataDirectory')}
      </a>
    </p>
  );
}

function IataAirportSearch({
  disabled,
  onPick,
}: {
  disabled?: boolean;
  onPick: (iata: string) => void;
}) {
  const { t, i18n } = useTranslation();
  const [query, setQuery] = useState('');
  const [hits, setHits] = useState<CommunityAirportHit[]>([]);
  const [open, setOpen] = useState(false);

  useEffect(() => {
    const trimmed = query.trim();
    if (trimmed.length < 2) {
      setHits([]);
      setOpen(false);
      return;
    }
    let cancelled = false;
    const timer = window.setTimeout(() => {
      void api.searchCommunityAirports(trimmed, i18n.language).then(
        (rows) => {
          if (cancelled) return;
          setHits(rows);
          setOpen(rows.length > 0);
        },
        () => {
          if (!cancelled) {
            setHits([]);
            setOpen(false);
          }
        }
      );
    }, 300);
    return () => {
      cancelled = true;
      window.clearTimeout(timer);
    };
  }, [query, i18n.language]);

  return (
    <div className="space-y-1">
      <Label htmlFor="community-iata-search">{t('settings.community.iataSearch')}</Label>
      <Input
        id="community-iata-search"
        value={query}
        disabled={disabled}
        autoComplete="off"
        spellCheck={false}
        placeholder={t('settings.community.iataSearchPlaceholder')}
        onChange={(event) => setQuery(event.target.value)}
        onFocus={() => {
          if (hits.length > 0) setOpen(true);
        }}
      />
      {open && hits.length > 0 && (
        <ul
          role="listbox"
          aria-label={t('settings.community.iataSearch')}
          className="max-h-48 overflow-auto rounded-md border border-border bg-card py-1"
        >
          {hits.map((hit) => (
            <li key={hit.iata}>
              <button
                type="button"
                role="option"
                className="flex w-full items-center justify-between gap-3 px-3 py-1.5 text-left text-sm hover:bg-accent"
                onClick={() => {
                  onPick(hit.iata);
                  setQuery(hit.label);
                  setOpen(false);
                }}
              >
                <span>{hit.label}</span>
                <span className="font-medium text-muted-foreground">{hit.iata}</span>
              </button>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

function StatRow({ label, value }: { label: string; value: string }) {
  return (
    <div className="flex justify-between items-center gap-4">
      <span className="text-sm text-muted-foreground">{label}</span>
      <span className="font-medium text-right">{value}</span>
    </div>
  );
}

export function SettingsCommunitySection({
  className,
  onStatusChange,
}: {
  className?: string;
  onStatusChange?: (status: CommunityStatus) => void;
}) {
  const { t } = useTranslation();
  const [status, setStatus] = useState<CommunityStatus | null>(null);
  const [statusError, setStatusError] = useState<string | null>(null);
  const [iataDraft, setIataDraft] = useState('');
  const [bindResult, setBindResult] = useState<CommunityIataBindResult | null>(null);
  const [meStats, setMeStats] = useState<CommunityMeStats | null>(null);
  const [communityStats, setCommunityStats] = useState<CommunityPublicStats | null>(null);
  const [statsError, setStatsError] = useState<string | null>(null);
  const [busy, setBusy] = useState<'join' | 'enable' | 'bind' | 'override' | null>(null);

  const applyStatus = useCallback(
    (next: CommunityStatus) => {
      setStatus(next);
      setIataDraft(next.iata);
      onStatusChange?.(next);
    },
    [onStatusChange]
  );

  const loadStatus = useCallback(async () => {
    try {
      const next = await api.getCommunity();
      applyStatus(next);
      setStatusError(null);
      return next;
    } catch (err) {
      setStatusError(formatApiError(err, t));
      return null;
    }
  }, [applyStatus, t]);

  const loadStats = useCallback(
    async (next: CommunityStatus) => {
      try {
        const community = await api.getCommunityStats();
        setCommunityStats(community);
        if (next.iata) {
          setMeStats(await api.getCommunityMeStats());
        } else {
          setMeStats(null);
        }
        setStatsError(null);
      } catch (err) {
        setStatsError(formatApiError(err, t));
      }
    },
    [t]
  );

  useEffect(() => {
    void loadStatus();
  }, [loadStatus]);

  useEffect(() => {
    if (!status?.enabled) {
      setMeStats(null);
      setCommunityStats(null);
      setStatsError(null);
      return;
    }
    void loadStats(status);
  }, [status, loadStats]);

  const handleCommunityError = (err: unknown) => {
    if (err instanceof ApiError && err.status === 429 && err.message.includes('IATA')) {
      toast.error(t('settings.community.iataChangeCap'));
      return;
    }
    if (err instanceof ApiError && err.status === 403) {
      toast.error(t('settings.community.lockedEnable'));
      return;
    }
    toast.error(formatApiError(err, t));
  };

  const handleJoin = async () => {
    const iata = normalizeIata(iataDraft);
    if (!IATA_RE.test(iata)) {
      toast.error(t('settings.community.iataInvalid'));
      return;
    }
    setBusy('join');
    try {
      applyStatus(await api.updateCommunity({ enabled: true, iata }));
      const result = await api.bindCommunityIata({ iata });
      setBindResult(result);
      const next = await api.getCommunity();
      applyStatus(next);
      await loadStats(next);
      toast.success(t('settings.community.bindSuccess'));
    } catch (err) {
      handleCommunityError(err);
      await loadStatus();
    } finally {
      setBusy(null);
    }
  };

  const handleEnableChange = async (enabled: boolean) => {
    if (enabled && status?.locked) return;
    setBusy('enable');
    try {
      applyStatus(await api.updateCommunity({ enabled }));
      if (!enabled) {
        setBindResult(null);
        toast.success(t('settings.community.left'));
      }
    } catch (err) {
      handleCommunityError(err);
    } finally {
      setBusy(null);
    }
  };

  const handleBind = async () => {
    const iata = normalizeIata(iataDraft);
    if (!IATA_RE.test(iata)) {
      toast.error(t('settings.community.iataInvalid'));
      return;
    }
    setBusy('bind');
    try {
      applyStatus(await api.updateCommunity({ iata }));
      const result = await api.bindCommunityIata({ iata });
      setBindResult(result);
      const next = await api.getCommunity();
      applyStatus(next);
      await loadStats(next);
      toast.success(t('settings.community.bindSuccess'));
    } catch (err) {
      handleCommunityError(err);
    } finally {
      setBusy(null);
    }
  };

  const handleOverride = async () => {
    setBusy('override');
    try {
      const result = await api.overrideCommunityIata();
      setBindResult(result);
      if (status) {
        await loadStats(status);
      }
      toast.success(t('settings.community.overrideSuccess'));
    } catch (err) {
      handleCommunityError(err);
    } finally {
      setBusy(null);
    }
  };

  const concordanceSource = bindResult?.concordance ?? meStats?.concordance ?? '';
  const showOverride = Boolean(concordanceSource) && needsOverride(concordanceSource);
  const iataValid = IATA_RE.test(normalizeIata(iataDraft));

  if (!status && !statusError) {
    return (
      <div className={className}>
        <p className="text-[0.8125rem] text-muted-foreground">{t('settings.community.loading')}</p>
      </div>
    );
  }

  if (!status) {
    return (
      <div className={className}>
        <p className="text-[0.8125rem] text-muted-foreground">{statusError}</p>
      </div>
    );
  }

  return (
    <div className={className}>
      <div className="space-y-1">
        <SettingsGroupHeader title={t('settings.community.joinTitle')} storedOn="server" instant />
        <p className="text-[0.8125rem] text-muted-foreground">{t('settings.community.intro')}</p>
      </div>

      {status.locked && (
        <p className="mt-3 text-[0.8125rem] text-muted-foreground">
          {t('settings.community.locked')}
        </p>
      )}

      <div className="mt-4 flex items-start gap-3 rounded-md border border-border/60 p-3">
        <Checkbox
          id="community-enable"
          checked={status.enabled}
          disabled={busy !== null || (status.locked && !status.enabled)}
          onCheckedChange={(checked) => {
            void handleEnableChange(checked === true);
          }}
          className="mt-0.5"
        />
        <div className="space-y-1">
          <Label htmlFor="community-enable">{t('settings.community.enable')}</Label>
          <p className="text-[0.8125rem] text-muted-foreground">
            {status.enabled
              ? t('settings.community.disableHelp')
              : t('settings.community.enableHelp')}
          </p>
        </div>
      </div>

      {!status.enabled && (
        <div className="mt-4 space-y-3 rounded-md border border-input bg-muted/20 p-3">
          <p className="text-[0.8125rem] text-muted-foreground">
            {t('settings.community.joinHelp')}
          </p>
          <div className="space-y-1">
            <Label htmlFor="community-join-iata">{t('settings.community.iata')}</Label>
            <Input
              id="community-join-iata"
              value={iataDraft}
              maxLength={3}
              autoCapitalize="characters"
              autoComplete="off"
              spellCheck={false}
              placeholder={t('settings.community.iataPlaceholder')}
              onChange={(event) => setIataDraft(normalizeIata(event.target.value))}
              className="w-24 uppercase"
            />
            <IataHelp />
          </div>
          <IataAirportSearch disabled={busy !== null || status.locked} onPick={setIataDraft} />
          <Button
            type="button"
            onClick={() => void handleJoin()}
            disabled={busy !== null || status.locked || !iataValid}
          >
            {busy === 'join' ? t('settings.community.joining') : t('settings.community.joinCta')}
          </Button>
        </div>
      )}

      {status.enabled && (
        <div className="mt-4 space-y-3">
          <div className="space-y-1">
            <Label htmlFor="community-iata">{t('settings.community.iata')}</Label>
            <div className="flex flex-wrap items-center gap-2">
              <Input
                id="community-iata"
                value={iataDraft}
                maxLength={3}
                autoCapitalize="characters"
                autoComplete="off"
                spellCheck={false}
                placeholder={t('settings.community.iataPlaceholder')}
                onChange={(event) => setIataDraft(normalizeIata(event.target.value))}
                className="w-24 uppercase"
              />
              <Button
                type="button"
                variant="outline"
                size="sm"
                onClick={() => void handleBind()}
                disabled={busy !== null || !iataValid}
              >
                {busy === 'bind' ? t('settings.community.binding') : t('settings.community.bind')}
              </Button>
            </div>
            <IataHelp />
            <IataAirportSearch disabled={busy !== null} onPick={setIataDraft} />
          </div>

          {!status.publisher_configured && (
            <p className="text-[0.8125rem] text-muted-foreground">
              {t('settings.community.noIataPublishOff')}
            </p>
          )}
          {status.publisher_configured && (
            <p className="text-[0.8125rem] text-muted-foreground">
              {status.publisher_connected
                ? t('settings.community.publisherConnected')
                : t('settings.community.publisherDisconnected')}
            </p>
          )}

          {concordanceSource && (
            <div className="space-y-2">
              <h4 className="text-sm font-semibold">{t('settings.community.concordance')}</h4>
              <p className="text-[0.8125rem] text-muted-foreground">
                {t(concordanceMessageKey(concordanceSource))}
              </p>
              {bindResult?.distance_km != null && (
                <p className="text-[0.8125rem] text-muted-foreground">
                  {t('settings.community.concordanceDistance', {
                    km: bindResult.distance_km.toFixed(1),
                  })}
                </p>
              )}
              {showOverride && (
                <div className="space-y-2">
                  <p className="text-[0.8125rem] text-muted-foreground">
                    {t('settings.community.overrideHelp')}
                  </p>
                  <Button
                    type="button"
                    variant="outline"
                    className="border-amber-500/50 text-amber-700 hover:bg-amber-500/10 dark:text-amber-400"
                    onClick={() => void handleOverride()}
                    disabled={busy !== null}
                  >
                    {busy === 'override'
                      ? t('settings.community.overriding')
                      : t('settings.community.iAmSure')}
                  </Button>
                </div>
              )}
            </div>
          )}
        </div>
      )}

      <Separator className="my-6" />

      <div className="space-y-2">
        <SettingsGroupHeader
          title={t('settings.community.privacyTitle')}
          storedOn="server"
          instant
        />
        <ul className="list-disc space-y-2 pl-5 text-[0.8125rem] text-muted-foreground">
          <li>{t('settings.community.privacyAccount')}</li>
          <li>{t('settings.community.privacyPrivateKey')}</li>
          <li>{t('settings.community.privacyRotate')}</li>
          <li>{t('settings.community.privacyStop')}</li>
          <li>{t('settings.community.privacyHashtags')}</li>
          <li>{t('settings.community.privacyDirectory')}</li>
        </ul>
      </div>

      {status.enabled && (
        <>
          <Separator className="my-6" />
          <div className="space-y-3">
            <SettingsGroupHeader
              title={t('settings.community.meTitle')}
              storedOn="server"
              instant
            />
            {statsError && !meStats ? (
              <p className="text-[0.8125rem] text-muted-foreground">{statsError}</p>
            ) : meStats ? (
              <div className="space-y-2">
                <StatRow
                  label={t('settings.community.meHashes24h')}
                  value={meStats.unique_hashes_24h.toLocaleString()}
                />
                <StatRow
                  label={t('settings.community.meHashes7d')}
                  value={meStats.unique_hashes_7d.toLocaleString()}
                />
                <StatRow label={t('settings.community.meIata')} value={meStats.iata || '—'} />
                <StatRow
                  label={t('settings.community.meRank')}
                  value={
                    meStats.rank_in_iata == null
                      ? t('settings.community.meRankNone')
                      : String(meStats.rank_in_iata)
                  }
                />
              </div>
            ) : (
              <p className="text-[0.8125rem] text-muted-foreground">
                {t('settings.community.loading')}
              </p>
            )}
          </div>

          <div className="mt-6 space-y-3">
            <SettingsGroupHeader
              title={t('settings.community.communityTitle')}
              storedOn="server"
              instant
            />
            {statsError && !communityStats ? (
              <p className="text-[0.8125rem] text-muted-foreground">{statsError}</p>
            ) : communityStats ? (
              <div className="space-y-2">
                <StatRow
                  label={t('settings.community.observersOnline')}
                  value={communityStats.observers_online.toLocaleString()}
                />
                <StatRow
                  label={t('settings.community.iataActive')}
                  value={communityStats.iata_active.toLocaleString()}
                />
                <StatRow
                  label={t('settings.community.uniqueHashes24h')}
                  value={communityStats.unique_hashes_24h.toLocaleString()}
                />
              </div>
            ) : (
              <p className="text-[0.8125rem] text-muted-foreground">
                {t('settings.community.loading')}
              </p>
            )}
          </div>
        </>
      )}
    </div>
  );
}
