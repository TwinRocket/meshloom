import { useEffect, useState } from 'react';
import { Trans, useTranslation } from 'react-i18next';
import { AlertTriangle } from 'lucide-react';

import { api } from '../api';
import type { HealthStatus } from '../types';
import { Button } from './ui/button';
import { Checkbox } from './ui/checkbox';
import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle } from './ui/dialog';
import { toast } from './ui/sonner';

const STORAGE_KEY = 'meshcore_security_warning_acknowledged';

function readAcknowledgedState(): boolean {
  if (typeof window === 'undefined') {
    return false;
  }
  try {
    return window.localStorage.getItem(STORAGE_KEY) === 'true';
  } catch {
    return false;
  }
}

function writeAcknowledgedState(): void {
  if (typeof window === 'undefined') {
    return;
  }
  try {
    window.localStorage.setItem(STORAGE_KEY, 'true');
  } catch {
    // Best effort only; the warning will continue to show if localStorage is unavailable.
  }
}

interface SecurityWarningModalProps {
  health: HealthStatus | null;
}

const envCodeClass = 'break-all rounded bg-muted px-1 py-0.5 text-foreground';

export function SecurityWarningModal({ health }: SecurityWarningModalProps) {
  const { t } = useTranslation();
  const [acknowledged, setAcknowledged] = useState(readAcknowledgedState);
  const [confirmedRisk, setConfirmedRisk] = useState(false);
  const [disablingBots, setDisablingBots] = useState(false);
  const [botsDisabledLocally, setBotsDisabledLocally] = useState(false);

  const shouldWarn =
    health !== null &&
    health.bots_disabled !== true &&
    health.basic_auth_enabled !== true &&
    !botsDisabledLocally &&
    !acknowledged;

  useEffect(() => {
    if (!shouldWarn) {
      setConfirmedRisk(false);
    }
  }, [shouldWarn]);

  useEffect(() => {
    if (health?.bots_disabled !== true) {
      setBotsDisabledLocally(false);
    }
  }, [health?.bots_disabled, health?.bots_disabled_source]);

  if (!shouldWarn) {
    return null;
  }

  return (
    <Dialog open>
      <DialogContent
        hideCloseButton
        className="w-[calc(100vw-1rem)] max-w-[42rem] gap-5 overflow-y-auto px-4 py-5 dialog-safe-height sm:w-full sm:max-h-[min(85dvh,48rem)] sm:px-6"
        onEscapeKeyDown={(event) => event.preventDefault()}
        onInteractOutside={(event) => event.preventDefault()}
      >
        <DialogHeader className="space-y-0 text-left">
          <div className="flex items-center gap-3">
            <div className="inline-flex h-11 w-11 shrink-0 items-center justify-center rounded-full border border-destructive/30 bg-destructive/10 text-destructive">
              <AlertTriangle className="h-5 w-5" aria-hidden="true" />
            </div>
            <DialogTitle className="leading-tight">{t('security.title')}</DialogTitle>
          </div>
        </DialogHeader>

        <hr className="border-border" />

        <div className="space-y-3 break-words text-sm leading-6 text-muted-foreground">
          <DialogDescription>{t('security.intro')}</DialogDescription>
          <p>{t('security.networkRisk')}</p>
          <p className="font-semibold text-foreground">{t('security.onlySafe')}</p>
          <p>
            <Trans
              i18nKey="security.envHint"
              components={{
                bots: <code className={envCodeClass} />,
                user: <code className={envCodeClass} />,
                pass: <code className={envCodeClass} />,
              }}
            />
          </p>
          <p>{t('security.temporary')}</p>
        </div>

        <div className="space-y-2">
          <Button
            type="button"
            className="h-auto w-full whitespace-normal py-3 text-center"
            disabled={disablingBots}
            onClick={async () => {
              setDisablingBots(true);
              try {
                await api.disableBotsUntilRestart();
                setBotsDisabledLocally(true);
                toast.success(t('security.botsDisabledToast'));
              } catch (err) {
                toast.error(t('security.disableBotsFailed'), {
                  description: err instanceof Error ? err.message : undefined,
                });
              } finally {
                setDisablingBots(false);
              }
            }}
          >
            {disablingBots ? t('security.disablingBots') : t('security.disableBots')}
          </Button>
        </div>

        <div className="space-y-3 rounded-md border border-input bg-muted/20 p-4">
          <label className="flex items-start gap-3">
            <Checkbox
              checked={confirmedRisk}
              onCheckedChange={(checked) => setConfirmedRisk(checked === true)}
              aria-label={t('security.acknowledgeAria')}
              className="mt-0.5"
            />
            <span className="text-sm leading-6 text-foreground">{t('security.acknowledge')}</span>
          </label>

          <Button
            type="button"
            className="h-auto w-full whitespace-normal py-3 text-center"
            variant="outline"
            disabled={!confirmedRisk || disablingBots}
            onClick={() => {
              writeAcknowledgedState();
              setAcknowledged(true);
            }}
          >
            {t('security.dismiss')}
          </Button>
        </div>
      </DialogContent>
    </Dialog>
  );
}
