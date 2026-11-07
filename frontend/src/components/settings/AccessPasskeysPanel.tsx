import { useEffect, useState, type FormEvent } from 'react';
import { useTranslation } from 'react-i18next';
import { KeyRound, Trash2 } from 'lucide-react';

import { api } from '../../api';
import { formatAccessError } from '../../auth/accessErrors';
import { formatPasskeyError, passkeysSupported, registerPasskey } from '../../auth/passkeys';
import { useSession } from '../../auth/SessionProvider';
import type { AuthMe, PasskeyRow } from '../../auth/types';
import { Button } from '../ui/button';
import { Input } from '../ui/input';
import { Label } from '../ui/label';
import { toast } from '../ui/sonner';
import { formatEpoch } from './accessShared';
import { SettingsGroup, SettingsGroupHeader } from './settingsPrimitives';

/** The name a new passkey starts with: the reader can keep it. */
const NAME_MAX_LENGTH = 64;

/** Add form: the proofs the server asks for, a name, then the browser's own prompt. */
function AddPasskeyForm({
  totpOn,
  onAdded,
  onCancel,
}: {
  totpOn: boolean;
  onAdded: () => void;
  onCancel: () => void;
}) {
  const { t } = useTranslation();
  const [name, setName] = useState('');
  const [password, setPassword] = useState('');
  const [code, setCode] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const ready = !!name.trim() && !!password && (!totpOn || !!code.trim());

  const submit = async (event: FormEvent) => {
    event.preventDefault();
    if (busy || !ready) return;
    setBusy(true);
    setError(null);
    try {
      await registerPasskey({
        password,
        code: totpOn ? code.trim() : undefined,
        name: name.trim(),
      });
      toast.success(t('settings.access.passkeys.added'));
      onAdded();
    } catch (err) {
      setError(formatPasskeyError(err, t));
      // The password and the code were spent on this try (a recovery code is used up).
      setPassword('');
      setCode('');
      setBusy(false);
    }
  };

  return (
    <form onSubmit={submit} className="space-y-3" noValidate>
      <div className="grid gap-3 md:grid-cols-2">
        <div className="space-y-1.5 md:col-span-2">
          <Label htmlFor="passkey-name">{t('settings.access.passkeys.name')}</Label>
          <Input
            id="passkey-name"
            maxLength={NAME_MAX_LENGTH}
            autoComplete="off"
            placeholder={t('settings.access.passkeys.namePlaceholder')}
            value={name}
            onChange={(event) => setName(event.target.value)}
          />
        </div>
        <div className="space-y-1.5">
          <Label htmlFor="passkey-password">{t('auth.totp.currentPassword')}</Label>
          <Input
            id="passkey-password"
            type="password"
            autoComplete="current-password"
            value={password}
            onChange={(event) => setPassword(event.target.value)}
          />
        </div>
        {totpOn && (
          <div className="space-y-1.5">
            <Label htmlFor="passkey-code">{t('auth.totp.codeField')}</Label>
            <Input
              id="passkey-code"
              autoComplete="one-time-code"
              spellCheck={false}
              className="font-mono tracking-wider"
              value={code}
              onChange={(event) => setCode(event.target.value)}
            />
          </div>
        )}
      </div>
      {totpOn && (
        <p className="text-[0.8125rem] text-muted-foreground">
          {t('settings.access.passkeys.codeHelp')}
        </p>
      )}
      {error && (
        <p role="alert" className="text-sm text-destructive">
          {error}
        </p>
      )}
      <div className="flex flex-wrap gap-2">
        <Button type="submit" disabled={busy || !ready}>
          {t('settings.access.passkeys.addSubmit')}
        </Button>
        <Button type="button" variant="ghost" onClick={onCancel}>
          {t('auth.totp.cancel')}
        </Button>
      </div>
    </form>
  );
}

/** Removing one passkey asks for the password again. */
function RemovePasskeyForm({
  passkey,
  onRemoved,
  onCancel,
}: {
  passkey: PasskeyRow;
  onRemoved: () => void;
  onCancel: () => void;
}) {
  const { t } = useTranslation();
  const [password, setPassword] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const submit = async (event: FormEvent) => {
    event.preventDefault();
    if (busy || !password) return;
    setBusy(true);
    setError(null);
    try {
      await api.deletePasskey(passkey.id, password);
      toast.success(t('settings.access.passkeys.removed'));
      onRemoved();
    } catch (err) {
      setError(formatAccessError(err, t));
      setPassword('');
      setBusy(false);
    }
  };

  return (
    <form onSubmit={submit} className="mt-2 w-full space-y-2" noValidate>
      <Label htmlFor={`passkey-remove-${passkey.id}`}>
        {t('settings.access.passkeys.removePassword', { name: passkey.name })}
      </Label>
      <Input
        id={`passkey-remove-${passkey.id}`}
        type="password"
        autoComplete="current-password"
        value={password}
        onChange={(event) => setPassword(event.target.value)}
      />
      {error && (
        <p role="alert" className="text-sm text-destructive">
          {error}
        </p>
      )}
      <div className="flex flex-wrap gap-2">
        <Button type="submit" variant="destructive" disabled={busy || !password}>
          {t('settings.access.passkeys.removeSubmit')}
        </Button>
        <Button type="button" variant="ghost" onClick={onCancel}>
          {t('auth.totp.cancel')}
        </Button>
      </div>
    </form>
  );
}

/**
 * Settings → Users & Access → Passkeys: the caller's own passkeys (name, creation, last
 * use), add one and remove one. Adding needs the password and, with two-factor
 * authentication on, a code too: a passkey signs in without either. The browser side lives
 * in `auth/passkeys.ts` (the WebAuthn library loads only when a ceremony starts). The
 * panel says why instead of hiding when passkeys cannot be used here.
 */
export function AccessPasskeysPanel() {
  const { t, i18n } = useTranslation();
  const { via, session } = useSession();
  const [me, setMe] = useState<AuthMe | null>(null);
  const [rows, setRows] = useState<PasskeyRow[] | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [adding, setAdding] = useState(false);
  const [removing, setRemoving] = useState<number | null>(null);
  const [version, setVersion] = useState(0);

  const inIngress = via === 'ingress';
  const applicable = !!me && me.provider === 'local' && me.has_password && !inIngress;
  // Listing and removing work anywhere; only a ceremony needs the secure context.
  const canCeremony = session?.methods.passkey === true && passkeysSupported();

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const account = await api.getAuthMe();
        const list =
          account.provider === 'local' && account.has_password && via !== 'ingress'
            ? await api.listPasskeys()
            : null;
        if (cancelled) return;
        setMe(account);
        setRows(list);
        setLoadError(null);
      } catch (err) {
        if (!cancelled) setLoadError(formatAccessError(err, t));
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [t, via, version]);

  if (loadError) {
    return (
      <SettingsGroup>
        <p role="alert" className="text-sm text-destructive">
          {loadError}
        </p>
      </SettingsGroup>
    );
  }
  if (!me) return null;

  const reload = () => {
    setAdding(false);
    setRemoving(null);
    setVersion((value) => value + 1);
  };

  return (
    <SettingsGroup>
      <SettingsGroupHeader
        id="access-passkeys"
        title={t('settings.access.passkeys.title')}
        storedOn="server"
      />
      {!applicable && (
        <p className="text-[0.8125rem] text-muted-foreground" data-testid="passkeys-not-applicable">
          {t(
            inIngress ? 'settings.access.passkeys.ingress' : 'settings.access.passkeys.noPassword'
          )}
        </p>
      )}
      {applicable && rows && (
        <>
          <p className="text-[0.8125rem] text-muted-foreground">
            {t('settings.access.passkeys.help')}
          </p>
          {rows.length === 0 ? (
            <p className="text-[0.8125rem] text-muted-foreground">
              {t('settings.access.passkeys.none')}
            </p>
          ) : (
            <ul className="divide-y divide-border">
              {rows.map((row) => (
                <li key={row.id} className="flex flex-wrap items-center gap-x-4 gap-y-2 py-2">
                  <KeyRound className="h-4 w-4 shrink-0 text-muted-foreground" aria-hidden="true" />
                  <div className="min-w-0 flex-1 basis-48">
                    <div className="break-words text-sm font-medium">
                      {row.name}
                      {row.backup_eligible && (
                        <span className="ml-2 rounded-full bg-primary/15 px-2 py-0.5 text-[0.6875rem] text-primary">
                          {t('settings.access.passkeys.synced')}
                        </span>
                      )}
                    </div>
                    <div className="text-[0.8125rem] text-muted-foreground">
                      {t('settings.access.passkeys.created', {
                        date: formatEpoch(row.created_at, i18n.language),
                      })}
                      {' · '}
                      {row.last_used_at
                        ? t('settings.access.passkeys.lastUsed', {
                            date: formatEpoch(row.last_used_at, i18n.language),
                          })
                        : t('settings.access.passkeys.neverUsed')}
                    </div>
                  </div>
                  {removing !== row.id && (
                    <Button
                      type="button"
                      variant="outline"
                      size="sm"
                      aria-label={t('settings.access.passkeys.remove', { name: row.name })}
                      onClick={() => {
                        setAdding(false);
                        setRemoving(row.id);
                      }}
                    >
                      <Trash2 className="mr-1 h-4 w-4" aria-hidden="true" />
                      {t('settings.access.passkeys.removeButton')}
                    </Button>
                  )}
                  {removing === row.id && (
                    <RemovePasskeyForm
                      passkey={row}
                      onRemoved={reload}
                      onCancel={() => setRemoving(null)}
                    />
                  )}
                </li>
              ))}
            </ul>
          )}
          {!adding && canCeremony && (
            <div>
              <Button
                type="button"
                onClick={() => {
                  setRemoving(null);
                  setAdding(true);
                }}
              >
                {t('settings.access.passkeys.add')}
              </Button>
            </div>
          )}
          {!canCeremony && (
            <p
              className="text-[0.8125rem] text-muted-foreground"
              data-testid="passkeys-unavailable"
            >
              {t('settings.access.passkeys.unavailable')}
            </p>
          )}
          {adding && (
            <AddPasskeyForm
              totpOn={me.totp_enabled === true}
              onAdded={reload}
              onCancel={() => setAdding(false)}
            />
          )}
        </>
      )}
    </SettingsGroup>
  );
}
