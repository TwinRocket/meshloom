import { lazy, Suspense, useCallback, useEffect, useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';

import { api } from '../../../api';
import { formatAccessError } from '../../../auth/accessErrors';
import { Button } from '../../ui/button';
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '../../ui/dialog';
import { formatCountdown } from '../AccessPairingPanel';
import { ENROLL_LIFETIME_S } from './remoteShared';

const PairingQr = lazy(() => import('../PairingQr'));

/** How often the dialog asks whether a device used the shown code. */
export const ENROLL_POLL_MS = 3000;

type Phase =
  | { kind: 'loading' }
  | { kind: 'shown'; url: string; deadline: number }
  | { kind: 'enrolled' }
  | { kind: 'expired' }
  | { kind: 'error'; message: string };

/**
 * The enrolment QR code of the caller's next device (`POST /api/remote/enroll`, LAN
 * only). The URL carries a one-time token in its fragment: it lives in this dialog's
 * state only, is dropped on close, expiry or use, and is never logged. The countdown
 * runs on the local clock from the token's fixed lifetime, so a skewed server clock
 * cannot show a dead code as alive. While the code is shown, the dialog polls
 * `GET /api/remote` and reports the new device as soon as it appears.
 */
export function RemoteEnrollDialog({
  open,
  knownDeviceIds,
  onClose,
  onEnrolled,
}: {
  open: boolean;
  /** The device ids held before the code was issued: a new id is the enrolled device. */
  knownDeviceIds: readonly string[];
  onClose: () => void;
  onEnrolled: () => void;
}) {
  const { t } = useTranslation();
  const [phase, setPhase] = useState<Phase>({ kind: 'loading' });
  const [now, setNow] = useState(() => Date.now());
  const epoch = useRef(0);
  const known = useRef<readonly string[]>(knownDeviceIds);
  const enrolledRef = useRef(onEnrolled);
  useEffect(() => {
    enrolledRef.current = onEnrolled;
  });
  useEffect(() => {
    known.current = knownDeviceIds;
  }, [knownDeviceIds]);

  // Sets the phase only once the answer is in (never synchronously inside an effect).
  const request = useCallback(() => {
    const started = ++epoch.current;
    api.issueRemoteEnroll().then(
      (ticket) => {
        if (started !== epoch.current) return;
        const issuedAt = Date.now();
        setNow(issuedAt);
        setPhase({ kind: 'shown', url: ticket.url, deadline: issuedAt + ENROLL_LIFETIME_S * 1000 });
      },
      (err: unknown) => {
        if (started === epoch.current) {
          setPhase({ kind: 'error', message: formatAccessError(err, t) });
        }
      }
    );
  }, [t]);

  const issueAgain = () => {
    setPhase({ kind: 'loading' });
    request();
  };

  useEffect(() => {
    if (!open) return;
    request();
    return () => {
      // Closing drops the code: an answer that comes later is not shown.
      epoch.current += 1;
      setPhase({ kind: 'loading' });
    };
  }, [open, request]);

  const deadline = phase.kind === 'shown' ? phase.deadline : null;
  useEffect(() => {
    if (deadline === null) return;
    const id = window.setInterval(() => {
      const current = Date.now();
      setNow(current);
      if (current >= deadline) setPhase({ kind: 'expired' });
    }, 1000);
    return () => window.clearInterval(id);
  }, [deadline]);

  useEffect(() => {
    if (deadline === null) return;
    const id = window.setInterval(() => {
      api
        .getRemote()
        .then((status) => {
          const fresh = status.devices.some(
            (device) => device.origin === 'qr' && !known.current.includes(device.id)
          );
          if (!fresh) return;
          setPhase((current) => (current.kind === 'shown' ? { kind: 'enrolled' } : current));
          enrolledRef.current();
        })
        .catch(() => undefined);
    }, ENROLL_POLL_MS);
    return () => window.clearInterval(id);
  }, [deadline]);

  const secondsLeft = deadline === null ? 0 : Math.max(0, (deadline - now) / 1000);

  return (
    <Dialog open={open} onOpenChange={(next) => !next && onClose()}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>{t('settings.remote.enroll.dialogTitle')}</DialogTitle>
          <DialogDescription>{t('settings.remote.enroll.dialogHelp')}</DialogDescription>
        </DialogHeader>
        <div className="space-y-4">
          {phase.kind === 'shown' && (
            <>
              <p
                role="alert"
                className="rounded-md border border-warning/50 bg-warning/10 p-3 text-sm font-medium"
              >
                {t('settings.remote.enroll.secretWarning')}
              </p>
              <div className="flex justify-center">
                <div className="max-w-full rounded-md bg-white p-3" data-testid="remote-enroll-qr">
                  <Suspense fallback={<div className="h-56 w-56 max-w-full" aria-hidden="true" />}>
                    <PairingQr value={phase.url} title={t('settings.remote.enroll.qrAlt')} />
                  </Suspense>
                </div>
              </div>
              <p
                className="text-center text-sm"
                aria-live="off"
                data-testid="remote-enroll-countdown"
              >
                {t('settings.remote.enroll.countdown', { time: formatCountdown(secondsLeft) })}
              </p>
            </>
          )}
          {phase.kind === 'loading' && (
            <p role="status" className="text-sm text-muted-foreground">
              {t('settings.remote.loading')}
            </p>
          )}
          {phase.kind === 'enrolled' && (
            <p
              role="status"
              data-testid="remote-enroll-done"
              className="rounded-md border border-success/50 bg-success/10 p-3 text-sm"
            >
              {t('settings.remote.enroll.done')}
            </p>
          )}
          {phase.kind === 'expired' && (
            <p
              role="status"
              data-testid="remote-enroll-expired"
              className="rounded-md border border-border bg-muted/40 p-3 text-sm"
            >
              {t('settings.remote.enroll.expired')}
            </p>
          )}
          {phase.kind === 'error' && (
            <p role="alert" className="text-sm text-destructive">
              {phase.message}
            </p>
          )}
        </div>
        <DialogFooter>
          {(phase.kind === 'expired' || phase.kind === 'error' || phase.kind === 'enrolled') && (
            <Button type="button" variant="outline" onClick={issueAgain}>
              {t(
                phase.kind === 'enrolled'
                  ? 'settings.remote.enroll.another'
                  : 'settings.remote.enroll.regenerate'
              )}
            </Button>
          )}
          <Button type="button" onClick={onClose}>
            {t('settings.remote.enroll.close')}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
