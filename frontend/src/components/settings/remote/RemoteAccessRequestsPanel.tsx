import { useCallback, useEffect, useState, type FormEvent } from 'react';
import { useTranslation } from 'react-i18next';

import { api } from '../../../api';
import { formatAccessError } from '../../../auth/accessErrors';
import { Button } from '../../ui/button';
import { Input } from '../../ui/input';
import { Label } from '../../ui/label';
import { toast } from '../../ui/sonner';
import { deviceLabel, formatEpoch } from '../accessShared';
import { SettingsGroup, SettingsGroupHeader } from '../settingsPrimitives';
import { REMOTE_LABEL_MAX, untrustedText } from './remoteShared';
import type { RemoteAccessRequest } from './remoteTypes';

/** Pending requests live 10 min on the instance: refresh while the panel is open. */
export const ACCESS_REQUESTS_POLL_MS = 10_000;

/**
 * Access requests from unknown devices (`/api/remote/access-requests`). The pending
 * list never shows the code: the reader types the code the new device shows, so a
 * request cannot be approved without holding that device. Everything the unknown
 * device sent (its user agent) is untrusted text: rendered by React as text, stripped
 * of control and direction characters (`untrustedText`), never as markup.
 */
export function RemoteAccessRequestsPanel({
  online,
  onApproved,
}: {
  /** The relay link is up: approving needs it (503 `remote_offline` otherwise). */
  online: boolean;
  onApproved: () => void;
}) {
  const { t, i18n } = useTranslation();
  const [rows, setRows] = useState<RemoteAccessRequest[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [code, setCode] = useState('');
  const [label, setLabel] = useState('');
  const [busy, setBusy] = useState(false);
  const [version, setVersion] = useState(0);
  const reload = useCallback(() => setVersion((value) => value + 1), []);

  useEffect(() => {
    let cancelled = false;
    api
      .listRemoteAccessRequests()
      .then((list) => {
        if (cancelled) return;
        setRows(list);
        setError(null);
      })
      .catch((err) => {
        if (!cancelled) setError(formatAccessError(err, t));
      });
    return () => {
      cancelled = true;
    };
  }, [version, t]);

  useEffect(() => {
    const id = window.setInterval(reload, ACCESS_REQUESTS_POLL_MS);
    return () => window.clearInterval(id);
  }, [reload]);

  const approve = async (event: FormEvent) => {
    event.preventDefault();
    const typed = code.trim();
    if (!typed || busy) return;
    setBusy(true);
    setError(null);
    try {
      await api.approveRemoteAccessRequest(typed, label.trim() || undefined);
      toast.success(t('settings.remote.requests.approved'));
      setCode('');
      setLabel('');
      onApproved();
    } catch (err) {
      setError(formatAccessError(err, t));
    } finally {
      setBusy(false);
      reload();
    }
  };

  const deny = async (row: RemoteAccessRequest) => {
    setBusy(true);
    setError(null);
    try {
      await api.denyRemoteAccessRequest(row.request_id);
      toast.success(t('settings.remote.requests.denied'));
    } catch (err) {
      setError(formatAccessError(err, t));
    } finally {
      setBusy(false);
      reload();
    }
  };

  return (
    <SettingsGroup>
      <SettingsGroupHeader
        id="remote-requests"
        title={t('settings.remote.requests.title')}
        storedOn="server"
      />
      <p className="text-[0.8125rem] text-muted-foreground">{t('settings.remote.requests.help')}</p>
      <p
        role="note"
        data-testid="remote-requests-warning"
        className="rounded-md border border-warning/50 bg-warning/10 p-3 text-[0.8125rem] font-medium"
      >
        {t('settings.remote.requests.socialWarning')}
      </p>
      {rows && rows.length === 0 && (
        <p className="text-sm text-muted-foreground">{t('settings.remote.requests.none')}</p>
      )}
      {rows && rows.length > 0 && (
        <ul className="divide-y divide-border" data-testid="remote-requests-list">
          {rows.map((row) => {
            const agent = untrustedText(row.user_agent);
            return (
              <li key={row.request_id} className="flex flex-wrap items-center gap-x-4 gap-y-2 py-2">
                <div className="min-w-0 flex-1">
                  <div className="text-sm font-medium">
                    {deviceLabel(agent || null, t('settings.remote.requests.unknownDevice'))}
                  </div>
                  <div className="mt-1 text-[0.8125rem] text-muted-foreground">
                    {t('settings.remote.requests.askedAt', {
                      when: formatEpoch(row.created_at, i18n.language),
                    })}
                  </div>
                  {agent && (
                    <div className="mt-1 text-[0.75rem] text-muted-foreground">
                      {t('settings.remote.requests.userAgent')}{' '}
                      <code
                        dir="ltr"
                        className="break-all rounded bg-muted px-1 py-0.5 font-mono"
                        data-testid="remote-request-agent"
                      >
                        {agent}
                      </code>
                    </div>
                  )}
                </div>
                <Button variant="outline" size="sm" disabled={busy} onClick={() => void deny(row)}>
                  {t('settings.remote.requests.deny')}
                </Button>
              </li>
            );
          })}
        </ul>
      )}
      <form onSubmit={approve} className="grid gap-3 sm:grid-cols-2">
        <div className="space-y-1.5">
          <Label htmlFor="remote-request-code">{t('settings.remote.requests.code')}</Label>
          <Input
            id="remote-request-code"
            autoComplete="off"
            autoCapitalize="characters"
            spellCheck={false}
            maxLength={32}
            placeholder="XXXXX-XXXXX"
            value={code}
            onChange={(event) => setCode(event.target.value)}
          />
        </div>
        <div className="space-y-1.5">
          <Label htmlFor="remote-request-label">{t('settings.remote.requests.label')}</Label>
          <Input
            id="remote-request-label"
            autoComplete="off"
            maxLength={REMOTE_LABEL_MAX}
            placeholder={t('settings.remote.requests.labelPlaceholder')}
            value={label}
            onChange={(event) => setLabel(event.target.value)}
          />
        </div>
        <div className="sm:col-span-2">
          <Button type="submit" disabled={busy || !code.trim() || !online}>
            {t('settings.remote.requests.approve')}
          </Button>
          {!online && (
            <p className="mt-1 text-[0.8125rem] text-muted-foreground">
              {t('settings.remote.requests.offline')}
            </p>
          )}
        </div>
      </form>
      {error && (
        <p role="alert" className="text-sm text-destructive">
          {error}
        </p>
      )}
    </SettingsGroup>
  );
}
