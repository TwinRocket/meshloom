import { useCallback, useEffect, useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';

import { api, formatApiError } from '../../api';
import {
  loadRadioWifi,
  noteWifiRebootStarted,
  setRadioWifiState,
  useRadioWifiLoadFailed,
  useRadioWifiState,
} from '../../stores/radioWifiStore';
import type { HealthStatus, RadioTransportConfig, RadioWifiChangeResult } from '../../types';
import {
  connectedOverTcp,
  TCP_SWITCH_REVERT_DELAY_MS,
  tcpSwitchApplied,
  transportKindLabelKey,
} from '../../utils/radioTcpSwitch';
import {
  EMPTY_WIFI_FORM,
  RadioWifiCredentialsFields,
  type WifiFormValues,
  wifiFormError,
  wifiRequestFromForm,
} from '../RadioWifiCredentialsFields';
import { RadioWifiRebootDialog } from '../RadioWifiRebootDialog';
import { Button } from '../ui/button';
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
import { SettingsGroup, SettingsGroupHeader } from './settingsPrimitives';

export { TCP_SWITCH_REVERT_DELAY_MS };
const TCP_OFFER_DISMISSED_KEY = 'meshloom.wifiTcpOfferDismissed.';

function offerDismissed(publicKey: string | null | undefined): boolean {
  if (!publicKey) return false;
  try {
    return window.localStorage.getItem(TCP_OFFER_DISMISSED_KEY + publicKey) === '1';
  } catch {
    return false;
  }
}

function rememberOfferDismissed(publicKey: string | null | undefined): void {
  if (!publicKey) return;
  try {
    window.localStorage.setItem(TCP_OFFER_DISMISSED_KEY + publicKey, '1');
  } catch {
    // Private window or blocked storage: the offer comes back next time.
  }
}

// `setup`: "Set up Wi-Fi" on a radio with no network name (also turns Wi-Fi on).
type CredentialsMode = 'store' | 'change' | 'setup';
type SwitchPhase = 'idle' | 'switching' | 'waiting' | 'offerRevert' | 'reverting';
type SwitchTarget = { ip: string; port: number };

/**
 * Wi-Fi of the connected radio (companion CLI, firmware v1.18+ built with Wi-Fi).
 * Hidden when the radio has no CLI or no Wi-Fi. Every change that needs a reboot
 * asks first; the reboot goes through `POST /api/radio/wifi/reboot`.
 */
export function RadioWifiSettings({ health }: { health: HealthStatus | null }) {
  const { t } = useTranslation();
  const wifi = useRadioWifiState();
  const loadFailed = useRadioWifiLoadFailed();
  const [transport, setTransport] = useState<RadioTransportConfig | null>(null);
  const [credentialsMode, setCredentialsMode] = useState<CredentialsMode | null>(null);
  const [form, setForm] = useState<WifiFormValues>(EMPTY_WIFI_FORM);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [partialWarning, setPartialWarning] = useState(false);
  const [disableOpen, setDisableOpen] = useState(false);
  const [rebootOpen, setRebootOpen] = useState(false);
  const [rebootBusy, setRebootBusy] = useState(false);
  const [rebootError, setRebootError] = useState<string | null>(null);
  const [retrying, setRetrying] = useState(false);
  const [tcpPort, setTcpPort] = useState('');
  const [offerHidden, setOfferHidden] = useState(false);
  const [switchPhase, setSwitchPhase] = useState<SwitchPhase>('idle');
  const [switchTarget, setSwitchTarget] = useState<SwitchTarget | null>(null);
  const [switchError, setSwitchError] = useState<string | null>(null);
  const healthRef = useRef(health);
  useEffect(() => {
    healthRef.current = health;
  }, [health]);
  const revertTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  // Only the latest transport answer counts (a re-read racing the switch's own).
  const transportSeqRef = useRef(0);

  const isTcp = transport?.transport === 'tcp';
  const connectionInfo = health?.connection_info ?? null;
  const radioConnected = Boolean(health?.radio_connected);

  const adoptTransport = (next: RadioTransportConfig) => {
    transportSeqRef.current += 1;
    setTransport(next);
  };

  // The panel stays mounted while the radio reconnects (it renders nothing in
  // between): read the transport again whenever the connection changes, so the
  // TCP offer and the "can make the radio unreachable" warnings follow a switch
  // made in the transport panel above.
  useEffect(() => {
    const seq = ++transportSeqRef.current;
    api
      .getRadioTransport()
      .then((next) => {
        if (seq === transportSeqRef.current) setTransport(next);
      })
      .catch(() => {
        // The transport kind only adds warnings and the TCP offer.
      });
  }, [connectionInfo, radioConnected]);

  useEffect(
    () => () => {
      transportSeqRef.current += 1;
      if (revertTimerRef.current !== null) clearTimeout(revertTimerRef.current);
    },
    []
  );

  // A radio that went away, or another radio, closes every dialog and forgets
  // what was typed: a password never waits in state for the next radio.
  const dialogKey = wifi?.check === 'done' && wifi.supported ? (wifi.radio_public_key ?? '') : null;
  const [shownDialogKey, setShownDialogKey] = useState(dialogKey);
  if (dialogKey !== shownDialogKey) {
    setShownDialogKey(dialogKey);
    setCredentialsMode(null);
    setForm(EMPTY_WIFI_FORM);
    setDisableOpen(false);
    setRebootOpen(false);
  }

  // Back over TCP at the switch target: the revert offer is moot. Health from
  // before the switch (still "connected" over Bluetooth or USB) never counts.
  const reconnected =
    (switchPhase === 'waiting' || switchPhase === 'offerRevert') &&
    switchTarget !== null &&
    connectedOverTcp(health, switchTarget.ip, switchTarget.port);
  const phase: SwitchPhase = reconnected ? 'idle' : switchPhase;

  const applyResult = useCallback((result: RadioWifiChangeResult) => {
    setRadioWifiState(result.state);
  }, []);

  const closeCredentials = () => {
    if (busy) return;
    setCredentialsMode(null);
    setForm(EMPTY_WIFI_FORM);
    setError(null);
  };

  const rebootBlocked = Boolean(wifi?.store_incomplete || partialWarning);

  const openCredentials = (mode: CredentialsMode) => {
    const ssid = wifi?.store_incomplete ? wifi.store_target_ssid : wifi?.ssid;
    setForm({ ...EMPTY_WIFI_FORM, ssid: ssid ?? '' });
    setError(null);
    setCredentialsMode(mode);
  };

  const submitCredentials = async () => {
    const invalid = wifiFormError(form, t);
    if (invalid) {
      setError(invalid);
      return;
    }
    setBusy(true);
    setError(null);
    const openNetwork = form.openNetwork;
    const turnOnAfter = credentialsMode === 'setup' && wifi?.enabled === false;
    try {
      const result = await api.setRadioWifiCredentials(wifiRequestFromForm(form, false));
      applyResult(result);
      setPartialWarning(false);
      setCredentialsMode(null);
      toast.success(t(openNetwork ? 'settings.radio.wifi.savedOpen' : 'settings.radio.wifi.saved'));
      let rebootRequired = result.reboot_required;
      if (turnOnAfter && result.state.enabled === false) {
        // "Set up Wi-Fi" on a radio with Wi-Fi off: turn it on as well, so the
        // one reboot offered below applies both.
        try {
          const enabled = await api.setRadioWifiEnabled({ enabled: true, reboot: false });
          applyResult(enabled);
          rebootRequired = rebootRequired || enabled.reboot_required;
        } catch (err) {
          setError(formatApiError(err, t) || t('settings.radio.wifi.saveFailed'));
          rebootRequired = false;
        }
      }
      if (rebootRequired) {
        setRebootError(null);
        setRebootOpen(true);
      }
    } catch (err) {
      const code = (err as { detail?: { code?: string } })?.detail?.code;
      if (code === 'wifi_store_partial') setPartialWarning(true);
      setError(formatApiError(err, t) || t('settings.radio.wifi.saveFailed'));
    } finally {
      // Never keep the password in React state once the request settled.
      setForm((prev) => ({ ...prev, password: '', confirm: '' }));
      setBusy(false);
    }
  };

  const changeEnabled = async (enabled: boolean, reboot: boolean) => {
    setBusy(true);
    setError(null);
    try {
      const result = await api.setRadioWifiEnabled({ enabled, reboot });
      applyResult(result);
      setDisableOpen(false);
      if (result.rebooting) {
        // Only a reboot that turns Wi-Fi on can end with an address to switch to.
        if (enabled) noteWifiRebootStarted();
        toast.info(t('settings.radio.wifi.rebooting'));
      } else if (result.reboot_required && enabled && !rebootBlocked) {
        // Never offer a reboot while the stored credentials may be incomplete.
        setRebootError(null);
        setRebootOpen(true);
      }
    } catch (err) {
      setError(formatApiError(err, t) || t('settings.radio.wifi.saveFailed'));
    } finally {
      setBusy(false);
    }
  };

  const confirmReboot = async () => {
    setRebootBusy(true);
    setRebootError(null);
    try {
      const result = await api.rebootRadioForWifi();
      applyResult(result);
      // A pending "turn Wi-Fi off" ends with no address: no TCP offer then.
      if (result.state.enabled === true) noteWifiRebootStarted();
      setRebootOpen(false);
      toast.info(t('settings.radio.wifi.rebooting'));
    } catch (err) {
      setRebootError(formatApiError(err, t) || t('settings.radio.wifi.saveFailed'));
    } finally {
      setRebootBusy(false);
    }
  };

  const retry = async () => {
    setRetrying(true);
    setError(null);
    try {
      await loadRadioWifi(true, { throwOnError: true });
    } catch (err) {
      setError(formatApiError(err, t) || t('settings.radio.wifi.loadFailed'));
    } finally {
      setRetrying(false);
    }
  };

  const switchToTcp = async (ip: string, port: number) => {
    if (revertTimerRef.current !== null) clearTimeout(revertTimerRef.current);
    revertTimerRef.current = null;
    setSwitchTarget({ ip, port });
    setSwitchPhase('switching');
    setSwitchError(null);
    try {
      const next = await api.updateRadioTransport({
        transport: 'tcp',
        tcp_host: ip,
        tcp_port: port,
      });
      adoptTransport(next);
      // The PUT ran the whole reconnect, but a failed reconnect still answers
      // 200 and broadcasts nothing: health may still show the old session. Give
      // Meshloom a moment more, then offer the way back unless health shows the
      // radio reached over TCP at this address.
      setSwitchPhase('waiting');
      revertTimerRef.current = setTimeout(() => {
        revertTimerRef.current = null;
        if (connectedOverTcp(healthRef.current, ip, port)) {
          setSwitchPhase('idle');
          return;
        }
        setSwitchError(t('settings.radio.wifi.switchTcpRevertTimeout'));
        setSwitchPhase('offerRevert');
      }, TCP_SWITCH_REVERT_DELAY_MS);
    } catch (err) {
      setSwitchError(formatApiError(err, t) || t('settings.radio.wifi.switchTcpFailed'));
      const fresh = await api.getRadioTransport().catch(() => null);
      if (fresh) adoptTransport(fresh);
      // The way back only when the server saved the switch (the request failed
      // after that, e.g. a proxy timeout). A PUT refused before saving leaves the
      // previous_transport of an older change: never offer that.
      setSwitchPhase(tcpSwitchApplied(fresh, ip, port) ? 'offerRevert' : 'idle');
    }
  };

  const revertTransport = async () => {
    setSwitchPhase('reverting');
    try {
      const next = await api.revertRadioTransport();
      adoptTransport(next);
      setSwitchPhase('idle');
      setSwitchError(null);
    } catch (err) {
      setSwitchError(formatApiError(err, t) || t('settings.radio.wifi.switchTcpFailed'));
      setSwitchPhase('offerRevert');
    }
  };

  const showLoadFailed = !wifi && loadFailed;
  if (!wifi || wifi.supported === false) {
    // Still offer the way back after a failed TCP switch, even with no Wi-Fi state.
    if (phase !== 'offerRevert' && phase !== 'reverting' && !showLoadFailed) return null;
  }

  const header = (
    <SettingsGroupHeader
      id="radio-wifi-settings"
      title={t('settings.radio.wifi.title')}
      storedOn="radio"
    />
  );

  const revertBlock =
    phase === 'offerRevert' || phase === 'reverting' ? (
      <div className="space-y-2 rounded-md border border-warning/50 p-3" role="alert">
        {switchError && <p className="text-sm">{switchError}</p>}
        {transport?.previous_transport_kind && (
          <Button
            type="button"
            variant="outline"
            disabled={phase === 'reverting'}
            onClick={() => void revertTransport()}
          >
            {phase === 'reverting'
              ? t('settings.radio.wifi.switchTcpReverting')
              : t('settings.radio.wifi.switchTcpRevert', {
                  kind: t(transportKindLabelKey(transport.previous_transport_kind)),
                })}
          </Button>
        )}
      </div>
    ) : null;

  if (!wifi || wifi.supported === false) {
    return (
      <SettingsGroup id="radio-wifi-settings">
        {header}
        {showLoadFailed && (
          <div className="space-y-2" role="status">
            <p className="text-[0.8125rem]">{t('settings.radio.wifi.loadFailed')}</p>
            <Button
              type="button"
              variant="outline"
              disabled={retrying}
              onClick={() => void retry()}
            >
              {retrying ? t('settings.radio.wifi.retrying') : t('settings.radio.wifi.retry')}
            </Button>
            {error && (
              <p className="text-sm text-destructive" role="alert">
                {error}
              </p>
            )}
          </div>
        )}
        {revertBlock}
      </SettingsGroup>
    );
  }

  if (wifi.check === 'pending') {
    return (
      <SettingsGroup id="radio-wifi-settings">
        {header}
        <p className="text-[0.8125rem] text-muted-foreground" role="status">
          {t('settings.radio.wifi.checking')}
        </p>
      </SettingsGroup>
    );
  }

  if (wifi.check === 'failed') {
    return (
      <SettingsGroup id="radio-wifi-settings">
        {header}
        <div className="space-y-2" role="status">
          <p className="text-[0.8125rem]">{t('settings.radio.wifi.checkFailed')}</p>
          {wifi.next_retry_at ? (
            <p className="text-[0.8125rem] text-muted-foreground">
              {t('settings.radio.wifi.nextRetry', {
                time: new Date(wifi.next_retry_at * 1000).toLocaleTimeString(),
              })}
            </p>
          ) : null}
          <Button type="button" variant="outline" disabled={retrying} onClick={() => void retry()}>
            {retrying ? t('settings.radio.wifi.retrying') : t('settings.radio.wifi.retry')}
          </Button>
        </div>
        {error && (
          <p className="text-sm text-destructive" role="alert">
            {error}
          </p>
        )}
        {revertBlock}
      </SettingsGroup>
    );
  }

  const storedLabel =
    wifi.credentials_stored === true
      ? t('settings.radio.wifi.storedYes')
      : wifi.credentials_stored === false
        ? t('settings.radio.wifi.storedNo')
        : t('settings.radio.wifi.storedUnknown');
  const noNetworkName = wifi.ssid === null || wifi.ssid === undefined;
  const offerPort = tcpPort === '' ? String(wifi.tcp_port || 5000) : tcpPort;
  const parsedPort = Number.parseInt(offerPort, 10);
  const portValid = Number.isInteger(parsedPort) && parsedPort >= 1 && parsedPort <= 65535;
  const showTcpOffer =
    Boolean(wifi.connected && wifi.ip) &&
    transport !== null &&
    !isTcp &&
    !offerHidden &&
    !offerDismissed(wifi.radio_public_key) &&
    (phase === 'idle' || phase === 'switching');
  const showRebootNow = wifi.reboot_pending && !rebootBlocked;

  return (
    <SettingsGroup id="radio-wifi-settings">
      {header}
      <p className="max-w-prose text-[0.8125rem] text-muted-foreground">
        {t('settings.radio.wifi.intro')}
      </p>

      {wifi.store_incomplete && (
        <div className="space-y-2 rounded-md border border-warning/50 p-3" role="alert">
          <p className="text-sm">
            {t('settings.radio.wifi.storeIncompleteWarning', {
              ssid: wifi.store_target_ssid ?? '',
            })}
          </p>
          <Button type="button" variant="outline" onClick={() => openCredentials('store')}>
            {t('settings.radio.wifi.storeNow')}
          </Button>
        </div>
      )}
      {partialWarning && !wifi.store_incomplete && (
        <p className="rounded-md border border-warning/50 p-3 text-sm" role="alert">
          {t('settings.radio.wifi.storePartialWarning')}
        </p>
      )}

      <dl className="grid max-w-md grid-cols-[auto,1fr] gap-x-4 gap-y-1 text-sm">
        <dt className="text-muted-foreground">{t('settings.radio.wifi.ssid')}</dt>
        <dd className="break-all">
          {noNetworkName ? t('settings.radio.wifi.ssidUnset') : <span>{wifi.ssid}</span>}
        </dd>
        <dt className="text-muted-foreground">{t('settings.radio.wifi.enabled')}</dt>
        <dd>
          {wifi.enabled ? t('settings.radio.wifi.enabledOn') : t('settings.radio.wifi.enabledOff')}
        </dd>
        <dt className="text-muted-foreground">{t('settings.radio.wifi.status')}</dt>
        <dd>
          {wifi.connected
            ? t('settings.radio.wifi.connected')
            : t('settings.radio.wifi.disconnected')}
        </dd>
        <dt className="text-muted-foreground">{t('settings.radio.wifi.ip')}</dt>
        <dd>{wifi.ip ?? t('settings.radio.wifi.notConnected')}</dd>
        <dt className="text-muted-foreground">{t('settings.radio.wifi.stored')}</dt>
        <dd>
          {storedLabel}
          {wifi.open_network && wifi.credentials_stored ? (
            <span className="text-muted-foreground">
              {' '}
              ({t('settings.radio.wifi.openNetworkStored')})
            </span>
          ) : null}
        </dd>
      </dl>
      <p className="max-w-prose text-[0.8125rem] text-muted-foreground">
        {t('settings.radio.wifi.storedHelp')}
      </p>

      {showRebootNow && (
        <div className="flex flex-wrap items-center gap-2" role="status">
          <span className="text-sm">{t('settings.radio.wifi.rebootPending')}</span>
          <Button
            type="button"
            variant="outline"
            onClick={() => {
              setRebootError(null);
              setRebootOpen(true);
            }}
          >
            {t('settings.radio.wifi.rebootNow')}
          </Button>
        </div>
      )}

      <div className="flex flex-wrap gap-2">
        {!wifi.credentials_stored && !noNetworkName && !wifi.store_incomplete && (
          <Button type="button" variant="outline" onClick={() => openCredentials('store')}>
            {t('settings.radio.wifi.storeNow')}
          </Button>
        )}
        {!noNetworkName && (
          <Button type="button" variant="outline" onClick={() => openCredentials('change')}>
            {t('settings.radio.wifi.changeNetwork')}
          </Button>
        )}
        {noNetworkName ? (
          <Button type="button" variant="outline" onClick={() => openCredentials('setup')}>
            {t('settings.radio.wifi.setUp')}
          </Button>
        ) : wifi.enabled ? (
          <Button
            type="button"
            variant="outline"
            disabled={busy}
            onClick={() => {
              setError(null);
              setDisableOpen(true);
            }}
          >
            {t('settings.radio.wifi.disable')}
          </Button>
        ) : (
          <Button
            type="button"
            variant="outline"
            disabled={busy}
            onClick={() => void changeEnabled(true, false)}
          >
            {t('settings.radio.wifi.enable')}
          </Button>
        )}
      </div>
      {noNetworkName && (
        <p className="max-w-prose text-[0.8125rem] text-muted-foreground">
          {t('settings.radio.wifi.setUpHelp')}
        </p>
      )}

      {showTcpOffer && wifi.ip && (
        <div className="space-y-2 rounded-md border border-border/60 p-3">
          <h4 className="text-sm font-semibold">{t('settings.radio.wifi.switchTcpTitle')}</h4>
          <p className="text-sm">
            {t('settings.radio.wifi.switchTcpBody', { ip: wifi.ip, port: offerPort })}
          </p>
          <div className="flex flex-wrap items-end gap-2">
            <div className="space-y-1">
              <Label htmlFor="radio-wifi-tcp-port">{t('settings.radio.wifi.switchTcpPort')}</Label>
              <Input
                id="radio-wifi-tcp-port"
                inputMode="numeric"
                className="w-28"
                value={offerPort}
                disabled={phase === 'switching'}
                onChange={(e) => setTcpPort(e.target.value)}
              />
            </div>
            <Button
              type="button"
              disabled={!portValid || phase === 'switching'}
              onClick={() => void switchToTcp(wifi.ip as string, parsedPort)}
            >
              {phase === 'switching'
                ? t('settings.radio.wifi.switchTcpSwitching')
                : t('settings.radio.wifi.switchTcpAction')}
            </Button>
            <Button
              type="button"
              variant="ghost"
              disabled={phase === 'switching'}
              onClick={() => {
                rememberOfferDismissed(wifi.radio_public_key);
                setOfferHidden(true);
                setSwitchError(null);
              }}
            >
              {t('settings.radio.wifi.switchTcpDismiss')}
            </Button>
          </div>
          {switchError && phase === 'idle' && (
            <p className="text-sm text-destructive" role="alert">
              {switchError}
            </p>
          )}
        </div>
      )}
      {revertBlock}

      {error && credentialsMode === null && (
        <p className="text-sm text-destructive" role="alert">
          {error}
        </p>
      )}

      <Dialog
        open={credentialsMode !== null}
        onOpenChange={(next) => {
          if (!next) closeCredentials();
        }}
      >
        <DialogContent className="max-w-md">
          <DialogHeader>
            <DialogTitle>
              {credentialsMode === 'store'
                ? t('settings.radio.wifi.dialogStoreTitle')
                : t('settings.radio.wifi.dialogChangeTitle')}
            </DialogTitle>
            <DialogDescription>{t('settings.radio.wifi.dialogIntro')}</DialogDescription>
          </DialogHeader>
          {credentialsMode === 'setup' && wifi.enabled === false && (
            <p className="text-sm" role="note">
              {t('settings.radio.wifi.setUpEnables')}
            </p>
          )}
          <RadioWifiCredentialsFields
            idPrefix="radio-wifi-settings"
            values={form}
            onChange={setForm}
            disabled={busy}
          />
          {isTcp && (
            <p className="text-sm text-warning" role="note">
              {t('settings.radio.wifi.tcpWarning')}
            </p>
          )}
          {error && (
            <p className="text-sm text-destructive" role="alert">
              {error}
            </p>
          )}
          <DialogFooter className="gap-2">
            <Button type="button" variant="secondary" disabled={busy} onClick={closeCredentials}>
              {t('settings.radio.wifi.cancel')}
            </Button>
            <Button type="button" disabled={busy} onClick={() => void submitCredentials()}>
              {busy ? t('settings.radio.wifi.saving') : t('settings.radio.wifi.save')}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      <Dialog
        open={disableOpen}
        onOpenChange={(next) => {
          if (!next && !busy) setDisableOpen(false);
        }}
      >
        <DialogContent className="max-w-md">
          <DialogHeader>
            <DialogTitle>{t('settings.radio.wifi.disableConfirmTitle')}</DialogTitle>
            <DialogDescription>{t('settings.radio.wifi.disableConfirmBody')}</DialogDescription>
          </DialogHeader>
          {isTcp && (
            <p className="text-sm text-warning" role="note">
              {t('settings.radio.wifi.tcpWarning')}
            </p>
          )}
          {error && (
            <p className="text-sm text-destructive" role="alert">
              {error}
            </p>
          )}
          <DialogFooter className="gap-2">
            <Button
              type="button"
              variant="secondary"
              disabled={busy}
              onClick={() => setDisableOpen(false)}
            >
              {t('settings.radio.wifi.cancel')}
            </Button>
            <Button
              type="button"
              variant={rebootBlocked ? 'default' : 'outline'}
              disabled={busy}
              onClick={() => void changeEnabled(false, false)}
            >
              {t('settings.radio.wifi.disableLater')}
            </Button>
            {!rebootBlocked && (
              <Button type="button" disabled={busy} onClick={() => void changeEnabled(false, true)}>
                {t('settings.radio.wifi.disableAndReboot')}
              </Button>
            )}
          </DialogFooter>
        </DialogContent>
      </Dialog>

      <RadioWifiRebootDialog
        open={rebootOpen}
        isTcp={isTcp}
        busy={rebootBusy}
        error={rebootError}
        onConfirm={() => void confirmReboot()}
        onLater={() => setRebootOpen(false)}
      />
    </SettingsGroup>
  );
}
