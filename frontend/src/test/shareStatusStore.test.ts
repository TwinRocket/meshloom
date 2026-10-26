import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { api } from '../api';
import {
  getShareStatusSnapshot,
  loadShareStatus,
  resetShareStatusStoreForTests,
  setShareStatus,
} from '../stores/shareStatusStore';
import type { ShareStatus } from '../types';

function status(enabled: boolean): ShareStatus {
  return {
    enabled,
    host: 'meshl.ink',
    hosts: ['meshl.ink'],
    identity_ready: true,
    dm_encryption_available: true,
    banned: false,
    unreachable: false,
    shorten_mode: 'never',
    preview_policy: 'private',
  } as ShareStatus;
}

describe('shareStatusStore error flag', () => {
  beforeEach(() => resetShareStatusStoreForTests());
  afterEach(() => vi.restoreAllMocks());

  it('sets error when the only request fails', async () => {
    vi.spyOn(api, 'getShareStatus').mockRejectedValue(new Error('offline'));
    await expect(loadShareStatus()).rejects.toThrow('offline');
    expect(getShareStatusSnapshot()).toEqual({ status: null, error: true });
  });

  it('does not flag an error when a newer status landed during a failing load', async () => {
    let reject!: (err: Error) => void;
    vi.spyOn(api, 'getShareStatus').mockReturnValue(
      new Promise((_, rej) => {
        reject = rej;
      })
    );
    const load = loadShareStatus();
    // An external success (a settings change elsewhere) arrives while the load is in flight.
    setShareStatus(status(true));
    reject(new Error('late failure'));
    await expect(load).rejects.toThrow('late failure');
    expect(getShareStatusSnapshot()).toEqual({ status: status(true), error: false });
  });

  it('keeps the external success when the failing load was started before it', async () => {
    let reject!: (err: Error) => void;
    vi.spyOn(api, 'getShareStatus').mockReturnValue(
      new Promise((_, rej) => {
        reject = rej;
      })
    );
    const load = loadShareStatus();
    setShareStatus(status(false));
    reject(new Error('late failure'));
    await expect(load).rejects.toThrow();
    const snap = getShareStatusSnapshot();
    expect(snap.error).toBe(false);
    expect(snap.status).toEqual(status(false));
  });

  it('clears the error on the next success', async () => {
    vi.spyOn(api, 'getShareStatus')
      .mockRejectedValueOnce(new Error('offline'))
      .mockResolvedValueOnce(status(true));
    await expect(loadShareStatus()).rejects.toThrow();
    await expect(loadShareStatus()).resolves.toEqual(status(true));
    expect(getShareStatusSnapshot()).toEqual({ status: status(true), error: false });
  });
});
