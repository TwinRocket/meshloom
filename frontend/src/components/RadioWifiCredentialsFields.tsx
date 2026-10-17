import type { TFunction } from 'i18next';
import { useTranslation } from 'react-i18next';

import type { RadioWifiCredentialsRequest } from '../types';
import { Checkbox } from './ui/checkbox';
import { Input } from './ui/input';
import { Label } from './ui/label';

/** What the Wi-Fi credentials form holds. Cleared by the caller once a request settles. */
export interface WifiFormValues {
  ssid: string;
  password: string;
  confirm: string;
  openNetwork: boolean;
}

export const EMPTY_WIFI_FORM: WifiFormValues = {
  ssid: '',
  password: '',
  confirm: '',
  openNetwork: false,
};

const SSID_MAX_BYTES = 32;
const PASSWORD_MAX_BYTES = 63;
const NOT_SET_SSID = '(not set)';
// eslint-disable-next-line no-control-regex
const CONTROL_CHARS = /[\u0000-\u001f\u007f-\u009f]/;

function byteLength(value: string): number {
  return new TextEncoder().encode(value).length;
}

/** Why the form cannot be sent yet, or null. Mirrors the backend checks (never trims). */
export function wifiFormError(values: WifiFormValues, t: TFunction): string | null {
  const ssidBytes = byteLength(values.ssid);
  if (ssidBytes < 1 || ssidBytes > SSID_MAX_BYTES || CONTROL_CHARS.test(values.ssid)) {
    return t('settings.radio.wifi.ssidInvalid');
  }
  if (values.ssid === NOT_SET_SSID) {
    return t('settings.radio.wifi.ssidReserved');
  }
  if (values.openNetwork) return null;
  if (!values.password) return t('settings.radio.wifi.passwordRequired');
  if (byteLength(values.password) > PASSWORD_MAX_BYTES || CONTROL_CHARS.test(values.password)) {
    return t('settings.radio.wifi.passwordTooLong');
  }
  if (values.password !== values.confirm) return t('settings.radio.wifi.passwordMismatch');
  return null;
}

/** The request body for the form (the SSID and password exactly as typed). */
export function wifiRequestFromForm(
  values: WifiFormValues,
  reboot = false
): RadioWifiCredentialsRequest {
  return values.openNetwork
    ? { ssid: values.ssid, password: null, open_network: true, reboot }
    : { ssid: values.ssid, password: values.password, open_network: false, reboot };
}

/** SSID, "open network", password typed twice, and the warnings that go with them. */
export function RadioWifiCredentialsFields({
  idPrefix,
  values,
  onChange,
  disabled = false,
}: {
  idPrefix: string;
  values: WifiFormValues;
  onChange: (next: WifiFormValues) => void;
  disabled?: boolean;
}) {
  const { t } = useTranslation();
  const set = (patch: Partial<WifiFormValues>) => onChange({ ...values, ...patch });
  const short = !values.openNetwork && values.password.length > 0 && values.password.length < 8;
  const mismatch =
    !values.openNetwork && values.confirm.length > 0 && values.password !== values.confirm;

  return (
    <div className="space-y-3">
      <div className="space-y-1">
        <Label htmlFor={`${idPrefix}-ssid`}>{t('settings.radio.wifi.ssid')}</Label>
        <Input
          id={`${idPrefix}-ssid`}
          value={values.ssid}
          autoComplete="off"
          spellCheck={false}
          disabled={disabled}
          onChange={(e) => set({ ssid: e.target.value })}
        />
        <p className="text-[0.8125rem] text-muted-foreground">
          {t('settings.radio.wifi.ssidHelp')}
        </p>
      </div>
      <div className="flex items-center gap-2">
        <Checkbox
          id={`${idPrefix}-open`}
          checked={values.openNetwork}
          disabled={disabled}
          onCheckedChange={(checked) =>
            set({ openNetwork: checked === true, password: '', confirm: '' })
          }
        />
        <Label htmlFor={`${idPrefix}-open`}>{t('settings.radio.wifi.openNetwork')}</Label>
      </div>
      {!values.openNetwork && (
        <>
          <div className="space-y-1">
            <Label htmlFor={`${idPrefix}-password`}>{t('settings.radio.wifi.password')}</Label>
            <Input
              id={`${idPrefix}-password`}
              type="password"
              autoComplete="new-password"
              value={values.password}
              disabled={disabled}
              onChange={(e) => set({ password: e.target.value })}
            />
          </div>
          <div className="space-y-1">
            <Label htmlFor={`${idPrefix}-confirm`}>
              {t('settings.radio.wifi.passwordConfirm')}
            </Label>
            <Input
              id={`${idPrefix}-confirm`}
              type="password"
              autoComplete="new-password"
              value={values.confirm}
              disabled={disabled}
              onChange={(e) => set({ confirm: e.target.value })}
            />
          </div>
          {short && (
            <p className="text-[0.8125rem] text-warning">
              {t('settings.radio.wifi.passwordShort')}
            </p>
          )}
          {mismatch && (
            <p className="text-[0.8125rem] text-destructive">
              {t('settings.radio.wifi.passwordMismatch')}
            </p>
          )}
        </>
      )}
      <p className="text-[0.8125rem] text-muted-foreground">
        {t('settings.radio.wifi.wrongPasswordWarning')}
      </p>
    </div>
  );
}
