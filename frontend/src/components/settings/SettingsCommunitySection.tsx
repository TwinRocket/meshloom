import { useCallback, useEffect, useId, useRef, useState } from 'react';
import { Trans, useTranslation } from 'react-i18next';
import { toast } from '../ui/sonner';
import { api, ApiError, formatApiError } from '../../api';
import type {
  CommunityIataBindResult,
  CommunityIataSuggestion,
  CommunityMeStats,
  CommunityPublicStats,
  CommunityStatus,
} from '../../types';
import { Button } from '../ui/button';
import { Input } from '../ui/input';
import { Label } from '../ui/label';
import { Separator } from '../ui/separator';
import { SettingsGroupHeader } from './settingsPrimitives';
import { formatNumber } from '../../utils/formatNumber';
import {
  communityStatusActive,
  communityWaitingForPosition,
  loadCommunityStatus,
  setCommunityStatus,
  useCommunityStatus,
} from '../../stores/communityStore';
import { useCommunityLeave } from '../../hooks/useCommunityLeave';

const IATA_RE = /^[A-Za-z]{3}$/;
// The address the opt-out dialog gives too (CommunityLeaveDialog).
const COMMUNITY_CONTACT_EMAIL = 'privacy@meshloom.app';
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

type SuggestionChip = {
  code: string;
  reason:
    | 'settings.community.suggest.neighbors'
    | 'settings.community.suggest.nearest'
    | 'settings.community.suggest.both';
};

function suggestionChips(suggestion: CommunityIataSuggestion | null): SuggestionChip[] {
  const neighbors = suggestion?.neighbors ?? null;
  const nearest = suggestion?.nearest_airport ?? null;
  if (neighbors && neighbors === nearest) {
    return [{ code: neighbors, reason: 'settings.community.suggest.both' }];
  }
  const chips: SuggestionChip[] = [];
  if (neighbors) chips.push({ code: neighbors, reason: 'settings.community.suggest.neighbors' });
  if (nearest) chips.push({ code: nearest, reason: 'settings.community.suggest.nearest' });
  return chips;
}

/**
 * Up to two codes to pick from, never applied on their own. Nothing is asked
 * until the user clicks "Suggest codes": the backend then sends the radio's
 * position, rounded to about 1 km, to Community (even while off), or falls
 * back to its own airport list.
 */
function IataSuggestions({
  selected,
  disabled,
  onPick,
}: {
  selected: string;
  disabled?: boolean;
  onPick: (iata: string) => void;
}) {
  const { t } = useTranslation();
  const titleId = useId();
  const helpId = useId();
  const [phase, setPhase] = useState<'idle' | 'loading' | 'done' | 'failed'>('idle');
  const [suggestion, setSuggestion] = useState<CommunityIataSuggestion | null>(null);
  const mounted = useRef(true);
  useEffect(() => {
    mounted.current = true;
    return () => {
      mounted.current = false;
    };
  }, []);

  const ask = () => {
    setPhase('loading');
    void api.suggestCommunityIata().then(
      (next) => {
        if (!mounted.current) return;
        setSuggestion(next);
        setPhase('done');
      },
      () => {
        if (!mounted.current) return;
        setSuggestion(null);
        setPhase('failed');
      }
    );
  };

  const chips = suggestionChips(suggestion);
  return (
    <div className="space-y-1.5">
      <div className="flex flex-wrap items-center gap-2">
        <Button
          type="button"
          variant="outline"
          size="sm"
          onClick={ask}
          disabled={disabled || phase === 'loading'}
          aria-describedby={helpId}
        >
          {phase === 'loading'
            ? t('settings.community.suggest.loading')
            : t('settings.community.suggest.button')}
        </Button>
        <p id={helpId} className="min-w-0 flex-1 text-[0.8125rem] text-muted-foreground">
          {t('settings.community.suggest.help')}
        </p>
      </div>
      <div aria-live="polite">
        {phase === 'done' && chips.length === 0 && (
          <p className="text-[0.8125rem] text-muted-foreground">
            {t('settings.community.suggest.none')}
          </p>
        )}
        {phase === 'failed' && (
          <p className="text-[0.8125rem] text-muted-foreground">
            {t('settings.community.suggest.failed')}
          </p>
        )}
        {phase === 'done' && chips.length > 0 && (
          <div role="group" aria-labelledby={titleId} className="space-y-1.5">
            <p id={titleId} className="text-[0.8125rem] text-muted-foreground">
              {t('settings.community.suggest.title')}
            </p>
            <div className="flex flex-wrap gap-2">
              {chips.map((chip) => (
                <Button
                  key={chip.code}
                  type="button"
                  variant="outline"
                  size="sm"
                  disabled={disabled}
                  aria-pressed={selected === chip.code}
                  onClick={() => onPick(chip.code)}
                  className="h-auto gap-2 py-1"
                >
                  <span className="font-semibold tracking-wide">{chip.code}</span>{' '}
                  <span className="font-normal text-muted-foreground">{t(chip.reason)}</span>
                </Button>
              ))}
            </div>
          </div>
        )}
      </div>
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

export function SettingsCommunitySection({ className }: { className?: string }) {
  const { t } = useTranslation();
  // The store is the only copy: our answers write it, and it follows other
  // tabs and the API on its own.
  const status = useCommunityStatus();
  const [statusError, setStatusError] = useState<string | null>(null);
  const [iataDraft, setIataDraft] = useState('');
  const [bindResult, setBindResult] = useState<CommunityIataBindResult | null>(null);
  const [meStats, setMeStats] = useState<CommunityMeStats | null>(null);
  const [communityStats, setCommunityStats] = useState<CommunityPublicStats | null>(null);
  const [statsError, setStatsError] = useState<string | null>(null);
  const [busy, setBusy] = useState<'join' | 'bind' | 'override' | null>(null);
  const [retrying, setRetrying] = useState(false);
  const { requestLeave, leaveDialog } = useCommunityLeave(() => setBindResult(null));
  // Waiting for the radio's position: on, but no stats.
  const active = communityStatusActive(status);
  const waiting = communityWaitingForPosition(status);

  // The draft follows the saved IATA whenever it changes (our actions, another
  // tab, the API); typing is left alone while the saved value stays the same.
  const savedIata = status?.iata;
  const [draftSource, setDraftSource] = useState<string | undefined>(undefined);
  if (savedIata !== draftSource) {
    setDraftSource(savedIata);
    if (savedIata !== undefined) setIataDraft(savedIata);
  }
  const enabled = status?.enabled;
  const [bindResultEnabled, setBindResultEnabled] = useState(enabled);
  if (enabled !== bindResultEnabled) {
    setBindResultEnabled(enabled);
    if (enabled === false) setBindResult(null);
  }

  const loadStatus = useCallback(async () => {
    try {
      const next = await loadCommunityStatus();
      setStatusError(null);
      return next;
    } catch (err) {
      setStatusError(formatApiError(err, t));
      return null;
    }
  }, [t]);

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
    if (!status || !communityStatusActive(status)) {
      setMeStats(null);
      setCommunityStats(null);
      setStatsError(null);
      return;
    }
    void loadStats(status);
  }, [status, loadStats]);

  const handleCommunityError = (err: unknown, joining = false) => {
    if (err instanceof ApiError && err.status === 429 && err.message.includes('IATA')) {
      toast.error(
        t(joining ? 'settings.community.joinIataChangeCap' : 'settings.community.iataChangeCap')
      );
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
      // One call: the backend binds the IATA with Community first (a token for
      // this IATA), and only then turns Community on, saves the IATA and starts
      // the publisher. A refused bind (e.g. the 3-changes-a-week cap) leaves
      // Community off and the saved IATA as it was.
      const result = await api.bindCommunityIata({ iata, enable: true });
      setBindResult(result);
      const next = await api.getCommunity();
      setCommunityStatus(next);
      await loadStats(next);
      toast.success(t('settings.community.bindSuccess'));
    } catch (err) {
      handleCommunityError(err, true);
      await loadStatus();
    } finally {
      setBusy(null);
    }
  };

  // Leaving is the only thing this component sends through `PATCH /api/community`
  // (after the exit dialog). Turning Community on goes through the join form,
  // which binds the IATA first.
  const handleLeave = () => {
    requestLeave();
  };

  // Manual only: the backend never retries an unconfirmed opt-out on its own.
  const handleRetryOptOut = async () => {
    setRetrying(true);
    try {
      setCommunityStatus(await api.retryCommunityOptOut());
      toast.success(t('settings.community.optOut.retryDone'));
    } catch (err) {
      toast.error(formatApiError(err, t));
      await loadStatus();
    } finally {
      setRetrying(false);
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
      // One call: the backend clears the old region's status, binds the new
      // IATA with Community, then saves it and reconnects (in that order).
      const result = await api.bindCommunityIata({ iata });
      setBindResult(result);
      const next = await api.getCommunity();
      setCommunityStatus(next);
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

      {status.enabled && (
        <div className="mt-4 flex flex-wrap items-center gap-3 rounded-md border border-border/60 p-3">
          <p
            id="community-leave-help"
            className="min-w-0 flex-1 text-[0.8125rem] text-muted-foreground"
          >
            {t('settings.community.disableHelp')}
          </p>
          <Button
            type="button"
            variant="outline"
            onClick={handleLeave}
            disabled={busy !== null}
            aria-describedby="community-leave-help"
          >
            {t('settings.community.leave')}
          </Button>
        </div>
      )}

      {status.banned && (
        <div
          data-testid="community-banned"
          role="status"
          className="mt-4 space-y-1 rounded-md border border-border/60 bg-muted/30 p-3"
        >
          <p className="text-[0.8125rem] font-medium">{t('settings.community.banned.title')}</p>
          <p className="text-[0.8125rem] text-muted-foreground">
            <Trans
              i18nKey="settings.community.banned.body"
              components={{
                email: (
                  <a className="underline" href={`mailto:${COMMUNITY_CONTACT_EMAIL}`}>
                    {COMMUNITY_CONTACT_EMAIL}
                  </a>
                ),
              }}
            />
          </p>
        </div>
      )}

      {!status.enabled && status.opt_out_pending && (
        <div
          data-testid="community-opt-out-pending"
          className="mt-4 space-y-2 rounded-md border border-warning/40 bg-warning/10 p-3"
        >
          <p className="text-[0.8125rem] font-medium text-warning">
            {t('settings.community.optOut.pendingTitle')}
          </p>
          <p id="community-opt-out-pending-help" className="text-[0.8125rem] text-muted-foreground">
            {t('settings.community.optOut.pendingBody')}
          </p>
          <Button
            type="button"
            variant="outline"
            size="sm"
            onClick={() => void handleRetryOptOut()}
            disabled={retrying || busy !== null}
            aria-describedby="community-opt-out-pending-help"
          >
            {retrying
              ? t('settings.community.optOut.retrying')
              : t('settings.community.optOut.retry')}
          </Button>
        </div>
      )}

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
          <IataSuggestions selected={iataDraft} disabled={busy !== null} onPick={setIataDraft} />
          <Button
            type="button"
            onClick={() => void handleJoin()}
            disabled={busy !== null || !iataValid}
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
            <IataSuggestions selected={iataDraft} disabled={busy !== null} onPick={setIataDraft} />
          </div>

          {waiting && (
            <p className="text-[0.8125rem] text-warning">
              {t('settings.community.waiting.settingsNote')}
            </p>
          )}

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
          <li>{t('settings.community.privacyPackets')}</li>
          <li>{t('settings.community.privacyRadio')}</li>
          <li>{t('settings.community.privacyHashtags')}</li>
          <li>{t('settings.community.privacyDirectory')}</li>
        </ul>
      </div>

      {active && (
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
                  value={formatNumber(meStats.unique_hashes_24h)}
                />
                <StatRow
                  label={t('settings.community.meHashes7d')}
                  value={formatNumber(meStats.unique_hashes_7d)}
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
                  value={formatNumber(communityStats.observers_online)}
                />
                <StatRow
                  label={t('settings.community.iataActive')}
                  value={formatNumber(communityStats.iata_active)}
                />
                <StatRow
                  label={t('settings.community.uniqueHashes24h')}
                  value={formatNumber(communityStats.unique_hashes_24h)}
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
      {leaveDialog}
    </div>
  );
}
