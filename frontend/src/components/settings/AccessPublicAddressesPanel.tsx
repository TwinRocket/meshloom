import { useEffect, useState } from 'react';
import { useTranslation } from 'react-i18next';

import { api } from '../../api';
import { formatAccessError } from '../../auth/accessErrors';
import type { AuthSettings, HostGuardMode, RejectedHost } from '../../auth/types';
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
import { Input } from '../ui/input';
import { Label } from '../ui/label';
import { toast } from '../ui/sonner';
import { SettingsGroup, SettingsGroupHeader } from './settingsPrimitives';

interface View {
  urls: string[];
  mode: HostGuardMode;
  rejected: RejectedHost[];
}

const toView = (settings: AuthSettings): View => ({
  urls: settings.public_urls ?? [],
  mode: settings.host_guard_mode ?? 'observe',
  rejected: settings.rejected_hosts ?? [],
});

/**
 * Administrator only: the addresses Meshloom is reached under (`public_urls`), the
 * names the Host guard saw and would refuse (`rejected_hosts`, each with an "Allow"
 * button that declares it), and the observe/enforce switch. Every change is saved at
 * once; the server validates the entries (coded `errors.auth_public_urls_invalid`) and
 * refuses an enforce that would reject the address in use (`auth_host_guard_would_lock_out`).
 */
export function AccessPublicAddressesPanel() {
  const { t } = useTranslation();
  const [view, setView] = useState<View | null>(null);
  const [draft, setDraft] = useState('');
  const [busy, setBusy] = useState(false);
  const [allowing, setAllowing] = useState<RejectedHost | null>(null);

  useEffect(() => {
    let cancelled = false;
    api
      .getAuthSettings()
      .then((settings) => {
        if (!cancelled) setView(toView(settings));
      })
      .catch(() => {
        // Not readable (older server, refused): the panel stays hidden.
      });
    return () => {
      cancelled = true;
    };
  }, []);

  if (view === null) return null;

  const patch = async (change: Partial<AuthSettings>, success: string): Promise<boolean> => {
    setBusy(true);
    try {
      setView(toView(await api.patchAuthSettings(change)));
      toast.success(success);
      return true;
    } catch (err) {
      toast.error(formatAccessError(err, t));
      return false;
    } finally {
      setBusy(false);
    }
  };

  const add = async () => {
    const entry = draft.trim();
    if (!entry) return;
    if (await patch({ public_urls: [...view.urls, entry] }, t('settings.access.public.saved'))) {
      setDraft('');
    }
  };

  const enforcing = view.mode === 'enforce';

  return (
    <SettingsGroup>
      <SettingsGroupHeader
        id="access-public"
        title={t('settings.access.public.title')}
        storedOn="server"
        instant
      />
      <p className="text-[0.8125rem] text-muted-foreground">{t('settings.access.public.help')}</p>

      {view.urls.length === 0 ? (
        <p className="text-[0.8125rem] text-muted-foreground">{t('settings.access.public.none')}</p>
      ) : (
        <ul className="space-y-2" aria-label={t('settings.access.public.title')}>
          {view.urls.map((url) => (
            <li key={url} className="flex flex-wrap items-center gap-2">
              <code className="font-mono text-sm break-all">{url}</code>
              <Button
                variant="outline"
                size="sm"
                disabled={busy}
                aria-label={t('settings.access.public.removeFor', { url })}
                onClick={() =>
                  void patch(
                    { public_urls: view.urls.filter((u) => u !== url) },
                    t('settings.access.public.saved')
                  )
                }
              >
                {t('settings.access.public.remove')}
              </Button>
            </li>
          ))}
        </ul>
      )}

      <form
        className="flex flex-wrap items-center gap-2"
        onSubmit={(event) => {
          event.preventDefault();
          void add();
        }}
      >
        <Input
          aria-label={t('settings.access.public.address')}
          value={draft}
          placeholder="https://meshloom.example.com"
          spellCheck={false}
          className="w-80 max-w-full font-mono"
          onChange={(event) => setDraft(event.target.value)}
        />
        <Button type="submit" variant="outline" disabled={busy || !draft.trim()}>
          {t('settings.access.public.add')}
        </Button>
      </form>

      <div className="space-y-2">
        <h4 className="text-sm font-medium">{t('settings.access.public.rejectedTitle')}</h4>
        <p className="text-[0.8125rem] text-muted-foreground">
          {t('settings.access.public.rejectedHelp')}
        </p>
        <p
          role="note"
          className="rounded-md border border-warning/50 bg-warning/10 p-3 text-[0.8125rem]"
        >
          {t('settings.access.public.rejectedRisk')}
        </p>
        {view.rejected.length === 0 ? (
          <p className="text-[0.8125rem] text-muted-foreground">
            {t('settings.access.public.rejectedNone')}
          </p>
        ) : (
          <ul className="space-y-2">
            {view.rejected.map((entry) => (
              <li key={entry.host} className="flex flex-wrap items-center gap-2">
                <code className="font-mono text-sm break-all">{entry.host}</code>
                <span className="text-xs text-muted-foreground">
                  {t('settings.access.public.seen', {
                    count: entry.count,
                    last: new Date(entry.last_seen * 1000).toLocaleString(),
                  })}
                </span>
                <Button
                  size="sm"
                  disabled={busy}
                  aria-label={t('settings.access.public.allowFor', { host: entry.host })}
                  onClick={() => setAllowing(entry)}
                >
                  {t('settings.access.public.allow')}
                </Button>
              </li>
            ))}
          </ul>
        )}
      </div>

      <div className="space-y-1.5">
        <div className="flex items-center gap-2">
          <Checkbox
            id="access-public-enforce"
            checked={enforcing}
            disabled={busy}
            onCheckedChange={(checked) =>
              void patch(
                { host_guard_mode: checked === true ? 'enforce' : 'observe' },
                t(
                  checked === true
                    ? 'settings.access.public.enforceOn'
                    : 'settings.access.public.enforceOff'
                )
              )
            }
          />
          <Label htmlFor="access-public-enforce">{t('settings.access.public.enforce')}</Label>
        </div>
        <p className="text-[0.8125rem] text-muted-foreground">
          {t(
            enforcing
              ? 'settings.access.public.enforceHelpOn'
              : 'settings.access.public.enforceHelpOff'
          )}
        </p>
      </div>
      <Dialog open={allowing !== null} onOpenChange={(next) => !next && setAllowing(null)}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>{t('settings.access.public.confirmTitle')}</DialogTitle>
            <DialogDescription>{t('settings.access.public.confirmRisk')}</DialogDescription>
          </DialogHeader>
          {allowing && (
            <div className="space-y-1">
              <code
                data-testid="allow-host"
                className="block rounded-md bg-muted p-2 font-mono text-sm break-all"
              >
                {allowing.host}
              </code>
              <p className="text-xs text-muted-foreground">
                {t('settings.access.public.seen', {
                  count: allowing.count,
                  last: new Date(allowing.last_seen * 1000).toLocaleString(),
                })}
              </p>
            </div>
          )}
          <DialogFooter>
            <Button variant="outline" onClick={() => setAllowing(null)}>
              {t('settings.access.cancel')}
            </Button>
            <Button
              variant="destructive"
              disabled={busy}
              onClick={() => {
                const target = allowing;
                setAllowing(null);
                if (!target) return;
                void patch(
                  { public_urls: [...view.urls, `https://${target.host}`] },
                  t('settings.access.public.allowed', { host: target.host })
                );
              }}
            >
              {t('settings.access.public.confirmAllow', { host: allowing?.host ?? '' })}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </SettingsGroup>
  );
}
