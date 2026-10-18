import { useEffect, useMemo, useState } from 'react';
import { useTranslation } from 'react-i18next';

import { api } from '../../api';
import type { AuthSettings } from '../../auth/types';
import { Button } from '../ui/button';
import { toast } from '../ui/sonner';
import { cn } from '../../lib/utils';
import { AdvancedBlock, SettingsGroupHeader } from './settingsPrimitives';

const IPV4 = /^(\d{1,3})\.(\d{1,3})\.(\d{1,3})\.(\d{1,3})$/;

function isIpv6(text: string): boolean {
  if (!text.includes(':') || /[^0-9a-fA-F:.]/.test(text)) return false;
  try {
    new URL(`http://[${text}]/`);
    return true;
  } catch {
    return false;
  }
}

/**
 * Whether `raw` is an IP address or a CIDR network.
 *
 * Mirrors `normalize_proxy` in `app/middleware/proxy_headers.py`, which has the final
 * say and stores the canonical network form (`10.0.0.1` becomes `10.0.0.1/32`). A
 * network covering every address (`/0`) is refused.
 */
export function isProxyEntry(raw: string): boolean {
  const parts = raw.trim().split('/');
  if (parts.length > 2 || !parts[0]) return false;
  const [address, prefix] = parts;
  let bits: number;
  const v4 = IPV4.exec(address);
  if (v4) {
    if (v4.slice(1).some((octet) => Number(octet) > 255)) return false;
    bits = 32;
  } else if (isIpv6(address)) {
    bits = 128;
  } else {
    return false;
  }
  if (prefix === undefined) return true;
  if (!/^\d{1,3}$/.test(prefix)) return false;
  const length = Number(prefix);
  return length > 0 && length <= bits;
}

function parseProxies(text: string): { proxies: string[]; invalid: string[] } {
  const proxies: string[] = [];
  const invalid: string[] = [];
  for (const line of text.split('\n')) {
    const entry = line.trim();
    if (!entry) continue;
    if (!isProxyEntry(entry)) invalid.push(entry);
    else if (!proxies.includes(entry)) proxies.push(entry);
  }
  return { proxies, invalid };
}

/**
 * Settings → Local Configuration → Trusted proxies: the reverse proxies whose
 * `X-Forwarded-For` and `X-Forwarded-Proto` Meshloom believes. Empty trusts none, and
 * the client address is then always the TCP peer's.
 */
export function TrustedProxiesSettings({
  load = api.getAuthSettings,
  save: saveSettings = api.patchAuthSettings,
}: {
  load?: () => Promise<AuthSettings>;
  save?: (patch: Partial<AuthSettings>) => Promise<AuthSettings>;
}) {
  const { t } = useTranslation();
  const [saved, setSaved] = useState<string[] | null>(null);
  const [draft, setDraft] = useState('');
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    let cancelled = false;
    load()
      .then((settings) => {
        if (cancelled) return;
        setSaved(settings.trusted_proxies);
        setDraft(settings.trusted_proxies.join('\n'));
      })
      .catch(() => {
        // Not an admin, or the server is unreachable: the field stays hidden.
      });
    return () => {
      cancelled = true;
    };
  }, [load]);

  const parsed = useMemo(() => parseProxies(draft), [draft]);
  if (saved === null) return null;

  const dirty = parsed.proxies.join('\n') !== saved.join('\n');
  const invalid = parsed.invalid.length > 0;

  const save = async () => {
    setSaving(true);
    try {
      const settings = await saveSettings({ trusted_proxies: parsed.proxies });
      setSaved(settings.trusted_proxies);
      setDraft(settings.trusted_proxies.join('\n'));
      toast.success(t('settings.trustedProxies.saved'));
    } catch (err) {
      toast.error(t('settings.trustedProxies.saveFailed'), {
        description: err instanceof Error ? err.message : undefined,
      });
    } finally {
      setSaving(false);
    }
  };

  return (
    <AdvancedBlock title={t('settings.trustedProxies.title')}>
      <SettingsGroupHeader
        id="trusted-proxies-label"
        title={t('settings.trustedProxies.label')}
        storedOn="server"
        dirty={dirty}
      />
      <p className="text-[0.8125rem] text-muted-foreground">{t('settings.trustedProxies.help')}</p>
      <div className="space-y-2">
        <textarea
          id="trusted-proxies"
          aria-labelledby="trusted-proxies-label"
          value={draft}
          onChange={(event) => setDraft(event.target.value)}
          spellCheck={false}
          rows={3}
          placeholder="127.0.0.1"
          aria-invalid={invalid}
          aria-describedby={invalid ? 'trusted-proxies-error' : undefined}
          className={cn(
            'w-full rounded-md border bg-background px-3 py-2 text-sm font-mono',
            invalid ? 'border-destructive' : 'border-input'
          )}
        />
        {invalid ? (
          <p id="trusted-proxies-error" className="text-xs text-destructive" role="alert">
            {t('settings.trustedProxies.invalid', { entries: parsed.invalid.join(', ') })}
          </p>
        ) : (
          parsed.proxies.length === 0 && (
            <p className="text-xs text-muted-foreground">{t('settings.trustedProxies.none')}</p>
          )
        )}
      </div>
      <Button onClick={() => void save()} disabled={!dirty || invalid || saving}>
        {saving ? t('settings.trustedProxies.saving') : t('settings.trustedProxies.save')}
      </Button>
    </AdvancedBlock>
  );
}
