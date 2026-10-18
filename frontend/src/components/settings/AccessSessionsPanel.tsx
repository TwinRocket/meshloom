import { useEffect, useState } from 'react';
import { useTranslation } from 'react-i18next';

import { api } from '../../api';
import { formatAccessError } from '../../auth/accessErrors';
import type { AuthSessionRow } from '../../auth/types';
import { Button } from '../ui/button';
import { toast } from '../ui/sonner';
import { AccessConfirmDialog } from './AccessConfirmDialog';
import { deviceLabel, formatEpoch } from './accessShared';
import { SettingsGroup, SettingsGroupHeader } from './settingsPrimitives';

/** The caller's own sessions: revoke one, or sign out everywhere else. */
export function AccessSessionsPanel() {
  const { t, i18n } = useTranslation();
  const [rows, setRows] = useState<AuthSessionRow[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [confirmingOthers, setConfirmingOthers] = useState(false);

  const [version, setVersion] = useState(0);
  const reload = () => setVersion((value) => value + 1);

  useEffect(() => {
    let cancelled = false;
    api
      .listAuthSessions()
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

  const run = async (action: () => Promise<unknown>, success: string) => {
    setBusy(true);
    setError(null);
    try {
      await action();
      toast.success(success);
    } catch (err) {
      const message = formatAccessError(err, t);
      setError(message);
      toast.error(message);
    } finally {
      reload();
      setBusy(false);
    }
  };

  const others = rows?.filter((row) => !row.current).length ?? 0;

  return (
    <SettingsGroup>
      <SettingsGroupHeader
        id="access-sessions"
        title={t('settings.access.sessions.title')}
        storedOn="server"
      />
      <p className="text-[0.8125rem] text-muted-foreground">{t('settings.access.sessions.help')}</p>
      {error && (
        <p role="alert" className="text-sm text-destructive">
          {error}
        </p>
      )}
      <ul className="divide-y divide-border">
        {rows?.map((row) => (
          <li key={row.id} className="flex flex-wrap items-center gap-x-4 gap-y-2 py-2">
            <div className="min-w-48 flex-1">
              <div className="text-sm font-medium">
                {deviceLabel(row.user_agent, t('settings.access.sessions.unknownDevice'))}
                {row.current && (
                  <span className="ml-2 rounded-full bg-primary/15 px-2 py-0.5 text-[0.6875rem] text-primary">
                    {t('settings.access.sessions.current')}
                  </span>
                )}
              </div>
              <div className="text-[0.8125rem] text-muted-foreground">
                {[
                  row.ip,
                  row.via &&
                    t(`settings.access.sessions.via.${row.via}`, { defaultValue: row.via }),
                  t('settings.access.sessions.lastSeen', {
                    when: formatEpoch(row.last_seen_at, i18n.language),
                  }),
                ]
                  .filter(Boolean)
                  .join(' · ')}
              </div>
            </div>
            {!row.current && (
              <Button
                variant="outline"
                size="sm"
                disabled={busy}
                onClick={() =>
                  void run(
                    () => api.revokeAuthSession(row.id),
                    t('settings.access.sessions.revoked')
                  )
                }
              >
                {t('settings.access.sessions.revoke')}
              </Button>
            )}
          </li>
        ))}
      </ul>
      {rows && others > 0 && (
        <Button variant="outline" disabled={busy} onClick={() => setConfirmingOthers(true)}>
          {t('settings.access.sessions.signOutOthers')}
        </Button>
      )}
      <AccessConfirmDialog
        open={confirmingOthers}
        title={t('settings.access.sessions.signOutOthersTitle')}
        help={t('settings.access.sessions.signOutOthersHelp')}
        confirmLabel={t('settings.access.sessions.signOutOthers')}
        onCancel={() => setConfirmingOthers(false)}
        onConfirm={() => {
          setConfirmingOthers(false);
          void run(
            () => api.revokeOtherAuthSessions(),
            t('settings.access.sessions.othersRevoked')
          );
        }}
      />
    </SettingsGroup>
  );
}
