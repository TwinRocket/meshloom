import { useEffect, useState } from 'react';
import { useTranslation } from 'react-i18next';

import { api, formatApiError } from '../api';
import {
  noteWifiRebootStarted,
  setRadioWifiState,
  setWifiBackWithIpHandler,
  useRadioWifiState,
} from '../stores/radioWifiStore';
import type { RadioTransportConfig } from '../types';
import {
  EMPTY_WIFI_FORM,
  RadioWifiCredentialsFields,
  type WifiFormValues,
  wifiFormError,
  wifiRequestFromForm,
} from './RadioWifiCredentialsFields';
import { RadioWifiRebootDialog } from './RadioWifiRebootDialog';
import { showWifiSwitchToast } from './radioWifiSwitchToast';
import { Button } from './ui/button';
import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle } from './ui/dialog';
import { toast } from './ui/sonner';

/** The form, with the SSID left null until the user edits it (pre-filled from the radio). */
type PromptForm = Omit<WifiFormValues, 'ssid'> & { ssid: string | null };

const EMPTY_PROMPT_FORM: PromptForm = { ...EMPTY_WIFI_FORM, ssid: null };

/**
 * Asks once per radio for its Wi-Fi password, so the credentials get stored in the
 * radio (the flasher's "update without wipe" then needs no retyping). Opens in any
 * tab when the server state says `prompt_needed`. Exactly two ways out, so "never
 * ask again" is never decided by accident: no close button, Escape and clicks
 * outside are ignored.
 */
export function RadioWifiPrompt() {
  const { t } = useTranslation();
  const wifi = useRadioWifiState();
  const [form, setForm] = useState<PromptForm>(EMPTY_PROMPT_FORM);
  const [busy, setBusy] = useState<'store' | 'dismiss' | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [transport, setTransport] = useState<RadioTransportConfig | null>(null);
  const [rebootFollowUp, setRebootFollowUp] = useState(false);
  const [rebootBusy, setRebootBusy] = useState(false);
  const [rebootError, setRebootError] = useState<string | null>(null);

  const shownKey = wifi?.radio_public_key ?? null;
  const open = Boolean(wifi?.prompt_needed && shownKey);
  // Closed for any reason (answered in another tab, radio gone or changed): forget
  // what was typed, so the next prompt, maybe for another radio, starts empty.
  const formKey = open ? shownKey : null;
  const [formFor, setFormFor] = useState(formKey);
  if (formKey !== formFor) {
    setFormFor(formKey);
    setForm(EMPTY_PROMPT_FORM);
    setError(null);
  }
  const prefilled = wifi?.store_incomplete
    ? (wifi.store_target_ssid ?? wifi.ssid ?? '')
    : (wifi?.ssid ?? '');
  const values: WifiFormValues = { ...form, ssid: form.ssid ?? prefilled };
  const isTcp = transport?.transport === 'tcp';

  // The radio is back on Wi-Fi after a reboot this tab sent: offer TCP.
  useEffect(() => {
    setWifiBackWithIpHandler((state) => void showWifiSwitchToast(state));
    return () => setWifiBackWithIpHandler(null);
  }, []);

  useEffect(() => {
    if (!open) return;
    let cancelled = false;
    api
      .getRadioTransport()
      .then((next) => {
        if (!cancelled) setTransport(next);
      })
      .catch(() => {
        // Only the TCP warning depends on it.
      });
    return () => {
      cancelled = true;
    };
  }, [open]);

  const reset = () => {
    setForm(EMPTY_PROMPT_FORM);
    setError(null);
  };

  const store = async () => {
    const invalid = wifiFormError(values, t);
    if (invalid) {
      setError(invalid);
      return;
    }
    setBusy('store');
    setError(null);
    try {
      const result = await api.setRadioWifiCredentials(wifiRequestFromForm(values, false));
      toast.success(
        t(values.openNetwork ? 'wifiPrompt.storedOpen' : 'wifiPrompt.stored', {
          ssid: values.ssid,
        })
      );
      if (result.reboot_required) {
        setRebootError(null);
        setRebootFollowUp(true);
      }
      reset();
      setRadioWifiState(result.state);
    } catch (err) {
      setError(formatApiError(err, t) || t('settings.radio.wifi.saveFailed'));
      // Never keep the password once the request settled.
      setForm((prev) => ({ ...prev, password: '', confirm: '' }));
    } finally {
      setBusy(null);
    }
  };

  const dismiss = async () => {
    if (!shownKey) return;
    setBusy('dismiss');
    setError(null);
    try {
      const state = await api.dismissRadioWifiPrompt({ radio_public_key: shownKey });
      reset();
      setRadioWifiState(state);
    } catch (err) {
      setError(formatApiError(err, t));
    } finally {
      setBusy(null);
    }
  };

  const reboot = async () => {
    setRebootBusy(true);
    setRebootError(null);
    try {
      const result = await api.rebootRadioForWifi();
      setRadioWifiState(result.state);
      if (result.state.enabled === true) noteWifiRebootStarted();
      setRebootFollowUp(false);
      toast.info(t('settings.radio.wifi.rebooting'));
    } catch (err) {
      setRebootError(formatApiError(err, t));
    } finally {
      setRebootBusy(false);
    }
  };

  const ssidEdited = form.ssid !== null && form.ssid !== prefilled;

  return (
    <>
      {open && (
        <Dialog open>
          <DialogContent
            hideCloseButton
            className="w-[calc(100vw-1rem)] max-w-[30rem] gap-4 overflow-y-auto px-4 py-5 dialog-safe-height sm:w-full sm:px-6"
            onEscapeKeyDown={(event) => event.preventDefault()}
            onInteractOutside={(event) => event.preventDefault()}
          >
            <DialogHeader className="space-y-2 text-left">
              <DialogTitle>{t('wifiPrompt.title')}</DialogTitle>
              <DialogDescription className="text-[0.8125rem] text-muted-foreground">
                {t('wifiPrompt.reason')}
              </DialogDescription>
            </DialogHeader>
            {wifi?.store_incomplete && (
              <p className="text-sm text-warning" role="alert">
                {t('settings.radio.wifi.storeIncompleteWarning', {
                  ssid: wifi.store_target_ssid ?? '',
                })}
              </p>
            )}
            <RadioWifiCredentialsFields
              idPrefix="radio-wifi-prompt"
              values={values}
              onChange={(next) => setForm(next)}
              disabled={busy !== null}
            />
            {isTcp && ssidEdited && (
              <p className="text-sm text-warning" role="note">
                {t('settings.radio.wifi.tcpWarning')}
              </p>
            )}
            {error && (
              <p className="text-sm text-destructive" role="alert">
                {error}
              </p>
            )}
            <div className="flex flex-col-reverse gap-2 sm:flex-row sm:justify-end">
              <Button
                type="button"
                variant="secondary"
                disabled={busy !== null}
                onClick={() => void dismiss()}
              >
                {busy === 'dismiss' ? t('wifiPrompt.dismissing') : t('wifiPrompt.dismiss')}
              </Button>
              <Button type="button" disabled={busy !== null} onClick={() => void store()}>
                {busy === 'store' ? t('settings.radio.wifi.saving') : t('wifiPrompt.store')}
              </Button>
            </div>
          </DialogContent>
        </Dialog>
      )}
      <RadioWifiRebootDialog
        open={rebootFollowUp}
        isTcp={isTcp}
        busy={rebootBusy}
        error={rebootError}
        onConfirm={() => void reboot()}
        onLater={() => setRebootFollowUp(false)}
      />
    </>
  );
}
