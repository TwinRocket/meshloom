import { useState } from 'react';
import { useTranslation } from 'react-i18next';

import { api, formatApiError } from '../api';
import {
  isRadioIdentityGate,
  publicKeyPrefix,
  type HealthStatus,
  type RadioIdentityInfo,
} from '../types';
import { Button } from './ui/button';
import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle } from './ui/dialog';
import { toast } from './ui/sonner';

interface RadioIdentityModalProps {
  health: HealthStatus | null;
  forceOpen?: boolean;
  onAdopted: () => void | Promise<void>;
  onResolved?: () => void | Promise<void>;
}

function formatLastActivity(
  lastActivity: number | null | undefined,
  neverLabel: string,
  locale: string
): string {
  if (lastActivity == null || lastActivity <= 0) {
    return neverLabel;
  }
  return new Date(lastActivity * 1000).toLocaleString(locale);
}

function IdentityStat({ label, value }: { label: string; value: string }) {
  return (
    <div className="flex items-center justify-between gap-3 py-0.5">
      <span className="text-[0.625rem] uppercase tracking-wider text-muted-foreground font-medium">
        {label}
      </span>
      <span className="text-sm font-mono tabular-nums">{value}</span>
    </div>
  );
}

export function RadioIdentityModal({
  health,
  forceOpen = false,
  onAdopted,
  onResolved,
}: RadioIdentityModalProps) {
  const { t, i18n } = useTranslation();
  const [busy, setBusy] = useState<'adopt' | 'reject' | null>(null);

  const radioState = health?.radio_state;
  const open = forceOpen || isRadioIdentityGate(radioState);
  const identity: RadioIdentityInfo | null = health?.identity ?? null;
  const isLegacy = radioState === 'identity_unbound_legacy';

  if (!open) {
    return null;
  }

  const previousPrefix = publicKeyPrefix(identity?.previous_public_key);
  const newPrefix = publicKeyPrefix(identity?.new_public_key);

  const handleAdopt = async (confirmWipe: boolean) => {
    setBusy('adopt');
    try {
      await api.adoptRadioIdentity({ confirm_wipe: confirmWipe });
      await onAdopted();
      await onResolved?.();
    } catch (err) {
      toast.error(t('radioIdentity.adoptFailed'), {
        description: formatApiError(err, t),
      });
    } finally {
      setBusy(null);
    }
  };

  const handleReject = async () => {
    setBusy('reject');
    try {
      await api.rejectRadioIdentity();
      await onResolved?.();
    } catch (err) {
      toast.error(t('radioIdentity.rejectFailed'), {
        description: formatApiError(err, t),
      });
    } finally {
      setBusy(null);
    }
  };

  return (
    <Dialog open>
      <DialogContent
        hideCloseButton
        className="w-[calc(100vw-1rem)] max-w-[32rem] gap-5 overflow-y-auto px-4 py-5 dialog-safe-height sm:w-full sm:max-h-[min(85dvh,40rem)] sm:px-6"
        onEscapeKeyDown={(event) => event.preventDefault()}
        onInteractOutside={(event) => event.preventDefault()}
      >
        <DialogHeader className="space-y-2 text-left">
          <DialogTitle>
            {isLegacy ? t('radioIdentity.unboundTitle') : t('radioIdentity.mismatchTitle')}
          </DialogTitle>
          <DialogDescription className="text-[0.8125rem] text-muted-foreground">
            {isLegacy ? t('radioIdentity.unboundBody') : t('radioIdentity.mismatchBody')}
          </DialogDescription>
        </DialogHeader>

        <div className="space-y-2 rounded-md border border-input bg-muted/20 p-3">
          <IdentityStat
            label={t('radioIdentity.previousKey')}
            value={previousPrefix || t('radioIdentity.unknownKey')}
          />
          <IdentityStat
            label={t('radioIdentity.newKey')}
            value={newPrefix || t('radioIdentity.unknownKey')}
          />
          {identity?.new_name ? (
            <IdentityStat label={t('radioIdentity.newName')} value={identity.new_name} />
          ) : null}
          <p className="text-[0.8125rem] text-muted-foreground">
            {t('radioIdentity.meshContacts', { count: identity?.mesh_contacts ?? 0 })}
          </p>
          <p className="text-[0.8125rem] text-muted-foreground">
            {t('radioIdentity.meshMessages', { count: identity?.mesh_messages ?? 0 })}
          </p>
          <IdentityStat
            label={t('radioIdentity.lastActivity')}
            value={formatLastActivity(
              identity?.last_activity,
              t('radioIdentity.lastActivityNever'),
              i18n.language
            )}
          />
        </div>

        <div className="flex flex-col-reverse gap-2 sm:flex-row sm:justify-end">
          {isLegacy ? (
            <>
              <Button
                type="button"
                variant="outline"
                className="border-destructive/50 text-destructive hover:bg-destructive/10"
                disabled={busy !== null}
                onClick={() => {
                  void handleAdopt(true);
                }}
              >
                {busy === 'adopt' ? t('radioIdentity.adopting') : t('radioIdentity.newRadio')}
              </Button>
              <Button
                type="button"
                disabled={busy !== null}
                onClick={() => {
                  void handleAdopt(false);
                }}
              >
                {busy === 'adopt'
                  ? t('radioIdentity.adopting')
                  : t('radioIdentity.bindWithoutWipe')}
              </Button>
            </>
          ) : (
            <>
              <Button
                type="button"
                variant="outline"
                disabled={busy !== null}
                onClick={() => {
                  void handleReject();
                }}
              >
                {busy === 'reject' ? t('radioIdentity.rejecting') : t('radioIdentity.reject')}
              </Button>
              <Button
                type="button"
                variant="outline"
                className="border-destructive/50 text-destructive hover:bg-destructive/10"
                disabled={busy !== null}
                onClick={() => {
                  void handleAdopt(true);
                }}
              >
                {busy === 'adopt' ? t('radioIdentity.adopting') : t('radioIdentity.wipeContinue')}
              </Button>
            </>
          )}
        </div>
      </DialogContent>
    </Dialog>
  );
}
