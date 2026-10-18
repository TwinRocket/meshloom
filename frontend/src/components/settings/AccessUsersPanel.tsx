import { useEffect, useState, type FormEvent } from 'react';
import { useTranslation } from 'react-i18next';

import { api } from '../../api';
import { formatAccessError } from '../../auth/accessErrors';
import { passwordStrength } from '../../auth/passwordStrength';
import { useSession } from '../../auth/SessionProvider';
import { PASSWORD_MIN_LENGTH, type AuthRole, type AuthUserRow } from '../../auth/types';
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
import { ACCESS_ROLES, SELECT_CLASS } from './accessShared';
import { SettingsGroup, SettingsGroupHeader } from './settingsPrimitives';

/** Only a local account outside `kiosk` has a password (`_takes_password` in app/routers/auth.py). */
function takesPassword(user: Pick<AuthUserRow, 'provider' | 'role'>): boolean {
  return user.provider === 'local' && user.role !== 'kiosk';
}

function Badge({ children, tone }: { children: string; tone?: 'warn' | 'muted' }) {
  const color =
    tone === 'warn'
      ? 'bg-warning/15 text-warning'
      : tone === 'muted'
        ? 'bg-muted text-muted-foreground'
        : 'bg-primary/15 text-primary';
  return <span className={`rounded-full px-2 py-0.5 text-[0.6875rem] ${color}`}>{children}</span>;
}

function RoleSelect({
  id,
  value,
  onChange,
  disabled,
  label,
}: {
  id?: string;
  value: AuthRole;
  onChange: (role: AuthRole) => void;
  disabled?: boolean;
  label?: string;
}) {
  const { t } = useTranslation();
  return (
    <select
      id={id}
      aria-label={label}
      className={SELECT_CLASS}
      value={value}
      disabled={disabled}
      onChange={(event) => onChange(event.target.value as AuthRole)}
    >
      {ACCESS_ROLES.map((role) => (
        <option key={role} value={role}>
          {t(`auth.role.${role}`)}
        </option>
      ))}
    </select>
  );
}

/** A password and the "change at next sign-in" switch, shared by create and reset. */
function PasswordFields({
  idPrefix,
  password,
  onPassword,
  mustChange,
  onMustChange,
}: {
  idPrefix: string;
  password: string;
  onPassword: (value: string) => void;
  mustChange: boolean;
  onMustChange: (value: boolean) => void;
}) {
  const { t } = useTranslation();
  return (
    <>
      <div className="space-y-1.5">
        <Label htmlFor={`${idPrefix}-password`}>{t('settings.access.users.password')}</Label>
        <Input
          id={`${idPrefix}-password`}
          type="password"
          autoComplete="new-password"
          value={password}
          onChange={(event) => onPassword(event.target.value)}
        />
        <p className="text-[0.8125rem] text-muted-foreground">
          {t('auth.setup.passwordPolicy', { min_length: PASSWORD_MIN_LENGTH })}
        </p>
      </div>
      <div className="flex items-center gap-2">
        <Checkbox
          id={`${idPrefix}-must-change`}
          checked={mustChange}
          onCheckedChange={(checked) => onMustChange(checked === true)}
        />
        <Label htmlFor={`${idPrefix}-must-change`}>{t('settings.access.users.mustChange')}</Label>
      </div>
    </>
  );
}

const passwordOk = (password: string) => {
  const strength = passwordStrength(password);
  return strength !== 'empty' && strength !== 'tooShort';
};

function CreateUserDialog({
  open,
  onOpenChange,
  onCreated,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  onCreated: () => void;
}) {
  const { t } = useTranslation();
  const [username, setUsername] = useState('');
  const [displayName, setDisplayName] = useState('');
  const [role, setRole] = useState<AuthRole>('member');
  const [password, setPassword] = useState('');
  const [mustChange, setMustChange] = useState(true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const needsPassword = role !== 'kiosk';
  const ready = username.trim().length > 0 && (!needsPassword || passwordOk(password));

  const submit = async (event: FormEvent) => {
    event.preventDefault();
    if (!ready || busy) return;
    setBusy(true);
    setError(null);
    try {
      await api.createAuthUser({
        username: username.trim(),
        role,
        display_name: displayName.trim(),
        ...(needsPassword ? { password, must_change_password: mustChange } : {}),
      });
      toast.success(t('settings.access.users.created'));
      setUsername('');
      setDisplayName('');
      setPassword('');
      onOpenChange(false);
      onCreated();
    } catch (err) {
      setError(formatAccessError(err, t));
    } finally {
      setBusy(false);
    }
  };

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent>
        <form onSubmit={submit} className="space-y-4">
          <DialogHeader>
            <DialogTitle>{t('settings.access.users.createTitle')}</DialogTitle>
            <DialogDescription>{t('settings.access.users.createHelp')}</DialogDescription>
          </DialogHeader>
          <div className="space-y-1.5">
            <Label htmlFor="access-new-username">{t('auth.username')}</Label>
            <Input
              id="access-new-username"
              autoComplete="off"
              value={username}
              onChange={(event) => setUsername(event.target.value)}
            />
          </div>
          <div className="space-y-1.5">
            <Label htmlFor="access-new-display-name">{t('auth.setup.displayName')}</Label>
            <Input
              id="access-new-display-name"
              maxLength={128}
              value={displayName}
              onChange={(event) => setDisplayName(event.target.value)}
            />
          </div>
          <div className="space-y-1.5">
            <Label htmlFor="access-new-role">{t('settings.access.users.role')}</Label>
            <RoleSelect id="access-new-role" value={role} onChange={setRole} />
            <p className="text-[0.8125rem] text-muted-foreground">
              {t(`settings.access.roleHelp.${role}`)}
            </p>
          </div>
          {needsPassword && (
            <PasswordFields
              idPrefix="access-create"
              password={password}
              onPassword={setPassword}
              mustChange={mustChange}
              onMustChange={setMustChange}
            />
          )}
          {error && (
            <p role="alert" className="text-sm text-destructive">
              {error}
            </p>
          )}
          <DialogFooter>
            <Button type="button" variant="outline" onClick={() => onOpenChange(false)}>
              {t('settings.access.cancel')}
            </Button>
            <Button type="submit" disabled={!ready || busy}>
              {t('settings.access.users.create')}
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}

function ResetPasswordDialog({
  user,
  onClose,
  onDone,
}: {
  user: AuthUserRow | null;
  onClose: () => void;
  onDone: () => void;
}) {
  const { t } = useTranslation();
  const [password, setPassword] = useState('');
  const [mustChange, setMustChange] = useState(true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const submit = async (event: FormEvent) => {
    event.preventDefault();
    if (!user || !passwordOk(password) || busy) return;
    setBusy(true);
    setError(null);
    try {
      await api.setAuthUserPassword(user.id, password, mustChange);
      toast.success(t('settings.access.users.passwordReset'));
      setPassword('');
      onClose();
      onDone();
    } catch (err) {
      setError(formatAccessError(err, t));
    } finally {
      setBusy(false);
    }
  };

  return (
    <Dialog open={user !== null} onOpenChange={(open) => !open && onClose()}>
      <DialogContent>
        <form onSubmit={submit} className="space-y-4">
          <DialogHeader>
            <DialogTitle>
              {t('settings.access.users.resetTitle', { username: user?.username ?? '' })}
            </DialogTitle>
            <DialogDescription>{t('settings.access.users.resetHelp')}</DialogDescription>
          </DialogHeader>
          <PasswordFields
            idPrefix="access-reset"
            password={password}
            onPassword={setPassword}
            mustChange={mustChange}
            onMustChange={setMustChange}
          />
          {error && (
            <p role="alert" className="text-sm text-destructive">
              {error}
            </p>
          )}
          <DialogFooter>
            <Button type="button" variant="outline" onClick={onClose}>
              {t('settings.access.cancel')}
            </Button>
            <Button type="submit" disabled={!passwordOk(password) || busy}>
              {t('settings.access.users.resetSubmit')}
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}

function UserRow({
  user,
  isSelf,
  busy,
  onPatch,
  onReset,
  onRevoke,
  onDelete,
}: {
  user: AuthUserRow;
  isSelf: boolean;
  busy: boolean;
  onPatch: (user: AuthUserRow, patch: { role?: AuthRole; disabled?: boolean }) => void;
  onReset: (user: AuthUserRow) => void;
  onRevoke: (user: AuthUserRow) => void;
  onDelete: (user: AuthUserRow) => void;
}) {
  const { t } = useTranslation();
  return (
    <li className="space-y-2 py-3" data-testid={`access-user-${user.username}`}>
      <div className="flex flex-wrap items-center gap-2">
        <span className="text-sm font-medium">{user.display_name || user.username}</span>
        {user.display_name && (
          <span className="text-[0.8125rem] text-muted-foreground">{user.username}</span>
        )}
        <Badge tone="muted">
          {t(`settings.access.users.provider.${user.provider}`, {
            defaultValue: user.provider,
          })}
        </Badge>
        {isSelf && <Badge>{t('settings.access.users.you')}</Badge>}
        {user.disabled && <Badge tone="warn">{t('settings.access.users.disabled')}</Badge>}
        {user.must_change_password && (
          <Badge tone="warn">{t('settings.access.users.mustChangeBadge')}</Badge>
        )}
      </div>
      <div className="flex flex-wrap items-center gap-2">
        <RoleSelect
          value={user.role}
          label={t('settings.access.users.roleOf', { username: user.username })}
          disabled={busy || isSelf}
          onChange={(role) => onPatch(user, { role })}
        />
        <Button
          variant="outline"
          size="sm"
          disabled={busy || isSelf}
          onClick={() => onPatch(user, { disabled: !user.disabled })}
        >
          {user.disabled ? t('settings.access.users.enable') : t('settings.access.users.disable')}
        </Button>
        {takesPassword(user) && (
          <Button variant="outline" size="sm" disabled={busy} onClick={() => onReset(user)}>
            {t('settings.access.users.reset')}
          </Button>
        )}
        <Button variant="outline" size="sm" disabled={busy} onClick={() => onRevoke(user)}>
          {t('settings.access.users.revokeSessions')}
        </Button>
        <Button
          variant="outline"
          size="sm"
          disabled={busy || isSelf}
          onClick={() => onDelete(user)}
        >
          {t('settings.access.users.delete')}
        </Button>
      </div>
    </li>
  );
}

/** Administrator only: every account, with the actions `/api/auth/users` offers. */
export function AccessUsersPanel() {
  const { t } = useTranslation();
  const { user: me } = useSession();
  const [users, setUsers] = useState<AuthUserRow[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [creating, setCreating] = useState(false);
  const [resetting, setResetting] = useState<AuthUserRow | null>(null);
  const [deleting, setDeleting] = useState<AuthUserRow | null>(null);

  const [version, setVersion] = useState(0);
  const reload = () => setVersion((value) => value + 1);

  useEffect(() => {
    let cancelled = false;
    api
      .listAuthUsers()
      .then((list) => {
        if (cancelled) return;
        setUsers(list);
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
    try {
      await action();
      toast.success(success);
    } catch (err) {
      toast.error(formatAccessError(err, t));
    } finally {
      reload();
      setBusy(false);
    }
  };

  const patch = (user: AuthUserRow, change: { role?: AuthRole; disabled?: boolean }) =>
    run(
      () => api.patchAuthUser(user.id, change),
      change.role ? t('settings.access.users.roleChanged') : t('settings.access.users.updated')
    );

  return (
    <SettingsGroup>
      <SettingsGroupHeader
        id="access-users"
        title={t('settings.access.users.title')}
        storedOn="server"
      />
      <p className="text-[0.8125rem] text-muted-foreground">{t('settings.access.users.help')}</p>
      {error && (
        <p role="alert" className="text-sm text-destructive">
          {error}
        </p>
      )}
      <ul className="divide-y divide-border">
        {users?.map((user) => (
          <UserRow
            key={user.id}
            user={user}
            isSelf={me?.id === user.id}
            busy={busy}
            onPatch={(target, change) => void patch(target, change)}
            onReset={setResetting}
            onRevoke={(target) =>
              void run(
                () => api.revokeAuthUserSessions(target.id),
                t('settings.access.users.sessionsRevoked', { username: target.username })
              )
            }
            onDelete={setDeleting}
          />
        ))}
      </ul>
      <Button onClick={() => setCreating(true)}>{t('settings.access.users.add')}</Button>
      <CreateUserDialog open={creating} onOpenChange={setCreating} onCreated={reload} />
      <ResetPasswordDialog user={resetting} onClose={() => setResetting(null)} onDone={reload} />
      <Dialog open={deleting !== null} onOpenChange={(open) => !open && setDeleting(null)}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>
              {t('settings.access.users.deleteTitle', { username: deleting?.username ?? '' })}
            </DialogTitle>
            <DialogDescription>{t('settings.access.users.deleteHelp')}</DialogDescription>
          </DialogHeader>
          <DialogFooter>
            <Button variant="outline" onClick={() => setDeleting(null)}>
              {t('settings.access.cancel')}
            </Button>
            <Button
              variant="destructive"
              disabled={busy}
              onClick={() => {
                const target = deleting;
                setDeleting(null);
                if (target) {
                  void run(
                    () => api.deleteAuthUser(target.id),
                    t('settings.access.users.deleted', { username: target.username })
                  );
                }
              }}
            >
              {t('settings.access.users.delete')}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </SettingsGroup>
  );
}
