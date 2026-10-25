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
import { cn } from '../../lib/utils';
import { Button } from '../ui/button';
import { Input } from '../ui/input';
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
 * Administrator only: trusted networks, `cidr → account` rules that sign a direct
 * request in without a login (`app/auth/providers/trusted_network.py`). The server
 * has the final say on every rule (coded errors under `errors.auth_trusted_*`).
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

  useEffect(() => {
    let cancelled = false;
    Promise.all([api.getAuthSettings(), api.listAuthUsers()])
      .then(([settings, list]) => {
        if (cancelled) return;
        setUsers(list);
        setSaved(settings.trusted_networks ?? []);
        setDraft(toDraft(settings.trusted_networks));
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

  const update = (index: number, patch: Partial<DraftRule>) =>
    setDraft((rules) => rules.map((rule, i) => (i === index ? { ...rule, ...patch } : rule)));

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
        {draft.map((rule, index) => (
          <li key={index} className="flex flex-wrap items-center gap-2">
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
                update(index, { userId: event.target.value ? Number(event.target.value) : null })
              }
            >
              <option value="">{t('settings.access.trustedNetworks.pickUser')}</option>
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
          </li>
        ))}
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
