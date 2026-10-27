import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, describe, expect, it, vi } from 'vitest';

import { ApiError, api } from '../api';
import type { AuthSettings, IpBansView } from '../auth/types';
import { AccessIpBanPanel } from '../components/settings/AccessIpBanPanel';
import { toast } from '../components/ui/sonner';
import i18n from '../i18n';

vi.mock('../components/ui/sonner', () => ({
  toast: Object.assign(vi.fn(), { error: vi.fn(), success: vi.fn(), warning: vi.fn() }),
  Toaster: () => null,
}));

const t = i18n.t.bind(i18n);

const settings = (over: Partial<AuthSettings> = {}): AuthSettings =>
  ({
    ip_ban_enabled: true,
    ip_ban_max_failures: 10,
    ip_ban_window_minutes: 10,
    ip_ban_duration_minutes: 60,
    ...over,
  }) as AuthSettings;

const VIEW: IpBansView = {
  bans: [
    {
      address: '198.51.100.20',
      banned_at: 1_800_000_000,
      expires_at: 1_800_003_600,
      failures: 10,
      route: 'login',
    },
  ],
  whitelist: [{ network: '203.0.113.0/24', note: 'office', created_at: 1_800_000_000 }],
};

afterEach(() => {
  vi.restoreAllMocks();
});

describe('AccessIpBanPanel', () => {
  it('stays hidden when the server has no ban settings', async () => {
    const get = vi.spyOn(api, 'getAuthSettings').mockResolvedValue({} as AuthSettings);
    vi.spyOn(api, 'getIpBans').mockResolvedValue(VIEW);
    const { container } = render(<AccessIpBanPanel />);
    await waitFor(() => expect(get).toHaveBeenCalled());
    expect(container).toBeEmptyDOMElement();
  });

  it('lists bans and the whitelist, and unbans an address', async () => {
    vi.spyOn(api, 'getAuthSettings').mockResolvedValue(settings());
    const list = vi
      .spyOn(api, 'getIpBans')
      .mockResolvedValueOnce(VIEW)
      .mockResolvedValue({ ...VIEW, bans: [] });
    const unban = vi.spyOn(api, 'unbanIp').mockResolvedValue(undefined);
    render(<AccessIpBanPanel />);

    expect(await screen.findByText('198.51.100.20')).toBeInTheDocument();
    expect(screen.getByText('203.0.113.0/24')).toBeInTheDocument();
    await userEvent.click(
      screen.getByRole('button', { name: `${t('settings.access.ipBan.unban')} 198.51.100.20` })
    );
    expect(unban).toHaveBeenCalledWith('198.51.100.20');
    expect(await screen.findByText(t('settings.access.ipBan.noBans'))).toBeInTheDocument();
    expect(list).toHaveBeenCalledTimes(2);
  });

  it('refuses thresholds out of bounds without calling the server', async () => {
    vi.spyOn(api, 'getAuthSettings').mockResolvedValue(settings());
    vi.spyOn(api, 'getIpBans').mockResolvedValue(VIEW);
    const patch = vi.spyOn(api, 'patchAuthSettings');
    render(<AccessIpBanPanel />);

    const failures = await screen.findByLabelText(t('settings.access.ipBan.maxFailures'));
    await userEvent.clear(failures);
    await userEvent.type(failures, '2');
    await userEvent.click(screen.getByRole('button', { name: t('settings.access.ipBan.save') }));
    expect(await screen.findByRole('alert')).toBeInTheDocument();
    expect(patch).not.toHaveBeenCalled();

    await userEvent.clear(failures);
    await userEvent.type(failures, '5');
    patch.mockResolvedValue(settings({ ip_ban_max_failures: 5 }));
    await userEvent.click(screen.getByRole('button', { name: t('settings.access.ipBan.save') }));
    expect(patch).toHaveBeenCalledWith({
      ip_ban_max_failures: 5,
      ip_ban_window_minutes: 10,
      ip_ban_duration_minutes: 60,
    });
  });

  it('adds a network to the whitelist and shows a coded refusal', async () => {
    vi.spyOn(api, 'getAuthSettings').mockResolvedValue(settings());
    vi.spyOn(api, 'getIpBans').mockResolvedValue({ bans: [], whitelist: [] });
    const add = vi
      .spyOn(api, 'addIpBanWhitelist')
      .mockRejectedValueOnce(
        new ApiError('Conflict', 409, { code: 'auth_ban_whitelist_full', params: { max: 256 } })
      )
      .mockResolvedValue({ network: '192.0.2.0/24', note: '', created_at: 1 });
    render(<AccessIpBanPanel />);

    await userEvent.type(
      await screen.findByLabelText(t('settings.access.ipBan.network')),
      '192.0.2.0/24'
    );
    const submit = screen.getByRole('button', { name: t('settings.access.ipBan.add') });
    await userEvent.click(submit);
    await waitFor(() =>
      expect(toast.error).toHaveBeenCalledWith(t('errors.auth_ban_whitelist_full', { max: 256 }))
    );
    await userEvent.click(submit);
    expect(add).toHaveBeenLastCalledWith('192.0.2.0/24', '');
    await waitFor(() =>
      expect(toast.success).toHaveBeenCalledWith(
        t('settings.access.ipBan.added', { network: '192.0.2.0/24' })
      )
    );
  });

  it('turns the ban off', async () => {
    vi.spyOn(api, 'getAuthSettings').mockResolvedValue(settings());
    vi.spyOn(api, 'getIpBans').mockResolvedValue(VIEW);
    const patch = vi
      .spyOn(api, 'patchAuthSettings')
      .mockResolvedValue(settings({ ip_ban_enabled: false }));
    render(<AccessIpBanPanel />);
    const toggle = await screen.findByRole('checkbox', {
      name: t('settings.access.ipBan.toggle'),
    });
    await userEvent.click(toggle);
    expect(patch).toHaveBeenCalledWith({ ip_ban_enabled: false });
    await waitFor(() => expect(toggle).not.toBeChecked());
  });
});
