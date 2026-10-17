import { useCallback, useEffect, useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { api, formatApiError } from '../../api';
import type { HealthStatus, RadioBlePairingStatus, RadioTransportConfig } from '../../types';
import { Button } from '../ui/button';
import { toast } from '../ui/sonner';

/** Poll interval while a save or a pairing runs (the backend answers without D-Bus then). */
const POLL_MS = 2000;
/**
 * Poll interval while the radio is disconnected: the connection monitor may pair
 * in the background, and backoff attempts come and go. One cached GetManagedObjects.
 */
const BACKGROUND_POLL_MS = 5000;
/** Failures whose fix starts with Forget pairing (see errors.<code>Detail). */
const FORGET_FIRST_CODES = new Set(['ble_pair_rejected']);

function normaliseAddress(address: string): string {
  return address.trim().toUpperCase();
}

/**
 * BlueZ pairing state of the saved BLE radio, with Pair now / Pair again and
 * Forget pairing. Meshloom pairs with the saved PIN by itself when it connects;
 * this shows how that went and lets the user retry or drop a stale bond.
 */
export function RadioBlePairing({
  savedTransport,
  currentAddress,
  health,
  saving,
  onHealthRefresh,
  onBusyChange,
}: {
  savedTransport: RadioTransportConfig;
  currentAddress: string;
  health: HealthStatus | null;
  /** The panel's Save and connect is in flight (it may pair). */
  saving: boolean;
  onHealthRefresh?: () => Promise<void>;
  /** True while pairing runs or a request is in flight: the panel disables Scan. */
  onBusyChange?: (busy: boolean) => void;
}) {
  const { t, i18n } = useTranslation();
  const [status, setStatusValue] = useState<RadioBlePairingStatus | null>(null);
  // When `status` arrived: `retry_in` counts down from there.
  const [statusAt, setStatusAt] = useState(0);
  const [now, setNow] = useState(() => Date.now());
  const [requesting, setRequesting] = useState(false);
  const [actionError, setActionError] = useState<string | null>(null);
  const mounted = useRef(true);

  const applicable =
    savedTransport.transport === 'ble' &&
    Boolean(savedTransport.ble_address) &&
    savedTransport.ble_pin_configured &&
    normaliseAddress(savedTransport.ble_address) === normaliseAddress(currentAddress);

  const setStatus = useCallback((next: RadioBlePairingStatus) => {
    const at = Date.now();
    setStatusValue(next);
    setStatusAt(at);
    setNow(at);
  }, []);

  const refresh = useCallback(async () => {
    try {
      const next = await api.getRadioBlePairing();
      if (mounted.current) setStatus(next);
    } catch {
      // The state line keeps its last value; a failing GET is not worth a toast.
    }
  }, [setStatus]);

  useEffect(() => {
    mounted.current = true;
    return () => {
      mounted.current = false;
    };
  }, []);

  const radioConnected = health?.radio_connected;
  useEffect(() => {
    if (!applicable || saving) return;
    void refresh();
  }, [applicable, saving, radioConnected, refresh]);

  const pairingNow = status?.state === 'pairing';
  const fastPolling = applicable && (saving || pairingNow || requesting);
  // A pairing started by the connection monitor (not by this panel) shows up too,
  // and so does each backoff attempt.
  const backgroundPolling =
    applicable &&
    health != null &&
    !radioConnected &&
    status?.state !== 'unsupported' &&
    status?.state !== 'not_configured';
  const pollMs = fastPolling ? POLL_MS : backgroundPolling ? BACKGROUND_POLL_MS : null;
  useEffect(() => {
    if (pollMs == null) return;
    const id = window.setInterval(() => void refresh(), pollMs);
    return () => window.clearInterval(id);
  }, [pollMs, refresh]);

  const retryIn = status?.state === 'failed' ? (status.retry_in ?? 0) : 0;
  const retryLeft = retryIn > 0 ? retryIn - Math.floor((now - statusAt) / 1000) : 0;
  const counting = retryLeft > 0;
  useEffect(() => {
    if (!counting) return;
    const id = window.setInterval(() => setNow(Date.now()), 1000);
    return () => window.clearInterval(id);
  }, [counting]);

  const busy = saving || pairingNow || requesting;
  useEffect(() => {
    onBusyChange?.(busy);
  }, [busy, onBusyChange]);
  useEffect(() => () => onBusyChange?.(false), [onBusyChange]);

  if (!applicable) {
    return (
      <p className="max-w-prose text-[0.8125rem] text-muted-foreground">
        {t('settings.radio.blePairingSaveFirst')}
      </p>
    );
  }

  const errorText = (code: string | null): string => {
    if (!code) return '';
    const key = `errors.${code}`;
    const text = t(key, { error: code });
    return text === key ? code : text;
  };
  const errorDetail = (code: string | null): string | null => {
    if (!code) return null;
    const key = `errors.${code}Detail`;
    return i18n.exists(key) ? t(key) : null;
  };

  const handlePair = async () => {
    setRequesting(true);
    setActionError(null);
    try {
      const next = await api.pairRadioBle();
      if (mounted.current) setStatus(next);
      toast.success(t('settings.radio.blePairDone'));
      await onHealthRefresh?.();
    } catch (err) {
      if (mounted.current) setActionError(formatApiError(err, t));
      await refresh();
    } finally {
      if (mounted.current) setRequesting(false);
    }
  };

  const handleForget = async () => {
    if (!window.confirm(t('settings.radio.bleForgetConfirm'))) return;
    setRequesting(true);
    setActionError(null);
    try {
      const next = await api.forgetRadioBlePairing();
      if (mounted.current) setStatus(next);
      toast.success(t('settings.radio.bleForgetDone'));
      await onHealthRefresh?.();
    } catch (err) {
      if (mounted.current) setActionError(formatApiError(err, t));
      await refresh();
    } finally {
      if (mounted.current) setRequesting(false);
    }
  };

  const state = status?.state;
  const staleBond = status?.hint === 'stale_bond_suspected';
  let line: string | null;
  let detail: string | null = null;
  switch (state) {
    case 'paired':
      line = t('settings.radio.blePairingState.paired');
      break;
    case 'not_paired':
      line = t('settings.radio.blePairingState.notPaired');
      break;
    case 'pairing':
      line = t('settings.radio.blePairingState.pairing');
      break;
    case 'unknown_device':
      line = t('settings.radio.blePairingState.unknownDevice');
      break;
    case 'unsupported':
      line = t('settings.radio.blePairingState.unsupported');
      break;
    case 'unavailable':
      line = t('settings.radio.blePairingState.unavailable', {
        reason: errorText(status?.error_code ?? null),
      });
      detail = errorDetail(status?.error_code ?? null);
      break;
    case 'failed':
      line = t('settings.radio.blePairingState.failed', {
        reason: errorText(status?.error_code ?? null),
      });
      detail = errorDetail(status?.error_code ?? null);
      break;
    case 'not_configured':
      line = t('settings.radio.blePairingSaveFirst');
      break;
    default:
      line = null;
  }

  const canPair =
    state === 'not_paired' || state === 'unknown_device' || state === 'failed' || staleBond;
  // After a refusal BlueZ may hold no bond (state 'failed'), yet the fix the
  // message gives is Forget pairing, then Pair again.
  const forgetFirst =
    staleBond || (state === 'failed' && FORGET_FIRST_CODES.has(status?.error_code ?? ''));
  const canForget = state === 'paired' || forgetFirst;
  const pairLabel =
    state === 'failed' || staleBond
      ? t('settings.radio.blePairAgain')
      : t('settings.radio.blePairNow');

  return (
    <div className="space-y-2 rounded-md border border-border p-3" data-testid="ble-pairing">
      <p className="text-[0.8125rem] font-semibold">{t('settings.radio.blePairingTitle')}</p>
      {line && (
        <p className="max-w-prose text-[0.8125rem] text-muted-foreground" aria-live="polite">
          {line}
        </p>
      )}
      {detail && <p className="max-w-prose text-[0.8125rem] text-muted-foreground">{detail}</p>}
      {state === 'failed' && status?.auto_pair_blocked && (
        <p className="max-w-prose text-[0.8125rem] text-muted-foreground">
          {t('settings.radio.blePairingAutoBlocked')}
        </p>
      )}
      {counting && (
        <p className="max-w-prose text-[0.8125rem] text-muted-foreground">
          {t('settings.radio.blePairingRetryIn', { seconds: retryLeft })}
        </p>
      )}
      {staleBond && (
        <p className="max-w-prose text-[0.8125rem] text-warning">
          {t('settings.radio.blePairingStaleBond')}
        </p>
      )}
      {actionError && (
        <p className="text-[0.8125rem] text-destructive" role="alert">
          {actionError}
        </p>
      )}
      {(canPair || canForget) && (
        <div className="flex flex-wrap gap-2">
          {forgetFirst && (
            <Button
              type="button"
              variant="outline"
              size="sm"
              onClick={() => void handleForget()}
              disabled={busy}
            >
              {t('settings.radio.bleForget')}
            </Button>
          )}
          {canPair && (
            <Button
              type="button"
              variant="outline"
              size="sm"
              onClick={() => void handlePair()}
              disabled={busy}
            >
              {requesting ? t('settings.radio.blePairingInProgress') : pairLabel}
            </Button>
          )}
          {!forgetFirst && canForget && (
            <Button
              type="button"
              variant="outline"
              size="sm"
              onClick={() => void handleForget()}
              disabled={busy}
            >
              {t('settings.radio.bleForget')}
            </Button>
          )}
        </div>
      )}
    </div>
  );
}
