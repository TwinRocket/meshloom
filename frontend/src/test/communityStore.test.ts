import { waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import type { CommunityStatus } from '../types';

const mocks = vi.hoisted(() => ({
  getCommunity: vi.fn<() => Promise<CommunityStatus>>(),
}));

vi.mock('../api', () => ({
  api: { getCommunity: () => mocks.getCommunity() },
}));

import {
  getCommunitySnapshot,
  loadCommunityStatus,
  noteCommunityLiveOptedOut,
  refreshCommunityStatus,
  seedCommunityEnabled,
  setCommunityStatus,
} from '../stores/communityStore';

const on: CommunityStatus = {
  enabled: true,
  iata: 'LYS',
  publisher_configured: true,
  publisher_connected: true,
};

describe('communityStore', () => {
  beforeEach(() => {
    mocks.getCommunity.mockReset();
  });

  it('starts unknown, takes the settings seed, then the real answer wins', () => {
    expect(getCommunitySnapshot().enabled).toBeNull();
    seedCommunityEnabled(true);
    expect(getCommunitySnapshot().enabled).toBe(true);
    setCommunityStatus({ ...on, enabled: false });
    seedCommunityEnabled(true);
    expect(getCommunitySnapshot().enabled).toBe(false);
  });

  it('shares one request between concurrent refreshes', async () => {
    mocks.getCommunity.mockResolvedValue(on);
    await Promise.all([refreshCommunityStatus(), refreshCommunityStatus()]);
    expect(mocks.getCommunity).toHaveBeenCalledTimes(1);
    expect(getCommunitySnapshot().status).toEqual(on);
  });

  it('keeps the last state on a failure; load rejects with the error, refresh gives null', async () => {
    setCommunityStatus(on);
    const failure = new Error('Radio key is not available');
    mocks.getCommunity.mockRejectedValue(failure);

    await expect(loadCommunityStatus()).rejects.toBe(failure);
    await expect(refreshCommunityStatus()).resolves.toBeNull();
    expect(getCommunitySnapshot().status).toEqual(on);
  });

  it('re-reads the status when another tab turns Community off', async () => {
    setCommunityStatus(on);
    mocks.getCommunity.mockResolvedValue({ ...on, enabled: false });

    noteCommunityLiveOptedOut(true);
    await waitFor(() => {
      expect(getCommunitySnapshot().enabled).toBe(false);
    });
    expect(mocks.getCommunity).toHaveBeenCalledTimes(1);
  });

  it('re-reads on a status that agrees with the switch: the IATA may have moved', async () => {
    setCommunityStatus(on);
    mocks.getCommunity.mockResolvedValue({ ...on, iata: 'CDG' });

    noteCommunityLiveOptedOut(false);
    await waitFor(() => {
      expect(getCommunitySnapshot().status?.iata).toBe('CDG');
    });
    expect(getCommunitySnapshot().enabled).toBe(true);
  });

  it('ignores a status before anything is known', () => {
    noteCommunityLiveOptedOut(true);
    expect(mocks.getCommunity).not.toHaveBeenCalled();
  });
});
