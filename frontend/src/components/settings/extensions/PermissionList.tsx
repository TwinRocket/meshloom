import { useTranslation } from 'react-i18next';

import { httpHosts, permissionKey, privacyOf, readsPrivateAndConnects } from './pluginsShared';

type Permissions = Record<string, Record<string, unknown>>;

/** The plain-words line(s) under a permission's title, from its parameters. */
function useDetailLines(name: string, params: Record<string, unknown>): string[] {
  const { t } = useTranslation();
  const key = permissionKey(name);
  const lines: string[] = [];
  if (name === 'messages.read') {
    lines.push(t(params.channels === 'all' ? `${key}.channels_all` : `${key}.channels_public`));
    if (params.direct === true) lines.push(t(`${key}.direct`));
    if (params.outgoing === true) lines.push(t(`${key}.outgoing`));
  } else if (name === 'messages.send') {
    const targets = typeof params.targets === 'string' ? params.targets : 'reply';
    lines.push(t(`${key}.targets_${targets}`));
    lines.push(
      t(`${key}.quota`, {
        per_hour: typeof params.per_hour === 'number' ? params.per_hour : 60,
        per_day: typeof params.per_day === 'number' ? params.per_day : 500,
      })
    );
  }
  return lines;
}

function PermissionRow({
  name,
  params,
  checked,
  onToggle,
  disabled,
}: {
  name: string;
  params: Record<string, unknown>;
  checked?: boolean;
  onToggle?: (granted: boolean) => void;
  disabled?: boolean;
}) {
  const { t } = useTranslation();
  const key = permissionKey(name);
  const hosts = name === 'http' ? httpHosts({ http: params }).join(', ') : '';
  const lines = useDetailLines(name, params);
  return (
    <li className="flex items-start gap-2 py-2">
      {onToggle && (
        <input
          type="checkbox"
          className="mt-1 h-4 w-4 shrink-0"
          checked={checked ?? true}
          disabled={disabled}
          aria-label={t(`${key}.title`)}
          onChange={(e) => onToggle(e.target.checked)}
        />
      )}
      <div className="min-w-0 text-sm">
        <div className="font-medium">{t(`${key}.title`)}</div>
        <div className="text-muted-foreground break-words">
          {t(`${key}.description`, { hosts })}
        </div>
        {lines.map((line) => (
          <div key={line} className="text-muted-foreground break-words">
            {line}
          </div>
        ))}
      </div>
    </li>
  );
}

/**
 * Permissions in plain words. Without `granted` it is the consent screen (everything
 * declared); with `granted` and `onChange` each row is a switch for the drawer.
 */
export function PermissionList({
  declared,
  granted,
  onToggle,
  disabled,
}: {
  declared: Permissions;
  granted?: Permissions;
  onToggle?: (name: string, granted: boolean) => void;
  disabled?: boolean;
}) {
  const names = Object.keys(declared);
  if (names.length === 0) return null;
  return (
    <ul className="divide-y divide-border rounded-md border border-border px-3">
      {names.map((name) => (
        <PermissionRow
          key={name}
          name={name}
          params={declared[name] ?? {}}
          checked={granted ? name in granted : undefined}
          onToggle={onToggle ? (g) => onToggle(name, g) : undefined}
          disabled={disabled}
        />
      ))}
    </ul>
  );
}

/** The exfiltration sentence, the third-party hosts and the privacy declaration. */
export function ConsentWarnings({
  permissions,
  manifest,
}: {
  permissions: Permissions;
  manifest: Record<string, unknown>;
}) {
  const { t } = useTranslation();
  const hosts = httpHosts(permissions).join(', ');
  const privacy = privacyOf(manifest);
  return (
    <div className="space-y-2 text-sm">
      {readsPrivateAndConnects(permissions) && (
        <p
          role="alert"
          className="rounded-md border border-destructive/50 bg-destructive/10 p-3 text-destructive"
          data-testid="plugin-exfiltration-warning"
        >
          {t('plugins.permissions.http.private_messages_warning', { hosts })}
        </p>
      )}
      {privacy && (
        <div className="rounded-md border border-border p-3" data-testid="plugin-privacy">
          <div className="font-medium">{t('settings.extensions.privacy.title')}</div>
          <p className="text-muted-foreground break-words">
            {privacy.sendsTo.length > 0
              ? t('settings.extensions.privacy.sends', { hosts: privacy.sendsTo.join(', ') })
              : t('settings.extensions.privacy.sendsNone')}
          </p>
          <p className="text-muted-foreground">
            {t(
              privacy.storesContent
                ? 'settings.extensions.privacy.stores'
                : 'settings.extensions.privacy.storesNone'
            )}
          </p>
        </div>
      )}
    </div>
  );
}
