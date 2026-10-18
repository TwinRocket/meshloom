import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { ApiError, api } from '../api';
import type { AuthSettings } from '../auth/types';
import { AccessPublicAddressesPanel } from '../components/settings/AccessPublicAddressesPanel';
import i18n from '../i18n';

const toastError = vi.fn();
vi.mock('../components/ui/sonner', () => ({
  toast: Object.assign(vi.fn(), { error: (...a: unknown[]) => toastError(...a), success: vi.fn() }),
  Toaster: () => null,
}));

const t = i18n.t.bind(i18n);

let settings: AuthSettings;
let patch: ReturnType<typeof vi.fn>;

beforeEach(() => {
  toastError.mockReset();
  settings = {
    trusted_proxies: [],
    audit_retention_days: 366,
    ingress_default_role: 'member',
    public_urls: ['https://mesh.example.com'],
    host_guard_mode: 'observe',
    rejected_hosts: [
      { host: 'proxy.example.org', count: 3, first_seen: 1_700_000_000, last_seen: 1_700_000_100 },
    ],
  };
  vi.spyOn(api, 'getAuthSettings').mockImplementation(async () => structuredClone(settings));
  patch = vi.fn(async (change: Partial<AuthSettings>) => {
    settings = { ...settings, ...change };
    if (change.public_urls) {
      settings.rejected_hosts = (settings.rejected_hosts ?? []).filter(
        (h) => !change.public_urls!.includes(`https://${h.host}`)
      );
    }
    return structuredClone(settings);
  });
  vi.spyOn(api, 'patchAuthSettings').mockImplementation(patch as never);
});

afterEach(() => vi.restoreAllMocks());

describe('AccessPublicAddressesPanel', () => {
  it('lists the declared addresses and the observed hosts', async () => {
    render(<AccessPublicAddressesPanel />);
    expect(await screen.findByText('https://mesh.example.com')).toBeInTheDocument();
    expect(screen.getByText('proxy.example.org')).toBeInTheDocument();
  });

  it('adds an address', async () => {
    const user = userEvent.setup();
    render(<AccessPublicAddressesPanel />);
    await user.type(
      await screen.findByLabelText(t('settings.access.public.address')),
      'https://b.example.net'
    );
    await user.click(screen.getByRole('button', { name: t('settings.access.public.add') }));
    await waitFor(() =>
      expect(patch).toHaveBeenCalledWith({
        public_urls: ['https://mesh.example.com', 'https://b.example.net'],
      })
    );
    expect(await screen.findByText('https://b.example.net')).toBeInTheDocument();
  });

  it('removes an address', async () => {
    const user = userEvent.setup();
    render(<AccessPublicAddressesPanel />);
    await user.click(
      await screen.findByRole('button', {
        name: t('settings.access.public.removeFor', { url: 'https://mesh.example.com' }),
      })
    );
    await waitFor(() => expect(patch).toHaveBeenCalledWith({ public_urls: [] }));
  });

  it('"Allow" declares an observed host and clears it from the list', async () => {
    const user = userEvent.setup();
    render(<AccessPublicAddressesPanel />);
    await user.click(
      await screen.findByRole('button', {
        name: t('settings.access.public.allowFor', { host: 'proxy.example.org' }),
      })
    );
    await waitFor(() =>
      expect(patch).toHaveBeenCalledWith({
        public_urls: ['https://mesh.example.com', 'https://proxy.example.org'],
      })
    );
    expect(await screen.findByText('https://proxy.example.org')).toBeInTheDocument();
    expect(screen.getByText(t('settings.access.public.rejectedNone'))).toBeInTheDocument();
  });

  it('switches the guard to enforce', async () => {
    const user = userEvent.setup();
    render(<AccessPublicAddressesPanel />);
    await user.click(
      await screen.findByRole('checkbox', { name: t('settings.access.public.enforce') })
    );
    await waitFor(() => expect(patch).toHaveBeenCalledWith({ host_guard_mode: 'enforce' }));
    expect(await screen.findByText(t('settings.access.public.enforceHelpOn'))).toBeInTheDocument();
  });

  it('shows the translated server refusal and keeps the state', async () => {
    const user = userEvent.setup();
    patch.mockRejectedValueOnce(
      new ApiError('Bad Request', 400, {
        code: 'auth_host_guard_would_lock_out',
        message: 'x',
        params: { host: 'mesh.example.com' },
      })
    );
    render(<AccessPublicAddressesPanel />);
    await user.click(
      await screen.findByRole('checkbox', { name: t('settings.access.public.enforce') })
    );
    await waitFor(() => expect(toastError).toHaveBeenCalled());
    expect(toastError.mock.calls[0][0]).toContain('mesh.example.com');
    expect(
      screen.getByRole('checkbox', { name: t('settings.access.public.enforce') })
    ).toHaveAttribute('data-state', 'unchecked');
  });
});
