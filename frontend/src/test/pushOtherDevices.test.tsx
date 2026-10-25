import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';

const toastMocks = vi.hoisted(() => ({ error: vi.fn(), success: vi.fn() }));
vi.mock('../components/ui/sonner', () => ({ toast: toastMocks }));
vi.mock('../contexts/PushSubscriptionContext', () => ({
  usePush: () => ({
    isSupported: false,
    allSubscriptions: [],
    preferences: null,
    loading: false,
    subscribe: vi.fn(),
    currentSubscriptionId: null,
    patchPreferences: vi.fn(),
    deleteSubscription: vi.fn(),
    testPush: vi.fn(),
    refreshSubscriptions: vi.fn(),
  }),
}));

import { api } from '../api';
import { PushDevicesBlock } from '../components/settings/notifications/PushDevicesBlock';
import '../i18n';
import type { PushSubscriptionInfo } from '../types';

function device(overrides: Partial<PushSubscriptionInfo>): PushSubscriptionInfo {
  return {
    id: 'x',
    endpoint: 'https://fcm.googleapis.com',
    p256dh: '',
    auth: '',
    label: '',
    language: 'en',
    created_at: 0,
    last_success_at: null,
    failure_count: 0,
    mine: false,
    owner_username: 'lea',
    ...overrides,
  };
}

afterEach(() => {
  vi.restoreAllMocks();
});

describe('PushDevicesBlock, other accounts (MSHL-290)', () => {
  it('lists the other accounts devices for an admin and removes one', async () => {
    const list = vi
      .spyOn(api, 'getPushSubscriptions')
      .mockResolvedValueOnce([
        device({ id: 'own', label: 'My phone', mine: true, owner_username: 'admin' }),
        device({ id: 'other', label: 'Lea phone' }),
      ])
      .mockResolvedValueOnce([]);
    const remove = vi.spyOn(api, 'deletePushSubscription').mockResolvedValue({ deleted: true });

    render(<PushDevicesBlock appSettings={null} />);

    expect(await screen.findByText('Lea phone')).toBeInTheDocument();
    expect(screen.queryByText('My phone')).not.toBeInTheDocument();
    expect(list).toHaveBeenCalledWith('all');
    const block = screen.getByTestId('push-other-devices');
    expect(within(block).getByText(/lea$/)).toBeInTheDocument();

    fireEvent.click(within(block).getByRole('button'));
    await waitFor(() => expect(remove).toHaveBeenCalledWith('other'));
    await waitFor(() => expect(screen.queryByTestId('push-other-devices')).not.toBeInTheDocument());
  });
});
