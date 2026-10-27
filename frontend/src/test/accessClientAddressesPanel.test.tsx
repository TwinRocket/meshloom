import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, describe, expect, it, vi } from 'vitest';

import { api } from '../api';
import type { AuthSettings } from '../auth/types';
import { AccessClientAddressesPanel } from '../components/settings/AccessClientAddressesPanel';
import i18n from '../i18n';

vi.mock('../components/ui/sonner', () => ({
  toast: Object.assign(vi.fn(), { error: vi.fn(), success: vi.fn(), warning: vi.fn() }),
  Toaster: () => null,
}));

const t = i18n.t.bind(i18n);

const settingsOf = (over: Partial<AuthSettings>): AuthSettings =>
  ({
    trusted_proxies: [],
    addresses_identify_clients: false,
    shared_address_install: true,
    client_public: false,
    ...over,
  }) as AuthSettings;

afterEach(() => {
  vi.restoreAllMocks();
});

describe('AccessClientAddressesPanel', () => {
  it('suggests the setting to a public client on Docker without a proxy, then saves it', async () => {
    vi.spyOn(api, 'getAuthSettings').mockResolvedValue(settingsOf({ client_public: true }));
    const patch = vi
      .spyOn(api, 'patchAuthSettings')
      .mockResolvedValue(settingsOf({ client_public: true, addresses_identify_clients: true }));
    render(<AccessClientAddressesPanel />);

    expect(await screen.findByTestId('client-addresses-suggest')).toBeInTheDocument();
    await userEvent.click(
      screen.getByRole('checkbox', { name: t('settings.access.clientAddresses.toggle') })
    );
    expect(patch).toHaveBeenCalledWith({ addresses_identify_clients: true });
    await waitFor(() => expect(screen.queryByTestId('client-addresses-suggest')).toBeNull());
    expect(
      screen.getByRole('checkbox', { name: t('settings.access.clientAddresses.toggle') })
    ).toBeChecked();
  });

  it.each([
    ['a local client', { client_public: false }],
    ['a declared proxy', { client_public: true, trusted_proxies: ['10.0.0.2/32'] }],
  ])('does not suggest it with %s', async (_label, over) => {
    vi.spyOn(api, 'getAuthSettings').mockResolvedValue(settingsOf(over));
    render(<AccessClientAddressesPanel />);
    await screen.findByText(t('settings.access.clientAddresses.title'));
    expect(screen.queryByTestId('client-addresses-suggest')).toBeNull();
  });

  it('stays hidden outside Docker while off, and shows when already on', async () => {
    const get = vi
      .spyOn(api, 'getAuthSettings')
      .mockResolvedValue(settingsOf({ shared_address_install: false, client_public: true }));
    const { unmount } = render(<AccessClientAddressesPanel />);
    await waitFor(() => expect(get).toHaveBeenCalled());
    expect(screen.queryByText(t('settings.access.clientAddresses.title'))).toBeNull();
    unmount();

    get.mockResolvedValue(
      settingsOf({ shared_address_install: false, addresses_identify_clients: true })
    );
    render(<AccessClientAddressesPanel />);
    expect(await screen.findByText(t('settings.access.clientAddresses.title'))).toBeInTheDocument();
  });
});
