import { useEffect, useState } from 'react';
import { useTranslation } from 'react-i18next';

import { api } from '../../api';
import { formatAccessError } from '../../auth/accessErrors';
import type {
  AccessSecurityNotice,
  AuthSettings,
  AuthUserRow,
  TrustedNetworkRule,
} from '../../auth/types';
import { isPublicOrWideCidr } from '../../lib/cidr';
import { cn } from '../../lib/utils';
import { Button } from '../ui/button';
import { Checkbox } from '../ui/checkbox';
import { Input } from '../ui/input';
import { Label } from '../ui/label';
import { toast } from '../ui/sonner';
import { SELECT_CLASS } from './accessShared';
import { SettingsGroup, SettingsGroupHeader } from './settingsPrimitives';

interface DraftRule {
  cidr: string;
  userId: number | null;
}

function toDraft(rules: TrustedNetworkRule[] | undefined): DraftRule[] {
  return (rules ?? []).map((rule) => ({ cidr: rule.cidr, userId: rule.user_id }));
}

function sameRules(draft: DraftRule[], saved: TrustedNetworkRule[]): boolean {
  return (
    draft.length === saved.length &&
    draft.every((rule, i) => rule.cidr.trim() === saved[i].cidr && rule.userId === saved[i].user_id)
  );
}

/**
 * The Access security notice: the one warning Access keeps, shown at the top of
 * Settings → Users & Access. The server decides when it applies (`security_notices`
 * of `GET /api/auth/settings`).
 */
export function AccessSecurityNoticeBanner({ notices }: { notices: AccessSecurityNotice[] }) {
  const { t } = useTranslation();
  if (notices.length === 0) return null;
  return (
    <div
      role="alert"
      className="space-y-1 rounded-md border border-destructive/50 bg-destructive/10 p-3 text-[0.8125rem]"
      data-testid="access-security-notice"
    >
      <p className="font-medium">{t('settings.access.securityNotice.title')}</p>
      {notices.map((notice) => (
        <p key={notice}>{t(`settings.access.securityNotice.${notice}`)}</p>
      ))}
    </div>
  );
}

/**
 * Administrator only: login-free on the local network (`lan_login_free`, off by
 * default, saved at once and audited by the server) and its trusted networks,
 * `cidr → account` rules that sign a direct request from a local address in without a
 * login (`app/auth/providers/trusted_network.py`). The rules apply only while the
 * switch is on. The server has the final say on every rule (coded errors under
 * `errors.auth_trusted_*`).
 */
export function AccessTrustedNetworksPanel({
  onNotices,
}: {
  onNotices?: (notices: AccessSecurityNotice[]) => void;
}) {
  const { t } = useTranslation();
  const [saved, setSaved] = useState<TrustedNetworkRule[] | null>(null);
  const [draft, setDraft] = useState<DraftRule[]>([]);
  const [users, setUsers] = useState<AuthUserRow[]>([]);
  const [saving, setSaving] = useState(false);
  const [enabled, setEnabled] = useState(false);
  const [switching, setSwitching] = useState(false);

  useEffect(() => {
    let cancelled = false;
    Promise.all([api.getAuthSettings(), api.listAuthUsers()])
      .then(([settings, list]) => {
        if (cancelled) return;
        setUsers(list);
        setSaved(settings.trusted_networks ?? []);
        setDraft(toDraft(settings.trusted_networks));
        setEnabled(settings.lan_login_free ?? false);
        onNotices?.(settings.security_notices ?? []);
      })
      .catch(() => {
        // Not readable (older server, refused): the panel stays hidden.
      });
    return () => {
      cancelled = true;
    };
  }, [onNotices]);

  if (saved === null) return null;

  const enabledUsers = users.filter((user) => !user.disabled);
  const userById = new Map(users.map((user) => [user.id, user]));
  const dirty = !sameRules(draft, saved);
  const incomplete = draft.some((rule) => !rule.cidr.trim() || rule.userId === null);
  const admins = draft.filter(
    (rule) => rule.userId !== null && userById.get(rule.userId)?.role === 'admin'
  );

  // A rule whose account was disabled or must change its password never applies.
  const inactiveReason = (
    rule: DraftRule
  ): 'userDisabledNotice' | 'userMustChangeNotice' | null => {
    const account = rule.userId === null ? undefined : userById.get(rule.userId);
    if (!account) return null;
    if (account.disabled) return 'userDisabledNotice';
    return account.must_change_password ? 'userMustChangeNotice' : null;
  };

  const update = (index: number, patch: Partial<DraftRule>) =>
    setDraft((rules) => rules.map((rule, i) => (i === index ? { ...rule, ...patch } : rule)));

  const toggle = async (next: boolean) => {
    setSwitching(true);
    try {
      const settings: AuthSettings = await api.patchAuthSettings({ lan_login_free: next });
      setEnabled(settings.lan_login_free ?? next);
      toast.success(
        t(
          next
            ? 'settings.access.trustedNetworks.switchOn'
            : 'settings.access.trustedNetworks.switchOff'
        )
      );
    } catch (err) {
      toast.error(formatAccessError(err, t));
    } finally {
      setSwitching(false);
    }
  };

  const save = async () => {
    setSaving(true);
    try {
      const settings: AuthSettings = await api.patchAuthSettings({
        trusted_networks: draft.map((rule) => ({ cidr: rule.cidr.trim(), user_id: rule.userId! })),
      });
      setSaved(settings.trusted_networks ?? []);
      setDraft(toDraft(settings.trusted_networks));
      onNotices?.(settings.security_notices ?? []);
      toast.success(t('settings.access.trustedNetworks.saved'));
    } catch (err) {
      toast.error(formatAccessError(err, t));
    } finally {
      setSaving(false);
    }
  };

  return (
    <SettingsGroup>
      <SettingsGroupHeader
        id="access-trusted-networks"
        title={t('settings.access.trustedNetworks.title')}
        storedOn="server"
        dirty={dirty}
      />
      <div className="flex items-center gap-2">
        <Checkbox
          id="access-lan-login-free"
          checked={enabled}
          disabled={switching}
          onCheckedChange={(checked) => void toggle(checked === true)}
        />
        <Label htmlFor="access-lan-login-free">{t('settings.access.trustedNetworks.switch')}</Label>
      </div>
      <p
        role="note"
        data-testid="lan-login-free-warning"
        className="rounded-md border border-destructive/50 bg-destructive/10 p-2 text-[0.8125rem]"
      >
        {t('settings.access.trustedNetworks.switchWarning')}
      </p>
      {!enabled && saved.length > 0 && (
        <p
          className="text-[0.8125rem] text-muted-foreground"
          data-testid="lan-login-free-off-notice"
        >
          {t('settings.access.trustedNetworks.switchOffNotice')}
        </p>
      )}
      <p className="text-[0.8125rem] text-muted-foreground">
        {t('settings.access.trustedNetworks.help')}
      </p>
      <p className="text-[0.8125rem] text-muted-foreground">
        {t('settings.access.trustedNetworks.proxyClosed')}
      </p>
      {draft.length === 0 && (
        <p className="text-[0.8125rem] text-muted-foreground">
          {t('settings.access.trustedNetworks.none')}
        </p>
      )}
      <ul className="space-y-2">
        {draft.map((rule, index) => {
          const reason = inactiveReason(rule);
          const cidr = rule.cidr.trim();
          return (
            <li key={index} className="space-y-1">
              <div className="flex flex-wrap items-center gap-2">
                <Input
                  aria-label={t('settings.access.trustedNetworks.cidr')}
                  value={rule.cidr}
                  placeholder="192.168.1.0/24"
                  spellCheck={false}
                  className={cn('w-48 font-mono')}
                  onChange={(event) => update(index, { cidr: event.target.value })}
                />
                <select
                  aria-label={t('settings.access.trustedNetworks.user')}
                  className={SELECT_CLASS}
                  value={rule.userId ?? ''}
                  onChange={(event) =>
                    update(index, {
                      userId: event.target.value ? Number(event.target.value) : null,
                    })
                  }
                >
                  <option value="">{t('settings.access.trustedNetworks.pickUser')}</option>
                  {rule.userId !== null && userById.get(rule.userId)?.disabled && (
                    <option value={rule.userId}>{userById.get(rule.userId)!.username}</option>
                  )}
                  {enabledUsers.map((user) => (
                    <option key={user.id} value={user.id}>
                      {user.display_name || user.username} ({t(`auth.role.${user.role}`)})
                    </option>
                  ))}
                </select>
                <Button
                  variant="outline"
                  size="sm"
                  onClick={() => setDraft((rules) => rules.filter((_, i) => i !== index))}
                >
                  {t('settings.access.trustedNetworks.remove')}
                </Button>
              </div>
              {isPublicOrWideCidr(cidr) && (
                <p
                  role="note"
                  data-testid="trusted-networks-wide-warning"
                  className="rounded-md border border-destructive/50 bg-destructive/10 p-2 text-[0.8125rem]"
                >
                  {t('settings.access.trustedNetworks.publicOrWideWarning', { cidr })}
                </p>
              )}
              {reason && (
                <p
                  role="note"
                  data-testid="trusted-networks-inactive-user"
                  className="rounded-md border border-destructive/50 bg-destructive/10 p-2 text-[0.8125rem]"
                >
                  {t(`settings.access.trustedNetworks.${reason}`, { cidr })}
                </p>
              )}
            </li>
          );
        })}
      </ul>
      {admins.length > 0 && (
        <p
          role="note"
          data-testid="trusted-networks-admin-warning"
          className="rounded-md border border-destructive/50 bg-destructive/10 p-3 text-[0.8125rem]"
        >
          {t('settings.access.trustedNetworks.adminWarning', {
            networks: admins.map((rule) => rule.cidr.trim()).join(', '),
          })}
        </p>
      )}
      <div className="flex flex-wrap gap-2">
        <Button
          variant="outline"
          onClick={() => setDraft((rules) => [...rules, { cidr: '', userId: null }])}
        >
          {t('settings.access.trustedNetworks.add')}
        </Button>
        <Button onClick={() => void save()} disabled={!dirty || incomplete || saving}>
          {saving
            ? t('settings.access.trustedNetworks.saving')
            : t('settings.access.trustedNetworks.save')}
        </Button>
      </div>
    </SettingsGroup>
  );
}
