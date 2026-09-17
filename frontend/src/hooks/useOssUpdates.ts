import { useCallback, useEffect, useRef, useState } from 'react';

import { ApiError, api } from '../api';
import i18n from '../i18n';
import type { OssUpdateJob, OssUpdateJobPhase, OssUpdateStatus } from '../types';

/** Backend already caches at 300s; this is just so a long-lived tab notices. */
export const OSS_UPDATE_POLL_MS = 5 * 60 * 1000;
export const OSS_UPDATE_JOB_POLL_MS = 1500;
export const OSS_UPDATE_RESTART_TIMEOUT_MS = 5 * 60 * 1000;
/** Helper finished and the API is back, but current !== target. */
export const OSS_UPDATE_SUCCEEDED_STALE_MS = 15 * 1000;
export const OSS_UPDATE_RELOAD_FLASH_MS = 500;
export const UPDATE_TARGET_STORAGE_KEY = 'meshloom.updateTarget';

const PHASE_PERCENT: Record<OssUpdateJobPhase, number> = {
  preparing: 10,
  downloading: 50,
  installing: 80,
  restarting: 90,
  done: 100,
};

export function normalizeUpdateVersion(value: string | null | undefined): string {
  if (!value) return '';
  const text = value.trim();
  if (text.length > 1 && (text[0] === 'v' || text[0] === 'V') && /\d/.test(text[1])) {
    return text.slice(1);
  }
  return text;
}

export function versionsMatch(
  left: string | null | undefined,
  right: string | null | undefined
): boolean {
  const a = normalizeUpdateVersion(left);
  const b = normalizeUpdateVersion(right);
  return a.length > 0 && a === b;
}

export function jobProgressPercent(
  job: OssUpdateJob | null | undefined,
  fallbackPhase?: OssUpdateJobPhase | null
): number {
  if (typeof job?.percent === 'number' && Number.isFinite(job.percent)) {
    return Math.max(0, Math.min(100, job.percent));
  }
  const phase = job?.phase ?? fallbackPhase ?? null;
  if (phase && phase in PHASE_PERCENT) {
    return PHASE_PERCENT[phase];
  }
  return 0;
}

export const ossUpdateActions = {
  reloadWindow(): void {
    window.location.reload();
  },
};

function readUpdateTarget(): string | null {
  try {
    return sessionStorage.getItem(UPDATE_TARGET_STORAGE_KEY);
  } catch {
    return null;
  }
}

function writeUpdateTarget(latest: string): void {
  try {
    sessionStorage.setItem(UPDATE_TARGET_STORAGE_KEY, latest);
  } catch {
    // Private mode — this tab still keeps the in-memory target.
  }
}

function isApplyInProgressError(err: unknown): boolean {
  return (
    err instanceof ApiError &&
    err.status === 409 &&
    (err.detail === 'apply_in_progress' || err.message === 'apply_in_progress')
  );
}

function clearUpdateTarget(): void {
  try {
    sessionStorage.removeItem(UPDATE_TARGET_STORAGE_KEY);
  } catch {
    // ignore
  }
}

export interface UseOssUpdatesResult {
  status: OssUpdateStatus | null;
  refresh: () => Promise<void>;
  apply: () => Promise<void>;
  setAutoUpdate: (enabled: boolean) => Promise<void>;
  showProgress: boolean;
  applying: boolean;
  progressPercent: number;
  progressPhase: OssUpdateJobPhase | null;
  applyError: string | null;
  dismissProgress: () => void;
}

export function useOssUpdates(): UseOssUpdatesResult {
  const [status, setStatus] = useState<OssUpdateStatus | null>(null);
  const [showProgress, setShowProgress] = useState(false);
  const [applying, setApplying] = useState(false);
  const [progressPercent, setProgressPercent] = useState(0);
  const [progressPhase, setProgressPhase] = useState<OssUpdateJobPhase | null>(null);
  const [applyError, setApplyError] = useState<string | null>(null);

  const statusRef = useRef<OssUpdateStatus | null>(null);
  const targetRef = useRef<string | null>(null);
  const jobTimerRef = useRef<number | null>(null);
  const restartTimerRef = useRef<number | null>(null);
  const succeededTimerRef = useRef<number | null>(null);
  const reloadingRef = useRef(false);
  const cancelledRef = useRef(false);

  const persistStatus = (next: OssUpdateStatus) => {
    statusRef.current = next;
    setStatus(next);
  };

  const stopRestartTimeout = () => {
    if (restartTimerRef.current != null) {
      window.clearTimeout(restartTimerRef.current);
      restartTimerRef.current = null;
    }
  };

  const stopSucceededTimeout = () => {
    if (succeededTimerRef.current != null) {
      window.clearTimeout(succeededTimerRef.current);
      succeededTimerRef.current = null;
    }
  };

  const stopJobPolling = () => {
    if (jobTimerRef.current != null) {
      window.clearInterval(jobTimerRef.current);
      jobTimerRef.current = null;
    }
    stopRestartTimeout();
    stopSucceededTimeout();
  };

  const failRestartTimeout = () => {
    stopJobPolling();
    setApplying(false);
    setShowProgress(true);
    setApplyError(i18n.t('updates.restartTimeout'));
  };

  const armRestartTimeout = () => {
    if (restartTimerRef.current != null) return;
    restartTimerRef.current = window.setTimeout(() => {
      restartTimerRef.current = null;
      failRestartTimeout();
    }, OSS_UPDATE_RESTART_TIMEOUT_MS);
  };

  const failSucceededStale = () => {
    stopJobPolling();
    setApplying(false);
    setShowProgress(true);
    setApplyError(i18n.t('updates.versionUnchanged'));
  };

  const armSucceededStaleTimeout = () => {
    if (succeededTimerRef.current != null) return;
    succeededTimerRef.current = window.setTimeout(() => {
      succeededTimerRef.current = null;
      failSucceededStale();
    }, OSS_UPDATE_SUCCEEDED_STALE_MS);
  };

  const flashAndReload = async () => {
    if (reloadingRef.current || cancelledRef.current) return;
    reloadingRef.current = true;
    stopJobPolling();
    setApplying(true);
    setShowProgress(true);
    setApplyError(null);
    setProgressPhase('done');
    setProgressPercent(100);
    await new Promise<void>((resolve) => {
      window.setTimeout(resolve, OSS_UPDATE_RELOAD_FLASH_MS);
    });
    if (!cancelledRef.current) {
      ossUpdateActions.reloadWindow();
    }
  };

  const currentTarget = () =>
    targetRef.current ?? readUpdateTarget() ?? statusRef.current?.latest ?? null;

  const tickJob = async () => {
    if (reloadingRef.current || cancelledRef.current) return;
    const target = currentTarget();
    if (target) targetRef.current = target;

    try {
      if (typeof api.getUpdates !== 'function') return;
      const data = await api.getUpdates();
      if (cancelledRef.current || !data || typeof data.current !== 'string') return;
      persistStatus(data);

      if (data.job?.state === 'failed') {
        stopJobPolling();
        setApplying(false);
        setShowProgress(true);
        setApplyError(data.job.error || i18n.t('updates.failed'));
        setProgressPhase(data.job.phase);
        setProgressPercent(jobProgressPercent(data.job));
        return;
      }

      if (target && versionsMatch(data.current, target)) {
        await flashAndReload();
        return;
      }

      if (data.job?.state === 'succeeded') {
        setApplying(true);
        setShowProgress(true);
        setApplyError(null);
        setProgressPhase(data.job.phase);
        setProgressPercent(jobProgressPercent(data.job));
        stopRestartTimeout();
        armSucceededStaleTimeout();
        return;
      }

      if (data.job?.state === 'applying') {
        stopSucceededTimeout();
        setApplying(true);
        setShowProgress(true);
        setApplyError(null);
        setProgressPhase(data.job.phase);
        setProgressPercent(jobProgressPercent(data.job));
        if (data.job.phase === 'restarting') {
          armRestartTimeout();
        } else {
          stopRestartTimeout();
        }
      }
    } catch {
      if (cancelledRef.current) return;
      setApplying(true);
      setShowProgress(true);
      setProgressPhase('restarting');
      setProgressPercent(PHASE_PERCENT.restarting);
      armRestartTimeout();
      try {
        const health = await api.getHealth();
        if (cancelledRef.current) return;
        if (target && versionsMatch(health.app_info?.version, target)) {
          await flashAndReload();
        }
      } catch {
        // API and health are both down — keep waiting until timeout.
      }
    }
  };

  const startJobPolling = () => {
    if (jobTimerRef.current != null) {
      window.clearInterval(jobTimerRef.current);
    }
    void tickJob();
    jobTimerRef.current = window.setInterval(() => {
      void tickJob();
    }, OSS_UPDATE_JOB_POLL_MS);
  };

  const loadStatus = async () => {
    if (typeof api.getUpdates !== 'function') return;
    try {
      const data = await api.getUpdates();
      if (cancelledRef.current || !data || typeof data.current !== 'string') return;
      persistStatus(data);
      const stored = readUpdateTarget();
      if (stored && versionsMatch(data.current, stored)) {
        clearUpdateTarget();
        targetRef.current = null;
        return;
      }
      if (data.job?.state === 'applying' || (stored && !versionsMatch(data.current, stored))) {
        targetRef.current = stored ?? data.latest;
        setShowProgress(true);
        setApplying(true);
        setApplyError(null);
        const fallbackPhase: OssUpdateJobPhase | null =
          data.job.phase ?? (stored ? 'restarting' : null);
        setProgressPhase(fallbackPhase);
        setProgressPercent(jobProgressPercent(data.job, fallbackPhase));
        startJobPolling();
      }
    } catch {
      const stored = readUpdateTarget();
      if (!stored || cancelledRef.current) return;
      targetRef.current = stored;
      setShowProgress(true);
      setApplying(true);
      setProgressPhase('restarting');
      setProgressPercent(PHASE_PERCENT.restarting);
      startJobPolling();
    }
  };

  const refresh = useCallback(async () => {
    await loadStatus();
  }, []);

  const apply = useCallback(async () => {
    const latest = statusRef.current?.latest;
    if (latest) {
      writeUpdateTarget(latest);
      targetRef.current = latest;
    }
    setShowProgress(true);
    setApplying(true);
    setApplyError(null);
    setProgressPhase('preparing');
    setProgressPercent(PHASE_PERCENT.preparing);
    try {
      if (typeof api.applyUpdate !== 'function') {
        startJobPolling();
        return;
      }
      const next = await api.applyUpdate();
      if (cancelledRef.current) return;
      persistStatus(next);
      if (next.job?.state === 'failed') {
        stopJobPolling();
        setApplying(false);
        setApplyError(next.job.error || i18n.t('updates.failed'));
        setProgressPhase(next.job.phase);
        setProgressPercent(jobProgressPercent(next.job));
        return;
      }
      if (versionsMatch(next.current, targetRef.current)) {
        await flashAndReload();
        return;
      }
      setProgressPhase(next.job?.phase ?? 'preparing');
      setProgressPercent(jobProgressPercent(next.job, next.job?.phase ?? 'preparing'));
      startJobPolling();
    } catch (err) {
      if (cancelledRef.current) return;
      if (isApplyInProgressError(err)) {
        startJobPolling();
        return;
      }
      if (err instanceof ApiError && err.status >= 400 && err.status < 500) {
        setApplying(false);
        setApplyError(err.message);
        return;
      }
      setProgressPhase('restarting');
      setProgressPercent(PHASE_PERCENT.restarting);
      startJobPolling();
    }
  }, []);

  const setAutoUpdate = useCallback(async (enabled: boolean) => {
    if (typeof api.patchUpdateSettings !== 'function') return;
    const next = await api.patchUpdateSettings({ auto_update: enabled });
    if (!cancelledRef.current) persistStatus(next);
  }, []);

  const dismissProgress = useCallback(() => {
    setShowProgress((visible) => {
      if (!visible) return false;
      return applying && !applyError ? true : false;
    });
    if (!(applying && !applyError)) {
      setApplyError(null);
    }
  }, [applyError, applying]);

  useEffect(() => {
    cancelledRef.current = false;
    void loadStatus();
    const timer = window.setInterval(() => {
      if (jobTimerRef.current != null) return;
      void loadStatus();
    }, OSS_UPDATE_POLL_MS);
    return () => {
      cancelledRef.current = true;
      window.clearInterval(timer);
      stopJobPolling();
    };
    // Mount-only: polling helpers close over refs so they stay current.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  return {
    status,
    refresh,
    apply,
    setAutoUpdate,
    showProgress,
    applying,
    progressPercent,
    progressPhase,
    applyError,
    dismissProgress,
  };
}
