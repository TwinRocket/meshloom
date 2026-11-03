import { useCallback, useEffect, useMemo, useState } from 'react';
import { useTranslation } from 'react-i18next';

import { api } from '../../api';
import { formatAccessError } from '../../auth/accessErrors';
import { useCapabilities, useSession } from '../../auth/SessionProvider';
import type { HealthStatus } from '../../types';
import { userDocsUrl } from '../../utils/userDocsUrl';
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
import { Label } from '../ui/label';
import { toast } from '../ui/sonner';
import { AccessConfirmDialog } from './AccessConfirmDialog';
import { formatEpoch } from './accessShared';
import { SettingsGroup, SettingsGroupHeader } from './settingsPrimitives';
import type { SettingsSection } from './settingsConstants';
import { RemoteDomainPanel } from './remote/RemoteDomainPanel';
import { RemoteAccessRequestsPanel } from './remote/RemoteAccessRequestsPanel';
import { RemoteDevicesPanel } from './remote/RemoteDevicesPanel';
import { RemoteEnrollDialog } from './remote/RemoteEnrollDialog';
import {
  BADGE_TONE_CLASS,
  pluginsIsolationReduced,
  remoteBadge,
  untrustedText,
} from './remote/remoteShared';
import type { RemoteStatus } from './remote/remoteTypes';

// A local Remote error code (`remote_*`, e.g. a Community attestation refusal): shown
// as its translated sentence instead of the raw code.
const KNOWN_ERROR_RE = /^remote_[a-z_]{1,48}$/;

/** The link state changes on its own (reconnects, pauses): refresh while the section is open. */
export const REMOTE_STATUS_POLL_MS = 15_000;

type Confirm = 'disable' | 'unregister' | 'rotate' | null;

/**
 * Settings → Remote access (Meshloom Remote, docs/dev/remote.md). A blind relay: no
 * TwinRocket account, no link step. An administrator turns it on (the instance
 * registers its own key and gets a name), then every user enrols their phone or
 * browser with a QR code shown here on the local network, or approves an access
 * request by typing the code the new device shows. On screen: one actionable line
 * (passkey or 2FA) and a link to docs/user/<lang>/deep/remote.md, which says what the
 * relay sees (MSHL-449: the details live in the docs and the terms, not in the UI).
 *
 * Every account role but the kiosk opens it (`ACCOUNT_SETTINGS_SECTIONS`): enable,
 * disable, unregister, rotate and revoke-all are shown to administrators only, the
 * server refuses them to anyone else.
 */
export function SettingsRemoteSection({
  className,
  health,
  onOpenSection,
}: {
  className?: string;
  health: HealthStatus | null;
  onOpenSection?: (section: SettingsSection) => void;
}) {
  const { t, i18n } = useTranslation();
  const { isAdmin } = useCapabilities();
  const { user, via } = useSession();
  const [status, setStatus] = useState<RemoteStatus | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [confirm, setConfirm] = useState<Confirm>(null);
  const [enabling, setEnabling] = useState(false);
  const [acknowledged, setAcknowledged] = useState(false);
  const [enrolling, setEnrolling] = useState(false);
  const [userNames, setUserNames] = useState<ReadonlyMap<number, string>>(new Map());
  const [version, setVersion] = useState(0);
  const reload = useCallback(() => setVersion((value) => value + 1), []);

  useEffect(() => {
    let cancelled = false;
    api
      .getRemote()
      .then((next) => {
        if (cancelled) return;
        setStatus(next);
        setLoadError(null);
      })
      .catch((err) => {
        if (!cancelled) setLoadError(formatAccessError(err, t));
      });
    return () => {
      cancelled = true;
    };
  }, [version, t]);

  useEffect(() => {
    const id = window.setInterval(reload, REMOTE_STATUS_POLL_MS);
    return () => window.clearInterval(id);
  }, [reload]);

  // An administrator sees every account's devices: name their owners (best effort).
  useEffect(() => {
    if (!isAdmin) return;
    let cancelled = false;
    api
      .listAuthUsers()
      .then((rows) => {
        if (!cancelled) setUserNames(new Map(rows.map((row) => [row.id, row.username])));
      })
      .catch(() => undefined);
    return () => {
      cancelled = true;
    };
  }, [isAdmin]);

  const run = async (action: () => Promise<RemoteStatus>, done: string) => {
    setBusy(true);
    setActionError(null);
    try {
      setStatus(await action());
      toast.success(t(done));
    } catch (err) {
      setActionError(formatAccessError(err, t));
      reload();
    } finally {
      setBusy(false);
    }
  };

  const reduced = pluginsIsolationReduced(health);
  const openEnable = () => {
    setAcknowledged(false);
    setEnabling(true);
  };
  const enable = () => {
    setEnabling(false);
    void run(() => api.enableRemote(), 'settings.remote.enabledToast');
  };
  const restart = () =>
    run(async () => {
      await api.disableRemote();
      return api.enableRemote();
    }, 'settings.remote.restartedToast');

  const confirmAction = () => {
    const chosen = confirm;
    setConfirm(null);
    if (chosen === 'disable') void run(() => api.disableRemote(), 'settings.remote.disabledToast');
    if (chosen === 'unregister') {
      void run(() => api.unregisterRemote(), 'settings.remote.unregisteredToast');
    }
    if (chosen === 'rotate') void run(() => api.rotateRemote(), 'settings.remote.rotatedToast');
  };

  const deviceIds = useMemo(() => status?.devices.map((device) => device.id) ?? [], [status]);
  const canCopy = typeof navigator !== 'undefined' && !!navigator.clipboard;
  const copyHostname = async (hostname: string) => {
    try {
      await navigator.clipboard.writeText(`https://${hostname}`);
      toast.success(t('settings.remote.status.copied'));
    } catch {
      toast.error(t('settings.remote.status.copyFailed'));
    }
  };

  const badge = status ? remoteBadge(status) : null;
  const registered = status?.state === 'registered';
  const viaRelay = via === 'relay';

  return (
    <div className={className} data-testid="settings-remote">
      <SettingsGroup>
        <SettingsGroupHeader title={t('settings.remote.about.title')} storedOn="server" />
        <p className="text-sm">{t('settings.remote.about.intro')}</p>
        <p role="note" data-testid="remote-privacy-note" className="text-[0.8125rem]">
          {t('settings.remote.about.secure')}{' '}
          <a
            href={userDocsUrl(i18n.language, 'deep/remote')}
            target="_blank"
            rel="noreferrer"
            className="underline underline-offset-2 hover:text-primary transition-colors"
          >
            {t('settings.remote.about.learnMore')}
          </a>
        </p>
      </SettingsGroup>

      <SettingsGroup>
        <SettingsGroupHeader
          id="remote-status"
          title={t('settings.remote.status.title')}
          storedOn="server"
          instant
        />
        {!status && !loadError && (
          <p role="status" className="text-sm text-muted-foreground">
            {t('settings.remote.loading')}
          </p>
        )}
        {loadError && (
          <p role="alert" className="text-sm text-destructive">
            {loadError}
          </p>
        )}
        {status && badge && (
          <div className="space-y-3">
            <div className="flex flex-wrap items-center gap-2">
              <span
                data-testid="remote-badge"
                data-badge={badge.id}
                className={`rounded-full border px-2.5 py-0.5 text-[0.75rem] font-medium ${BADGE_TONE_CLASS[badge.tone]}`}
              >
                {t(`settings.remote.badge.${badge.id}`)}
              </span>
            </div>
            <p className="text-[0.8125rem]" data-testid="remote-badge-help">
              {t(`settings.remote.badgeHelp.${badge.id}`)}
            </p>
            {status.last_error &&
              status.state === 'unregistered' &&
              (KNOWN_ERROR_RE.test(status.last_error) &&
              i18n.exists(`errors.${status.last_error}`) ? (
                <p
                  className="text-[0.8125rem] text-muted-foreground"
                  data-testid="remote-last-error"
                >
                  {t(`errors.${status.last_error}`)}
                </p>
              ) : (
                <p
                  className="text-[0.8125rem] text-muted-foreground"
                  data-testid="remote-last-error"
                >
                  {t('settings.remote.status.lastError')}{' '}
                  <code className="break-all rounded bg-muted px-1 py-0.5 font-mono">
                    {untrustedText(status.last_error, 300)}
                  </code>
                </p>
              ))}
            <dl className="grid gap-x-4 gap-y-2 text-sm sm:grid-cols-[max-content_1fr]">
              {status.hostname && (
                <>
                  <dt className="text-muted-foreground">{t('settings.remote.status.hostname')}</dt>
                  <dd className="flex min-w-0 flex-wrap items-center gap-2">
                    <code className="break-all font-mono" dir="ltr" data-testid="remote-hostname">
                      {`https://${status.hostname}`}
                    </code>
                    {canCopy && (
                      <Button
                        type="button"
                        variant="outline"
                        size="sm"
                        onClick={() => void copyHostname(status.hostname as string)}
                      >
                        {t('settings.remote.status.copy')}
                      </Button>
                    )}
                  </dd>
                </>
              )}
              <dt className="text-muted-foreground">{t('settings.remote.status.fingerprint')}</dt>
              <dd>
                <code className="font-mono" dir="ltr" data-testid="remote-fingerprint">
                  {status.key_fingerprint ?? t('settings.remote.status.noKey')}
                </code>
              </dd>
              {status.registered_at && (
                <>
                  <dt className="text-muted-foreground">
                    {t('settings.remote.status.registeredAt')}
                  </dt>
                  <dd>{formatEpoch(status.registered_at, i18n.language)}</dd>
                </>
              )}
            </dl>
            {isAdmin && (
              <div className="flex flex-wrap gap-2">
                {badge.action === 'enable' && (
                  <Button disabled={busy} onClick={openEnable}>
                    {t(
                      status.state === 'disabled'
                        ? 'settings.remote.actions.enable'
                        : 'settings.remote.actions.retry'
                    )}
                  </Button>
                )}
                {badge.action === 'restart' && (
                  <Button disabled={busy} onClick={() => void restart()}>
                    {t('settings.remote.actions.restart')}
                  </Button>
                )}
                {badge.action === 'community' && onOpenSection && (
                  <Button disabled={busy} onClick={() => onOpenSection('community')}>
                    {t('settings.remote.actions.community')}
                  </Button>
                )}
                {badge.action === 'update' && onOpenSection && (
                  <Button disabled={busy} onClick={() => onOpenSection('updates')}>
                    {t('settings.remote.actions.update')}
                  </Button>
                )}
                {status.enabled && (
                  <Button variant="outline" disabled={busy} onClick={() => setConfirm('disable')}>
                    {t('settings.remote.actions.disable')}
                  </Button>
                )}
                {status.enabled && status.slug && (
                  <Button
                    variant="outline"
                    disabled={busy}
                    onClick={() => setConfirm('unregister')}
                  >
                    {t('settings.remote.actions.unregister')}
                  </Button>
                )}
                {status.key_fingerprint && (
                  <Button
                    variant={badge.action === 'rotate' ? 'destructive' : 'outline'}
                    disabled={busy}
                    onClick={() => setConfirm('rotate')}
                  >
                    {t('settings.remote.actions.rotate')}
                  </Button>
                )}
              </div>
            )}
            {!isAdmin && status.state !== 'registered' && (
              <p className="text-[0.8125rem] text-muted-foreground">
                {t('settings.remote.status.askAdmin')}
              </p>
            )}
            {actionError && (
              <p role="alert" className="text-sm text-destructive">
                {actionError}
              </p>
            )}
          </div>
        )}
      </SettingsGroup>

      {status && registered && (
        <SettingsGroup>
          <SettingsGroupHeader
            id="remote-enroll"
            title={t('settings.remote.enroll.title')}
            storedOn="server"
          />
          <p className="text-[0.8125rem] text-muted-foreground">
            {t('settings.remote.enroll.help')}
          </p>
          {viaRelay ? (
            <p
              role="note"
              data-testid="remote-enroll-lan-only"
              className="rounded-md border border-border bg-muted/40 p-3 text-[0.8125rem]"
            >
              {t('settings.remote.enroll.lanOnly')}
            </p>
          ) : (
            <div>
              <Button onClick={() => setEnrolling(true)}>{t('settings.remote.enroll.show')}</Button>
            </div>
          )}
          <RemoteEnrollDialog
            open={enrolling}
            knownDeviceIds={deviceIds}
            currentUserId={user?.id ?? null}
            onClose={() => setEnrolling(false)}
            onEnrolled={reload}
          />
        </SettingsGroup>
      )}

      {isAdmin && status && registered && status.hostname && (
        <RemoteDomainPanel hostname={status.hostname} />
      )}

      {status && registered && (
        <RemoteAccessRequestsPanel online={status.relay_connected} onApproved={reload} />
      )}

      {status && (status.devices.length > 0 || registered) && (
        <RemoteDevicesPanel
          devices={status.devices}
          isAdmin={isAdmin}
          currentUserId={user?.id ?? null}
          userNames={userNames}
          onChanged={reload}
        />
      )}

      <Dialog open={enabling} onOpenChange={(next) => !next && setEnabling(false)}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>{t('settings.remote.enable.title')}</DialogTitle>
            <DialogDescription>{t('settings.remote.enable.help')}</DialogDescription>
          </DialogHeader>
          {reduced && (
            <div
              role="alert"
              data-testid="remote-enable-isolation-warning"
              className="space-y-2 rounded-md border border-destructive/50 bg-destructive/10 p-3 text-[0.8125rem]"
            >
              <p>{t('settings.remote.enable.isolationWarning')}</p>
              <div className="flex items-start gap-2">
                <Checkbox
                  id="remote-enable-ack"
                  className="mt-0.5"
                  checked={acknowledged}
                  onCheckedChange={(checked) => setAcknowledged(checked === true)}
                />
                <Label htmlFor="remote-enable-ack" className="text-[0.8125rem] leading-snug">
                  {t('settings.remote.enable.isolationAck')}
                </Label>
              </div>
            </div>
          )}
          <DialogFooter>
            <Button variant="outline" onClick={() => setEnabling(false)}>
              {t('settings.access.cancel')}
            </Button>
            <Button disabled={busy || (reduced && !acknowledged)} onClick={enable}>
              {t('settings.remote.enable.confirm')}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      <AccessConfirmDialog
        open={confirm !== null}
        title={confirm ? t(`settings.remote.confirm.${confirm}.title`) : ''}
        help={confirm ? t(`settings.remote.confirm.${confirm}.help`) : ''}
        confirmLabel={confirm ? t(`settings.remote.confirm.${confirm}.confirm`) : ''}
        destructive={confirm !== 'disable'}
        onCancel={() => setConfirm(null)}
        onConfirm={confirmAction}
      />
    </div>
  );
}
