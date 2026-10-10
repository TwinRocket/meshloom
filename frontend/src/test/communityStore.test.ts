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
  noteCommunityLiveOptedOut,
  refreshCommunityStatus,
  seedCommunityEnabled,
  setCommunityStatus,
} from '../stores/communityStore';

const on: CommunityStatus = {
  enabled: true,
  iata: 'LYS',
  broker_host: '',
  api_base: '',
  publisher_configured: true,
  publisher_connected: true,
  env_seeded: true,
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

  it('re-reads the status when another tab turns Community off', async () => {
    setCommunityStatus(on);
    mocks.getCommunity.mockResolvedValue({ ...on, enabled: false });

    noteCommunityLiveOptedOut(false);
    expect(mocks.getCommunity).not.toHaveBeenCalled();

    noteCommunityLiveOptedOut(true);
    await waitFor(() => {
      expect(getCommunitySnapshot().enabled).toBe(false);
    });
    expect(mocks.getCommunity).toHaveBeenCalledTimes(1);
  });
});
