import { useCallback, useEffect, useState, type FormEvent } from 'react';
import { useTranslation } from 'react-i18next';

import { ApiError, api, getApiErrorCode } from '../../api';
import { formatAccessError } from '../../auth/accessErrors';
import type {
  AuthUserRow,
  OidcLinksView,
  OidcPreset,
  OidcSettings,
  OidcSettingsPatch,
  OidcTestResult,
  OidcTokenAuthMethod,
} from '../../auth/types';
import { Button } from '../ui/button';
import { Checkbox } from '../ui/checkbox';
import { Input } from '../ui/input';
import { Label } from '../ui/label';
import { toast } from '../ui/sonner';
import { SELECT_CLASS, formatEpoch } from './accessShared';
import { SettingsGroup, SettingsGroupHeader } from './settingsPrimitives';

/**
 * What a preset fills in. Only a prefill: the server stores the fields, never derives
 * anything from the preset (`docs/user/{en,fr}/deep/access.md`, "OpenID Connect").
 */
export const OIDC_PRESETS: Record<OidcPreset, { issuer: string; label: string }> = {
  generic: { issuer: '', label: '' },
  google: { issuer: 'https://accounts.google.com', label: 'Google' },
  discord: { issuer: 'https://discord.com', label: 'Discord' },
  keycloak: { issuer: '', label: 'Keycloak' },
};

const PRESET_ORDER: OidcPreset[] = ['generic', 'google', 'discord', 'keycloak'];
const AUTH_METHODS: OidcTokenAuthMethod[] = ['client_secret_basic', 'client_secret_post'];

type Draft = Omit<
  OidcSettings,
  'client_secret_set' | 'updated_at' | 'redirect_uris' | 'secret_store_available'
>;

const draftOf = (s: OidcSettings): Draft => ({
  enabled: s.enabled,
  preset: s.preset,
  label: s.label,
  issuer: s.issuer,
  client_id: s.client_id,
  scopes: s.scopes,
  token_auth_method: s.token_auth_method,
  auto_create: s.auto_create,
  allow_private_network: s.allow_private_network,
});

/** Accounts an identity may be linked to: enabled, local, not the kiosk (the server checks again). */
const linkable = (u: AuthUserRow) => u.provider === 'local' && u.role !== 'kiosk' && !u.disabled;

/**
 * Administrator only: sign-in with an OpenID Connect provider the owner brings (MSHL-427,
 * `/api/auth/oidc/*`). The provider form (the client secret is write-only), a test of the
 * saved provider, the redirect URIs to register there, and the links that let an external
 * identity sign in an existing account.
 */
export function AccessOidcPanel() {
  const { t, i18n } = useTranslation();
  const [settings, setSettings] = useState<OidcSettings | null>(null);
  const [draft, setDraft] = useState<Draft | null>(null);
  const [secret, setSecret] = useState('');
  const [busy, setBusy] = useState(false);
  const [tested, setTested] = useState<OidcTestResult | null>(null);

  useEffect(() => {
    let cancelled = false;
    api
      .getOidcSettings()
      .then((value) => {
        if (cancelled) return;
        setSettings(value);
        setDraft(draftOf(value));
      })
      .catch(() => {
        // Older server or refused: the panel stays hidden.
      });
    return () => {
      cancelled = true;
    };
  }, []);

  if (!settings || !draft) return null;

  const set = <K extends keyof Draft>(key: K, value: Draft[K]) =>
    setDraft((d) => (d ? { ...d, [key]: value } : d));

  const choosePreset = (preset: OidcPreset) => {
    const fill = OIDC_PRESETS[preset];
    setDraft((d) =>
      d
        ? {
            ...d,
            preset,
            issuer: fill.issuer || d.issuer,
            label: d.label || fill.label,
          }
        : d
    );
  };

  const unchanged =
    JSON.stringify(draft) === JSON.stringify(draftOf(settings)) && secret.trim() === '';

  const save = async (event: FormEvent) => {
    event.preventDefault();
    if (busy || unchanged) return;
    setBusy(true);
    const patch: OidcSettingsPatch = { ...draft };
    if (secret.trim()) patch.client_secret = secret.trim();
    try {
      const saved = await api.patchOidcSettings(patch);
      setSettings(saved);
      setDraft(draftOf(saved));
      setSecret('');
      setTested(null);
      toast.success(t('settings.access.oidc.saved'));
    } catch (err) {
      toast.error(formatAccessError(err, t));
    } finally {
      setBusy(false);
    }
  };

  const clearSecret = async () => {
    setBusy(true);
    try {
      const saved = await api.patchOidcSettings({ clear_client_secret: true });
      setSettings(saved);
      toast.success(t('settings.access.oidc.secretCleared'));
    } catch (err) {
      toast.error(formatAccessError(err, t));
    } finally {
      setBusy(false);
    }
  };

  const test = async () => {
    setBusy(true);
    setTested(null);
    try {
      setTested(await api.testOidc());
    } catch (err) {
      const parsed = err instanceof ApiError ? getApiErrorCode(err.detail) : null;
      const issuer =
        parsed?.code === 'auth_oidc_issuer_mismatch' ? String(parsed.params.issuer ?? '') : '';
      toast.error(
        issuer
          ? `${formatAccessError(err, t)} ${t('settings.access.oidc.announcedIssuer', { issuer })}`
          : formatAccessError(err, t)
      );
    } finally {
      setBusy(false);
    }
  };

  const text = (key: 'label' | 'issuer' | 'client_id' | 'scopes', id: string, placeholder = '') => (
    <div className="space-y-1.5">
      <Label htmlFor={id}>{t(`settings.access.oidc.${key}`)}</Label>
      <Input
        id={id}
        value={draft[key]}
        placeholder={placeholder}
        autoComplete="off"
        spellCheck={false}
        onChange={(event) => set(key, event.target.value)}
      />
    </div>
  );

  return (
    <SettingsGroup>
      <SettingsGroupHeader
        id="access-oidc"
        title={t('settings.access.oidc.title')}
        storedOn="server"
        dirty={!unchanged}
      />
      <p className="text-[0.8125rem] text-muted-foreground">{t('settings.access.oidc.help')}</p>
      <form onSubmit={save} className="space-y-3" noValidate>
        <div className="flex items-center gap-2">
          <Checkbox
            id="access-oidc-enabled"
            checked={draft.enabled}
            onCheckedChange={(checked) => set('enabled', checked === true)}
          />
          <Label htmlFor="access-oidc-enabled">{t('settings.access.oidc.enabled')}</Label>
        </div>
        <div className="space-y-1.5">
          <Label htmlFor="access-oidc-preset">{t('settings.access.oidc.preset')}</Label>
          <select
            id="access-oidc-preset"
            className={SELECT_CLASS}
            value={draft.preset}
            onChange={(event) => choosePreset(event.target.value as OidcPreset)}
          >
            {PRESET_ORDER.map((preset) => (
              <option key={preset} value={preset}>
                {t(`settings.access.oidc.presets.${preset}`)}
              </option>
            ))}
          </select>
          <p className="text-[0.8125rem] text-muted-foreground">
            {t(`settings.access.oidc.presetHelp.${draft.preset}`)}
          </p>
        </div>
        {text('label', 'access-oidc-label', t('auth.oidc.defaultLabel'))}
        {text(
          'issuer',
          'access-oidc-issuer',
          draft.preset === 'keycloak' ? 'https://keycloak.example/realms/home' : 'https://'
        )}
        {text('client_id', 'access-oidc-client-id')}
        <div className="space-y-1.5">
          <Label htmlFor="access-oidc-secret">{t('settings.access.oidc.clientSecret')}</Label>
          <div className="flex flex-wrap items-center gap-2">
            <Input
              id="access-oidc-secret"
              type="password"
              autoComplete="new-password"
              className="max-w-sm"
              value={secret}
              placeholder={settings.client_secret_set ? t('settings.access.oidc.secretKept') : ''}
              onChange={(event) => setSecret(event.target.value)}
            />
            {settings.client_secret_set && (
              <Button
                type="button"
                variant="outline"
                disabled={busy}
                onClick={() => void clearSecret()}
              >
                {t('settings.access.oidc.clearSecret')}
              </Button>
            )}
          </div>
          {!settings.secret_store_available && (
            <p role="alert" className="text-sm text-destructive">
              {t('errors.auth_oidc_secret_store_unavailable')}
            </p>
          )}
        </div>
        {text('scopes', 'access-oidc-scopes')}
        <div className="space-y-1.5">
          <Label htmlFor="access-oidc-auth-method">{t('settings.access.oidc.authMethod')}</Label>
          <select
            id="access-oidc-auth-method"
            className={SELECT_CLASS}
            value={draft.token_auth_method}
            onChange={(event) =>
              set('token_auth_method', event.target.value as OidcTokenAuthMethod)
            }
          >
            {AUTH_METHODS.map((method) => (
              <option key={method} value={method}>
                {t(`settings.access.oidc.authMethods.${method}`)}
              </option>
            ))}
          </select>
        </div>
        <div className="flex items-start gap-2">
          <Checkbox
            id="access-oidc-private"
            className="mt-0.5"
            checked={draft.allow_private_network}
            onCheckedChange={(checked) => set('allow_private_network', checked === true)}
          />
          <div className="space-y-0.5">
            <Label htmlFor="access-oidc-private">{t('settings.access.oidc.privateNetwork')}</Label>
            <p className="text-[0.8125rem] text-muted-foreground">
              {t('settings.access.oidc.privateNetworkHelp')}
            </p>
          </div>
        </div>
        <div className="flex items-start gap-2">
          <Checkbox
            id="access-oidc-auto-create"
            className="mt-0.5"
            checked={draft.auto_create}
            onCheckedChange={(checked) => set('auto_create', checked === true)}
          />
          <div className="space-y-0.5">
            <Label htmlFor="access-oidc-auto-create">{t('settings.access.oidc.autoCreate')}</Label>
            <p className="text-[0.8125rem] text-muted-foreground">
              {t('settings.access.oidc.autoCreateHelp')}
            </p>
          </div>
        </div>
        <div className="space-y-1">
          <p className="text-sm font-medium">{t('settings.access.oidc.redirectUris')}</p>
          <p className="text-[0.8125rem] text-muted-foreground">
            {t('settings.access.oidc.redirectUrisHelp')}
          </p>
          <ul className="space-y-0.5" data-testid="oidc-redirect-uris">
            {settings.redirect_uris.map((uri) => (
              <li key={uri}>
                <code className="break-all text-xs">{uri}</code>
              </li>
            ))}
          </ul>
        </div>
        <div className="flex flex-wrap gap-2">
          <Button type="submit" disabled={busy || unchanged}>
            {t('settings.access.oidc.save')}
          </Button>
          <Button
            type="button"
            variant="outline"
            disabled={busy || !settings.issuer}
            onClick={() => void test()}
          >
            {t('settings.access.oidc.test')}
          </Button>
        </div>
        {tested && (
          <p role="status" className="text-sm text-muted-foreground" data-testid="oidc-test-ok">
            {t('settings.access.oidc.testOk', { issuer: tested.issuer, keys: tested.keys })}
          </p>
        )}
      </form>
      <OidcLinksSection issuer={settings.issuer} language={i18n.language} />
    </SettingsGroup>
  );
}

function OidcLinksSection({ issuer, language }: { issuer: string; language: string }) {
  const { t } = useTranslation();
  const [view, setView] = useState<OidcLinksView | null>(null);
  const [users, setUsers] = useState<AuthUserRow[]>([]);
  const [userId, setUserId] = useState<number | null>(null);
  const [kind, setKind] = useState<'email' | 'subject'>('email');
  const [value, setValue] = useState('');
  const [busy, setBusy] = useState(false);

  const reload = useCallback(async () => {
    try {
      setView(await api.listOidcLinks());
    } catch {
      // Refused or older server: the list stays hidden.
    }
  }, []);

  useEffect(() => {
    let cancelled = false;
    api
      .listOidcLinks()
      .then((value) => {
        if (!cancelled) setView(value);
      })
      .catch(() => {
        // Refused or older server: the list stays hidden.
      });
    api
      .listAuthUsers()
      .then((rows) => {
        if (cancelled) return;
        const usable = rows.filter(linkable);
        setUsers(usable);
        setUserId((current) => current ?? usable[0]?.id ?? null);
      })
      .catch(() => {
        if (!cancelled) setUsers([]);
      });
    return () => {
      cancelled = true;
    };
  }, [issuer]);

  if (!view) return null;

  const add = async (event: FormEvent) => {
    event.preventDefault();
    if (busy || userId === null || !value.trim()) return;
    setBusy(true);
    try {
      await api.addOidcLink(
        kind === 'email'
          ? { user_id: userId, email: value.trim() }
          : { user_id: userId, subject: value.trim() }
      );
      setValue('');
      await reload();
      toast.success(t('settings.access.oidc.links.added'));
    } catch (err) {
      toast.error(formatAccessError(err, t));
    } finally {
      setBusy(false);
    }
  };

  const remove = async (id: number) => {
    setBusy(true);
    try {
      await api.deleteOidcLink(id);
      await reload();
    } catch (err) {
      toast.error(formatAccessError(err, t));
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="space-y-2 border-t border-border pt-3" data-testid="oidc-links">
      <p className="text-sm font-medium">{t('settings.access.oidc.links.title')}</p>
      <p className="text-[0.8125rem] text-muted-foreground">
        {t('settings.access.oidc.links.help')}
      </p>
      {view.links.length === 0 ? (
        <p className="text-[0.8125rem] text-muted-foreground">
          {t('settings.access.oidc.links.empty')}
        </p>
      ) : (
        <ul className="space-y-1">
          {view.links.map((link) => (
            <li
              key={link.id}
              className="flex flex-wrap items-center justify-between gap-2 text-sm"
              data-testid={`oidc-link-${link.id}`}
            >
              <span className="min-w-0 break-all">
                <span className="font-medium">{link.username || `#${link.user_id}`}</span>
                {' · '}
                {link.email ?? link.subject}
                {link.issuer !== view.issuer && (
                  <span className="ml-1 text-xs text-destructive">
                    {t('settings.access.oidc.links.otherIssuer')}
                  </span>
                )}
                <span className="ml-1 text-xs text-muted-foreground">
                  {link.last_login_at
                    ? t('settings.access.oidc.links.lastLogin', {
                        when: formatEpoch(link.last_login_at, language),
                      })
                    : link.subject
                      ? t('settings.access.oidc.links.neverUsed')
                      : t('settings.access.oidc.links.waitingFirst')}
                </span>
              </span>
              <Button
                type="button"
                variant="outline"
                size="sm"
                disabled={busy}
                onClick={() => void remove(link.id)}
              >
                {t('settings.access.oidc.links.remove')}
              </Button>
            </li>
          ))}
        </ul>
      )}
      {issuer && users.length > 0 && (
        <form onSubmit={add} className="flex flex-wrap items-end gap-2" noValidate>
          <div className="space-y-1.5">
            <Label htmlFor="access-oidc-link-user">{t('settings.access.oidc.links.account')}</Label>
            <select
              id="access-oidc-link-user"
              className={SELECT_CLASS}
              value={userId ?? ''}
              onChange={(event) => setUserId(Number(event.target.value))}
            >
              {users.map((u) => (
                <option key={u.id} value={u.id}>
                  {u.username}
                </option>
              ))}
            </select>
          </div>
          <div className="space-y-1.5">
            <Label htmlFor="access-oidc-link-kind">{t('settings.access.oidc.links.kind')}</Label>
            <select
              id="access-oidc-link-kind"
              className={SELECT_CLASS}
              value={kind}
              onChange={(event) => setKind(event.target.value as 'email' | 'subject')}
            >
              <option value="email">{t('settings.access.oidc.links.byEmail')}</option>
              <option value="subject">{t('settings.access.oidc.links.bySubject')}</option>
            </select>
          </div>
          <div className="space-y-1.5">
            <Label htmlFor="access-oidc-link-value">
              {t(
                kind === 'email'
                  ? 'settings.access.oidc.links.email'
                  : 'settings.access.oidc.links.subject'
              )}
            </Label>
            <Input
              id="access-oidc-link-value"
              value={value}
              autoComplete="off"
              spellCheck={false}
              onChange={(event) => setValue(event.target.value)}
            />
          </div>
          <Button type="submit" disabled={busy || userId === null || !value.trim()}>
            {t('settings.access.oidc.links.add')}
          </Button>
        </form>
      )}
    </div>
  );
}
