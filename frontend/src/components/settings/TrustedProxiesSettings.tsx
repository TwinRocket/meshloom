import { useEffect, useMemo, useState } from 'react';
import { useTranslation } from 'react-i18next';

import { api, formatApiError } from '../../api';
import { useSession } from '../../auth/SessionProvider';
import { behindUntrustedHttpsProxy, type AuthSettings } from '../../auth/types';
import { Button } from '../ui/button';
import { toast } from '../ui/sonner';
import { cn } from '../../lib/utils';
import { AdvancedBlock, SettingsGroupHeader } from './settingsPrimitives';

// Mirrors MIN_PREFIX in app/middleware/proxy_headers.py.
const MIN_PREFIX = { 4: 8, 6: 16 } as const;

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

interface ProxyEntry {
  version: 4 | 6;
  octets: number[] | null;
  /** The prefix length, the full address length when none was typed. */
  prefix: number;
}

function parseEntry(raw: string): ProxyEntry | null {
  const parts = raw.trim().split('/');
  if (parts.length > 2 || !parts[0]) return null;
  const [address, prefix] = parts;
  let version: 4 | 6;
  let octets: number[] | null = null;
  const v4 = IPV4.exec(address);
  if (v4) {
    octets = v4.slice(1).map(Number);
    if (octets.some((octet) => octet > 255)) return null;
    version = 4;
  } else if (isIpv6(address) && !/^::ffff:/i.test(address)) {
    version = 6;
  } else {
    return null;
  }
  const bits = version === 4 ? 32 : 128;
  if (prefix === undefined) return { version, octets, prefix: bits };
  if (!/^\d{1,3}$/.test(prefix)) return null;
  const length = Number(prefix);
  if (length < MIN_PREFIX[version] || length > bits) return null;
  return { version, octets, prefix: length };
}

/**
 * Whether `raw` is an IP address or a CIDR network Meshloom accepts.
 *
 * Mirrors `normalize_proxy` in `app/middleware/proxy_headers.py`, which has the final
 * say and stores the canonical network form (`10.0.0.1` becomes `10.0.0.1/32`):
 * nothing wider than /8 (IPv4) or /16 (IPv6), no IPv4-mapped IPv6 form.
 */
export function isProxyEntry(raw: string): boolean {
  return parseEntry(raw) !== null;
}

/**
 * Why an accepted entry is risky, or null. A whole network (or a Docker bridge
 * gateway such as `172.17.0.1`, or Docker Desktop's `192.168.65.x`) also covers
 * clients that reach a published port through the gateway: they could then forge
 * `X-Forwarded-For`. Only the proxy's own address is safe.
 */
export function proxyEntryRisk(raw: string): 'network' | 'gateway' | null {
  const entry = parseEntry(raw);
  if (!entry) return null;
  if (entry.prefix < (entry.version === 4 ? 32 : 128)) return 'network';
  const octets = entry.octets;
  if (!octets) return null;
  const dockerBridge = octets[0] === 172 && octets[1] >= 16 && octets[1] <= 31;
  if (dockerBridge && octets[2] === 0 && octets[3] === 1) return 'gateway';
  if (octets[0] === 192 && octets[1] === 168 && octets[2] === 65) return 'gateway';
  return null;
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
  const { session } = useSession();
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
  const risky = parsed.proxies.filter((entry) => proxyEntryRisk(entry) !== null);

  const save = async () => {
    setSaving(true);
    try {
      const settings = await saveSettings({ trusted_proxies: parsed.proxies });
      setSaved(settings.trusted_proxies);
      setDraft(settings.trusted_proxies.join('\n'));
      toast.success(t('settings.trustedProxies.saved'));
    } catch (err) {
      toast.error(t('settings.trustedProxies.saveFailed'), {
        description: formatApiError(err, t),
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
      {behindUntrustedHttpsProxy(session) && (
        <p
          role="note"
          className="rounded-md border border-warning/50 bg-warning/10 p-3 text-[0.8125rem]"
        >
          {t('settings.trustedProxies.httpsNotTrusted')}
        </p>
      )}
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
        {risky.length > 0 && (
          <p className="text-xs text-warning" data-testid="trusted-proxies-risk">
            {t('settings.trustedProxies.broadEntry', { entries: risky.join(', ') })}
          </p>
        )}
      </div>
      <Button onClick={() => void save()} disabled={!dirty || invalid || saving}>
        {saving ? t('settings.trustedProxies.saving') : t('settings.trustedProxies.save')}
      </Button>
    </AdvancedBlock>
  );
}
