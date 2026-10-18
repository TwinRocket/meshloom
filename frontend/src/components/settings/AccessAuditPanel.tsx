import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { useVirtualizer } from '@tanstack/react-virtual';
import { useTranslation } from 'react-i18next';

import { api } from '../../api';
import { formatAccessError } from '../../auth/accessErrors';
import type { AuthAuditEntry, AuthAuditPage, AuthUserRow } from '../../auth/types';
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
const ESTIMATED_ROW_HEIGHT = 64;
const FALLBACK_VIEWPORT_HEIGHT = 480;

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
  // Kept raw and formatted at render, so a language switch retranslates it without a refetch.
  const [error, setError] = useState<unknown>(null);
  const [users, setUsers] = useState<AuthUserRow[]>([]);
  const [filters, setFilters] = useState<AuditFilters>(EMPTY_AUDIT_FILTERS);
  const scrollRef = useRef<HTMLDivElement>(null);
  // Bumped on unmount: a response that lands afterwards is ignored.
  const generation = useRef(0);

  const appendPage = (page: AuthAuditPage) => {
    setEntries((prev) => {
      const seen = new Set(prev.map((e) => e.id));
      return [...prev, ...page.entries.filter((e) => !seen.has(e.id))];
    });
    setCursor(page.next_cursor);
  };

  const loadMore = async () => {
    if (!cursor || loading) return;
    const mine = generation.current;
    setLoading(true);
    setError(null);
    try {
      const page = await api.getAuthAudit(cursor, PAGE_SIZE);
      if (mine === generation.current) appendPage(page);
    } catch (err) {
      if (mine === generation.current) setError(err);
    } finally {
      if (mine === generation.current) setLoading(false);
    }
  };

  // Once per mount: `t` is not a dependency, so a language switch keeps the pages.
  useEffect(() => {
    const mine = generation.current;
    api
      .getAuthAudit(null, PAGE_SIZE)
      .then((page) => {
        if (mine !== generation.current) return;
        appendPage(page);
        setLoaded(true);
      })
      .catch((err) => {
        if (mine === generation.current) setError(err);
      });
    api
      .listAuthUsers()
      .then((list) => {
        if (mine === generation.current) setUsers(list);
      })
      .catch(() => {});
    return () => {
      generation.current += 1;
    };
  }, []);

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
  const virtualizer = useVirtualizer({
    count: visible.length,
    getScrollElement: () => scrollRef.current,
    estimateSize: () => ESTIMATED_ROW_HEIGHT,
    getItemKey: (index) => visible[index]?.id ?? index,
    overscan: 6,
    measureElement: (element) => element.getBoundingClientRect().height || ESTIMATED_ROW_HEIGHT,
    observeElementRect: (instance, cb) => {
      const element = instance.scrollElement;
      if (!element) return;
      const report = () => {
        const rect = element.getBoundingClientRect();
        cb({ width: rect.width, height: rect.height || FALLBACK_VIEWPORT_HEIGHT });
      };
      report();
      const observer = new ResizeObserver(report);
      observer.observe(element);
      return () => observer.disconnect();
    },
  });
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

      {error != null && (
        <p role="alert" className="text-sm text-destructive">
          {formatAccessError(error, t)}
        </p>
      )}
      {loaded && entries.length === 0 && (
        <p className="text-sm text-muted-foreground">{t('settings.access.audit.empty')}</p>
      )}
      {entries.length > 0 && visible.length === 0 && (
        <p className="text-sm text-muted-foreground">{t('settings.access.audit.noMatch')}</p>
      )}

      {visible.length > 0 && (
        <div
          ref={scrollRef}
          className="h-[28rem] overflow-y-auto overscroll-contain rounded-md border border-border"
        >
          <ul
            aria-label={t('settings.access.audit.title')}
            className="relative w-full"
            style={{ height: virtualizer.getTotalSize() }}
          >
            {virtualizer.getVirtualItems().map((item) => {
              const entry = visible[item.index];
              return (
                <li
                  key={entry.id}
                  ref={virtualizer.measureElement}
                  data-index={item.index}
                  data-testid="audit-row"
                  className="absolute left-0 top-0 w-full space-y-1 border-b border-border px-3 py-2"
                  style={{ transform: `translateY(${item.start}px)` }}
                >
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
              );
            })}
          </ul>
        </div>
      )}

      <div className="flex flex-wrap items-center gap-3">
        {cursor && (
          <Button variant="outline" size="sm" disabled={loading} onClick={loadMore}>
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
