import { useState, type FormEvent } from 'react';
import { useTranslation } from 'react-i18next';

import { api } from '../../../api';
import { formatAccessError } from '../../../auth/accessErrors';
import { Button } from '../../ui/button';
import { Input } from '../../ui/input';
import { Label } from '../../ui/label';
import { SettingsGroup, SettingsGroupHeader } from '../settingsPrimitives';
import { untrustedText } from './remoteShared';
import { REMOTE_DOMAIN_RESULTS, type RemoteDomainTest } from './remoteTypes';

/**
 * Custom domain test (`POST /api/remote/domain/test`, docs/dev/remote.md, "Custom
 * domain"). A personal domain cannot be a CNAME of the relay (Cloudflare error 1014);
 * the user sets a URL redirect to the instance's `<slug>.meshloom.link` at their
 * registrar or DNS host, and this checks it from the instance side: one request to the
 * typed domain, through the egress guard, redirect not followed. Administrators only
 * (the server refuses anyone else). The host the domain redirects to is untrusted
 * text. The domain is not stored: nothing here is a setting.
 */
export function RemoteDomainPanel({ hostname }: { hostname: string }) {
  const { t } = useTranslation();
  const [domain, setDomain] = useState('');
  const [busy, setBusy] = useState(false);
  const [result, setResult] = useState<RemoteDomainTest | null>(null);
  const [error, setError] = useState<string | null>(null);

  const submit = async (event: FormEvent) => {
    event.preventDefault();
    if (!domain.trim()) return;
    setBusy(true);
    setError(null);
    setResult(null);
    try {
      setResult(await api.testRemoteDomain(domain.trim()));
    } catch (err) {
      setError(formatAccessError(err, t));
    } finally {
      setBusy(false);
    }
  };

  const known = result !== null && REMOTE_DOMAIN_RESULTS.includes(result.result);
  const key = known ? result.result : 'unreachable';
  const ok = known && result.result === 'ok';

  return (
    <SettingsGroup>
      <SettingsGroupHeader
        id="remote-domain"
        title={t('settings.remote.domain.title')}
        storedOn="server"
      />
      <p className="text-[0.8125rem] text-muted-foreground">
        {t('settings.remote.domain.help', { hostname })}
      </p>
      <form onSubmit={submit} className="flex flex-wrap items-end gap-3">
        <div className="min-w-[14rem] flex-1 space-y-1">
          <Label htmlFor="remote-domain-input">{t('settings.remote.domain.label')}</Label>
          <Input
            id="remote-domain-input"
            dir="ltr"
            value={domain}
            maxLength={300}
            autoComplete="off"
            autoCapitalize="none"
            spellCheck={false}
            placeholder="chat.example.org"
            onChange={(event) => setDomain(event.target.value)}
          />
        </div>
        <Button type="submit" disabled={busy || !domain.trim()}>
          {busy ? t('settings.remote.domain.testing') : t('settings.remote.domain.test')}
        </Button>
      </form>
      {error && (
        <p role="alert" className="text-sm text-destructive">
          {error}
        </p>
      )}
      {result && (
        <div
          role="status"
          data-testid="remote-domain-result"
          data-result={key}
          className={`space-y-1 rounded-md border p-3 text-[0.8125rem] ${
            ok ? 'border-emerald-500/50 bg-emerald-500/10' : 'border-border bg-muted/40'
          }`}
        >
          <p className="font-medium">{t(`settings.remote.domain.result.${key}.title`)}</p>
          <p>
            {t(`settings.remote.domain.result.${key}.help`, {
              hostname,
              target: untrustedText(result.target_host ?? '', 120),
              status: result.status_code ?? '',
            })}
          </p>
          {key === 'unreachable' && result.detail && (
            <p className="text-muted-foreground">
              {t(`settings.remote.domain.reason.${result.detail}`, {
                defaultValue: t('settings.remote.domain.reason.connect_error'),
              })}
            </p>
          )}
        </div>
      )}
    </SettingsGroup>
  );
}
