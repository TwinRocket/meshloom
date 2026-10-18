import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { ApiError, api } from '../api';
import type { AuthSettings, AuthUserRow } from '../auth/types';
import { SettingsAccessSection } from '../components/settings/SettingsAccessSection';
import {
  AccessSecurityNoticeBanner,
  AccessTrustedNetworksPanel,
} from '../components/settings/AccessTrustedNetworksPanel';
import { toast } from '../components/ui/sonner';
import i18n from '../i18n';

vi.mock('../components/ui/sonner', () => ({
  toast: Object.assign(vi.fn(), { error: vi.fn(), success: vi.fn(), warning: vi.fn() }),
  Toaster: () => null,
}));

// The test language is the app default (French): assertions go through the same i18n.
const t = i18n.t.bind(i18n);

const user = (over: Partial<AuthUserRow>): AuthUserRow => ({
  id: 2,
  username: 'tablet',
  display_name: '',
  role: 'kiosk',
  provider: 'local',
  disabled: false,
  must_change_password: false,
  has_password: false,
  totp_enabled: false,
  created_at: 1,
  updated_at: 1,
  ...over,
});

let settings: AuthSettings;

beforeEach(() => {
  settings = {
    trusted_proxies: [],
    audit_retention_days: 366,
    ingress_default_role: 'member',
    trusted_networks: [],
    security_notices: [],
  };
  vi.spyOn(api, 'getAuthSettings').mockImplementation(async () => settings);
  vi.spyOn(api, 'listAuthUsers').mockResolvedValue([
    user({ id: 1, username: 'root', role: 'admin' }),
    user({ id: 2, username: 'tablet' }),
    user({ id: 3, username: 'gone', disabled: true }),
  ]);
});
afterEach(() => vi.restoreAllMocks());

async function addRule(cidr: string, userId: string) {
  fireEvent.click(await screen.findByText(t('settings.access.trustedNetworks.add')));
  const inputs = screen.getAllByLabelText(t('settings.access.trustedNetworks.cidr'));
  fireEvent.change(inputs[inputs.length - 1], { target: { value: cidr } });
  const selects = screen.getAllByLabelText(t('settings.access.trustedNetworks.user'));
  fireEvent.change(selects[selects.length - 1], { target: { value: userId } });
}

describe('AccessTrustedNetworksPanel', () => {
  it('saves a rule and offers only enabled accounts', async () => {
    const patch = vi.spyOn(api, 'patchAuthSettings').mockResolvedValue({
      ...settings,
      trusted_networks: [{ cidr: '192.168.1.0/24', user_id: 2 }],
    });
    render(<AccessTrustedNetworksPanel />);
    expect(await screen.findByText(t('settings.access.trustedNetworks.none'))).toBeInTheDocument();
    const save = screen.getByText(t('settings.access.trustedNetworks.save'));
    expect(save.closest('button')).toBeDisabled();

    await addRule(' 192.168.1.0/24 ', '2');
    const options = Array.from(
      screen.getByLabelText(t('settings.access.trustedNetworks.user')).querySelectorAll('option')
    ).map((option) => option.getAttribute('value'));
    expect(options).toEqual(['', '1', '2']);

    fireEvent.click(save);
    await waitFor(() =>
      expect(patch).toHaveBeenCalledWith({
        trusted_networks: [{ cidr: '192.168.1.0/24', user_id: 2 }],
      })
    );
    await waitFor(() =>
      expect(toast.success).toHaveBeenCalledWith(t('settings.access.trustedNetworks.saved'))
    );
  });

  it('warns strongly when a rule maps to an administrator', async () => {
    render(<AccessTrustedNetworksPanel />);
    await addRule('10.0.0.0/24', '2');
    expect(screen.queryByTestId('trusted-networks-admin-warning')).toBeNull();
    const select = screen.getByLabelText(t('settings.access.trustedNetworks.user'));
    fireEvent.change(select, { target: { value: '1' } });
    expect(screen.getByTestId('trusted-networks-admin-warning')).toHaveTextContent('10.0.0.0/24');
  });

  it('shows the coded refusal of an overlap with a trusted proxy', async () => {
    vi.spyOn(api, 'patchAuthSettings').mockRejectedValue(
      new ApiError('Bad Request', 400, {
        code: 'auth_trusted_network_overlaps_proxy',
        message: 'overlap',
        params: { cidr: '192.168.1.0/24', proxy: '192.168.1.5/32' },
      })
    );
    render(<AccessTrustedNetworksPanel />);
    await addRule('192.168.1.0/24', '2');
    fireEvent.click(screen.getByText(t('settings.access.trustedNetworks.save')));
    await waitFor(() =>
      expect(toast.error).toHaveBeenCalledWith(
        t('errors.auth_trusted_network_overlaps_proxy', {
          cidr: '192.168.1.0/24',
          proxy: '192.168.1.5/32',
        })
      )
    );
  });

  it('removes a saved rule', async () => {
    settings.trusted_networks = [{ cidr: '192.168.1.0/24', user_id: 2 }];
    const patch = vi.spyOn(api, 'patchAuthSettings').mockResolvedValue({
      ...settings,
      trusted_networks: [],
    });
    render(<AccessTrustedNetworksPanel />);
    fireEvent.click(await screen.findByText(t('settings.access.trustedNetworks.remove')));
    fireEvent.click(screen.getByText(t('settings.access.trustedNetworks.save')));
    await waitFor(() => expect(patch).toHaveBeenCalledWith({ trusted_networks: [] }));
  });
});

describe('Access security notice', () => {
  it('renders nothing without a notice', () => {
    const { container } = render(<AccessSecurityNoticeBanner notices={[]} />);
    expect(container).toBeEmptyDOMElement();
  });

  it('shows the server notices at the top of Users & Access', async () => {
    settings.trusted_networks = [{ cidr: '192.168.1.0/24', user_id: 1 }];
    settings.security_notices = ['trusted_admin_reduced_plugin_isolation'];
    vi.spyOn(api, 'getAuthAudit').mockResolvedValue({ entries: [], next_cursor: null });
    render(<SettingsAccessSection />);
    const notice = await screen.findByTestId('access-security-notice');
    expect(notice).toHaveTextContent(
      t('settings.access.securityNotice.trusted_admin_reduced_plugin_isolation')
    );
    expect(screen.getByTestId('trusted-networks-admin-warning')).toBeInTheDocument();
  });
});
