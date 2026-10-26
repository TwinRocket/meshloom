import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

const toastMocks = vi.hoisted(() => ({ error: vi.fn(), success: vi.fn() }));
vi.mock('../components/ui/sonner', () => ({ toast: toastMocks }));

import { api } from '../api';
import { SettingsIndexView } from '../components/SettingsIndexView';
import { SettingsUpdatesSection } from '../components/settings/SettingsUpdatesSection';
import { StoreTab } from '../components/settings/extensions/StoreTab';
import { UpdatesTab } from '../components/settings/extensions/UpdatesTab';
import { formatPluginReport, issuesUrl } from '../components/settings/extensions/PluginPage';
import type {
  PluginInstance,
  PluginUpdate,
  StoreRepositories,
  StoreRepository,
  StoreStatus,
} from '../components/settings/extensions/pluginsTypes';
import i18n from '../i18n';
import {
  getPluginUpdateCount,
  setPluginUpdateCount,
  takeRequestedExtensionsTab,
  requestExtensionsTab,
} from '../stores/pluginUpdatesStore';

const t = (key: string, options?: Record<string, unknown>) => i18n.t(key, options);

/** What a hostile author puts in a README: markup, a script, inline HTML and links. */
const HOSTILE_README = [
  '# Weather',
  '<script>window.__pwned = true</script>',
  '<img src=x onerror="window.__pwned = true">',
  '<a href="https://evil.example/steal">click here</a>',
  '[markdown link](javascript:window.__pwned=true)',
  '![tracker](https://evil.example/pixel.png)',
  '<iframe src="https://evil.example"></iframe>',
].join('\n');

const PERMISSIONS = { 'messages.read': { channels: 'public', direct: false, outgoing: false } };

function repo(overrides: Partial<StoreRepository> = {}): StoreRepository {
  return {
    repository: 'example/weather',
    display_name: 'Example/weather',
    added_at: 1,
    checked_at: 1_700_000_000,
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
      permissions: PERMISSIONS,
      privacy: { sends_to_third_parties: [], stores_message_content: false },
      compat: { meshloom: '>=5.0' },
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

function repositories(overrides: Partial<StoreRepositories> = {}): StoreRepositories {
  return {
    store_online: true,
    max_repositories: 50,
    warning: 'custom_unsigned',
    warning_text: { en: 'x', fr: 'x' },
    repositories: [repo()],
    ...overrides,
  };
}

function status(overrides: Partial<StoreStatus> = {}): StoreStatus {
  return {
    enabled: true,
    hosts: ['github.com', 'objects.githubusercontent.com', 'store.meshloom.app'],
    sends: ['ip_address', 'user_agent'],
    user_agent: 'Meshloom/5.0.0',
    refresh_interval_s: 21600,
    missed_when_disabled: ['updates', 'revocation_alerts'],
    refresh_running: true,
    last_refresh_at: 1_700_000_000,
    ...overrides,
  };
}

function instance(overrides: Partial<PluginInstance> = {}): PluginInstance {
  return {
    instance_id: 'aaaaaaaa-0000-4000-8000-000000000001',
    plugin_id: 'example.weather',
    label: 'Weather',
    kind: 'custom',
    version: '1.0.0',
    enabled: true,
    manifest: { permissions: {} },
    config: { city: 'Paris', token: '********' },
    config_schema: {},
    secret_fields: ['token'],
    scope: {},
    granted_permissions: {},
    quotas: {},
    auto_update: false,
    source: null,
    installed_at: 1,
    updated_at: 1,
    status: 'running',
    last_error: null,
    attention: null,
    sort_order: 0,
    summary: null,
    notices: [],
    restarts: 0,
    dropped_events: 0,
    ...overrides,
  } as PluginInstance;
}

function update(overrides: Partial<PluginUpdate> = {}): PluginUpdate {
  return {
    plugin_id: 'example.weather',
    kind: 'custom',
    repository: 'example/weather',
    from_version: '1.0.0',
    to_version: '1.2.0',
    name: { en: 'Weather', fr: 'Météo' },
    changelog: null,
    permissions: { added: [], changed: [], removed: [], privacy_widened: false },
    config_schema: { added: [], removed: [], required_added: [] },
    instances: [
      {
        instance_id: 'aaaaaaaa-0000-4000-8000-000000000001',
        label: 'Weather',
        version: '1.0.0',
        auto_update: false,
        hold: null,
        permissions: [],
        missing_fields: [],
      },
    ],
    ...overrides,
  };
}

beforeEach(() => {
  toastMocks.error.mockReset();
  toastMocks.success.mockReset();
  setPluginUpdateCount(0);
  delete (window as unknown as Record<string, unknown>).__pwned;
});
afterEach(() => {
  vi.restoreAllMocks();
});

describe('Store tab: activation card', () => {
  it('lists the hosts, what leaves and what is missed, from GET /store/status', async () => {
    vi.spyOn(api, 'getStoreStatus').mockResolvedValue(status({ enabled: false }));
    vi.spyOn(api, 'getStoreRepositories').mockResolvedValue(repositories({ store_online: false }));
    render(<StoreTab canManage locked={false} onInstalled={vi.fn()} />);
    await screen.findByTestId('store-offline');
    const hosts = within(screen.getByTestId('store-hosts'))
      .getAllByRole('listitem')
      .map((li) => li.textContent);
    expect(hosts).toEqual(['github.com', 'objects.githubusercontent.com', 'store.meshloom.app']);
    expect(screen.getByTestId('store-sends')).toHaveTextContent('Meshloom/5.0.0');
    expect(within(screen.getByTestId('store-missed')).getAllByRole('listitem')).toHaveLength(2);
  });

  it('one click turns the Store on and the card goes away', async () => {
    const get = vi
      .spyOn(api, 'getStoreStatus')
      .mockResolvedValueOnce(status({ enabled: false }))
      .mockResolvedValue(status());
    vi.spyOn(api, 'getStoreRepositories').mockResolvedValue(repositories());
    const update = vi.spyOn(api, 'updateSettings').mockResolvedValue({} as never);
    render(<StoreTab canManage locked={false} onInstalled={vi.fn()} />);
    fireEvent.click(
      await screen.findByRole('button', { name: t('settings.extensions.store.goOnline') })
    );
    await waitFor(() => expect(update).toHaveBeenCalledWith({ store_online: true }));
    await waitFor(() => expect(get).toHaveBeenCalledTimes(2));
    await waitFor(() => expect(screen.queryByTestId('store-offline')).not.toBeInTheDocument());
    expect(screen.getByTestId('store-online')).toBeInTheDocument();
  });

  it('a member requests nothing', async () => {
    const s = vi.spyOn(api, 'getStoreStatus').mockResolvedValue(status());
    const r = vi.spyOn(api, 'getStoreRepositories').mockResolvedValue(repositories());
    render(<StoreTab canManage={false} locked={false} onInstalled={vi.fn()} />);
    await screen.findByTestId('custom-repo-admin-only');
    expect(s).not.toHaveBeenCalled();
    expect(r).not.toHaveBeenCalled();
  });

  it('Refresh calls the route once and is held for a while', async () => {
    vi.spyOn(api, 'getStoreStatus').mockResolvedValue(status());
    vi.spyOn(api, 'getStoreRepositories').mockResolvedValue(repositories());
    const refresh = vi
      .spyOn(api, 'refreshStore')
      .mockResolvedValue({ ran: false, last_refresh_at: 1 });
    render(<StoreTab canManage locked={false} onInstalled={vi.fn()} />);
    const button = await screen.findByRole('button', { name: t('pluginStore.refresh.button') });
    fireEvent.click(button);
    await waitFor(() => expect(refresh).toHaveBeenCalledTimes(1));
    await waitFor(() =>
      expect(toastMocks.success).toHaveBeenCalledWith(t('pluginStore.refresh.skipped'))
    );
    expect(button).toBeDisabled();
    fireEvent.click(button);
    expect(refresh).toHaveBeenCalledTimes(1);
  });
});

describe('Store tab: plugin page', () => {
  async function openPage() {
    vi.spyOn(api, 'getStoreStatus').mockResolvedValue(status());
    vi.spyOn(api, 'getStoreRepositories').mockResolvedValue(
      repositories({
        repositories: [
          repo({
            entry: {
              ...repo().entry!,
              permissions: {
                ...PERMISSIONS,
                http: { hosts: ['api.open-meteo.com'], lan_hosts: ['192.168.1.20'] },
              },
            },
          }),
        ],
      })
    );
    render(<StoreTab canManage locked={false} onInstalled={vi.fn()} />);
    fireEvent.click(await screen.findByRole('button', { name: t('pluginStore.card.details') }));
    return screen.findByTestId('plugin-page');
  }

  it('renders a hostile README as inert text', async () => {
    const readme = vi
      .spyOn(api, 'getStoreReadme')
      .mockResolvedValue({ lang: 'en', text: HOSTILE_README, languages: ['en'] });
    const page = await openPage();
    const pre = await within(page).findByTestId('plugin-readme');
    expect(readme).toHaveBeenCalledWith('example/weather', expect.any(String));
    // Verbatim text, in one <pre>.
    expect(pre.tagName).toBe('PRE');
    expect(pre.textContent).toBe(HOSTILE_README);
    // No element came out of it.
    expect(pre.children).toHaveLength(0);
    expect(pre.querySelector('script, img, iframe, a')).toBeNull();
    expect(document.querySelector('script, iframe')).toBeNull();
    expect(page.querySelector('img')).toBeNull();
    expect((window as unknown as Record<string, unknown>).__pwned).toBeUndefined();
    // The only link of the page is the one built from the validated repository name.
    const links = Array.from(page.querySelectorAll('a')).map((a) => a.getAttribute('href'));
    expect(links).toEqual(['https://github.com/Example/weather/issues']);
    expect(page.querySelectorAll('[href*="evil"], [src*="evil"], [onerror]')).toHaveLength(0);
  });

  it('shows permissions, hosts, privacy, versions, the unreviewed warning and a report', async () => {
    vi.spyOn(api, 'getStoreReadme').mockResolvedValue({ lang: null, text: '', languages: [] });
    const page = await openPage();
    expect(within(page).getByTestId('plugin-page-unreviewed')).toHaveTextContent(
      t('pluginStore.card.unreviewed')
    );
    expect(await within(page).findByText(t('pluginStore.page.noReadme'))).toBeInTheDocument();
    expect(page).toHaveTextContent(t('plugins.permissions.messages_read.title'));
    expect(within(page).getByTestId('plugin-page-hosts')).toHaveTextContent('api.open-meteo.com');
    expect(within(page).getByTestId('plugin-page-hosts')).toHaveTextContent('192.168.1.20');
    expect(within(page).getByTestId('plugin-privacy')).toBeInTheDocument();
    expect(page).toHaveTextContent('v1.2.0 (v1.2.0)');
    expect(page).toHaveTextContent('store@meshloom.app');
  });

  it('copies a diagnostic that names the release and no config', async () => {
    vi.spyOn(api, 'getStoreReadme').mockResolvedValue({ lang: null, text: '', languages: [] });
    const writeText = vi.fn().mockResolvedValue(undefined);
    Object.defineProperty(navigator, 'clipboard', { value: { writeText }, configurable: true });
    const page = await openPage();
    fireEvent.click(
      within(page).getByRole('button', { name: t('pluginStore.page.copyDiagnostic') })
    );
    await waitFor(() => expect(writeText).toHaveBeenCalled());
    const text = writeText.mock.calls[0][0] as string;
    expect(text).toContain('example.weather 1.2.0');
    expect(text).toContain('repository: Example/weather');
    expect(text).toContain('a'.repeat(64));
  });

  it('issuesUrl only builds a URL from a valid owner/name', () => {
    expect(issuesUrl('Example/weather')).toBe('https://github.com/Example/weather/issues');
    expect(issuesUrl('x/y?z=1')).toBeNull();
    expect(issuesUrl('javascript:alert(1)')).toBeNull();
    expect(formatPluginReport(repo())).toContain('plugin: example.weather 1.2.0 (custom)');
  });
});

describe('Store tab: states and install', () => {
  it('an incompatible release is flagged and cannot be installed', async () => {
    vi.spyOn(api, 'getStoreStatus').mockResolvedValue(status());
    vi.spyOn(api, 'getStoreRepositories').mockResolvedValue(
      repositories({ repositories: [repo({ entry: { ...repo().entry!, compatible: false } })] })
    );
    render(<StoreTab canManage locked={false} onInstalled={vi.fn()} />);
    expect(await screen.findByTestId('custom-repo-incompatible')).toBeInTheDocument();
    expect(
      screen.getByRole('button', { name: t('settings.extensions.store.install') })
    ).toBeDisabled();
    expect(screen.getByTestId('custom-repo-badges')).toHaveTextContent(
      t('pluginStore.card.unreviewed')
    );
  });

  it('asks the release settings before installing and sends exactly the listed grant', async () => {
    const schema = {
      type: 'object',
      properties: { city: { type: 'string', title: 'City' } },
      required: ['city'],
    };
    vi.spyOn(api, 'getStoreStatus').mockResolvedValue(status());
    vi.spyOn(api, 'getStoreRepositories').mockResolvedValue(
      repositories({
        repositories: [repo({ entry: { ...repo().entry!, config_schema: schema } })],
      })
    );
    const install = vi
      .spyOn(api, 'installPlugin')
      .mockResolvedValue({ label: 'Weather' } as PluginInstance);
    const onInstalled = vi.fn();
    render(<StoreTab canManage locked={false} onInstalled={onInstalled} />);
    fireEvent.click(
      await screen.findByRole('button', { name: t('settings.extensions.store.install') })
    );
    const dialog = await screen.findByRole('dialog');
    // The consent lists the permission before anything is sent.
    expect(dialog).toHaveTextContent(t('plugins.permissions.messages_read.title'));
    fireEvent.click(within(dialog).getByRole('checkbox'));
    fireEvent.click(within(dialog).getByRole('button', { name: t('pluginStore.install.next') }));
    const input = await within(dialog).findByLabelText(/City/);
    fireEvent.change(input, { target: { value: 'Lyon' } });
    fireEvent.click(
      within(dialog).getByRole('button', { name: t('settings.extensions.add.install') })
    );
    await waitFor(() =>
      expect(install).toHaveBeenCalledWith({
        kind: 'custom',
        plugin_id: 'example.weather',
        repository: 'example/weather',
        version: '1.2.0',
        granted_permissions: PERMISSIONS,
        config: { city: 'Lyon' },
      })
    );
    await waitFor(() => expect(onInstalled).toHaveBeenCalled());
  });
});

describe('Updates tab', () => {
  function renderTab(
    opts: {
      updates?: PluginUpdate[];
      repos?: StoreRepositories;
      instances?: PluginInstance[];
      canManage?: boolean;
      onChanged?: () => void;
      st?: StoreStatus;
    } = {}
  ) {
    const getUpdates = vi
      .spyOn(api, 'getStoreUpdates')
      .mockResolvedValue(opts.updates ?? [update()]);
    vi.spyOn(api, 'getStoreRepositories').mockResolvedValue(opts.repos ?? repositories());
    vi.spyOn(api, 'getStoreStatus').mockResolvedValue(opts.st ?? status());
    const onOpen = vi.fn();
    render(
      <UpdatesTab
        instances={opts.instances ?? [instance()]}
        canManage={opts.canManage ?? true}
        locked={false}
        reloadKey={0}
        onChanged={opts.onChanged ?? vi.fn()}
        onOpenMeshloomUpdates={onOpen}
      />
    );
    return { getUpdates, onOpen };
  }

  it('lists the updates, publishes the count and links to the Meshloom updates', async () => {
    const { getUpdates, onOpen } = renderTab();
    expect(await screen.findByTestId('plugin-update-row')).toHaveTextContent('1.0.0 → 1.2.0');
    expect(getUpdates).toHaveBeenCalledWith(expect.any(String));
    expect(getPluginUpdateCount()).toBe(1);
    fireEvent.click(
      screen.getByRole('button', { name: t('pluginStore.updates.openMeshloomUpdates') })
    );
    expect(onOpen).toHaveBeenCalled();
  });

  it('says so when nothing is waiting, and when the source has no changelog', async () => {
    renderTab({ updates: [] });
    expect(await screen.findByTestId('updates-none')).toBeInTheDocument();
    expect(getPluginUpdateCount()).toBe(0);
  });

  it('shows the changelog as text and the permission and schema diffs', async () => {
    const hostile = '<script>window.__pwned = true</script>\n[x](javascript:alert(1))';
    renderTab({
      updates: [
        update({
          changelog: hostile,
          permissions: {
            added: ['http'],
            changed: ['messages.read'],
            removed: ['storage'],
            privacy_widened: true,
          },
          config_schema: { added: ['units'], removed: ['old'], required_added: ['token'] },
        }),
      ],
    });
    fireEvent.click(await screen.findByTestId('plugin-update-open'));
    const dialog = await screen.findByRole('dialog');
    const log = within(dialog).getByTestId('update-changelog');
    expect(log.textContent).toBe(hostile);
    expect(log.children).toHaveLength(0);
    expect(dialog.querySelector('script, a')).toBeNull();
    expect(within(dialog).getByTestId('update-permission-diff')).toHaveTextContent(
      t('plugins.permissions.http.title')
    );
    expect(within(dialog).getByTestId('update-permission-diff')).toHaveTextContent(
      t('pluginStore.updates.privacyWidened')
    );
    expect(within(dialog).getByTestId('update-schema-diff')).toHaveTextContent('token');
    expect(within(dialog).getByTestId('update-schema-diff')).toHaveTextContent('units');
  });

  it('no changelog: said plainly', async () => {
    renderTab();
    fireEvent.click(await screen.findByTestId('plugin-update-open'));
    const dialog = await screen.findByRole('dialog');
    expect(dialog).toHaveTextContent(t('pluginStore.updates.noChangelog'));
    expect(within(dialog).queryByTestId('update-changelog')).not.toBeInTheDocument();
  });

  it('a plain update sends no grant and no config', async () => {
    const onChanged = vi.fn();
    renderTab({ onChanged });
    const post = vi
      .spyOn(api, 'updatePlugin')
      .mockResolvedValue({ label: 'Weather' } as PluginInstance);
    fireEvent.click(await screen.findByTestId('plugin-update-open'));
    const dialog = await screen.findByRole('dialog');
    fireEvent.click(within(dialog).getByRole('button', { name: t('pluginStore.updates.update') }));
    await waitFor(() =>
      expect(post).toHaveBeenCalledWith('aaaaaaaa-0000-4000-8000-000000000001', {
        version: '1.2.0',
      })
    );
    await waitFor(() => expect(onChanged).toHaveBeenCalled());
  });

  it('consent_required: lists every permission of the release and sends exactly that', async () => {
    const permissions = {
      ...PERMISSIONS,
      'messages.send': { targets: 'reply', per_hour: 10, per_day: 50 },
      http: { hosts: ['api.open-meteo.com'] },
    };
    const entry = { ...repo().entry!, permissions };
    renderTab({
      updates: [
        update({
          permissions: {
            added: ['http', 'messages.send'],
            changed: [],
            removed: [],
            privacy_widened: false,
          },
          instances: [
            {
              ...update().instances[0],
              hold: 'consent_required',
              permissions: ['http', 'messages.send'],
            },
          ],
        }),
      ],
      repos: repositories({ repositories: [repo({ entry })] }),
    });
    const post = vi
      .spyOn(api, 'updatePlugin')
      .mockResolvedValue({ label: 'Weather' } as PluginInstance);
    fireEvent.click(await screen.findByTestId('plugin-update-open'));
    const dialog = await screen.findByRole('dialog');
    const consent = within(dialog).getByTestId('update-consent');
    // Every permission of the release is on the screen, not only the new ones.
    for (const name of ['messages_read', 'messages_send', 'http']) {
      expect(consent).toHaveTextContent(t(`plugins.permissions.${name}.title`));
    }
    const button = within(dialog).getByRole('button', { name: t('pluginStore.updates.update') });
    expect(button).toBeDisabled();
    fireEvent.click(within(consent).getByRole('checkbox'));
    fireEvent.click(button);
    await waitFor(() =>
      expect(post).toHaveBeenCalledWith('aaaaaaaa-0000-4000-8000-000000000001', {
        version: '1.2.0',
        granted_permissions: permissions,
      })
    );
  });

  it('config_required: asks the new fields and sends them with the update', async () => {
    const schema = {
      type: 'object',
      properties: {
        city: { type: 'string', title: 'City' },
        units: { type: 'string', title: 'Units' },
      },
      required: ['city', 'units'],
    };
    const entry = { ...repo().entry!, config_schema: schema };
    renderTab({
      updates: [
        update({
          config_schema: { added: ['units'], removed: [], required_added: ['units'] },
          instances: [
            { ...update().instances[0], hold: 'config_required', missing_fields: ['units'] },
          ],
        }),
      ],
      repos: repositories({ repositories: [repo({ entry })] }),
    });
    const post = vi
      .spyOn(api, 'updatePlugin')
      .mockResolvedValue({ label: 'Weather' } as PluginInstance);
    fireEvent.click(await screen.findByTestId('plugin-update-open'));
    const dialog = await screen.findByRole('dialog');
    fireEvent.click(within(dialog).getByRole('button', { name: t('pluginStore.install.next') }));
    const units = await within(dialog).findByLabelText(/Units/);
    fireEvent.change(units, { target: { value: 'metric' } });
    fireEvent.click(within(dialog).getByRole('button', { name: t('pluginStore.updates.update') }));
    await waitFor(() => expect(post).toHaveBeenCalled());
    const body = post.mock.calls[0][1];
    expect(body.version).toBe('1.2.0');
    expect(body.granted_permissions).toBeUndefined();
    expect(body.config).toMatchObject({ city: 'Paris', units: 'metric' });
  });

  it('toggles auto-update per instance', async () => {
    renderTab();
    const patch = vi
      .spyOn(api, 'patchPlugin')
      .mockResolvedValue({ label: 'Weather' } as PluginInstance);
    const toggle = await screen.findByRole('switch', {
      name: t('pluginStore.updates.autoToggle', { name: 'Weather' }),
    });
    fireEvent.click(toggle);
    await waitFor(() =>
      expect(patch).toHaveBeenCalledWith('aaaaaaaa-0000-4000-8000-000000000001', {
        auto_update: true,
      })
    );
  });

  it('Store off: updates cannot be applied; a member requests nothing', async () => {
    renderTab({ st: status({ enabled: false }) });
    expect(await screen.findByTestId('updates-store-offline')).toBeInTheDocument();
    expect(screen.getByTestId('plugin-update-open')).toBeDisabled();
  });

  it('a member sees a notice only', () => {
    const spy = vi.spyOn(api, 'getStoreUpdates');
    renderTab({ canManage: false });
    expect(screen.getByTestId('updates-admin-only')).toBeInTheDocument();
    expect(spy).not.toHaveBeenCalled();
  });
});

describe('badge store', () => {
  it('publishes the count to its readers', () => {
    expect(getPluginUpdateCount()).toBe(0);
    act(() => setPluginUpdateCount(3));
    expect(getPluginUpdateCount()).toBe(3);
  });
});

describe('badge and cross-links', () => {
  it('the Extensions row of the Settings index shows the update count', () => {
    act(() => setPluginUpdateCount(2));
    render(<SettingsIndexView onSelectSection={vi.fn()} />);
    expect(screen.getByTestId('extensions-updates-badge')).toHaveTextContent(
      t('pluginStore.navBadge', { count: 2 })
    );
    act(() => setPluginUpdateCount(0));
    expect(screen.queryByTestId('extensions-updates-badge')).not.toBeInTheDocument();
  });

  it("Meshloom's Updates links to the plugin updates (admins only)", () => {
    act(() => setPluginUpdateCount(1));
    const open = vi.fn();
    const { rerender } = render(<SettingsUpdatesSection onOpenExtensionUpdates={open} />);
    expect(screen.getByTestId('updates-extensions-link')).toHaveTextContent(
      t('pluginStore.updates.linkCount', { count: 1 })
    );
    fireEvent.click(
      screen.getByRole('button', { name: t('pluginStore.updates.openExtensionUpdates') })
    );
    expect(open).toHaveBeenCalled();
    rerender(<SettingsUpdatesSection />);
    expect(screen.queryByTestId('updates-extensions-link')).not.toBeInTheDocument();
  });

  it('a requested Extensions tab is read once', () => {
    requestExtensionsTab('updates');
    expect(takeRequestedExtensionsTab()).toBe('updates');
    expect(takeRequestedExtensionsTab()).toBeNull();
  });
});
