import { useEffect, useState, type FormEvent } from 'react';
import { useTranslation } from 'react-i18next';

import { api } from '../../api';
import { formatAccessError } from '../../auth/accessErrors';
import { capabilitiesOfRole, type Capability } from '../../auth/capabilities';
import { useSession } from '../../auth/SessionProvider';
import type { AuthRole, AuthTokenCreated, AuthTokenRow } from '../../auth/types';
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
import { AccessConfirmDialog } from './AccessConfirmDialog';
import { SELECT_CLASS, formatEpoch } from './accessShared';
import { SettingsGroup, SettingsGroupHeader } from './settingsPrimitives';

/** Roles that may hold tokens (`TOKEN_ROLES`, `app/auth/providers/token.py`). */
const TOKEN_ROLES: readonly AuthRole[] = ['admin', 'member'];
const TOKEN_NAME_MAX_LENGTH = 64;
const SCOPE_ORDER: readonly Capability[] = [
  'read',
  'read_private',
  'send',
  'repeater_admin',
  'admin',
];
const DAY_S = 86400;
const EXPIRY_PRESETS = [7, 30, 90, 365] as const;

/** The scopes a token of this role may hold: the role's own permissions. */
export function tokenScopesForRole(role: AuthRole | null): Capability[] {
  const granted = role ? capabilitiesOfRole(role) : new Set<Capability>();
  return SCOPE_ORDER.filter((scope) => granted.has(scope));
}

/** `YYYY-MM-DD` of a local date, the format of `<input type="date">`. */
function dateInputValue(date: Date): string {
  const pad = (n: number) => String(n).padStart(2, '0');
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}`;
}

/** Epoch seconds of the end of that local day (a chosen date is the last day of use). */
function endOfDay(value: string): number | null {
  const [y, m, d] = value.split('-').map(Number);
  if (!y || !m || !d) return null;
  return Math.floor(new Date(y, m - 1, d, 23, 59, 59).getTime() / 1000);
}

type Expiry = 'never' | (typeof EXPIRY_PRESETS)[number] | 'custom';

function CreateTokenDialog({
  open,
  scopes,
  onClose,
  onCreated,
}: {
  open: boolean;
  scopes: readonly Capability[];
  onClose: () => void;
  onCreated: (token: AuthTokenCreated) => void;
}) {
  const { t } = useTranslation();
  const [name, setName] = useState('');
  const [chosen, setChosen] = useState<string[]>([]);
  const [expiry, setExpiry] = useState<Expiry>('never');
  const [date, setDate] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // The dialog stays mounted: close it through here so nothing typed lingers.
  const close = () => {
    setName('');
    setChosen([]);
    setExpiry('never');
    setDate('');
    setError(null);
    onClose();
  };

  // Computed when the dialog is first built: a dialog left open past midnight is rare, and
  // the server refuses a past expiry anyway (`auth_token_expiry_invalid`).
  const [tomorrow] = useState(() => dateInputValue(new Date(Date.now() + DAY_S * 1000)));
  const expiresAtFor = (): number | null | undefined => {
    if (expiry === 'never') return null;
    if (expiry === 'custom') return date ? endOfDay(date) : undefined;
    return Math.floor(Date.now() / 1000) + expiry * DAY_S;
  };
  const ready =
    name.trim().length > 0 &&
    chosen.length > 0 &&
    (expiry !== 'custom' || (date !== '' && date >= tomorrow));

  const submit = async (event: FormEvent) => {
    event.preventDefault();
    const expiresAt = expiresAtFor();
    if (!ready || busy || expiresAt === undefined) return;
    setBusy(true);
    setError(null);
    try {
      const created = await api.createAuthToken({
        name: name.trim(),
        scopes: SCOPE_ORDER.filter((scope) => chosen.includes(scope)),
        expires_at: expiresAt,
      });
      close();
      onCreated(created);
    } catch (err) {
      setError(formatAccessError(err, t));
    } finally {
      setBusy(false);
    }
  };

  return (
    <Dialog open={open} onOpenChange={(next) => !next && close()}>
      <DialogContent>
        <form onSubmit={submit} className="space-y-4">
          <DialogHeader>
            <DialogTitle>{t('settings.access.tokens.createTitle')}</DialogTitle>
            <DialogDescription>{t('settings.access.tokens.createHelp')}</DialogDescription>
          </DialogHeader>
          <div className="space-y-1.5">
            <Label htmlFor="access-token-name">{t('settings.access.tokens.name')}</Label>
            <Input
              id="access-token-name"
              autoComplete="off"
              maxLength={TOKEN_NAME_MAX_LENGTH}
              placeholder={t('settings.access.tokens.namePlaceholder')}
              value={name}
              onChange={(event) => setName(event.target.value)}
            />
          </div>
          <fieldset className="space-y-2">
            <legend className="text-sm font-medium">{t('settings.access.tokens.scopes')}</legend>
            <p className="text-[0.8125rem] text-muted-foreground">
              {t('settings.access.tokens.scopesHelp')}
            </p>
            {scopes.map((scope) => (
              <div key={scope} className="flex items-start gap-2">
                <Checkbox
                  id={`access-token-scope-${scope}`}
                  className="mt-0.5"
                  aria-describedby={`access-token-scope-${scope}-help`}
                  checked={chosen.includes(scope)}
                  onCheckedChange={(checked) =>
                    setChosen((list) =>
                      checked === true ? [...list, scope] : list.filter((s) => s !== scope)
                    )
                  }
                />
                <div>
                  <Label htmlFor={`access-token-scope-${scope}`} className="font-mono">
                    {scope}
                  </Label>
                  <p
                    id={`access-token-scope-${scope}-help`}
                    className="text-[0.8125rem] text-muted-foreground"
                  >
                    {t(`settings.access.tokens.scope.${scope}`)}
                  </p>
                </div>
              </div>
            ))}
          </fieldset>
          <div className="space-y-1.5">
            <Label htmlFor="access-token-expiry">{t('settings.access.tokens.expiry')}</Label>
            <select
              id="access-token-expiry"
              className={`${SELECT_CLASS} w-full`}
              value={String(expiry)}
              onChange={(event) => {
                const value = event.target.value;
                setExpiry(
                  value === 'never' || value === 'custom' ? value : (Number(value) as Expiry)
                );
              }}
            >
              <option value="never">{t('settings.access.tokens.expiryNever')}</option>
              {EXPIRY_PRESETS.map((days) => (
                <option key={days} value={days}>
                  {t('settings.access.tokens.expiryDays', { count: days })}
                </option>
              ))}
              <option value="custom">{t('settings.access.tokens.expiryCustom')}</option>
            </select>
            {expiry === 'custom' && (
              <Input
                type="date"
                aria-label={t('settings.access.tokens.expiryDate')}
                min={tomorrow}
                value={date}
                onChange={(event) => setDate(event.target.value)}
              />
            )}
          </div>
          {error && (
            <p role="alert" className="text-sm text-destructive">
              {error}
            </p>
          )}
          <DialogFooter>
            <Button type="button" variant="outline" onClick={close}>
              {t('settings.access.cancel')}
            </Button>
            <Button type="submit" disabled={!ready || busy}>
              {t('settings.access.tokens.create')}
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}

/** The secret, once. Closing only through the button: a stray click must not lose it. */
function TokenSecretDialog({
  token,
  onClose,
}: {
  token: AuthTokenCreated | null;
  onClose: () => void;
}) {
  const { t } = useTranslation();
  const [copied, setCopied] = useState(false);
  // `navigator.clipboard` is absent over plain HTTP on a LAN: the field stays selectable.
  const canCopy = typeof navigator !== 'undefined' && !!navigator.clipboard;

  const copy = async () => {
    if (!token) return;
    try {
      await navigator.clipboard.writeText(token.token);
      setCopied(true);
    } catch {
      toast.error(t('settings.access.tokens.copyFailed'));
    }
  };

  const close = () => {
    setCopied(false);
    onClose();
  };

  return (
    <Dialog open={token !== null} onOpenChange={(next) => !next && close()}>
      <DialogContent
        hideCloseButton
        onInteractOutside={(event) => event.preventDefault()}
        onEscapeKeyDown={(event) => event.preventDefault()}
      >
        <DialogHeader>
          <DialogTitle>{t('settings.access.tokens.secretTitle')}</DialogTitle>
          <DialogDescription>
            {t('settings.access.tokens.secretHelp', { name: token?.name ?? '' })}
          </DialogDescription>
        </DialogHeader>
        <p
          role="alert"
          className="rounded-md border border-amber-500/50 bg-amber-500/10 p-3 text-sm font-medium"
        >
          {t('settings.access.tokens.secretWarning')}
        </p>
        <div className="space-y-1.5">
          <Label htmlFor="access-token-secret">{t('settings.access.tokens.secretLabel')}</Label>
          <div className="flex gap-2">
            <Input
              id="access-token-secret"
              readOnly
              autoComplete="off"
              spellCheck={false}
              className="font-mono"
              value={token?.token ?? ''}
              onFocus={(event) => event.currentTarget.select()}
            />
            {canCopy && (
              <Button type="button" variant="outline" onClick={() => void copy()}>
                {copied ? t('settings.access.tokens.copied') : t('settings.access.tokens.copy')}
              </Button>
            )}
          </div>
          <p className="text-[0.8125rem] text-muted-foreground" aria-live="polite">
            {t('settings.access.tokens.secretUsage')}
          </p>
        </div>
        <DialogFooter>
          <Button onClick={close}>{t('settings.access.tokens.secretDone')}</Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

/**
 * Settings → Users & Access → API and device tokens. A member or an administrator creates
 * tokens for their own account; an administrator also sees and revokes everyone's. The
 * server checks the scopes against the role (`auth_token_scope`); the form only offers
 * what would be accepted.
 */
export function AccessTokensPanel() {
  const { t, i18n } = useTranslation();
  const { role, via } = useSession();
  const [rows, setRows] = useState<AuthTokenRow[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [creating, setCreating] = useState(false);
  const [secret, setSecret] = useState<AuthTokenCreated | null>(null);
  const [revoking, setRevoking] = useState<AuthTokenRow | null>(null);
  const [version, setVersion] = useState(0);
  // Read when the list loads: the "expired" badge needs a clock, and render must stay pure.
  const [nowS, setNowS] = useState(0);
  const reload = () => setVersion((value) => value + 1);

  const isAdmin = role === 'admin';
  const scopes = tokenScopesForRole(role);
  const canHold = role !== null && TOKEN_ROLES.includes(role);

  useEffect(() => {
    if (!canHold) return;
    let cancelled = false;
    api
      .listAuthTokens()
      .then((list) => {
        if (cancelled) return;
        setRows(list);
        setNowS(Math.floor(Date.now() / 1000));
        setError(null);
      })
      .catch((err) => {
        if (!cancelled) setError(formatAccessError(err, t));
      });
    return () => {
      cancelled = true;
    };
  }, [version, canHold, t]);

  if (!canHold) return null;

  const revoke = async (row: AuthTokenRow) => {
    setBusy(true);
    setError(null);
    try {
      await api.revokeAuthToken(row.id);
      toast.success(t('settings.access.tokens.revoked'));
    } catch (err) {
      const message = formatAccessError(err, t);
      setError(message);
      toast.error(message);
    } finally {
      reload();
      setBusy(false);
    }
  };

  const when = (seconds: number) => formatEpoch(seconds, i18n.language);

  return (
    <SettingsGroup>
      <SettingsGroupHeader
        id="access-tokens"
        title={t('settings.access.tokens.title')}
        storedOn="server"
      />
      <p className="text-[0.8125rem] text-muted-foreground">{t('settings.access.tokens.help')}</p>
      {via === 'ingress' && (
        <p role="note" className="rounded-md border border-border bg-muted/40 p-3 text-[0.8125rem]">
          {t('settings.access.tokens.ingressNotice')}
        </p>
      )}
      {error && (
        <p role="alert" className="text-sm text-destructive">
          {error}
        </p>
      )}
      {rows && rows.length === 0 && (
        <p className="text-sm text-muted-foreground">{t('settings.access.tokens.none')}</p>
      )}
      <ul className="divide-y divide-border">
        {rows?.map((row) => {
          const expired = row.expires_at !== null && row.expires_at <= nowS;
          return (
            <li key={row.id} className="flex flex-wrap items-center gap-x-4 gap-y-2 py-2">
              <div className="min-w-48 flex-1">
                <div className="text-sm font-medium break-words">
                  {row.name}
                  {expired && (
                    <span className="ml-2 rounded-full bg-destructive/15 px-2 py-0.5 text-[0.6875rem] text-destructive">
                      {t('settings.access.tokens.expired')}
                    </span>
                  )}
                </div>
                <div className="mt-1 flex flex-wrap gap-1">
                  {row.scopes.map((scope) => (
                    <code
                      key={scope}
                      className="rounded bg-muted px-1.5 py-0.5 font-mono text-[0.6875rem]"
                    >
                      {scope}
                    </code>
                  ))}
                </div>
                <div className="mt-1 text-[0.8125rem] text-muted-foreground">
                  {[
                    isAdmin && t('settings.access.tokens.owner', { user: row.username ?? '?' }),
                    t('settings.access.tokens.created', { when: when(row.created_at) }),
                    row.last_used_at
                      ? t('settings.access.tokens.lastUsed', { when: when(row.last_used_at) })
                      : t('settings.access.tokens.neverUsed'),
                    row.expires_at
                      ? t('settings.access.tokens.expires', { when: when(row.expires_at) })
                      : t('settings.access.tokens.noExpiry'),
                  ]
                    .filter(Boolean)
                    .join(' · ')}
                </div>
              </div>
              <Button
                variant="outline"
                size="sm"
                disabled={busy}
                aria-label={t('settings.access.tokens.revokeNamed', { name: row.name })}
                onClick={() => setRevoking(row)}
              >
                {t('settings.access.tokens.revoke')}
              </Button>
            </li>
          );
        })}
      </ul>
      <Button variant="outline" disabled={busy} onClick={() => setCreating(true)}>
        {t('settings.access.tokens.add')}
      </Button>
      <CreateTokenDialog
        open={creating}
        scopes={scopes}
        onClose={() => setCreating(false)}
        onCreated={(token) => {
          setSecret(token);
          reload();
        }}
      />
      <TokenSecretDialog token={secret} onClose={() => setSecret(null)} />
      <AccessConfirmDialog
        open={revoking !== null}
        destructive
        title={t('settings.access.tokens.revokeTitle', { name: revoking?.name ?? '' })}
        help={t('settings.access.tokens.revokeHelp')}
        confirmLabel={t('settings.access.tokens.revoke')}
        onCancel={() => setRevoking(null)}
        onConfirm={() => {
          const row = revoking;
          setRevoking(null);
          if (row) void revoke(row);
        }}
      />
    </SettingsGroup>
  );
}
