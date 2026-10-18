import { useCallback, useEffect, useMemo, useState } from 'react';
import { useTranslation } from 'react-i18next';

import { api } from '../../api';
import { formatAccessError } from '../../auth/accessErrors';
import type { AuthAuditEntry, AuthUserRow } from '../../auth/types';
import { Button } from '../ui/button';
import { Input } from '../ui/input';
import { Label } from '../ui/label';
import { SELECT_CLASS, formatEpoch } from './accessShared';
import {
  AUDIT_FAMILIES,
  AUDIT_FAMILY_IDS,
  AUDIT_OUTCOMES,
  EMPTY_AUDIT_FILTERS,
  isKnownAuditAction,
  isKnownAuditOutcome,
  isKnownAuditVia,
  matchesAuditFilters,
  userIdOfTarget,
  type AuditFamily,
  type AuditFilters,
} from './auditShared';
import { SettingsGroup, SettingsGroupHeader } from './settingsPrimitives';

const PAGE_SIZE = 100;

/**
 * Settings → Users & Access → Audit log (administrators; `GET /auth/audit` refuses anyone
 * else). Pages are fetched by opaque cursor, newest first. The server filters nothing, so
 * the filters narrow the entries loaded so far; "load older" fetches the next page.
 * `details` is never shown: the server already strips secrets and the list needs none.
 */
export function AccessAuditPanel() {
  const { t, i18n } = useTranslation();
  const [entries, setEntries] = useState<AuthAuditEntry[]>([]);
  const [cursor, setCursor] = useState<string | null>(null);
  const [loaded, setLoaded] = useState(false);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [users, setUsers] = useState<AuthUserRow[]>([]);
  const [filters, setFilters] = useState<AuditFilters>(EMPTY_AUDIT_FILTERS);

  const loadPage = useCallback(
    async (from: string) => {
      setLoading(true);
      setError(null);
      try {
        const page = await api.getAuthAudit(from, PAGE_SIZE);
        setEntries((prev) => [...prev, ...page.entries]);
        setCursor(page.next_cursor);
      } catch (err) {
        setError(formatAccessError(err, t));
      } finally {
        setLoading(false);
      }
    },
    [t]
  );

  useEffect(() => {
    let cancelled = false;
    api
      .getAuthAudit(null, PAGE_SIZE)
      .then((page) => {
        if (cancelled) return;
        setEntries(page.entries);
        setCursor(page.next_cursor);
        setLoaded(true);
      })
      .catch((err) => {
        if (!cancelled) setError(formatAccessError(err, t));
      });
    api
      .listAuthUsers()
      .then((list) => {
        if (!cancelled) setUsers(list);
      })
      .catch(() => {});
    return () => {
      cancelled = true;
    };
  }, [t]);

  const userName = useCallback(
    (id: number) => {
      const user = users.find((u) => u.id === id);
      return user ? user.display_name || user.username : null;
    },
    [users]
  );

  const actorLabel = (entry: AuthAuditEntry): string => {
    if (entry.actor_user_id !== null) {
      return (
        userName(entry.actor_user_id) ??
        t('settings.access.audit.deletedUser', { id: entry.actor_user_id })
      );
    }
    if (entry.actor_token_id) {
      return t('settings.access.audit.token', { id: entry.actor_token_id.slice(0, 8) });
    }
    return t('settings.access.audit.anonymous');
  };

  const targetLabel = (target: string | null): string => {
    if (!target) return '';
    const id = userIdOfTarget(target);
    if (id === null) return target;
    return t('settings.access.audit.targetUser', {
      name: userName(id) ?? t('settings.access.audit.deletedUser', { id }),
    });
  };

  const actorIds = useMemo(
    () => [...new Set(entries.flatMap((e) => (e.actor_user_id === null ? [] : [e.actor_user_id])))],
    [entries]
  );
  const visible = useMemo(
    () => entries.filter((e) => matchesAuditFilters(e, filters)),
    [entries, filters]
  );
  const actionChoices: readonly string[] = filters.family
    ? AUDIT_FAMILIES[filters.family]
    : AUDIT_FAMILY_IDS.flatMap((f) => AUDIT_FAMILIES[f]);
  const filtered = JSON.stringify(filters) !== JSON.stringify(EMPTY_AUDIT_FILTERS);
  const set = (patch: Partial<AuditFilters>) => setFilters((prev) => ({ ...prev, ...patch }));

  const actionText = (action: string) =>
    isKnownAuditAction(action) ? t(`settings.access.audit.actions.${action}`) : action;

  return (
    <SettingsGroup>
      <SettingsGroupHeader
        id="access-audit"
        title={t('settings.access.audit.title')}
        storedOn="server"
      />
      <p className="text-[0.8125rem] text-muted-foreground">{t('settings.access.audit.help')}</p>

      <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-3">
        <FilterField id="audit-family" label={t('settings.access.audit.filterFamily')}>
          <select
            id="audit-family"
            className={SELECT_CLASS}
            value={filters.family}
            onChange={(e) => set({ family: e.target.value as AuditFamily | '', action: '' })}
          >
            <option value="">{t('settings.access.audit.all')}</option>
            {AUDIT_FAMILY_IDS.map((f) => (
              <option key={f} value={f}>
                {t(`settings.access.audit.families.${f}`)}
              </option>
            ))}
          </select>
        </FilterField>
        <FilterField id="audit-action" label={t('settings.access.audit.filterAction')}>
          <select
            id="audit-action"
            className={SELECT_CLASS}
            value={filters.action}
            onChange={(e) => set({ action: e.target.value })}
          >
            <option value="">{t('settings.access.audit.all')}</option>
            {actionChoices.map((a) => (
              <option key={a} value={a}>
                {actionText(a)}
              </option>
            ))}
          </select>
        </FilterField>
        <FilterField id="audit-outcome" label={t('settings.access.audit.filterOutcome')}>
          <select
            id="audit-outcome"
            className={SELECT_CLASS}
            value={filters.outcome}
            onChange={(e) => set({ outcome: e.target.value })}
          >
            <option value="">{t('settings.access.audit.all')}</option>
            {AUDIT_OUTCOMES.map((o) => (
              <option key={o} value={o}>
                {t(`settings.access.audit.outcomes.${o}`)}
              </option>
            ))}
          </select>
        </FilterField>
        <FilterField id="audit-actor" label={t('settings.access.audit.filterActor')}>
          <select
            id="audit-actor"
            className={SELECT_CLASS}
            value={filters.actor}
            onChange={(e) => set({ actor: e.target.value })}
          >
            <option value="">{t('settings.access.audit.all')}</option>
            <option value="anonymous">{t('settings.access.audit.anonymous')}</option>
            {actorIds.map((id) => (
              <option key={id} value={String(id)}>
                {userName(id) ?? t('settings.access.audit.deletedUser', { id })}
              </option>
            ))}
          </select>
        </FilterField>
        <FilterField id="audit-from" label={t('settings.access.audit.filterFrom')}>
          <Input
            id="audit-from"
            type="date"
            value={filters.from}
            max={filters.to || undefined}
            onChange={(e) => set({ from: e.target.value })}
          />
        </FilterField>
        <FilterField id="audit-to" label={t('settings.access.audit.filterTo')}>
          <Input
            id="audit-to"
            type="date"
            value={filters.to}
            min={filters.from || undefined}
            onChange={(e) => set({ to: e.target.value })}
          />
        </FilterField>
      </div>
      {filtered && (
        <Button variant="outline" size="sm" onClick={() => setFilters(EMPTY_AUDIT_FILTERS)}>
          {t('settings.access.audit.reset')}
        </Button>
      )}

      {error && (
        <p role="alert" className="text-sm text-destructive">
          {error}
        </p>
      )}
      {loaded && entries.length === 0 && (
        <p className="text-sm text-muted-foreground">{t('settings.access.audit.empty')}</p>
      )}
      {entries.length > 0 && visible.length === 0 && (
        <p className="text-sm text-muted-foreground">{t('settings.access.audit.noMatch')}</p>
      )}

      {visible.length > 0 && (
        <ul className="divide-y divide-border" aria-label={t('settings.access.audit.title')}>
          {visible.map((entry) => (
            <li key={entry.id} className="space-y-1 py-2" data-testid="audit-row">
              <div className="flex flex-wrap items-center gap-x-3 gap-y-1">
                <span className="text-sm font-medium">{actionText(entry.action)}</span>
                <OutcomeBadge outcome={entry.outcome} />
                {entry.via && (
                  <span className="rounded-full bg-muted px-2 py-0.5 text-[0.6875rem] text-muted-foreground">
                    {isKnownAuditVia(entry.via)
                      ? t(`settings.access.audit.via.${entry.via}`)
                      : entry.via}
                  </span>
                )}
              </div>
              <div className="flex flex-wrap gap-x-4 gap-y-0.5 text-[0.8125rem] text-muted-foreground">
                <span>{formatEpoch(entry.ts, i18n.language)}</span>
                <span>
                  {t('settings.access.audit.colActor')}: {actorLabel(entry)}
                </span>
                {entry.target && (
                  <span>
                    {t('settings.access.audit.colTarget')}: {targetLabel(entry.target)}
                  </span>
                )}
                {entry.ip && (
                  <span>
                    {t('settings.access.audit.colIp')}: {entry.ip}
                  </span>
                )}
              </div>
            </li>
          ))}
        </ul>
      )}

      <div className="flex flex-wrap items-center gap-3">
        {cursor && (
          <Button variant="outline" size="sm" disabled={loading} onClick={() => loadPage(cursor)}>
            {loading ? t('settings.access.audit.loading') : t('settings.access.audit.loadMore')}
          </Button>
        )}
        {loaded && !cursor && entries.length > 0 && (
          <span className="text-[0.8125rem] text-muted-foreground">
            {t('settings.access.audit.endOfLog')}
          </span>
        )}
        {entries.length > 0 && (
          <span className="text-[0.8125rem] text-muted-foreground">
            {t('settings.access.audit.loaded', { shown: visible.length, loaded: entries.length })}
          </span>
        )}
      </div>
    </SettingsGroup>
  );
}

function FilterField({
  id,
  label,
  children,
}: {
  id: string;
  label: string;
  children: React.ReactNode;
}) {
  return (
    <div className="flex flex-col gap-1">
      <Label htmlFor={id}>{label}</Label>
      {children}
    </div>
  );
}

function OutcomeBadge({ outcome }: { outcome: string }) {
  const { t } = useTranslation();
  const tone =
    outcome === 'success'
      ? 'bg-primary/15 text-primary'
      : outcome === 'failure' || outcome === 'error'
        ? 'bg-destructive/15 text-destructive'
        : 'bg-warning/15 text-warning';
  return (
    <span className={`rounded-full px-2 py-0.5 text-[0.6875rem] ${tone}`}>
      {isKnownAuditOutcome(outcome) ? t(`settings.access.audit.outcomes.${outcome}`) : outcome}
    </span>
  );
}
