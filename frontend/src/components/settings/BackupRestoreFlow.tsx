import { type ReactNode, useCallback, useEffect, useRef, useState } from 'react';
import type { TFunction } from 'i18next';
import { useTranslation } from 'react-i18next';
import { Button } from '../ui/button';
import { Input } from '../ui/input';
import { Label } from '../ui/label';
import { toast } from '../ui/sonner';
import { ApiError, api, formatApiError, getApiErrorCode } from '../../api';
import {
  BACKUP_FORMATS,
  type BackupInspectResult,
  type BackupRestoreItem,
  type BackupRestoreJob,
  type BackupRestoreStep,
  type BackupRestoreSteps,
} from '../../types';

const ENCRYPTED_FORMAT = 'meshloom-backup-v2-encrypted';
const V1_FORMATS = ['meshloom-backup-v1', 'remoteterm-backup-v1'];
const POLL_INTERVAL_MS = 1000;
/** Consecutive failed polls (network) before the job view gives up. */
const MAX_POLL_FAILURES = 30;
const RERUN_CODES = new Set(['radio_not_connected', 'radio_rebooting']);

function translated(t: TFunction, key: string, params?: Record<string, unknown>): string | null {
  const out = t(key, params);
  return out === key ? null : out;
}

/** `code:param` -> [code, param] (warnings and item names carry one parameter). */
function splitCode(raw: string): [string, string] {
  const at = raw.indexOf(':');
  return at < 0 ? [raw, ''] : [raw.slice(0, at), raw.slice(at + 1)];
}

/** A step or item code from the restore job, translated; unknown codes as is. */
export function restoreCodeText(code: string, t: TFunction): string {
  return (
    translated(t, `settings.fullRestore.codes.${code}`) ?? translated(t, `errors.${code}`) ?? code
  );
}

/** A restore warning (`code` or `code:param`), translated; unknown ones as is. */
export function restoreWarningText(raw: string, t: TFunction): string {
  const [code, value] = splitCode(raw);
  return translated(t, `settings.fullRestore.warnings.${code}`, { value }) ?? raw;
}

function restoreItemName(raw: string, t: TFunction): string {
  const [name, value] = splitCode(raw);
  return translated(t, `settings.fullRestore.items.${name}`, { value }) ?? raw;
}

/** Export warnings carried by the file, in the restore summary's words. */
function fileWarningTexts(warnings: string[], t: TFunction): string[] {
  const texts = warnings.map(
    (raw) => translated(t, `settings.fullRestore.fileWarnings.${splitCode(raw)[0]}`) ?? raw
  );
  return [...new Set(texts)];
}

function isReportedItem(item: BackupRestoreItem): boolean {
  return item.status === 'failed' || item.status === 'unsupported';
}

/**
 * What a finished restore changed on the server, for the client reload:
 * `identityChanged` when the identity step restored or bound a key (the history
 * may have been erased), `dataChanged` when the Meshloom data was written. A job
 * can end `failed` after either, so callers check this, not `job.state`.
 */
export function restoreChanges(job: BackupRestoreJob): {
  identityChanged: boolean;
  dataChanged: boolean;
} {
  const identity = job.steps.find((step) => step.step === 'identity');
  const identityChanged =
    identity?.status === 'done' && (identity.code === 'restored' || identity.code === 'bound');
  const dataChanged = job.steps.some((step) => step.step === 'meshloom' && step.status === 'done');
  return { identityChanged, dataChanged };
}

// Finished jobs whose onFinished already ran, so a view opened later runs it once
// for a job that ended while no view was open. sessionStorage keeps it across a
// reload; the in-memory copy covers a browser that refuses storage.
const HANDLED_JOBS_KEY = 'meshloom.backupRestore.handledJobs';
const handledJobs = new Set<string>();

function jobHandled(jobId: string): boolean {
  if (handledJobs.has(jobId)) return true;
  try {
    const stored: unknown = JSON.parse(sessionStorage.getItem(HANDLED_JOBS_KEY) ?? '[]');
    return Array.isArray(stored) && stored.includes(jobId);
  } catch {
    return false;
  }
}

function markJobHandled(jobId: string): void {
  handledJobs.add(jobId);
  try {
    const stored: unknown = JSON.parse(sessionStorage.getItem(HANDLED_JOBS_KEY) ?? '[]');
    const ids = Array.isArray(stored) ? stored.filter((id) => typeof id === 'string') : [];
    if (!ids.includes(jobId)) ids.push(jobId);
    sessionStorage.setItem(HANDLED_JOBS_KEY, JSON.stringify(ids.slice(-20)));
  } catch {
    // Storage refused: the in-memory copy still covers this page.
  }
}

type LoadedFile = { backup: Record<string, unknown>; passphrase?: string };

type Phase =
  | { kind: 'idle' }
  | { kind: 'locked'; file: LoadedFile }
  | { kind: 'inspecting' }
  | { kind: 'summary'; file: LoadedFile; inspect: BackupInspectResult }
  | { kind: 'job'; jobId: string; job: BackupRestoreJob | null };

function defaultSteps(inspect: BackupInspectResult): BackupRestoreSteps {
  return {
    identity: inspect.has_private_key,
    meshloom: true,
    radio_settings: inspect.has_radio_settings,
    ble_pin: inspect.has_radio_settings && !inspect.ble_pin_differs_on_ble,
    channels: true,
    contacts: true,
    wifi: inspect.has_wifi && !inspect.wifi_ssid_differs_on_tcp && !inspect.wifi_off_on_tcp,
  };
}

/** The identity decision only applies when the identity step runs. */
function identityChecks(inspect: BackupInspectResult, steps: BackupRestoreSteps) {
  const on = steps.identity && inspect.has_private_key;
  return {
    refusal: on ? inspect.identity_refusal : null,
    replace: on && inspect.identity_requires.replace_live_identity,
    wipe: on && inspect.identity_requires.wipe_history,
    /** An unchecked option: erase the history that has no bound owner. */
    optionalWipe:
      on && !inspect.identity_requires.wipe_history && Boolean(inspect.identity_allows_wipe),
  };
}

function keyPrefix(key: string | null | undefined): string {
  return (key ?? '').slice(0, 12);
}

/**
 * Restore any backup file: inspect first (what it holds, what restoring it
 * changes), then the confirmations the identity decision asks for, then the
 * restore job, polled step by step. Used in Settings > Database and in the
 * radio identity window.
 */
export function BackupRestoreFlow({
  resumeLatest = false,
  onFinished,
  onRunningChange,
}: {
  /** Show the most recent restore job on mount (after a reload) while it runs,
   * or once finished until a view handled it. A job that finished while no
   * view was open still gets its onFinished, once. */
  resumeLatest?: boolean;
  onFinished?: (job: BackupRestoreJob) => void;
  /** True while a restore job runs (the parent keeps itself open meanwhile). */
  onRunningChange?: (running: boolean) => void;
}) {
  const { t } = useTranslation();
  const [phase, setPhase] = useState<Phase>({ kind: 'idle' });
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [passphrase, setPassphrase] = useState('');
  const [steps, setSteps] = useState<BackupRestoreSteps | null>(null);
  const [confirm, setConfirm] = useState(false);
  const [confirmReplace, setConfirmReplace] = useState(false);
  const [confirmWipe, setConfirmWipe] = useState(false);
  const [starting, setStarting] = useState(false);
  const [rebooting, setRebooting] = useState(false);
  const [pollError, setPollError] = useState<string | null>(null);
  // The opened file, kept only for a re-inspect after an identity refusal: it may
  // hold the private key and the Wi-Fi password in clear, or the passphrase.
  const lastFileRef = useRef<LoadedFile | null>(null);
  const finishedRef = useRef<string | null>(null);

  useEffect(
    () => () => {
      lastFileRef.current = null;
    },
    []
  );

  const reset = () => {
    setPhase({ kind: 'idle' });
    setError(null);
    setNotice(null);
    setPollError(null);
    setPassphrase('');
    setSteps(null);
    setConfirm(false);
    setConfirmReplace(false);
    setConfirmWipe(false);
    lastFileRef.current = null;
  };

  const inspect = useCallback(
    async (file: LoadedFile) => {
      setError(null);
      setPhase({ kind: 'inspecting' });
      try {
        const result = await api.inspectBackup(file);
        lastFileRef.current = file;
        setPassphrase('');
        setSteps(defaultSteps(result));
        setConfirm(false);
        setConfirmReplace(false);
        setConfirmWipe(false);
        setPhase({ kind: 'summary', file, inspect: result });
      } catch (err) {
        setError(formatApiError(err, t));
        const code = err instanceof ApiError ? getApiErrorCode(err.detail)?.code : undefined;
        if (code === 'backup_bad_passphrase' || code === 'backup_passphrase_required') {
          setPhase({ kind: 'locked', file: { backup: file.backup } });
        } else {
          lastFileRef.current = null; // back to idle: nothing left to re-inspect
          setPhase({ kind: 'idle' });
        }
      }
    },
    [t]
  );

  // After a reload: show the latest job while it runs, or once finished until
  // a view has handled it. A handled one stays out of the way: the file picker
  // shows, and no stale reboot button.
  useEffect(() => {
    if (!resumeLatest) return;
    let cancelled = false;
    api
      .getLatestRestoreJob()
      .then((job) => {
        if (!cancelled && job) {
          if (job.state !== 'running' && jobHandled(job.job_id)) return;
          setPhase((current) =>
            current.kind === 'idle' ? { kind: 'job', jobId: job.job_id, job } : current
          );
        }
      })
      .catch(() => {});
    return () => {
      cancelled = true;
    };
  }, [resumeLatest]);

  const jobId = phase.kind === 'job' ? phase.jobId : null;
  const jobRunning = phase.kind === 'job' && (phase.job === null || phase.job.state === 'running');
  const running = jobRunning && !pollError;

  useEffect(() => {
    onRunningChange?.(running);
  }, [running, onRunningChange]);

  // Poll the running job.
  useEffect(() => {
    if (!jobId || !jobRunning || pollError) return;
    let cancelled = false;
    let failures = 0;
    const poll = async () => {
      try {
        const job = await api.getRestoreJob(jobId);
        failures = 0;
        if (!cancelled) {
          setPhase((current) =>
            current.kind === 'job' && current.jobId === jobId ? { ...current, job } : current
          );
        }
      } catch (err) {
        if (cancelled) return;
        const code = err instanceof ApiError ? getApiErrorCode(err.detail)?.code : undefined;
        if (
          code === 'backup_restore_job_not_found' ||
          (err instanceof ApiError && err.status === 404)
        ) {
          // Jobs live in memory: the server restarted and forgot this one.
          lastFileRef.current = null;
          setPollError(t('settings.fullRestore.jobLost'));
          return;
        }
        // A dropped connection: keep polling with the same id, for a while.
        failures += 1;
        if (failures >= MAX_POLL_FAILURES) {
          lastFileRef.current = null;
          setPollError(formatApiError(err, t));
        }
      }
    };
    void poll();
    const timer = window.setInterval(poll, POLL_INTERVAL_MS);
    return () => {
      cancelled = true;
      window.clearInterval(timer);
    };
  }, [jobId, jobRunning, pollError, t]);

  // A finished job: notify, and re-inspect when the identity decision changed.
  useEffect(() => {
    if (phase.kind !== 'job' || !phase.job || phase.job.state === 'running') return;
    if (finishedRef.current === phase.job.job_id) return;
    finishedRef.current = phase.job.job_id;
    const identity = phase.job.steps.find((step) => step.step === 'identity');
    const file = lastFileRef.current;
    if (identity?.status === 'failed' && identity.code?.startsWith('backup_identity_') && file) {
      setNotice(t('settings.fullRestore.identityRecheck'));
      void inspect(file);
      return;
    }
    lastFileRef.current = null; // no re-inspect from here: drop the secrets
    markJobHandled(phase.job.job_id);
    onFinished?.(phase.job);
  }, [phase, inspect, onFinished, t]);

  const handleFile = async (file: File) => {
    setError(null);
    setNotice(null);
    let parsed: unknown;
    try {
      parsed = JSON.parse(await file.text());
    } catch {
      setError(t('settings.fullRestore.invalidFile'));
      return;
    }
    if (!parsed || typeof parsed !== 'object' || Array.isArray(parsed)) {
      setError(t('settings.fullRestore.invalidFile'));
      return;
    }
    const backup = parsed as Record<string, unknown>;
    // A file without format is an old Meshloom-only (v1) export.
    const format = typeof backup.format === 'string' ? backup.format : 'meshloom-backup-v1';
    if (!(BACKUP_FORMATS as readonly string[]).includes(format)) {
      setError(t('errors.backup_invalid'));
      return;
    }
    if (format === ENCRYPTED_FORMAT) {
      setPassphrase('');
      setPhase({ kind: 'locked', file: { backup } });
      return;
    }
    await inspect({ backup });
  };

  const startRestore = async (file: LoadedFile, result: BackupInspectResult) => {
    if (!steps) return;
    setStarting(true);
    setError(null);
    setNotice(null);
    try {
      const response = await api.startFullRestore({
        confirm: true,
        backup: file.backup,
        ...(file.passphrase ? { passphrase: file.passphrase } : {}),
        steps,
        replace_live_identity: identityChecks(result, steps).replace && confirmReplace,
        wipe_history:
          (identityChecks(result, steps).wipe || identityChecks(result, steps).optionalWipe) &&
          confirmWipe,
        expected_live_public_key: result.live_radio_public_key,
      });
      finishedRef.current = null;
      setPhase({ kind: 'job', jobId: response.job_id, job: null });
    } catch (err) {
      const code = err instanceof ApiError ? getApiErrorCode(err.detail)?.code : undefined;
      if (code?.startsWith('backup_identity_')) {
        setNotice(t('settings.fullRestore.identityRecheck'));
        await inspect(file);
      } else {
        setError(formatApiError(err, t));
      }
    } finally {
      setStarting(false);
    }
  };

  const reboot = async () => {
    setRebooting(true);
    try {
      await api.rebootRadio();
      toast.success(t('settings.fullRestore.rebootSent'));
    } catch (err) {
      toast.error(formatApiError(err, t));
    } finally {
      setRebooting(false);
    }
  };

  const statusLabel = (status: BackupRestoreStep['status'], code: string | null) => {
    const text = t(`settings.fullRestore.status.${status}`);
    return code ? `${text} (${restoreCodeText(code, t)})` : text;
  };

  return (
    <div className="space-y-3">
      <h4 className="text-sm font-semibold">{t('settings.fullRestore.title')}</h4>
      <p className="text-[0.8125rem] text-muted-foreground">{t('settings.fullRestore.help')}</p>

      {notice && (
        <p className="text-[0.8125rem] text-warning" role="status">
          {notice}
        </p>
      )}
      {error && (
        <p className="text-sm text-destructive" role="alert">
          {error}
        </p>
      )}

      {phase.kind === 'idle' && (
        <div className="space-y-1">
          <Label htmlFor="backup-restore-file" className="text-xs">
            {t('settings.fullRestore.choose')}
          </Label>
          <Input
            id="backup-restore-file"
            type="file"
            accept="application/json,.json"
            onChange={(e) => {
              const file = e.target.files?.[0];
              e.target.value = '';
              if (file) void handleFile(file);
            }}
          />
        </div>
      )}

      {phase.kind === 'locked' && (
        <form
          className="space-y-2"
          onSubmit={(e) => {
            e.preventDefault();
            void inspect({ backup: phase.file.backup, passphrase });
          }}
        >
          <Label htmlFor="backup-restore-passphrase" className="text-xs">
            {t('settings.fullRestore.passphrase')}
          </Label>
          <Input
            id="backup-restore-passphrase"
            type="password"
            autoComplete="current-password"
            value={passphrase}
            onChange={(e) => setPassphrase(e.target.value)}
          />
          <div className="flex gap-2">
            <Button type="submit" variant="outline" disabled={!passphrase}>
              {t('settings.fullRestore.unlock')}
            </Button>
            <Button type="button" variant="ghost" onClick={reset}>
              {t('settings.fullRestore.close')}
            </Button>
          </div>
        </form>
      )}

      {phase.kind === 'inspecting' && (
        <p className="text-sm text-muted-foreground">{t('settings.fullRestore.inspecting')}</p>
      )}

      {phase.kind === 'summary' && steps && (
        <RestoreSummary
          inspect={phase.inspect}
          steps={steps}
          onSteps={setSteps}
          confirm={confirm}
          onConfirm={setConfirm}
          confirmReplace={confirmReplace}
          onConfirmReplace={setConfirmReplace}
          confirmWipe={confirmWipe}
          onConfirmWipe={setConfirmWipe}
          starting={starting}
          onRestore={() => void startRestore(phase.file, phase.inspect)}
          onCancel={reset}
        />
      )}

      {phase.kind === 'job' && (
        <div className="space-y-2" aria-live="polite">
          <ul className="space-y-1 text-sm">
            {(phase.job?.steps ?? []).map((step) => (
              <li key={step.step} data-step={step.step} data-status={step.status}>
                <span className="font-medium">{t(`settings.fullRestore.steps.${step.step}`)}</span>
                {': '}
                <span className="text-muted-foreground">{statusLabel(step.status, step.code)}</span>
                {step.items.some(isReportedItem) && (
                  <ul className="pl-5 list-disc text-[0.8125rem] text-muted-foreground">
                    {step.items.filter(isReportedItem).map((item) => (
                      <li key={item.name} data-item={item.name} data-status={item.status}>
                        {t('settings.fullRestore.itemsLabel', {
                          item: restoreItemName(item.name, t),
                          status: statusLabel(item.status, item.code),
                        })}
                      </li>
                    ))}
                  </ul>
                )}
              </li>
            ))}
          </ul>
          {pollError ? (
            <div className="space-y-2">
              <p className="text-sm text-destructive" role="alert">
                {pollError}
              </p>
              <Button variant="ghost" onClick={reset}>
                {t('settings.fullRestore.another')}
              </Button>
            </div>
          ) : phase.job === null || phase.job.state === 'running' ? (
            <p className="text-sm text-muted-foreground">{t('settings.fullRestore.restoring')}</p>
          ) : (
            <div className="space-y-2">
              <p className="text-sm font-semibold">
                {phase.job.state === 'done'
                  ? t('settings.fullRestore.done')
                  : t('settings.fullRestore.failed')}
              </p>
              {(phase.job.result?.warnings.length ?? 0) > 0 && (
                <div className="space-y-1" role="status">
                  <p className="text-xs font-semibold">{t('settings.fullRestore.warningsTitle')}</p>
                  <ul className="list-disc pl-5 text-[0.8125rem] text-muted-foreground space-y-0.5">
                    {phase.job.result?.warnings.map((warning) => (
                      <li key={warning}>{restoreWarningText(warning, t)}</li>
                    ))}
                  </ul>
                </div>
              )}
              {phase.job.steps.some((step) => step.code && RERUN_CODES.has(step.code)) && (
                <p className="text-[0.8125rem] text-muted-foreground">
                  {t('settings.fullRestore.rerunHint')}
                </p>
              )}
              {phase.job.result?.reboot_required && (
                <div className="space-y-1">
                  <p className="text-[0.8125rem]">{t('settings.fullRestore.rebootRequired')}</p>
                  <Button variant="outline" disabled={rebooting} onClick={reboot}>
                    {t('settings.fullRestore.rebootNow')}
                  </Button>
                </div>
              )}
              <p className="text-[0.8125rem] text-muted-foreground">
                {t('settings.fullRestore.communityReminder')}
              </p>
              <Button variant="ghost" onClick={reset}>
                {t('settings.fullRestore.another')}
              </Button>
            </div>
          )}
        </div>
      )}
    </div>
  );
}

function Check({
  checked,
  disabled,
  onChange,
  children,
}: {
  checked: boolean;
  disabled?: boolean;
  onChange: (value: boolean) => void;
  children: ReactNode;
}) {
  return (
    <label className={`flex items-start gap-3 ${disabled ? 'opacity-60' : 'cursor-pointer'}`}>
      <input
        type="checkbox"
        checked={checked}
        disabled={disabled}
        onChange={(e) => onChange(e.target.checked)}
        className="mt-0.5 w-4 h-4 rounded border-input accent-primary"
      />
      <span className="text-sm">{children}</span>
    </label>
  );
}

function RestoreSummary({
  inspect,
  steps,
  onSteps,
  confirm,
  onConfirm,
  confirmReplace,
  onConfirmReplace,
  confirmWipe,
  onConfirmWipe,
  starting,
  onRestore,
  onCancel,
}: {
  inspect: BackupInspectResult;
  steps: BackupRestoreSteps;
  onSteps: (steps: BackupRestoreSteps) => void;
  confirm: boolean;
  onConfirm: (value: boolean) => void;
  confirmReplace: boolean;
  onConfirmReplace: (value: boolean) => void;
  confirmWipe: boolean;
  onConfirmWipe: (value: boolean) => void;
  starting: boolean;
  onRestore: () => void;
  onCancel: () => void;
}) {
  const { t } = useTranslation();
  const v1 = V1_FORMATS.includes(inspect.format);
  const checks = identityChecks(inspect, steps);
  const set = (patch: Partial<BackupRestoreSteps>) => onSteps({ ...steps, ...patch });
  const blocked =
    checks.refusal !== null ||
    !confirm ||
    (checks.replace && !confirmReplace) ||
    (checks.wipe && !confirmWipe);

  return (
    <div className="space-y-3">
      <div className="rounded-md border border-border bg-muted/30 p-3 space-y-1 text-[0.8125rem]">
        <p className="font-semibold text-sm">{t('settings.fullRestore.summaryTitle')}</p>
        {inspect.exported_at > 0 && (
          <p>
            {t('settings.fullRestore.exportedAt', {
              date: new Date(inspect.exported_at * 1000).toLocaleString(),
            })}
          </p>
        )}
        {inspect.radio_name && (
          <p>{t('settings.fullRestore.radioName', { name: inspect.radio_name })}</p>
        )}
        {inspect.public_key && (
          <p>{t('settings.fullRestore.fileKey', { key: keyPrefix(inspect.public_key) })}</p>
        )}
        <p>
          {t('settings.fullRestore.counts', {
            contacts: inspect.counts.contacts,
            channels: inspect.counts.channels,
            groups: inspect.counts.groups,
          })}
        </p>
        {v1 ? (
          <p>{t('settings.fullRestore.v1Only')}</p>
        ) : (
          <>
            <p className={inspect.has_private_key ? undefined : 'text-warning'}>
              {inspect.has_private_key
                ? t('settings.fullRestore.hasIdentity')
                : t('settings.fullRestore.noIdentity')}
            </p>
            {inspect.has_wifi && (
              <p>
                {inspect.has_wifi_credentials === false
                  ? t('settings.fullRestore.hasWifiFlagOnly')
                  : t('settings.fullRestore.hasWifi')}
              </p>
            )}
            <p data-testid="identity-relation">
              {!steps.identity || !inspect.has_private_key
                ? t('settings.fullRestore.identity.no_identity')
                : inspect.identity_relation === 'same' && !inspect.live_radio_public_key
                  ? t('settings.fullRestore.identity.sameNotConnected')
                  : t(`settings.fullRestore.identity.${inspect.identity_relation}`)}
            </p>
          </>
        )}
        {inspect.bot_names.length > 0 && (
          <p className="text-warning">
            {t(
              inspect.bots_will_be_skipped
                ? 'settings.fullRestore.botsWillBeSkipped'
                : 'settings.fullRestore.botsRestoredDisabled',
              { names: inspect.bot_names.join(', ') }
            )}
          </p>
        )}
        {(inspect.integrations?.length ?? 0) > 0 && (
          <p className={inspect.integrations.some((i) => i.enabled) ? 'text-warning' : undefined}>
            {t('settings.fullRestore.integrations', {
              list: inspect.integrations
                .map((integration) =>
                  t(
                    integration.enabled
                      ? 'settings.fullRestore.integrationOn'
                      : 'settings.fullRestore.integrationOff',
                    { name: integration.name, type: integration.type }
                  )
                )
                .join(', '),
            })}
          </p>
        )}
        {inspect.radio_proxy_enabled && (
          <p className="text-warning">{t('settings.fullRestore.proxyEnabled')}</p>
        )}
        {(inspect.push_targets?.length ?? 0) > 0 && (
          <p className="text-warning">
            {t('settings.fullRestore.pushTargets', {
              list: (inspect.push_targets ?? [])
                .map((target) =>
                  target.label
                    ? t('settings.fullRestore.pushTarget', {
                        label: target.label,
                        host: target.host,
                      })
                    : target.host
                )
                .join(', '),
            })}
          </p>
        )}
        {inspect.warnings.length > 0 && (
          <div className="pt-1">
            <p className="text-xs font-semibold">{t('settings.fullRestore.fileWarningsTitle')}</p>
            <ul className="list-disc pl-5 space-y-0.5">
              {fileWarningTexts(inspect.warnings, t).map((text) => (
                <li key={text}>{text}</li>
              ))}
            </ul>
          </div>
        )}
      </div>

      <div className="space-y-2">
        {!v1 && inspect.has_private_key && (
          <Check checked={steps.identity} onChange={(value) => set({ identity: value })}>
            {t('settings.fullRestore.steps.identity')}
          </Check>
        )}
        <Check
          checked={steps.meshloom}
          onChange={(value) =>
            set(
              value
                ? { meshloom: true, channels: true, contacts: true }
                : { meshloom: false, channels: false, contacts: false }
            )
          }
        >
          {t('settings.fullRestore.steps.meshloom')}
        </Check>
        {!v1 && (
          <>
            {inspect.has_radio_settings && (
              <>
                <Check
                  checked={steps.radio_settings}
                  onChange={(value) => set({ radio_settings: value })}
                >
                  {t('settings.fullRestore.steps.radio_settings')}
                </Check>
                <div className="pl-7 space-y-1">
                  <Check
                    checked={steps.ble_pin}
                    disabled={!steps.radio_settings}
                    onChange={(value) => set({ ble_pin: value })}
                  >
                    {t('settings.fullRestore.steps.ble_pin')}
                  </Check>
                  {inspect.ble_pin_differs_on_ble && (
                    <p className="text-xs text-warning">
                      {t('settings.fullRestore.blePinWarning')}
                    </p>
                  )}
                </div>
              </>
            )}
            <Check
              checked={steps.channels}
              disabled={!steps.meshloom}
              onChange={(value) => set({ channels: value })}
            >
              {t('settings.fullRestore.steps.channels')}
            </Check>
            <Check
              checked={steps.contacts}
              disabled={!steps.meshloom}
              onChange={(value) => set({ contacts: value })}
            >
              {t('settings.fullRestore.steps.contacts')}
            </Check>
            {!steps.meshloom && (
              <p className="text-xs text-muted-foreground">
                {t('settings.fullRestore.meshloomStepRequired')}
              </p>
            )}
            {inspect.has_wifi && (
              <>
                <Check checked={steps.wifi} onChange={(value) => set({ wifi: value })}>
                  {t('settings.fullRestore.steps.wifi')}
                </Check>
                {inspect.wifi_ssid_differs_on_tcp && (
                  <p className="text-xs text-warning">{t('settings.fullRestore.wifiTcpWarning')}</p>
                )}
                {inspect.wifi_off_on_tcp && (
                  <p className="text-xs text-warning">
                    {t('settings.fullRestore.wifiOffTcpWarning')}
                  </p>
                )}
              </>
            )}
          </>
        )}
      </div>

      {checks.refusal === null && checks.replace && (
        <Check checked={confirmReplace} onChange={onConfirmReplace}>
          {t('settings.fullRestore.identityReplacesLiveConfirm', {
            name: inspect.live_radio_name ?? '?',
            key: keyPrefix(inspect.live_radio_public_key),
          })}
        </Check>
      )}
      {checks.refusal === null && checks.wipe && (
        <Check checked={confirmWipe} onChange={onConfirmWipe}>
          {t('settings.fullRestore.identityWipeConfirm')}
        </Check>
      )}
      {checks.refusal === null && checks.optionalWipe && (
        <Check checked={confirmWipe} onChange={onConfirmWipe}>
          {t('settings.fullRestore.identityWipeOptional')}
        </Check>
      )}

      <Check checked={confirm} onChange={onConfirm}>
        {t('settings.fullRestore.confirm')}
      </Check>

      <p className="text-[0.8125rem] text-muted-foreground">
        {t('settings.fullRestore.communityReminder')}
      </p>

      <div className="flex gap-2">
        <Button onClick={onRestore} disabled={blocked || starting}>
          {starting ? t('settings.fullRestore.restoring') : t('settings.fullRestore.restore')}
        </Button>
        <Button variant="ghost" onClick={onCancel}>
          {t('settings.fullRestore.close')}
        </Button>
      </div>
    </div>
  );
}
