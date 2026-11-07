import { useMemo, useState } from 'react';
import { useTranslation } from 'react-i18next';

import { Button } from '../ui/button';
import { toast } from '../ui/sonner';
import { cn } from '../../lib/utils';
import { AdvancedBlock, SettingsGroupHeader } from './settingsPrimitives';

/**
 * The form a browser sends in `Origin`, or null when `raw` is not one.
 *
 * Mirrors `normalize_origin` in `app/origin_policy.py`, which has the final say: http or
 * https, no path, query, fragment, credentials or wildcard.
 */
export function normalizeOrigin(raw: string): string | null {
  const text = raw.trim().replace(/\/+$/, '');
  if (!text || text.includes('*')) return null;
  let url: URL;
  try {
    url = new URL(text);
  } catch {
    return null;
  }
  if (url.protocol !== 'http:' && url.protocol !== 'https:') return null;
  if (url.username || url.password || url.search || url.hash) return null;
  if (url.pathname !== '/' || !url.hostname) return null;
  // new URL() keeps an empty "?" or "#" out of search/hash: refuse them here.
  if (/[?#]/.test(text)) return null;
  return url.origin;
}

function parseOrigins(text: string): { origins: string[]; invalid: string[] } {
  const origins: string[] = [];
  const invalid: string[] = [];
  for (const line of text.split('\n')) {
    if (!line.trim()) continue;
    const origin = normalizeOrigin(line);
    if (origin === null) invalid.push(line.trim());
    else if (!origins.includes(origin)) origins.push(origin);
  }
  return { origins, invalid };
}

/**
 * Settings → Local Configuration → Allowed origins: the other sites whose browser
 * requests may call this instance with credentials (CORS). Empty is same-origin
 * only, which is all the web app itself needs.
 */
export function AllowedOriginsSettings({
  origins,
  onSave,
}: {
  origins: string[];
  onSave: (origins: string[]) => Promise<void>;
}) {
  const { t } = useTranslation();
  const savedText = origins.join('\n');
  const [draft, setDraft] = useState(savedText);
  const [saving, setSaving] = useState(false);

  // A list saved elsewhere (another tab, the API) replaces the draft.
  const [shownSaved, setShownSaved] = useState(savedText);
  if (shownSaved !== savedText) {
    setShownSaved(savedText);
    setDraft(savedText);
  }

  const parsed = useMemo(() => parseOrigins(draft), [draft]);
  const dirty = parsed.origins.join('\n') !== savedText;
  const invalid = parsed.invalid.length > 0;

  const save = async () => {
    setSaving(true);
    try {
      await onSave(parsed.origins);
      setDraft(parsed.origins.join('\n'));
      toast.success(t('settings.allowedOrigins.saved'));
    } catch (err) {
      toast.error(t('settings.allowedOrigins.saveFailed'), {
        description: err instanceof Error ? err.message : undefined,
      });
    } finally {
      setSaving(false);
    }
  };

  return (
    <AdvancedBlock title={t('settings.allowedOrigins.title')}>
      <SettingsGroupHeader
        id="allowed-origins-label"
        title={t('settings.allowedOrigins.label')}
        storedOn="server"
        dirty={dirty}
      />
      <p className="text-[0.8125rem] text-muted-foreground">{t('settings.allowedOrigins.help')}</p>
      <div className="space-y-2">
        <textarea
          id="allowed-origins"
          aria-labelledby="allowed-origins-label"
          value={draft}
          onChange={(event) => setDraft(event.target.value)}
          spellCheck={false}
          rows={4}
          placeholder="https://dashboard.example.org"
          aria-invalid={invalid}
          aria-describedby={invalid ? 'allowed-origins-error' : undefined}
          className={cn(
            'w-full rounded-md border bg-background px-3 py-2 text-sm font-mono',
            invalid ? 'border-destructive' : 'border-input'
          )}
        />
        {invalid ? (
          <p id="allowed-origins-error" className="text-xs text-destructive" role="alert">
            {t('settings.allowedOrigins.invalid', { origins: parsed.invalid.join(', ') })}
          </p>
        ) : (
          parsed.origins.length === 0 && (
            <p className="text-xs text-muted-foreground">
              {t('settings.allowedOrigins.sameOriginOnly')}
            </p>
          )
        )}
      </div>
      <Button onClick={() => void save()} disabled={!dirty || invalid || saving}>
        {saving ? t('settings.allowedOrigins.saving') : t('settings.allowedOrigins.save')}
      </Button>
    </AdvancedBlock>
  );
}
