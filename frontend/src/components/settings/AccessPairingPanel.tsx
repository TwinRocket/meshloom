import { lazy, Suspense, useCallback, useEffect, useRef, useState, type FormEvent } from 'react';
import { useTranslation } from 'react-i18next';

import { api, ApiError, getApiErrorCode } from '../../api';
import { formatAccessError } from '../../auth/accessErrors';
import type { AuthPairingCreated, AuthPairingRow } from '../../auth/types';
import { useSession } from '../../auth/SessionProvider';
import { Button } from '../ui/button';
import { Checkbox } from '../ui/checkbox';
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '../ui/dialog';
import { Input } from '../ui/input';
import { Label } from '../ui/label';
import { toast } from '../ui/sonner';
import { SELECT_CLASS, formatEpoch } from './accessShared';
import { tokenScopesForRole } from './AccessTokensPanel';
import { SettingsGroup, SettingsGroupHeader } from './settingsPrimitives';

const PairingQr = lazy(() => import('./PairingQr'));

const LABEL_MAX_LENGTH = 64;
/** Lifetimes offered, in seconds (the server accepts 30 to 600). */
const TTL_PRESETS = [60, 180, 300, 600] as const;
const DEFAULT_TTL = 300;
const LIST_REFRESH_MS = 20_000;
/** How often the dialog asks whether the shown code was used by the device. */
const USED_POLL_MS = 5000;

/** `m:ss` of a number of seconds. */
export function formatCountdown(seconds: number): string {
  const s = Math.max(0, Math.ceil(seconds));
  return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, '0')}`;
}

/**
 * Seconds left until `deadline` (a local `Date.now()` value), ticking every second.
 * The deadline is built from the code's lifetime (`expires_at - created_at`) rather than
 * the server's clock, so a skewed clock cannot show a dead code as alive.
 */
function useSecondsLeft(deadline: number | null, onExpire: () => void): number {
  const [now, setNow] = useState(() => Date.now());
  const expireRef = useRef(onExpire);
  useEffect(() => {
    expireRef.current = onExpire;
  });
  useEffect(() => {
    if (deadline === null) return;
    const tick = () => {
      const current = Date.now();
      setNow(current);
      if (current >= deadline) expireRef.current();
    };
    // The first read comes from a callback: `now` is stale when the dialog was open before.
    const first = window.setTimeout(tick, 0);
    const id = window.setInterval(tick, 1000);
    return () => {
      window.clearTimeout(first);
      window.clearInterval(id);
    };
  }, [deadline]);
  return deadline === null ? 0 : Math.max(0, (deadline - now) / 1000);
}

type Shown = { pairing: AuthPairingCreated; deadline: number };
/** A code that is gone (expired or used): only what the message needs, never the secret. */
type Ended = { label: string; scopes: string[]; reason: 'expired' | 'used' };

function PairingDialog({
  open,
  scopes,
  onClose,
  onChanged,
}: {
  open: boolean;
  scopes: readonly string[];
  onClose: () => void;
  onChanged: () => void;
}) {
  const { t } = useTranslation();
  const [label, setLabel] = useState('');
  const [chosen, setChosen] = useState<string[]>([]);
  const [ttl, setTtl] = useState<number>(DEFAULT_TTL);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [shown, setShown] = useState<Shown | null>(null);

  const [ended, setEnded] = useState<Ended | null>(null);
  // Bumped when the dialog closes: a creation that answers after that is not shown.
  const epoch = useRef(0);

  // Drops the code and the QR text from state, keeping only what the message needs.
  const end = useCallback((pairing: AuthPairingCreated, reason: Ended['reason']) => {
    setShown(null);
    setEnded({ label: pairing.label, scopes: pairing.scopes, reason });
  }, []);
  const secondsLeft = useSecondsLeft(shown?.deadline ?? null, () => {
    if (shown) end(shown.pairing, 'expired');
  });

  // A device that scanned the code spends it: the code then leaves the pending list.
  const shownId = shown?.pairing.id;
  useEffect(() => {
    if (!shownId) return;
    const id = window.setInterval(() => {
      api
        .listAuthPairings()
        .then((list) => {
          if (list.some((row) => row.id === shownId)) return;
          setShown((current) => {
            if (current?.pairing.id !== shownId) return current;
            setEnded({
              label: current.pairing.label,
              scopes: current.pairing.scopes,
              reason: 'used',
            });
            return null;
          });
        })
        .catch(() => undefined);
    }, USED_POLL_MS);
    return () => window.clearInterval(id);
  }, [shownId]);

  const reset = () => {
    setShown(null);
    setEnded(null);
    setError(null);
  };
  // The dialog stays mounted: drop the code and the form on close so nothing lingers. A code
  // still pending is revoked: closing must not leave a live one-time credential behind.
  const close = () => {
    epoch.current += 1;
    if (shown) {
      const id = shown.pairing.id;
      void api
        .revokeAuthPairing(id)
        .catch(() => undefined)
        .finally(onChanged);
    }
    reset();
    setLabel('');
    setChosen([]);
    setTtl(DEFAULT_TTL);
    onClose();
  };

  const ready = label.trim().length > 0 && chosen.length > 0;

  const submit = async (event: FormEvent) => {
    event.preventDefault();
    if (!ready || busy) return;
    setBusy(true);
    setError(null);
    const started = epoch.current;
    try {
      const pairing = await api.createAuthPairing({
        label: label.trim(),
        scopes: scopes.filter((scope) => chosen.includes(scope)),
        ttl,
      });
      if (started !== epoch.current) {
        // Closed while the request was in flight: nobody saw this code, so revoke it.
        void api.revokeAuthPairing(pairing.id).catch(() => undefined);
        return;
      }
      setShown({
        pairing,
        deadline: Date.now() + (pairing.expires_at - pairing.created_at) * 1000,
      });
    } catch (err) {
      setError(formatAccessError(err, t));
    } finally {
      setBusy(false);
      onChanged();
    }
  };

  const cancelCode = async () => {
    if (!shown) return;
    setBusy(true);
    try {
      await api.revokeAuthPairing(shown.pairing.id);
      toast.success(t('settings.access.pairing.cancelled'));
      reset();
    } catch (err) {
      if (
        err instanceof ApiError &&
        getApiErrorCode(err.detail)?.code === 'auth_pairing_not_found'
      ) {
        // Already used by the device (or revoked elsewhere): the QR is dead.
        end(shown.pairing, 'used');
      } else {
        setError(formatAccessError(err, t));
      }
    } finally {
      setBusy(false);
      onChanged();
    }
  };

  const view = shown ? { label: shown.pairing.label, scopes: shown.pairing.scopes } : ended;

  return (
    <Dialog open={open} onOpenChange={(next) => !next && close()}>
      <DialogContent>
        {view ? (
          <div className="space-y-4">
            <DialogHeader>
              <DialogTitle>
                {t('settings.access.pairing.qrTitle', { label: view.label })}
              </DialogTitle>
              <DialogDescription>{t('settings.access.pairing.qrHelp')}</DialogDescription>
            </DialogHeader>
            <p
              role="alert"
              className="rounded-md border border-amber-500/50 bg-amber-500/10 p-3 text-sm font-medium"
            >
              {t('settings.access.pairing.secretWarning')}
            </p>
            {shown === null ? (
              <p
                role="status"
                data-testid="pairing-expired"
                className="rounded-md border border-border bg-muted/40 p-3 text-sm"
              >
                {t(
                  ended?.reason === 'used'
                    ? 'settings.access.pairing.used'
                    : 'settings.access.pairing.expired'
                )}
              </p>
            ) : (
              <>
                <div className="flex justify-center">
                  <div className="max-w-full rounded-md bg-white p-3" data-testid="pairing-qr">
                    <Suspense
                      fallback={<div className="h-56 w-56 max-w-full" aria-hidden="true" />}
                    >
                      <PairingQr
                        value={shown.pairing.qr_payload}
                        title={t('settings.access.pairing.qrAlt')}
                      />
                    </Suspense>
                  </div>
                </div>
                <p className="text-center text-sm" aria-live="off" data-testid="pairing-countdown">
                  {t('settings.access.pairing.countdown', { time: formatCountdown(secondsLeft) })}
                </p>
              </>
            )}
            <p className="text-[0.8125rem] text-muted-foreground">
              {t('settings.access.pairing.scopesGiven', {
                scopes: view.scopes.join(', '),
              })}
            </p>
            {error && (
              <p role="alert" className="text-sm text-destructive">
                {error}
              </p>
            )}
            <DialogFooter>
              {shown === null ? (
                <Button type="button" onClick={reset}>
                  {t('settings.access.pairing.regenerate')}
                </Button>
              ) : (
                <Button
                  type="button"
                  variant="outline"
                  disabled={busy}
                  onClick={() => void cancelCode()}
                >
                  {t('settings.access.pairing.cancelCode')}
                </Button>
              )}
              <Button
                type="button"
                variant={shown === null ? 'outline' : 'default'}
                onClick={close}
              >
                {t(
                  shown === null
                    ? 'settings.access.pairing.done'
                    : 'settings.access.pairing.closeRevokes'
                )}
              </Button>
            </DialogFooter>
          </div>
        ) : (
          <form onSubmit={submit} className="space-y-4">
            <DialogHeader>
              <DialogTitle>{t('settings.access.pairing.createTitle')}</DialogTitle>
              <DialogDescription>{t('settings.access.pairing.createHelp')}</DialogDescription>
            </DialogHeader>
            <div className="space-y-1.5">
              <Label htmlFor="access-pairing-label">{t('settings.access.pairing.label')}</Label>
              <Input
                id="access-pairing-label"
                autoComplete="off"
                maxLength={LABEL_MAX_LENGTH}
                placeholder={t('settings.access.pairing.labelPlaceholder')}
                value={label}
                onChange={(event) => setLabel(event.target.value)}
              />
            </div>
            <fieldset className="space-y-2">
              <legend className="text-sm font-medium">{t('settings.access.pairing.scopes')}</legend>
              <p className="text-[0.8125rem] text-muted-foreground">
                {t('settings.access.pairing.scopesHelp')}
              </p>
              {scopes.map((scope) => (
                <div key={scope} className="flex items-start gap-2">
                  <Checkbox
                    id={`access-pairing-scope-${scope}`}
                    className="mt-0.5"
                    aria-describedby={`access-pairing-scope-${scope}-help`}
                    checked={chosen.includes(scope)}
                    onCheckedChange={(checked) =>
                      setChosen((list) =>
                        checked === true ? [...list, scope] : list.filter((s) => s !== scope)
                      )
                    }
                  />
                  <div>
                    <Label htmlFor={`access-pairing-scope-${scope}`} className="font-mono">
                      {scope}
                    </Label>
                    <p
                      id={`access-pairing-scope-${scope}-help`}
                      className="text-[0.8125rem] text-muted-foreground"
                    >
                      {t(`settings.access.tokens.scope.${scope}`)}
                    </p>
                  </div>
                </div>
              ))}
            </fieldset>
            <div className="space-y-1.5">
              <Label htmlFor="access-pairing-ttl">{t('settings.access.pairing.lifetime')}</Label>
              <select
                id="access-pairing-ttl"
                className={`${SELECT_CLASS} w-full`}
                value={ttl}
                onChange={(event) => setTtl(Number(event.target.value))}
              >
                {TTL_PRESETS.map((seconds) => (
                  <option key={seconds} value={seconds}>
                    {t('settings.access.pairing.lifetimeMinutes', { count: seconds / 60 })}
                  </option>
                ))}
              </select>
            </div>
            {error && (
              <p role="alert" className="text-sm text-destructive">
                {error}
              </p>
            )}
            <DialogFooter>
              <Button type="button" variant="outline" onClick={close}>
                {t('settings.access.cancel')}
              </Button>
              <Button type="submit" disabled={!ready || busy}>
                {t('settings.access.pairing.create')}
              </Button>
            </DialogFooter>
          </form>
        )}
      </DialogContent>
    </Dialog>
  );
}

/**
 * Settings → Users & Access → Pair a device (administrators). Creates a single-use pairing
 * code and shows its QR (drawn in the browser, never by a remote service) with a countdown;
 * lists the pending codes and cancels them. The code exists only in the dialog that created
 * it: the list never holds it (`docs/dev/device-pairing.md`).
 */
export function AccessPairingPanel() {
  const { t, i18n } = useTranslation();
  const { role, via } = useSession();
  const [rows, setRows] = useState<AuthPairingRow[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [creating, setCreating] = useState(false);
  const [version, setVersion] = useState(0);
  const [nowS, setNowS] = useState(0);
  const reload = useCallback(() => setVersion((value) => value + 1), []);

  const isAdmin = role === 'admin';
  const scopes = tokenScopesForRole(role);

  useEffect(() => {
    if (!isAdmin) return;
    let cancelled = false;
    api
      .listAuthPairings()
      .then((list) => {
        if (cancelled) return;
        setRows(list);
        setNowS(Math.floor(Date.now() / 1000));
        setError(null);
      })
      .catch((err) => {
        if (!cancelled) setError(formatAccessError(err, t));
      });
    return () => {
      cancelled = true;
    };
  }, [version, isAdmin, t]);

  // Codes get used by devices and expire on their own: refresh while the panel is open.
  useEffect(() => {
    if (!isAdmin) return;
    const id = window.setInterval(reload, LIST_REFRESH_MS);
    return () => window.clearInterval(id);
  }, [isAdmin, reload]);

  if (!isAdmin) return null;

  const cancel = async (row: AuthPairingRow) => {
    setBusy(true);
    setError(null);
    try {
      await api.revokeAuthPairing(row.id);
      toast.success(t('settings.access.pairing.cancelled'));
    } catch (err) {
      const message = formatAccessError(err, t);
      setError(message);
      toast.error(message);
    } finally {
      reload();
      setBusy(false);
    }
  };

  const active = rows?.filter((row) => row.expires_at > nowS) ?? [];

  return (
    <SettingsGroup>
      <SettingsGroupHeader
        id="access-pairing"
        title={t('settings.access.pairing.title')}
        storedOn="server"
      />
      <p className="text-[0.8125rem] text-muted-foreground">{t('settings.access.pairing.help')}</p>
      {via === 'ingress' && (
        <p role="note" className="rounded-md border border-border bg-muted/40 p-3 text-[0.8125rem]">
          {t('settings.access.pairing.ingressNotice')}
        </p>
      )}
      {error && (
        <p role="alert" className="text-sm text-destructive">
          {error}
        </p>
      )}
      {rows && active.length === 0 && (
        <p className="text-sm text-muted-foreground">{t('settings.access.pairing.none')}</p>
      )}
      <ul className="divide-y divide-border">
        {active.map((row) => (
          <li key={row.id} className="flex flex-wrap items-center gap-x-4 gap-y-2 py-2">
            <div className="min-w-48 flex-1">
              <div className="text-sm font-medium break-words">{row.label}</div>
              <div className="mt-1 flex flex-wrap gap-1">
                {row.scopes.map((scope) => (
                  <code
                    key={scope}
                    className="rounded bg-muted px-1.5 py-0.5 font-mono text-[0.6875rem]"
                  >
                    {scope}
                  </code>
                ))}
              </div>
              <div className="mt-1 text-[0.8125rem] text-muted-foreground">
                {[
                  t('settings.access.pairing.owner', { user: row.username ?? '?' }),
                  t('settings.access.pairing.expiresAt', {
                    when: formatEpoch(row.expires_at, i18n.language),
                  }),
                ].join(' · ')}
              </div>
            </div>
            <Button
              variant="outline"
              size="sm"
              disabled={busy}
              aria-label={t('settings.access.pairing.cancelNamed', { label: row.label })}
              onClick={() => void cancel(row)}
            >
              {t('settings.access.pairing.cancelCode')}
            </Button>
          </li>
        ))}
      </ul>
      <Button variant="outline" disabled={busy} onClick={() => setCreating(true)}>
        {t('settings.access.pairing.add')}
      </Button>
      <PairingDialog
        open={creating}
        scopes={scopes}
        onClose={() => setCreating(false)}
        onChanged={reload}
      />
    </SettingsGroup>
  );
}
