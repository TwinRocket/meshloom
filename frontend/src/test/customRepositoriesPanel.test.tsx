import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

const toastMocks = vi.hoisted(() => ({ error: vi.fn(), success: vi.fn() }));
vi.mock('../components/ui/sonner', () => ({ toast: toastMocks }));

import { api, ApiError } from '../api';
import { CustomRepositoriesPanel } from '../components/settings/extensions/CustomRepositoriesPanel';
import type {
  PluginInstance,
  StoreRepositories,
  StoreRepository,
} from '../components/settings/extensions/pluginsTypes';
import i18n from '../i18n';

const t = (key: string, options?: Record<string, unknown>) => i18n.t(key, options);

function repo(overrides: Partial<StoreRepository> = {}): StoreRepository {
  return {
    repository: 'example/weather',
    display_name: 'Example/weather',
    added_at: 1,
    checked_at: 2,
    last_error: null,
    entry: {
      tier: 'custom',
      signed: false,
      repository: 'example/weather',
      plugin_id: 'example.weather',
      version: '1.2.0',
      tag: 'v1.2.0',
      name: { en: 'Weather', fr: 'Météo' },
      summary: { en: 'Weather answers.', fr: 'Répond la météo.' },
      permissions: { 'messages.read': { channels: 'public', direct: false, outgoing: false } },
      privacy: { sends_to_third_parties: false, stores_message_content: false },
      compat: null,
      compatible: true,
      config_schema: {},
      archive_sha256: 'a'.repeat(64),
      archive_size: 1234,
      warning: 'custom_unsigned',
      warning_text: { en: 'x', fr: 'x' },
    },
    instances: [],
    update_available: false,
    ...overrides,
  };
}

function payload(overrides: Partial<StoreRepositories> = {}): StoreRepositories {
  return {
    store_online: true,
    max_repositories: 50,
    warning: 'custom_unsigned',
    warning_text: { en: 'x', fr: 'x' },
    repositories: [repo()],
    ...overrides,
  };
}

function renderPanel(canManage = true, onInstalled = vi.fn()) {
  render(
    <CustomRepositoriesPanel canManage={canManage} locked={false} onInstalled={onInstalled} />
  );
  return onInstalled;
}

describe('CustomRepositoriesPanel', () => {
  beforeEach(() => {
    toastMocks.error.mockReset();
    toastMocks.success.mockReset();
  });
  afterEach(() => {
    vi.restoreAllMocks();
  });

  it('shows the warning: unsigned, sha256 is an integrity check only, no revocation', async () => {
    vi.spyOn(api, 'getStoreRepositories').mockResolvedValue(payload());
    renderPanel();
    const warning = await screen.findByTestId('custom-repo-warning');
    expect(warning).toHaveTextContent(t('settings.extensions.store.warning'));
    expect(t('settings.extensions.store.warning')).toMatch(/intégrité/);
    expect(i18n.getFixedT('en')('settings.extensions.store.warning')).toMatch(/integrity/);
    expect(await screen.findByText('Météo')).toBeInTheDocument();
  });

  it('adds a repository, and refuses an invalid name locally', async () => {
    vi.spyOn(api, 'getStoreRepositories').mockResolvedValue(payload({ repositories: [] }));
    const add = vi.spyOn(api, 'addStoreRepository').mockResolvedValue(repo());
    renderPanel();
    const input = await screen.findByLabelText(t('settings.extensions.store.repositoryLabel'));
    const button = screen.getByRole('button', { name: t('settings.extensions.store.add') });

    fireEvent.change(input, { target: { value: 'https://github.com/example/weather' } });
    expect(button).toBeDisabled();
    expect(screen.getByText(t('errors.repository_invalid'))).toBeInTheDocument();

    fireEvent.change(input, { target: { value: ' example/weather ' } });
    expect(button).toBeEnabled();
    fireEvent.click(button);
    await waitFor(() => expect(add).toHaveBeenCalledWith('example/weather'));
    await waitFor(() => expect(toastMocks.success).toHaveBeenCalled());
  });

  it('shows a readable error when the add fails', async () => {
    vi.spyOn(api, 'getStoreRepositories').mockResolvedValue(payload({ repositories: [] }));
    vi.spyOn(api, 'addStoreRepository').mockRejectedValue(
      new ApiError('x', 404, {
        code: 'repository_assets_missing',
        message: 'x',
        params: { missing: ['manifest.json', 'plugin.zip'] },
      })
    );
    renderPanel();
    const input = await screen.findByLabelText(t('settings.extensions.store.repositoryLabel'));
    fireEvent.change(input, { target: { value: 'example/weather' } });
    fireEvent.click(screen.getByRole('button', { name: t('settings.extensions.store.add') }));
    await waitFor(() =>
      expect(toastMocks.error).toHaveBeenCalledWith(
        t('errors.repository_assets_missing', { missing: 'manifest.json, plugin.zip' })
      )
    );
  });

  it("offline: no add, no install (the activation card is the Store tab's)", async () => {
    vi.spyOn(api, 'getStoreRepositories').mockResolvedValue(payload({ store_online: false }));
    renderPanel();
    await screen.findByTestId('store-offline-note');
    expect(screen.getByRole('button', { name: t('settings.extensions.store.add') })).toBeDisabled();
    expect(
      screen.getByRole('button', { name: t('settings.extensions.store.install') })
    ).toBeDisabled();
  });

  it('removes a repository after a confirmation', async () => {
    vi.spyOn(api, 'getStoreRepositories').mockResolvedValue(payload());
    const remove = vi.spyOn(api, 'removeStoreRepository').mockResolvedValue(undefined);
    renderPanel();
    const row = await screen.findByTestId('custom-repo-row');
    fireEvent.click(
      within(row).getByRole('button', { name: t('settings.extensions.store.remove') })
    );
    const dialog = await screen.findByRole('dialog');
    expect(remove).not.toHaveBeenCalled();
    fireEvent.click(
      within(dialog).getByRole('button', { name: t('settings.extensions.store.remove') })
    );
    await waitFor(() => expect(remove).toHaveBeenCalledWith('example/weather'));
  });

  it('installs with kind custom, the repository and the declared grant', async () => {
    vi.spyOn(api, 'getStoreRepositories').mockResolvedValue(payload());
    const install = vi
      .spyOn(api, 'installPlugin')
      .mockResolvedValue({ label: 'Météo' } as PluginInstance);
    const onInstalled = renderPanel();
    fireEvent.click(
      await screen.findByRole('button', { name: t('settings.extensions.store.install') })
    );
    const dialog = await screen.findByRole('dialog');
    const confirm = within(dialog).getByRole('button', {
      name: t('settings.extensions.add.install'),
    });
    expect(confirm).toBeDisabled();
    fireEvent.click(within(dialog).getByRole('checkbox'));
    fireEvent.click(confirm);
    await waitFor(() =>
      expect(install).toHaveBeenCalledWith({
        kind: 'custom',
        plugin_id: 'example.weather',
        repository: 'example/weather',
        version: expect.any(String),
        granted_permissions: {
          'messages.read': { channels: 'public', direct: false, outgoing: false },
        },
      })
    );
    await waitFor(() => expect(onInstalled).toHaveBeenCalled());
  });

  it('translates the last check error', async () => {
    vi.spyOn(api, 'getStoreRepositories').mockResolvedValue(
      payload({ repositories: [repo({ last_error: 'repository_no_release' })] })
    );
    renderPanel();
    expect(await screen.findByTestId('custom-repo-error')).toHaveTextContent(
      t('errors.repository_no_release')
    );
  });

  it('members: the warning and a notice, no request (the routes are admin-only)', async () => {
    const get = vi.spyOn(api, 'getStoreRepositories').mockResolvedValue(payload());
    renderPanel(false);
    expect(await screen.findByTestId('custom-repo-admin-only')).toBeInTheDocument();
    expect(screen.getByTestId('custom-repo-warning')).toBeInTheDocument();
    expect(get).not.toHaveBeenCalled();
    expect(
      screen.queryByLabelText(t('settings.extensions.store.repositoryLabel'))
    ).not.toBeInTheDocument();
    expect(
      screen.queryByRole('button', { name: t('settings.extensions.store.remove') })
    ).not.toBeInTheDocument();
  });
});
