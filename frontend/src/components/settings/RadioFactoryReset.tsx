import { useState } from 'react';
import { useTranslation } from 'react-i18next';
import { api, formatApiError } from '../../api';
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
import { SettingsGroup, SettingsGroupHeader } from './settingsPrimitives';

/**
 * Danger zone: erases the radio (identity, contacts, channels, radio settings).
 * The radio name must be typed exactly; the backend checks it again.
 */
export function RadioFactoryReset({ radioName }: { radioName: string }) {
  const { t } = useTranslation();
  const [open, setOpen] = useState(false);
  const [typed, setTyped] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [sent, setSent] = useState(false);

  const handleOpenChange = (next: boolean) => {
    if (busy) return;
    setOpen(next);
    if (!next) {
      setTyped('');
      setError(null);
    }
  };

  const confirm = async () => {
    setBusy(true);
    setError(null);
    try {
      await api.factoryResetRadio(typed);
      setOpen(false);
      setTyped('');
      setSent(true);
    } catch (err) {
      setError(formatApiError(err, t) || t('settings.radio.factoryReset.failed'));
    } finally {
      setBusy(false);
    }
  };

  const erased = ['erasedIdentity', 'erasedContacts', 'erasedSettings'] as const;
  const kept = ['keptDatabase', 'keptWifi'] as const;

  return (
    <SettingsGroup id="radio-factory-reset">
      <SettingsGroupHeader
        id="radio-factory-reset"
        title={t('settings.radio.factoryReset.title')}
        storedOn="radio"
      />
      <div className="space-y-3 rounded-md border border-destructive/50 p-3">
        <p className="max-w-prose text-[0.8125rem] text-muted-foreground">
          {t('settings.radio.factoryReset.intro')}
        </p>
        {radioName ? (
          <Button
            type="button"
            variant="outline"
            className="border-destructive/50 text-destructive hover:bg-destructive/10"
            onClick={() => {
              setSent(false);
              setOpen(true);
            }}
          >
            {t('settings.radio.factoryReset.button')}
          </Button>
        ) : (
          <p className="text-[0.8125rem] text-muted-foreground">
            {t('settings.radio.factoryReset.needsRadio')}
          </p>
        )}
        {sent && (
          <p className="max-w-prose text-sm" role="status">
            {t('settings.radio.factoryReset.sent')}
          </p>
        )}
      </div>

      <Dialog open={open} onOpenChange={handleOpenChange}>
        <DialogContent className="max-w-lg">
          <DialogHeader>
            <DialogTitle>{t('settings.radio.factoryReset.dialogTitle')}</DialogTitle>
            <DialogDescription>{t('settings.radio.factoryReset.dialogIntro')}</DialogDescription>
          </DialogHeader>
          <div className="space-y-3 text-sm">
            <div className="space-y-1">
              <h4 className="font-semibold text-destructive">
                {t('settings.radio.factoryReset.erasedTitle')}
              </h4>
              <ul className="list-disc space-y-1 pl-5">
                {erased.map((k) => (
                  <li key={k}>{t(`settings.radio.factoryReset.${k}`)}</li>
                ))}
              </ul>
            </div>
            <div className="space-y-1">
              <h4 className="font-semibold">{t('settings.radio.factoryReset.keptTitle')}</h4>
              <ul className="list-disc space-y-1 pl-5">
                {kept.map((k) => (
                  <li key={k}>{t(`settings.radio.factoryReset.${k}`)}</li>
                ))}
              </ul>
            </div>
            <p className="text-muted-foreground">{t('settings.radio.factoryReset.after')}</p>
            <div className="space-y-2">
              <Label htmlFor="radio-factory-reset-confirm">
                {t('settings.radio.factoryReset.confirmLabel', { name: radioName })}
              </Label>
              <Input
                id="radio-factory-reset-confirm"
                value={typed}
                autoComplete="off"
                disabled={busy}
                onChange={(e) => setTyped(e.target.value)}
              />
            </div>
            {error && (
              <div className="text-sm text-destructive" role="alert">
                {error}
              </div>
            )}
          </div>
          <DialogFooter className="gap-2">
            <Button
              type="button"
              variant="secondary"
              disabled={busy}
              onClick={() => handleOpenChange(false)}
            >
              {t('settings.radio.factoryReset.cancel')}
            </Button>
            <Button
              type="button"
              variant="destructive"
              disabled={busy || typed !== radioName}
              onClick={() => void confirm()}
            >
              {busy
                ? t('settings.radio.factoryReset.resetting')
                : t('settings.radio.factoryReset.confirm')}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </SettingsGroup>
  );
}
