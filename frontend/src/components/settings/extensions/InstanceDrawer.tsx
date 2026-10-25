import { useCallback, useEffect, useMemo, useState } from 'react';
import { useTranslation } from 'react-i18next';

import { api, formatApiError } from '../../../api';
import { Button } from '../../ui/button';
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '../../ui/dialog';
import { Sheet, SheetContent, SheetDescription, SheetHeader, SheetTitle } from '../../ui/sheet';
import { toast } from '../../ui/sonner';
import { InstanceBadges } from './InstanceBadges';
import { LocalPluginEditor } from './LocalPluginEditor';
import { focusConfigForm, PluginConfigForm } from './PluginConfigForm';
import { ConsentWarnings, PermissionList } from './PermissionList';
import { buildScope, parseScope, ScopeEditor } from './ScopeEditor';
import {
  attentionType,
  declaredPermissions,
  displayName,
  formatDiagnostic,
  isFrozen,
  sendQuotaState,
} from './pluginsShared';
import type { PluginInstance } from './pluginsTypes';

const LOG_POLL_MS = 5000;

function Group({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <section className="space-y-2">
      <h3 className="text-sm font-semibold">{title}</h3>
      {children}
    </section>
  );
}

/** Live tail of the instance's log (`GET /plugins/{id}/logs`, polled while the drawer is open). */
function LogsPanel({ instanceId }: { instanceId: string }) {
  const { t } = useTranslation();
  const [lines, setLines] = useState<string[] | null>(null);
  const [truncated, setTruncated] = useState(false);
  const [failed, setFailed] = useState(false);

  useEffect(() => {
    let alive = true;
    const load = () =>
      api
        .getPluginLogs(instanceId)
        .then((logs) => {
          if (!alive) return;
          setLines(logs.lines);
          setTruncated(logs.truncated);
          setFailed(false);
        })
        .catch(() => alive && setFailed(true));
    void load();
    const timer = setInterval(() => {
      if (document.visibilityState !== 'hidden') void load();
    }, LOG_POLL_MS);
    return () => {
      alive = false;
      clearInterval(timer);
    };
  }, [instanceId]);

  return (
    <div className="space-y-1">
      {failed && <p className="text-sm text-destructive">{t('settings.extensions.logs.failed')}</p>}
      {lines !== null && lines.length === 0 && (
        <p className="text-sm text-muted-foreground">{t('settings.extensions.logs.empty')}</p>
      )}
      {lines !== null && lines.length > 0 && (
        <pre
          data-testid="plugin-logs"
          className="max-h-56 overflow-auto whitespace-pre-wrap break-all rounded-md border border-border bg-muted/40 p-2 text-xs"
        >
          {lines.join('\n')}
        </pre>
      )}
      {truncated && (
        <p className="text-xs text-muted-foreground">{t('settings.extensions.logs.truncated')}</p>
      )}
    </div>
  );
}

export function InstanceDrawer({
  instance,
  locked,
  canManage = true,
  onClose,
  onChanged,
  onRemoved,
}: {
  instance: PluginInstance | null;
  /** Plugins are turned off on this server (`plugins_disabled_source`): no write. */
  locked: boolean;
  /** Admin only: every write, the log and the diagnostic (a member reads the status). */
  canManage?: boolean;
  onClose: () => void;
  onChanged: (instance: PluginInstance) => void;
  onRemoved: (instanceId: string) => void;
}) {
  return (
    <Sheet open={instance !== null} onOpenChange={(open) => !open && onClose()}>
      <SheetContent side="right" className="w-full overflow-y-auto p-0 sm:max-w-lg">
        {instance && (
          <DrawerBody
            key={instance.instance_id}
            instance={instance}
            locked={locked}
            canManage={canManage}
            onChanged={onChanged}
            onRemoved={onRemoved}
          />
        )}
      </SheetContent>
    </Sheet>
  );
}

function DrawerBody({
  instance,
  locked,
  canManage,
  onChanged,
  onRemoved,
}: {
  instance: PluginInstance;
  locked: boolean;
  canManage: boolean;
  onChanged: (instance: PluginInstance) => void;
  onRemoved: (instanceId: string) => void;
}) {
  const { t } = useTranslation();
  const frozen = isFrozen(instance);
  const reason = attentionType(instance);
  const declared = useMemo(() => declaredPermissions(instance), [instance]);
  const [granted, setGranted] = useState(
    () => ({ ...instance.granted_permissions }) as Record<string, Record<string, unknown>>
  );
  const [scope, setScope] = useState<Record<string, unknown>>(instance.scope);
  const quota = sendQuotaState(instance);
  const [perHour, setPerHour] = useState(String(quota.perHour));
  const [perDay, setPerDay] = useState(String(quota.perDay));
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [uninstallOpen, setUninstallOpen] = useState(false);
  const [keepData, setKeepData] = useState(false);
  const [diagnostic, setDiagnostic] = useState<string | null>(null);

  const run = useCallback(
    async (action: () => Promise<PluginInstance | void>, success?: string) => {
      setBusy(true);
      setError(null);
      try {
        const result = await action();
        if (result) onChanged(result);
        if (success) toast.success(success);
        return true;
      } catch (err) {
        setError(formatApiError(err, t));
        return false;
      } finally {
        setBusy(false);
      }
    },
    [onChanged, t]
  );

  const readOnly = locked || busy;
  const grantedChanged =
    JSON.stringify(Object.keys(granted).sort()) !==
    JSON.stringify(Object.keys(instance.granted_permissions).sort());
  const scopeChanged =
    JSON.stringify(buildScope(parseScope(scope))) !==
    JSON.stringify(buildScope(parseScope(instance.scope)));
  const hourNum = Number(perHour);
  const dayNum = Number(perDay);
  const quotaValid =
    Number.isInteger(hourNum) &&
    Number.isInteger(dayNum) &&
    hourNum >= 1 &&
    hourNum <= quota.maxHour &&
    dayNum >= 1 &&
    dayNum <= quota.maxDay;
  const quotaChanged = hourNum !== quota.perHour || dayNum !== quota.perDay;
  const hasSend = 'messages.send' in granted && 'messages.send' in declared;
  const dirty = grantedChanged || scopeChanged || (hasSend && quotaChanged);

  const save = () =>
    run(() => {
      const patch: Parameters<typeof api.patchPlugin>[1] = {};
      if (grantedChanged) patch.granted_permissions = granted;
      if (scopeChanged) patch.scope = buildScope(parseScope(scope));
      if (hasSend && quotaChanged) {
        patch.quotas = {
          ...instance.quotas,
          'messages.send': { per_hour: hourNum, per_day: dayNum },
        };
      }
      return api.patchPlugin(instance.instance_id, patch);
    }, t('settings.extensions.drawer.saved'));

  // Resending the grant is the acknowledgement the API asks for before it starts an
  // instance that waits for a review (`attention_required`).
  const acknowledge = () =>
    run(async () => {
      await api.patchPlugin(instance.instance_id, { granted_permissions: granted });
      return api.patchPlugin(instance.instance_id, { enabled: true });
    }, t('settings.extensions.drawer.accepted'));

  const copyDiagnostic = async () => {
    setError(null);
    try {
      const text = formatDiagnostic(await api.getPluginDiagnostic(instance.instance_id));
      try {
        await navigator.clipboard.writeText(text);
        toast.success(t('settings.extensions.drawer.diagnosticCopied'));
      } catch {
        // No clipboard over plain HTTP: show the block so it can be selected.
        setDiagnostic(text);
      }
    } catch (err) {
      setError(formatApiError(err, t));
    }
  };

  const uninstall = async () => {
    const ok = await run(
      () => api.deletePlugin(instance.instance_id, keepData),
      t('settings.extensions.drawer.uninstalled')
    );
    if (ok) {
      setUninstallOpen(false);
      onRemoved(instance.instance_id);
    }
  };

  const canRestart = instance.enabled && !frozen && instance.status !== 'needs_attention';

  return (
    <div className="space-y-5 p-4 pt-12">
      <SheetHeader className="space-y-2 text-left">
        <SheetTitle className="break-words">{displayName(instance)}</SheetTitle>
        <SheetDescription className="break-words">
          {instance.plugin_id} · v{instance.version}
        </SheetDescription>
        <InstanceBadges instance={instance} />
      </SheetHeader>

      {locked && (
        <p className="rounded-md border border-border p-3 text-sm">
          {t('settings.extensions.disabled.drawer')}
        </p>
      )}
      {!canManage && (
        <p
          data-testid="plugin-drawer-member-readonly"
          className="rounded-md border border-border p-3 text-sm text-muted-foreground"
        >
          {t('settings.extensions.drawerMemberReadOnly')}
        </p>
      )}
      {error && (
        <p role="alert" className="text-sm text-destructive">
          {error}
        </p>
      )}

      <Group title={t('settings.extensions.drawer.statusTitle')}>
        <dl className="space-y-1 text-sm">
          {instance.summary && (
            <div>
              <dt className="sr-only">{t('settings.extensions.drawer.summary')}</dt>
              <dd className="break-words">{instance.summary}</dd>
            </div>
          )}
          {instance.last_error && (
            <div>
              <dt className="text-muted-foreground">{t('settings.extensions.drawer.lastError')}</dt>
              <dd className="break-words text-destructive">{instance.last_error}</dd>
            </div>
          )}
          <div className="flex justify-between gap-3">
            <dt className="text-muted-foreground">{t('settings.extensions.drawer.restarts')}</dt>
            <dd>{instance.restarts}</dd>
          </div>
          <div className="flex justify-between gap-3">
            <dt className="text-muted-foreground">{t('settings.extensions.drawer.dropped')}</dt>
            <dd>{instance.dropped_events}</dd>
          </div>
        </dl>
        {frozen && (
          <p className="text-sm text-destructive">
            {t(`settings.extensions.blocked.${instance.status}`)}
          </p>
        )}
        {instance.status === 'needs_attention' && (
          <div className="space-y-2 rounded-md border border-warning/50 bg-warning/10 p-3 text-sm">
            <p>
              {t(`settings.extensions.attention.${reason ?? 'other'}.text`, {
                defaultValue: t('settings.extensions.attention.other.text'),
              })}
            </p>
            {reason === 'config_required' && (
              <Button
                size="sm"
                variant="outline"
                onClick={() => focusConfigForm(instance.instance_id)}
              >
                {t('settings.extensions.config.goTo')}
              </Button>
            )}
            {reason !== 'config_required' && reason !== 'reinstall' && !frozen && (
              <ConsentWarnings permissions={declared} manifest={instance.manifest} />
            )}
            {canManage && reason !== 'config_required' && reason !== 'reinstall' && !frozen && (
              <Button size="sm" onClick={acknowledge} disabled={readOnly}>
                {t('settings.extensions.drawer.acceptAndEnable')}
              </Button>
            )}
          </div>
        )}
      </Group>

      {instance.kind !== 'local' && (
        <Group title={t('settings.extensions.drawer.permissionsTitle')}>
          <p className="text-xs text-muted-foreground">
            {t('settings.extensions.drawer.permissionsHint')}
          </p>
          <PermissionList
            declared={declared}
            granted={granted}
            disabled={readOnly || frozen || !canManage}
            onToggle={(name, on) =>
              setGranted((prev) => {
                const next = { ...prev };
                if (on) next[name] = declared[name] ?? {};
                else delete next[name];
                return next;
              })
            }
          />
        </Group>
      )}

      {canManage && (
        <>
          <Group title={t('settings.extensions.drawer.scopeTitle')}>
            <ScopeEditor scope={scope} onChange={setScope} disabled={readOnly || frozen} />
          </Group>

          {hasSend && (
            <Group title={t('settings.extensions.drawer.quotasTitle')}>
              <p className="text-xs text-muted-foreground">
                {t('settings.extensions.drawer.quotasHint')}
              </p>
              <div className="grid grid-cols-2 gap-3 text-sm">
                <label className="space-y-1">
                  <span>{t('settings.extensions.drawer.perHour')}</span>
                  <input
                    type="number"
                    min={1}
                    max={quota.maxHour}
                    value={perHour}
                    disabled={readOnly || frozen}
                    onChange={(e) => setPerHour(e.target.value)}
                    className="h-10 w-full rounded-md border border-input bg-background px-2"
                  />
                </label>
                <label className="space-y-1">
                  <span>{t('settings.extensions.drawer.perDay')}</span>
                  <input
                    type="number"
                    min={1}
                    max={quota.maxDay}
                    value={perDay}
                    disabled={readOnly || frozen}
                    onChange={(e) => setPerDay(e.target.value)}
                    className="h-10 w-full rounded-md border border-input bg-background px-2"
                  />
                </label>
              </div>
              {!quotaValid && (
                <p className="text-xs text-destructive">
                  {t('settings.extensions.drawer.quotaInvalid', {
                    hour: quota.maxHour,
                    day: quota.maxDay,
                  })}
                </p>
              )}
            </Group>
          )}

          {dirty && (
            <Button onClick={save} disabled={readOnly || frozen || (hasSend && !quotaValid)}>
              {t('settings.extensions.drawer.save')}
            </Button>
          )}

          {instance.kind === 'local' && (
            <Group title={t('settings.extensions.local.title')}>
              <LocalPluginEditor
                instance={instance}
                locked={locked || frozen}
                onChanged={onChanged}
              />
            </Group>
          )}

          <Group title={t('settings.extensions.config.title')}>
            <PluginConfigForm instance={instance} locked={locked || frozen} onChanged={onChanged} />
          </Group>

          <Group title={t('settings.extensions.logs.title')}>
            <LogsPanel instanceId={instance.instance_id} />
          </Group>

          <div className="flex flex-wrap gap-2">
            <Button
              variant="outline"
              disabled={readOnly || !canRestart}
              onClick={() =>
                run(
                  () => api.restartPlugin(instance.instance_id),
                  t('settings.extensions.drawer.restarted')
                )
              }
            >
              {t('settings.extensions.drawer.restart')}
            </Button>
            <Button variant="outline" onClick={copyDiagnostic}>
              {t('settings.extensions.drawer.copyDiagnostic')}
            </Button>
            <Button variant="destructive" disabled={busy} onClick={() => setUninstallOpen(true)}>
              {t('settings.extensions.drawer.uninstall')}
            </Button>
          </div>
          {diagnostic && (
            <textarea
              readOnly
              aria-label={t('settings.extensions.drawer.copyDiagnostic')}
              className="h-40 w-full rounded-md border border-input bg-muted/40 p-2 text-xs"
              value={diagnostic}
              onFocus={(e) => e.currentTarget.select()}
            />
          )}
        </>
      )}

      <Dialog open={uninstallOpen} onOpenChange={setUninstallOpen}>
        <DialogContent className="w-[calc(100%-2rem)] max-w-md">
          <DialogHeader>
            <DialogTitle>
              {t('settings.extensions.uninstall.title', { name: displayName(instance) })}
            </DialogTitle>
            <DialogDescription>{t('settings.extensions.uninstall.text')}</DialogDescription>
          </DialogHeader>
          <label className="flex items-start gap-2 text-sm">
            <input
              type="checkbox"
              className="mt-1"
              checked={keepData}
              onChange={(e) => setKeepData(e.target.checked)}
            />
            <span>{t('settings.extensions.uninstall.keepData')}</span>
          </label>
          <DialogFooter className="gap-2">
            <Button variant="outline" onClick={() => setUninstallOpen(false)} disabled={busy}>
              {t('settings.extensions.cancel')}
            </Button>
            <Button variant="destructive" onClick={uninstall} disabled={busy}>
              {t('settings.extensions.uninstall.confirm')}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  );
}
