import { act, renderHook, waitFor } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';

import { ApiError, api } from '../api';
import {
  OSS_UPDATE_RESTART_TIMEOUT_MS,
  OSS_UPDATE_SUCCEEDED_STALE_MS,
  UPDATE_TARGET_STORAGE_KEY,
  UPDATE_TOAST_SEEN_KEY,
  jobProgressPercent,
  ossUpdateActions,
  useOssUpdates,
  versionsMatch,
} from '../hooks/useOssUpdates';
import i18n from '../i18n';
import type { OssUpdateJob, OssUpdateStatus } from '../types';
import { getSettingsHash } from '../utils/urlHash';

const toastMock = vi.hoisted(() => vi.fn());
vi.mock('../components/ui/sonner', () => ({
  toast: toastMock,
}));

const idleJob: OssUpdateJob = {
  state: 'idle',
  phase: null,
  percent: null,
  error: null,
  started_at: null,
};

function status(overrides: Partial<OssUpdateStatus> = {}): OssUpdateStatus {
  return {
    current: '1.0.0',
    latest: '1.1.0',
    update_available: true,
    html_url: 'https://example.invalid/release',
    install_kind: 'package',
    apply_supported: true,
    auto_update: false,
    auto_update_window_start: '02:00',
    auto_update_window_end: '05:00',
    auto_update_weekdays: [0, 1, 2, 3, 4, 5, 6],
    checked_at: 1_700_000_000,
    tz_name: 'UTC',
    next_auto_apply_at: null,
    job: idleJob,
    ...overrides,
  };
}

describe('jobProgressPercent', () => {
  it('prefers a numeric percent over the phase map', () => {
    expect(
      jobProgressPercent({
        state: 'applying',
        phase: 'installing',
        percent: 42,
        error: null,
        started_at: 1,
      })
    ).toBe(42);
  });

  it('maps phases when percent is null', () => {
    expect(jobProgressPercent({ ...idleJob, phase: 'preparing' })).toBe(10);
    expect(jobProgressPercent({ ...idleJob, phase: 'downloading' })).toBe(50);
    expect(jobProgressPercent({ ...idleJob, phase: 'installing' })).toBe(80);
    expect(jobProgressPercent({ ...idleJob, phase: 'restarting' })).toBe(90);
    expect(jobProgressPercent({ ...idleJob, phase: 'done' })).toBe(100);
  });
});

describe('versionsMatch', () => {
  it('ignores a leading v', () => {
    expect(versionsMatch('v1.1.0', '1.1.0')).toBe(true);
    expect(versionsMatch('1.0.0', '1.1.0')).toBe(false);
  });
});

describe('useOssUpdates', () => {
  afterEach(() => {
    vi.useRealTimers();
    vi.restoreAllMocks();
    toastMock.mockClear();
    sessionStorage.clear();
  });

  it('loads /api/updates on mount', async () => {
    const payload = status();
    vi.spyOn(api, 'getUpdates').mockResolvedValue(payload);

    const { result } = renderHook(() => useOssUpdates());
    await waitFor(() => {
      expect(result.current.status).toEqual(payload);
    });
    expect(api.getUpdates).toHaveBeenCalledTimes(1);
  });

  it('still stores a catalogue payload that omits the apply job fields', async () => {
    const payload = {
      current: '1.0.0',
      latest: '1.1.0',
      update_available: true,
      html_url: 'https://example.invalid/release',
    };
    vi.spyOn(api, 'getUpdates').mockResolvedValue(payload as OssUpdateStatus);

    const { result } = renderHook(() => useOssUpdates());
    await waitFor(() => {
      expect(result.current.status?.current).toBe('1.0.0');
    });
    expect(result.current.showProgress).toBe(false);
  });

  it('stores the target and POSTs apply', async () => {
    const payload = status();
    vi.spyOn(api, 'getUpdates').mockResolvedValue(payload);
    vi.spyOn(api, 'applyUpdate').mockResolvedValue({
      ...payload,
      job: {
        state: 'applying',
        phase: 'downloading',
        percent: 40,
        error: null,
        started_at: 10,
      },
    });

    const { result } = renderHook(() => useOssUpdates());
    await waitFor(() => {
      expect(result.current.status).toEqual(payload);
    });

    await act(async () => {
      await result.current.apply();
    });

    expect(sessionStorage.getItem(UPDATE_TARGET_STORAGE_KEY)).toBe('1.1.0');
    expect(api.applyUpdate).toHaveBeenCalledTimes(1);
    expect(result.current.applying).toBe(true);
    expect(result.current.showProgress).toBe(true);
    expect(result.current.progressPercent).toBe(40);
  });

  it('opens the overlay when a job is already applying', async () => {
    vi.spyOn(api, 'getUpdates').mockResolvedValue(
      status({
        job: {
          state: 'applying',
          phase: 'installing',
          percent: null,
          error: null,
          started_at: 1,
        },
      })
    );

    const { result } = renderHook(() => useOssUpdates());
    await waitFor(() => {
      expect(result.current.applying).toBe(true);
    });
    expect(result.current.showProgress).toBe(true);
    expect(result.current.progressPercent).toBe(80);
    expect(result.current.progressPhase).toBe('installing');
  });

  it('stops on a failed job and does not reload', async () => {
    const reload = vi.spyOn(ossUpdateActions, 'reloadWindow').mockImplementation(() => {});
    vi.spyOn(api, 'getUpdates').mockResolvedValue(
      status({
        job: {
          state: 'failed',
          phase: 'installing',
          percent: 80,
          error: 'apt failed',
          started_at: 1,
        },
      })
    );

    sessionStorage.setItem(UPDATE_TARGET_STORAGE_KEY, '1.1.0');
    const { result } = renderHook(() => useOssUpdates());
    await waitFor(() => {
      expect(result.current.applyError).toBe('apt failed');
    });
    expect(result.current.applying).toBe(false);
    expect(reload).not.toHaveBeenCalled();
  });

  it('flashes 100% and reloads when the new version is live', async () => {
    const reload = vi.spyOn(ossUpdateActions, 'reloadWindow').mockImplementation(() => {});
    vi.spyOn(api, 'getUpdates')
      .mockResolvedValueOnce(status())
      .mockResolvedValue(
        status({
          current: '1.1.0',
          update_available: false,
          job: { state: 'succeeded', phase: 'done', percent: null, error: null, started_at: 1 },
        })
      );
    vi.spyOn(api, 'applyUpdate').mockResolvedValue(
      status({
        job: {
          state: 'applying',
          phase: 'preparing',
          percent: null,
          error: null,
          started_at: 1,
        },
      })
    );

    const { result } = renderHook(() => useOssUpdates());
    await waitFor(() => {
      expect(result.current.status?.current).toBe('1.0.0');
    });

    await act(async () => {
      await result.current.apply();
    });

    await waitFor(() => {
      expect(result.current.progressPercent).toBe(100);
    });
    await waitFor(() => {
      expect(reload).toHaveBeenCalledTimes(1);
    });
  });

  it('polls health when /updates fails during apply', async () => {
    const reload = vi.spyOn(ossUpdateActions, 'reloadWindow').mockImplementation(() => {});
    vi.spyOn(api, 'getUpdates')
      .mockResolvedValueOnce(status())
      .mockRejectedValue(new Error('network'));
    vi.spyOn(api, 'applyUpdate').mockResolvedValue(
      status({
        job: {
          state: 'applying',
          phase: 'restarting',
          percent: null,
          error: null,
          started_at: 1,
        },
      })
    );
    vi.spyOn(api, 'getHealth').mockResolvedValue({
      status: 'ok',
      radio_connected: true,
      radio_initializing: false,
      connection_info: null,
      app_info: { version: '1.1.0', commit_hash: 'abc' },
      database_size_mb: 1,
      oldest_undecrypted_timestamp: null,
      fanout_statuses: {},
      bots_disabled: false,
    });

    const { result } = renderHook(() => useOssUpdates());
    await waitFor(() => {
      expect(result.current.status?.current).toBe('1.0.0');
    });
    await act(async () => {
      await result.current.apply();
    });

    await waitFor(() => {
      expect(api.getHealth).toHaveBeenCalled();
    });
    await waitFor(() => {
      expect(reload).toHaveBeenCalledTimes(1);
    });
  });

  it('fails after five minutes restarting on the old version', async () => {
    vi.useFakeTimers();
    const reload = vi.spyOn(ossUpdateActions, 'reloadWindow').mockImplementation(() => {});
    vi.spyOn(api, 'getUpdates')
      .mockResolvedValueOnce(status())
      .mockRejectedValue(new Error('down'));
    vi.spyOn(api, 'applyUpdate').mockRejectedValue(new Error('connection reset'));
    vi.spyOn(api, 'getHealth').mockResolvedValue({
      status: 'ok',
      radio_connected: true,
      radio_initializing: false,
      connection_info: null,
      app_info: { version: '1.0.0', commit_hash: 'abc' },
      database_size_mb: 1,
      oldest_undecrypted_timestamp: null,
      fanout_statuses: {},
      bots_disabled: false,
    });

    const { result } = renderHook(() => useOssUpdates());
    await act(async () => {
      await Promise.resolve();
    });
    await act(async () => {
      await result.current.apply();
    });
    expect(result.current.progressPhase).toBe('restarting');

    await act(async () => {
      await vi.advanceTimersByTimeAsync(OSS_UPDATE_RESTART_TIMEOUT_MS);
    });
    expect(result.current.applying).toBe(false);
    expect(result.current.applyError).toBeTruthy();
    expect(reload).not.toHaveBeenCalled();
  });

  it('unlocks the overlay when the helper succeeded without the new version', async () => {
    vi.useFakeTimers();
    const reload = vi.spyOn(ossUpdateActions, 'reloadWindow').mockImplementation(() => {});
    sessionStorage.setItem(UPDATE_TARGET_STORAGE_KEY, '1.1.0');
    vi.spyOn(api, 'getUpdates').mockResolvedValue(
      status({
        job: { state: 'succeeded', phase: 'done', percent: 100, error: null, started_at: 1 },
      })
    );

    const { result } = renderHook(() => useOssUpdates());
    await act(async () => {
      await Promise.resolve();
    });
    await act(async () => {
      await vi.advanceTimersByTimeAsync(OSS_UPDATE_SUCCEEDED_STALE_MS);
    });
    expect(result.current.applying).toBe(false);
    expect(result.current.applyError).toBeTruthy();
    expect(result.current.showProgress).toBe(true);
    expect(reload).not.toHaveBeenCalled();
  });

  it('clears a completed target after reload and does not reopen the overlay', async () => {
    sessionStorage.setItem(UPDATE_TARGET_STORAGE_KEY, '1.1.0');
    vi.spyOn(api, 'getUpdates').mockResolvedValue(
      status({ current: '1.1.0', update_available: false })
    );

    const { result } = renderHook(() => useOssUpdates());
    await waitFor(() => {
      expect(result.current.status?.current).toBe('1.1.0');
    });
    expect(sessionStorage.getItem(UPDATE_TARGET_STORAGE_KEY)).toBeNull();
    expect(result.current.showProgress).toBe(false);
  });

  it('PATCHes auto-update settings', async () => {
    vi.spyOn(api, 'getUpdates').mockResolvedValue(status());
    vi.spyOn(api, 'patchUpdateSettings').mockResolvedValue(status({ auto_update: true }));

    const { result } = renderHook(() => useOssUpdates());
    await waitFor(() => {
      expect(result.current.status).toBeTruthy();
    });
    await act(async () => {
      await result.current.setAutoUpdate(true);
    });
    expect(api.patchUpdateSettings).toHaveBeenCalledWith({ auto_update: true });
    expect(result.current.status?.auto_update).toBe(true);
  });

  it('POSTs /updates/refresh from checkNow', async () => {
    const payload = status({ latest: '1.0.0', update_available: false });
    const refreshed = status({ latest: '1.2.0', checked_at: 1_700_000_100 });
    vi.spyOn(api, 'getUpdates').mockResolvedValue(payload);
    vi.spyOn(api, 'refreshUpdates').mockResolvedValue(refreshed);

    const { result } = renderHook(() => useOssUpdates());
    await waitFor(() => {
      expect(result.current.status?.latest).toBe('1.0.0');
    });
    await act(async () => {
      await result.current.checkNow();
    });
    expect(api.refreshUpdates).toHaveBeenCalledTimes(1);
    expect(result.current.status?.latest).toBe('1.2.0');
    expect(result.current.checking).toBe(false);
  });

  it('PATCHes window settings through setUpdateSettings', async () => {
    vi.spyOn(api, 'getUpdates').mockResolvedValue(status());
    vi.spyOn(api, 'patchUpdateSettings').mockResolvedValue(
      status({
        auto_update: true,
        auto_update_window_start: '03:00',
        auto_update_weekdays: [0, 6],
      })
    );

    const { result } = renderHook(() => useOssUpdates());
    await waitFor(() => {
      expect(result.current.status).toBeTruthy();
    });
    await act(async () => {
      await result.current.setUpdateSettings({
        auto_update: true,
        auto_update_window_start: '03:00',
        auto_update_weekdays: [0, 6],
      });
    });
    expect(api.patchUpdateSettings).toHaveBeenCalledWith({
      auto_update: true,
      auto_update_window_start: '03:00',
      auto_update_weekdays: [0, 6],
    });
    expect(result.current.status?.auto_update_window_start).toBe('03:00');
  });

  it('toasts a new latest once and opens settings from Voir', async () => {
    const payload = status();
    vi.spyOn(api, 'getUpdates').mockResolvedValue(payload);

    const { result, unmount } = renderHook(() => useOssUpdates());
    await waitFor(() => {
      expect(result.current.status).toEqual(payload);
    });
    expect(toastMock).toHaveBeenCalledTimes(1);
    expect(toastMock).toHaveBeenCalledWith(
      i18n.t('updates.toastTitle'),
      expect.objectContaining({
        description: i18n.t('updates.toastBody', { current: '1.0.0', latest: '1.1.0' }),
        action: expect.objectContaining({ label: i18n.t('updates.toastSee') }),
      })
    );
    expect(sessionStorage.getItem(UPDATE_TOAST_SEEN_KEY)).toBe('1.1.0');

    const action = toastMock.mock.calls[0][1] as { action: { onClick: () => void } };
    action.action.onClick();
    expect(window.location.hash).toBe(getSettingsHash('updates'));

    unmount();
    toastMock.mockClear();
    const again = renderHook(() => useOssUpdates());
    await waitFor(() => {
      expect(again.result.current.status).toEqual(payload);
    });
    expect(toastMock).not.toHaveBeenCalled();
    again.unmount();
  });

  it('toasts again when latest changes and prefers onSeeUpdate', async () => {
    const onSeeUpdate = vi.fn();
    vi.spyOn(api, 'getUpdates').mockResolvedValue(status());
    sessionStorage.setItem(UPDATE_TOAST_SEEN_KEY, '1.1.0');

    const { result } = renderHook(() => useOssUpdates({ onSeeUpdate }));
    await waitFor(() => {
      expect(result.current.status?.latest).toBe('1.1.0');
    });
    expect(toastMock).not.toHaveBeenCalled();

    vi.spyOn(api, 'refreshUpdates').mockResolvedValue(status({ latest: '1.2.0' }));
    await act(async () => {
      await result.current.checkNow();
    });
    expect(toastMock).toHaveBeenCalledTimes(1);
    expect(sessionStorage.getItem(UPDATE_TOAST_SEEN_KEY)).toBe('1.2.0');

    const action = toastMock.mock.calls[0][1] as { action: { onClick: () => void } };
    action.action.onClick();
    expect(onSeeUpdate).toHaveBeenCalled();
  });

  it('keeps polling when apply is already in progress', async () => {
    vi.spyOn(api, 'getUpdates').mockResolvedValue(
      status({
        job: {
          state: 'applying',
          phase: 'downloading',
          percent: 50,
          error: null,
          started_at: 1,
        },
      })
    );
    vi.spyOn(api, 'applyUpdate').mockRejectedValue(
      new ApiError('apply_in_progress', 409, 'apply_in_progress')
    );

    const { result } = renderHook(() => useOssUpdates());
    await waitFor(() => {
      expect(result.current.status).toBeTruthy();
    });
    await act(async () => {
      await result.current.apply();
    });
    await waitFor(() => {
      expect(result.current.progressPercent).toBe(50);
    });
    expect(result.current.applying).toBe(true);
    expect(result.current.applyError).toBeNull();
  });

  it('surfaces apply_not_supported without treating it as a restart', async () => {
    vi.spyOn(api, 'getUpdates').mockResolvedValue(status());
    vi.spyOn(api, 'applyUpdate').mockRejectedValue(
      new ApiError('apply_not_supported', 409, 'apply_not_supported')
    );

    const { result } = renderHook(() => useOssUpdates());
    await waitFor(() => {
      expect(result.current.status).toBeTruthy();
    });
    await act(async () => {
      await result.current.apply();
    });
    expect(result.current.applying).toBe(false);
    expect(result.current.applyError).toBe('apply_not_supported');
  });
});
