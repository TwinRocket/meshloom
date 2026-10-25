import { lazy, Suspense, useEffect, useMemo, useState } from 'react';
import { useTranslation } from 'react-i18next';

import { api, ApiError, formatApiError, getApiErrorCode } from '../../../api';
import type { Channel } from '../../../types';
import { Button } from '../../ui/button';
import { Input } from '../../ui/input';
import { Label } from '../../ui/label';
import { toast } from '../../ui/sonner';
import { HooksPicker } from './CreateLocalDialog';
import {
  declaredPermissions,
  httpHosts,
  httpLanHosts,
  httpParams,
  permissionKey,
} from './pluginsShared';
import type {
  CheckFinding,
  LocalHook,
  PluginCheckResult,
  PluginInstance,
  PluginTestResult,
  ScanReason,
} from './pluginsTypes';

const BotCodeEditor = lazy(() =>
  import('../../BotCodeEditor').then((m) => ({ default: m.BotCodeEditor }))
);

type Permissions = Record<string, Record<string, unknown>>;

const LOCAL_PERMISSIONS = [
  'messages.read',
  'messages.send',
  'raw_packets.read',
  'contacts.read',
  'channels.read',
  'telemetry.read',
  'storage',
  'timer',
  'http',
] as const;

const DEFAULT_PARAMS: Record<string, Record<string, unknown>> = {
  'messages.read': { channels: 'public' },
  'messages.send': { targets: 'reply' },
  http: { hosts: [] },
};

/** One entry per non-empty line. */
function lines(text: string): string[] {
  return text
    .split('\n')
    .map((h) => h.trim())
    .filter(Boolean);
}

/** Test channel (the API default), a known channel, or a contact's public key. */
type TestTarget = 'default' | 'channel' | 'contact';

function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <section className="space-y-2">
      <h3 className="text-sm font-semibold">{title}</h3>
      {children}
    </section>
  );
}

function reasonText(t: (key: string, options?: Record<string, unknown>) => string, r: ScanReason) {
  return t(`settings.extensions.local.reason.${r.code}`, {
    defaultValue: r.code,
    detail: r.detail ?? '',
  });
}

function Findings({ items, tone }: { items: CheckFinding[]; tone: 'error' | 'warning' }) {
  const { t } = useTranslation();
  return (
    <ul className="space-y-1 text-sm">
      {items.map((f, index) => (
        <li
          key={`${f.code}-${f.line ?? 0}-${index}`}
          className={tone === 'error' ? 'text-destructive' : 'text-warning'}
        >
          {f.line ? (
            <span className="font-mono">
              {t('settings.extensions.local.line', { line: f.line })}
            </span>
          ) : null}{' '}
          {t(`settings.extensions.local.reason.${f.code}`, {
            defaultValue: f.message,
            detail: f.message.includes(': ') ? f.message.split(': ').slice(1).join(': ') : '',
          })}
        </li>
      ))}
    </ul>
  );
}

/**
 * The local plugin authoring flow, inside the instance drawer: code (saved with
 * `PUT source`), permissions and events, Check, and Test with a message.
 */
export function LocalPluginEditor({
  instance,
  locked,
  onChanged,
}: {
  instance: PluginInstance;
  locked: boolean;
  onChanged: (instance: PluginInstance) => void;
}) {
  const { t } = useTranslation();
  const saved = instance.source ?? '';
  const [code, setCode] = useState(saved);
  const [scan, setScan] = useState<ScanReason[] | null>(null);
  const [hosts, setHosts] = useState<string[]>([]);
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const dirty = code !== saved;
  const off = locked || busy !== null;

  const [granted, setGranted] = useState<Permissions>(
    () => ({ ...declaredPermissions(instance) }) as Permissions
  );
  const [hooks, setHooks] = useState<LocalHook[]>(
    () => ((instance.manifest.hooks as LocalHook[] | undefined) ?? []) as LocalHook[]
  );

  const [check, setCheck] = useState<PluginCheckResult | null>(null);

  const saveCode = async () => {
    setBusy('code');
    setError(null);
    try {
      const result = await api.putPluginSource(instance.instance_id, code);
      onChanged(result.instance);
      setScan(result.scan);
      setHosts(result.http_hosts);
      setCheck(null);
      toast.success(t('settings.extensions.local.codeSaved'));
    } catch (err) {
      const detail = err instanceof ApiError ? getApiErrorCode(err.detail) : null;
      const line = detail?.params.line;
      setError(
        detail?.code === 'source_syntax_error' && typeof line === 'number'
          ? t('settings.extensions.local.syntaxError', { line })
          : formatApiError(err, t)
      );
    } finally {
      setBusy(null);
    }
  };

  /** One PATCH with whatever changed (hooks and/or permissions), so a refusal leaves nothing half-saved. */
  const savePermissions = async (patch: { permissions?: Permissions; hooks?: LocalHook[] }) => {
    setBusy('permissions');
    setError(null);
    try {
      const updated = await api.patchPlugin(instance.instance_id, {
        ...(patch.permissions ? { granted_permissions: patch.permissions } : {}),
        ...(patch.hooks ? { hooks: patch.hooks } : {}),
      });
      onChanged(updated);
      return updated;
    } catch (err) {
      setError(formatApiError(err, t));
      return null;
    } finally {
      setBusy(null);
    }
  };

  const savePending = async () => {
    const currentHooks = (instance.manifest.hooks as string[] | undefined) ?? [];
    const hooksChanged =
      JSON.stringify([...hooks].sort()) !== JSON.stringify([...currentHooks].sort());
    const permissionsChanged =
      JSON.stringify(granted) !== JSON.stringify(declaredPermissions(instance));
    const updated = await savePermissions({
      ...(permissionsChanged ? { permissions: granted } : {}),
      ...(hooksChanged ? { hooks } : {}),
    });
    if (updated) {
      setGranted({ ...declaredPermissions(updated) } as Permissions);
      toast.success(t('settings.extensions.local.permissionsSaved'));
    }
  };

  const runCheck = async () => {
    setBusy('check');
    setError(null);
    try {
      const result = await api.checkPlugin(instance.instance_id);
      setCheck(result);
      setHosts(result.http_hosts);
    } catch (err) {
      setError(formatApiError(err, t));
    } finally {
      setBusy(null);
    }
  };

  // Only the host is saved: other unsaved edits in the section stay pending.
  const acceptHost = async (host: string) => {
    const saved = declaredPermissions(instance) as Permissions;
    const next = {
      ...saved,
      http: httpParams([...new Set([...httpHosts(saved), host])], httpLanHosts(saved)),
    };
    const updated = await savePermissions({ permissions: next });
    if (updated) {
      setGranted((prev) => ({
        ...prev,
        http: httpParams([...new Set([...httpHosts(prev), host])], httpLanHosts(prev)),
      }));
    }
  };
  const pendingHosts = hosts.filter((h) => !httpHosts(granted).includes(h));

  const permsChanged =
    JSON.stringify(granted) !== JSON.stringify(declaredPermissions(instance)) ||
    JSON.stringify([...hooks].sort()) !==
      JSON.stringify([...((instance.manifest.hooks as string[]) ?? [])].sort());

  return (
    <div className="space-y-5" data-testid="local-plugin-editor">
      {error && (
        <p role="alert" className="text-sm text-destructive">
          {error}
        </p>
      )}

      <Section title={t('settings.extensions.local.codeTitle')}>
        <Suspense
          fallback={
            <p className="text-sm text-muted-foreground">{t('settings.extensions.loading')}</p>
          }
        >
          <BotCodeEditor
            value={code}
            onChange={setCode}
            height="288px"
            ariaLabel={t('settings.extensions.local.editorAria')}
          />
        </Suspense>
        <div className="flex flex-wrap items-center gap-2">
          <Button onClick={saveCode} disabled={off || !dirty}>
            {t('settings.extensions.local.saveCode')}
          </Button>
          {dirty && (
            <span className="text-xs text-muted-foreground">
              {t('settings.extensions.local.unsaved')}
            </span>
          )}
        </div>
        {scan !== null && scan.length > 0 && (
          <div className="space-y-1" data-testid="local-scan">
            <p className="text-sm font-medium">{t('settings.extensions.local.scanTitle')}</p>
            <ul className="space-y-1 text-sm">
              {scan.map((r, index) => (
                <li
                  key={`${r.code}-${r.line}-${index}`}
                  className={r.informative ? 'text-muted-foreground' : 'text-warning'}
                >
                  {r.line > 0 && (
                    <span className="font-mono">
                      {t('settings.extensions.local.line', { line: r.line })}
                    </span>
                  )}{' '}
                  {reasonText(t, r)}
                </li>
              ))}
            </ul>
          </div>
        )}
        {pendingHosts.length > 0 && (
          <div className="space-y-1 rounded-md border border-warning/50 bg-warning/10 p-2 text-sm">
            <p>{t('settings.extensions.local.hostsFound')}</p>
            {pendingHosts.map((host) => (
              <div key={host} className="flex flex-wrap items-center gap-2">
                <span className="break-all font-mono">{host}</span>
                <Button size="sm" variant="outline" disabled={off} onClick={() => acceptHost(host)}>
                  {t('settings.extensions.local.allowHost', { host })}
                </Button>
              </div>
            ))}
          </div>
        )}
      </Section>

      <Section title={t('settings.extensions.local.permissionsTitle')}>
        <HooksPicker value={hooks} onChange={setHooks} disabled={off} />
        <PermissionsEditor value={granted} onChange={setGranted} disabled={off} />
        {permsChanged && (
          <Button onClick={() => void savePending()} disabled={off || hooks.length === 0}>
            {t('settings.extensions.local.savePermissions')}
          </Button>
        )}
      </Section>

      <Section title={t('settings.extensions.local.checkTitle')}>
        <p className="text-xs text-muted-foreground">{t('settings.extensions.local.checkHint')}</p>
        <Button variant="outline" onClick={runCheck} disabled={off || dirty}>
          {t('settings.extensions.local.check')}
        </Button>
        {dirty && (
          <p className="text-xs text-muted-foreground">
            {t('settings.extensions.local.saveFirst')}
          </p>
        )}
        {check && (
          <div className="space-y-2" data-testid="local-check">
            <p
              className={check.ok ? 'text-sm text-success' : 'text-sm text-destructive'}
              role="status"
            >
              {check.ok
                ? t('settings.extensions.local.checkOk')
                : t('settings.extensions.local.checkFailed')}
            </p>
            <Findings items={check.errors} tone="error" />
            <Findings items={check.warnings} tone="warning" />
          </div>
        )}
      </Section>

      <TestSection instance={instance} disabled={off || dirty} onError={setError} />
    </div>
  );
}

function PermissionsEditor({
  value,
  onChange,
  disabled,
}: {
  value: Permissions;
  onChange: (next: Permissions) => void;
  disabled: boolean;
}) {
  const { t } = useTranslation();
  const set = (name: string, params: Record<string, unknown> | null) => {
    const next = { ...value };
    if (params === null) delete next[name];
    else next[name] = params;
    onChange(next);
  };
  return (
    <div className="space-y-2">
      {LOCAL_PERMISSIONS.map((name) => {
        const params = value[name];
        const on = params !== undefined;
        const key = permissionKey(name);
        return (
          <div key={name} className="space-y-1">
            <label className="flex items-start gap-2 text-sm">
              <input
                type="checkbox"
                className="mt-1"
                disabled={disabled}
                checked={on}
                onChange={(e) =>
                  set(name, e.target.checked ? { ...(DEFAULT_PARAMS[name] ?? {}) } : null)
                }
              />
              <span>{t(`${key}.title`)}</span>
            </label>
            {on && name === 'messages.read' && (
              <div className="ml-6 space-y-1 text-sm">
                <select
                  aria-label={t(`${key}.title`)}
                  className="h-9 w-full rounded-md border border-input bg-background px-2"
                  disabled={disabled}
                  value={params.channels === 'all' ? 'all' : 'public'}
                  onChange={(e) => set(name, { ...params, channels: e.target.value })}
                >
                  <option value="public">{t(`${key}.channels_public`)}</option>
                  <option value="all">{t(`${key}.channels_all`)}</option>
                </select>
                {(['direct', 'outgoing'] as const).map((flag) => (
                  <label key={flag} className="flex items-center gap-2">
                    <input
                      type="checkbox"
                      disabled={disabled}
                      checked={params[flag] === true}
                      onChange={(e) => set(name, { ...params, [flag]: e.target.checked })}
                    />
                    <span>{t(`${key}.${flag}`)}</span>
                  </label>
                ))}
              </div>
            )}
            {on && name === 'messages.send' && (
              <select
                aria-label={t(`${key}.title`)}
                className="ml-6 h-9 w-[calc(100%-1.5rem)] rounded-md border border-input bg-background px-2 text-sm"
                disabled={disabled}
                value={typeof params.targets === 'string' ? params.targets : 'reply'}
                onChange={(e) => set(name, { ...params, targets: e.target.value })}
              >
                {['reply', 'scope', 'any'].map((target) => (
                  <option key={target} value={target}>
                    {t(`${key}.targets_${target}`)}
                  </option>
                ))}
              </select>
            )}
            {on && name === 'http' && (
              <div className="ml-6 space-y-1">
                <textarea
                  aria-label={t('settings.extensions.local.hostsLabel')}
                  rows={3}
                  className="w-full rounded-md border border-input bg-background px-2 py-1 font-mono text-sm"
                  disabled={disabled}
                  placeholder={t('settings.extensions.local.hostsPlaceholder')}
                  value={httpHosts(value).join('\n')}
                  onChange={(e) =>
                    set(name, httpParams(lines(e.target.value), httpLanHosts(value)))
                  }
                />
                <textarea
                  aria-label={t('settings.extensions.local.lanHostsLabel')}
                  rows={2}
                  className="w-full rounded-md border border-input bg-background px-2 py-1 font-mono text-sm"
                  disabled={disabled}
                  placeholder={t('settings.extensions.local.lanHostsPlaceholder')}
                  value={httpLanHosts(value).join('\n')}
                  onChange={(e) => set(name, httpParams(httpHosts(value), lines(e.target.value)))}
                />
              </div>
            )}
          </div>
        );
      })}
    </div>
  );
}

function TestSection({
  instance,
  disabled,
  onError,
}: {
  instance: PluginInstance;
  disabled: boolean;
  onError: (message: string | null) => void;
}) {
  const { t } = useTranslation();
  const [text, setText] = useState('');
  const [target, setTarget] = useState<TestTarget>('default');
  const [channels, setChannels] = useState<Channel[] | null>(null);
  const [channel, setChannel] = useState('');
  const [contact, setContact] = useState('');
  const [busy, setBusy] = useState(false);
  const [result, setResult] = useState<PluginTestResult | null>(null);
  const hasMessageHook = ((instance.manifest.hooks as string[] | undefined) ?? []).includes(
    'message'
  );

  useEffect(() => {
    if (target !== 'channel' || channels !== null) return;
    let alive = true;
    api
      .getChannels()
      .then((rows) => alive && setChannels(rows))
      .catch(() => alive && setChannels([]));
    return () => {
      alive = false;
    };
  }, [target, channels]);

  const canRun =
    text.trim() !== '' &&
    (target === 'default' ||
      (target === 'channel' && channel !== '') ||
      (target === 'contact' && contact.trim() !== ''));

  const run = async () => {
    setBusy(true);
    onError(null);
    try {
      setResult(
        await api.testPlugin(instance.instance_id, {
          text,
          ...(target === 'channel' ? { channel } : {}),
          ...(target === 'contact' ? { contact: contact.trim() } : {}),
        })
      );
    } catch (err) {
      setResult(null);
      onError(formatApiError(err, t));
    } finally {
      setBusy(false);
    }
  };

  const sends = useMemo(() => result?.sends ?? [], [result]);

  return (
    <Section title={t('settings.extensions.local.testTitle')}>
      <p className="text-xs text-muted-foreground">{t('settings.extensions.local.testHint')}</p>
      {!hasMessageHook && (
        <p className="text-xs text-warning">{t('settings.extensions.local.testNoHook')}</p>
      )}
      {!instance.enabled && (
        <p className="text-xs text-muted-foreground">
          {t('settings.extensions.local.testNeedsOn')}
        </p>
      )}
      <div className="space-y-1">
        <Label htmlFor="local-test-text">{t('settings.extensions.local.testText')}</Label>
        <Input
          id="local-test-text"
          value={text}
          maxLength={1000}
          onChange={(e) => setText(e.target.value)}
        />
      </div>
      <div className="space-y-1">
        <Label htmlFor="local-test-target">{t('settings.extensions.local.testTarget')}</Label>
        <select
          id="local-test-target"
          className="h-10 w-full rounded-md border border-input bg-background px-2 text-base md:text-sm"
          value={target}
          onChange={(e) => setTarget(e.target.value as TestTarget)}
        >
          <option value="default">{t('settings.extensions.local.targetDefault')}</option>
          <option value="channel">{t('settings.extensions.local.targetChannel')}</option>
          <option value="contact">{t('settings.extensions.local.targetContact')}</option>
        </select>
        {target === 'channel' && (
          <select
            aria-label={t('settings.extensions.local.targetChannel')}
            className="h-10 w-full rounded-md border border-input bg-background px-2 text-base md:text-sm"
            value={channel}
            onChange={(e) => setChannel(e.target.value)}
          >
            <option value="">{t('settings.extensions.local.pickChannel')}</option>
            {(channels ?? []).map((c) => (
              <option key={c.key} value={c.key}>
                {c.name}
              </option>
            ))}
          </select>
        )}
        {target === 'contact' && (
          <Input
            aria-label={t('settings.extensions.local.targetContact')}
            placeholder={t('settings.extensions.local.contactPlaceholder')}
            value={contact}
            onChange={(e) => setContact(e.target.value)}
          />
        )}
      </div>
      <Button
        variant="outline"
        onClick={run}
        disabled={disabled || busy || !canRun || !instance.enabled || !hasMessageHook}
      >
        {t('settings.extensions.local.test')}
      </Button>
      {result && (
        <div className="space-y-2" data-testid="local-test-result">
          <p className="rounded-md border border-border bg-muted/40 p-2 text-sm" role="status">
            {t('settings.extensions.local.nothingSent')}
          </p>
          <div>
            <p className="text-sm font-medium">{t('settings.extensions.local.echoTitle')}</p>
            {sends.length === 0 ? (
              <p className="text-sm text-muted-foreground">
                {t('settings.extensions.local.noReply')}
              </p>
            ) : (
              <ul className="space-y-1 text-sm">
                {sends.map((send, index) => (
                  <li key={index} className="break-words rounded-md border border-border p-2">
                    {typeof send.text === 'string' ? send.text : JSON.stringify(send)}
                  </li>
                ))}
              </ul>
            )}
            {result.truncated && (
              <p className="text-xs text-muted-foreground">
                {t('settings.extensions.local.truncated')}
              </p>
            )}
          </div>
          {result.logs.length > 0 && (
            <div>
              <p className="text-sm font-medium">{t('settings.extensions.local.testLog')}</p>
              <pre className="max-h-40 overflow-auto whitespace-pre-wrap break-all rounded-md border border-border bg-muted/40 p-2 text-xs">
                {result.logs.join('\n')}
              </pre>
            </div>
          )}
        </div>
      )}
    </Section>
  );
}
