import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

const toastMocks = vi.hoisted(() => ({ error: vi.fn(), success: vi.fn() }));
vi.mock('../components/ui/sonner', () => ({ toast: toastMocks }));

// The role the section renders for; null = no session (unrestricted, like a preview).
const roleRef = vi.hoisted(() => ({ current: null as null | 'admin' | 'member' }));
vi.mock('../auth/SessionProvider', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../auth/SessionProvider')>();
  const { capabilitiesFor } = await import('../auth/capabilities');
  return {
    ...actual,
    useCapabilities: () =>
      roleRef.current ? capabilitiesFor(roleRef.current) : actual.useCapabilities(),
  };
});

import { api, ApiError } from '../api';
import { SettingsExtensionsSection } from '../components/settings/SettingsExtensionsSection';
import { MigrationBanner } from '../components/settings/extensions/MigrationBanner';
import { lastErrorText } from '../components/settings/extensions/pluginsShared';
import type { BundledPlugin, PluginInstance } from '../components/settings/extensions/pluginsTypes';
import { capabilitiesFor } from '../auth/capabilities';
import i18n from '../i18n';
import type { HealthStatus } from '../types';

const t = (key: string, options?: Record<string, unknown>) => i18n.t(key, options);

function instance(overrides: Partial<PluginInstance> = {}): PluginInstance {
  return {
    instance_id: 'aaaaaaaa-0000-4000-8000-000000000001',
    plugin_id: 'meshloom.weather',
    label: 'Weather',
    kind: 'bundled',
    version: '1.0.0',
    enabled: true,
    manifest: {
      permissions: {
        'messages.read': { channels: 'public', direct: true, outgoing: false },
        'messages.send': { targets: 'reply', per_hour: 20 },
        http: { hosts: ['api.open-meteo.com'] },
      },
    },
    config: { greeting: 'hi', api_key: '********' },
    config_schema: {
      properties: {
        greeting: { type: 'string', title: 'Greeting' },
        api_key: { type: 'string', title: 'API key', writeOnly: true },
      },
    },
    secret_fields: ['api_key'],
    scope: {},
    granted_permissions: {
      'messages.read': { channels: 'public', direct: true, outgoing: false },
      'messages.send': { targets: 'reply', per_hour: 20 },
      http: { hosts: ['api.open-meteo.com'] },
    },
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
    restarts: 0,
    dropped_events: 0,
    ...overrides,
  };
}

const fixture: BundledPlugin = {
  plugin_id: 'meshloom.weather',
  version: '1.0.0',
  name: { en: 'Weather', fr: 'Météo' },
  summary: { en: 'Answers with the weather.', fr: 'Répond avec la météo.' },
  icon: null,
  categories: ['bot'],
  hooks: ['message'],
  permissions: {
    'messages.read': { channels: 'public', direct: true, outgoing: false },
    'messages.send': { targets: 'reply', per_hour: 20 },
    http: { hosts: ['api.open-meteo.com'] },
  },
  privacy: { sends_to_third_parties: ['api.open-meteo.com'], stores_message_content: false },
  config_schema: {},
  instances: 0,
};

const health = (extra: Partial<HealthStatus> = {}) =>
  ({ plugins_isolation: 'bwrap', plugins_disabled_source: null, ...extra }) as HealthStatus;

const NOT_PAUSED = { paused: false, since: null, by: null };

beforeEach(() => {
  roleRef.current = null;
  vi.spyOn(api, 'getPluginsPause').mockResolvedValue(NOT_PAUSED);
});

describe('SettingsExtensionsSection', () => {
  beforeEach(() => {
    vi.spyOn(api, 'getPluginLogs').mockResolvedValue({ lines: ['started ok'], truncated: false });
  });
  afterEach(() => {
    vi.restoreAllMocks();
    toastMocks.error.mockClear();
    toastMocks.success.mockClear();
  });

  it('shows the isolation level with its explanation', async () => {
    vi.spyOn(api, 'getPlugins').mockResolvedValue([]);
    render(<SettingsExtensionsSection health={health({ plugins_isolation: 'none' })} />);
    const box = screen.getByTestId('plugin-isolation');
    expect(box).toHaveTextContent(t('settings.extensions.isolation.level.none'));
    expect(box).toHaveTextContent(t('settings.extensions.isolation.explain.none'));
    expect(await screen.findByText(t('settings.extensions.empty'))).toBeInTheDocument();
  });

  it('lists the instances with their badges and last error', async () => {
    vi.spyOn(api, 'getPlugins').mockResolvedValue([
      instance({ status: 'error', last_error: 'boom' }),
      instance({
        instance_id: 'aaaaaaaa-0000-4000-8000-000000000002',
        label: 'Greeter',
        status: 'needs_attention',
        enabled: false,
        attention: { type: 'config_required', fields: ['greeting'] },
        sort_order: 1,
      }),
    ]);
    render(<SettingsExtensionsSection health={health()} />);
    expect(await screen.findByText('Weather')).toBeInTheDocument();
    expect(screen.getByText('boom')).toBeInTheDocument();
    expect(screen.getByText(t('settings.extensions.status.error'))).toBeInTheDocument();
    expect(
      screen.getByText(t('settings.extensions.attention.config_required.badge'))
    ).toBeInTheDocument();
    // config_required: the switch cannot turn it on from the list.
    expect(
      screen.getByRole('switch', {
        name: t('settings.extensions.enableToggle', { name: 'Greeter' }),
      })
    ).toBeDisabled();
  });

  it('never lets a revoked or incompatible instance be enabled', async () => {
    const patch = vi.spyOn(api, 'patchPlugin');
    vi.spyOn(api, 'getPlugins').mockResolvedValue([
      instance({ label: 'Gone', status: 'revoked', enabled: false }),
      instance({
        instance_id: 'aaaaaaaa-0000-4000-8000-000000000003',
        label: 'Old',
        status: 'incompatible',
        enabled: false,
        sort_order: 1,
      }),
    ]);
    render(<SettingsExtensionsSection health={health()} />);
    for (const name of ['Gone', 'Old']) {
      const toggle = await screen.findByRole('switch', {
        name: t('settings.extensions.enableToggle', { name }),
      });
      expect(toggle).toBeDisabled();
      fireEvent.click(toggle);
    }
    expect(patch).not.toHaveBeenCalled();
    fireEvent.click(
      screen.getByRole('button', { name: t('settings.extensions.openDetails', { name: 'Gone' }) })
    );
    expect(await screen.findByText(t('settings.extensions.blocked.revoked'))).toBeInTheDocument();
  });

  it('enables an instance through the API', async () => {
    const off = instance({ enabled: false, status: 'disabled' });
    vi.spyOn(api, 'getPlugins').mockResolvedValue([off]);
    const patch = vi
      .spyOn(api, 'patchPlugin')
      .mockResolvedValue({ ...off, enabled: true, status: 'running' });
    render(<SettingsExtensionsSection health={health()} />);
    fireEvent.click(await screen.findByRole('switch'));
    await waitFor(() => expect(patch).toHaveBeenCalledWith(off.instance_id, { enabled: true }));
    expect(await screen.findByText(t('settings.extensions.status.running'))).toBeInTheDocument();
  });

  it('adds a bundled plugin with consent, shows the exfiltration sentence, then reads its log and copies the diagnostic', async () => {
    const created = instance({ label: 'Weather' });
    const list = vi.spyOn(api, 'getPlugins').mockResolvedValue([]);
    vi.spyOn(api, 'getBundledPlugins').mockResolvedValue([fixture]);
    const install = vi.spyOn(api, 'installPlugin').mockResolvedValue(created);
    vi.spyOn(api, 'getPluginDiagnostic').mockResolvedValue({
      plugin_id: 'meshloom.weather',
      plugin_version: '1.0.0',
      kind: 'bundled',
      meshloom_version: '5.0.0',
      python_version: '3.12.0',
      isolation: 'bwrap',
      status: 'running',
      last_error: null,
      restarts: 0,
      dropped_events: 0,
      granted_permissions: ['http'],
      logs: ['started ok'],
    });
    const writeText = vi.fn().mockResolvedValue(undefined);
    Object.defineProperty(navigator, 'clipboard', { value: { writeText }, configurable: true });

    render(<SettingsExtensionsSection health={health()} />);
    await screen.findByText(t('settings.extensions.empty'));
    fireEvent.click(screen.getByRole('button', { name: t('settings.extensions.add.button') }));
    fireEvent.click(await screen.findByRole('button', { name: /Weather|Météo/ }));

    // Consent: plain-words permissions, the host, privacy and the dedicated sentence.
    const warning = await screen.findByTestId('plugin-exfiltration-warning');
    expect(warning).toHaveTextContent(
      t('plugins.permissions.http.private_messages_warning', { hosts: 'api.open-meteo.com' })
    );
    const privacy = screen.getByTestId('plugin-privacy');
    expect(privacy).toHaveTextContent(
      t('settings.extensions.privacy.sends', { hosts: 'api.open-meteo.com' })
    );
    expect(privacy).toHaveTextContent(t('settings.extensions.privacy.storesNone'));
    const installButton = screen.getByRole('button', {
      name: t('settings.extensions.add.install'),
    });
    expect(installButton).toBeDisabled();
    fireEvent.click(screen.getByRole('checkbox'));
    expect(installButton).toBeEnabled();
    list.mockResolvedValue([created]);
    fireEvent.click(installButton);

    await waitFor(() =>
      expect(install).toHaveBeenCalledWith({
        kind: 'bundled',
        plugin_id: 'meshloom.weather',
        granted_permissions: fixture.permissions,
      })
    );

    // The drawer opens on the new instance: log, secrets masked, diagnostic copy.
    expect(await screen.findByText('started ok')).toBeInTheDocument();
    expect(screen.getByText(t('schemaForm.secretSet'))).toBeInTheDocument();
    expect(screen.queryByText('********')).not.toBeInTheDocument();
    fireEvent.click(
      screen.getByRole('button', { name: t('settings.extensions.drawer.copyDiagnostic') })
    );
    await waitFor(() => expect(writeText).toHaveBeenCalledOnce());
    expect(writeText.mock.calls[0][0]).toContain('plugin: meshloom.weather 1.0.0');
    expect(toastMocks.success).toHaveBeenCalledWith(
      t('settings.extensions.drawer.diagnosticCopied')
    );
  });

  it('does not show the exfiltration sentence without http', async () => {
    vi.spyOn(api, 'getPlugins').mockResolvedValue([]);
    vi.spyOn(api, 'getBundledPlugins').mockResolvedValue([
      {
        ...fixture,
        permissions: { 'messages.read': { channels: 'all', direct: true } },
        privacy: { sends_to_third_parties: [], stores_message_content: false },
      },
    ]);
    render(<SettingsExtensionsSection health={health()} />);
    fireEvent.click(
      await screen.findByRole('button', { name: t('settings.extensions.add.button') })
    );
    fireEvent.click(await screen.findByRole('button', { name: /Weather|Météo/ }));
    await screen.findByTestId('plugin-privacy');
    expect(screen.queryByTestId('plugin-exfiltration-warning')).not.toBeInTheDocument();
  });

  it('names each LAN device in the consent, in English and French', async () => {
    const lanFixture: BundledPlugin = {
      ...fixture,
      permissions: {
        'messages.read': { channels: 'public', direct: true },
        http: { lan_hosts: ['printer.local', '[fd00::20]:8443'] },
      },
      privacy: { sends_to_third_parties: [], stores_message_content: false },
    };
    vi.spyOn(api, 'getPlugins').mockResolvedValue([]);
    vi.spyOn(api, 'getBundledPlugins').mockResolvedValue([lanFixture]);
    const install = vi.spyOn(api, 'installPlugin').mockResolvedValue(instance());
    render(<SettingsExtensionsSection health={health()} />);
    fireEvent.click(
      await screen.findByRole('button', { name: t('settings.extensions.add.button') })
    );
    fireEvent.click(await screen.findByRole('button', { name: /Weather|Météo/ }));

    const lan = await screen.findByTestId('plugin-lan-hosts');
    for (const host of ['printer.local', '[fd00::20]:8443']) {
      expect(lan).toHaveTextContent(t('plugins.permissions.http.lan_host', { host }));
    }
    expect(lan).toHaveTextContent(t('plugins.permissions.http.lan_note'));
    expect(
      screen.getByText(t('plugins.permissions.http.description_lan_only'))
    ).toBeInTheDocument();
    // LAN devices only: the title does not claim an Internet connection (MSHL-331).
    expect(screen.getByText(t('plugins.permissions.http.title_lan_only'))).toBeInTheDocument();
    expect(screen.queryByText(t('plugins.permissions.http.title'))).not.toBeInTheDocument();
    // LAN devices count as destinations for the exfiltration sentence.
    expect(screen.getByTestId('plugin-exfiltration-warning')).toHaveTextContent(
      t('plugins.permissions.http.private_messages_warning', {
        hosts: 'printer.local, [fd00::20]:8443',
      })
    );
    const fr = i18n.getFixedT('fr');
    expect(fr('plugins.permissions.http.lan_host', { host: 'printer.local' })).toBe(
      'Cette extension pourra communiquer avec cet appareil de votre réseau : printer.local'
    );

    fireEvent.click(screen.getByRole('checkbox'));
    fireEvent.click(screen.getByRole('button', { name: t('settings.extensions.add.install') }));
    await waitFor(() =>
      expect(install).toHaveBeenCalledWith({
        kind: 'bundled',
        plugin_id: 'meshloom.weather',
        granted_permissions: lanFixture.permissions,
      })
    );
  });

  it('shows a coded API error translated', async () => {
    vi.spyOn(api, 'getPlugins').mockResolvedValue([]);
    vi.spyOn(api, 'getBundledPlugins').mockResolvedValue([fixture]);
    vi.spyOn(api, 'installPlugin').mockRejectedValue(
      new ApiError('x', 400, { code: 'consent_required', message: 'x' })
    );
    render(<SettingsExtensionsSection health={health()} />);
    fireEvent.click(
      await screen.findByRole('button', { name: t('settings.extensions.add.button') })
    );
    fireEvent.click(await screen.findByRole('button', { name: /Weather|Météo/ }));
    fireEvent.click(await screen.findByRole('checkbox'));
    fireEvent.click(screen.getByRole('button', { name: t('settings.extensions.add.install') }));
    expect(await screen.findByText(t('errors.consent_required'))).toBeInTheDocument();
  });

  it('saves a lowered quota and refuses a raised one', async () => {
    const row = instance();
    vi.spyOn(api, 'getPlugins').mockResolvedValue([row]);
    const patch = vi.spyOn(api, 'patchPlugin').mockResolvedValue(row);
    render(<SettingsExtensionsSection health={health()} />);
    fireEvent.click(
      await screen.findByRole('button', {
        name: t('settings.extensions.openDetails', { name: 'Weather' }),
      })
    );
    const drawer = await screen.findByRole('dialog');
    const perHour = within(drawer).getByLabelText(t('settings.extensions.drawer.perHour'));
    fireEvent.change(perHour, { target: { value: '30' } });
    expect(within(drawer).getByText(/1/, { selector: 'p.text-destructive' })).toBeInTheDocument();
    expect(
      within(drawer).getByRole('button', { name: t('settings.extensions.drawer.save') })
    ).toBeDisabled();
    fireEvent.change(perHour, { target: { value: '5' } });
    fireEvent.click(
      within(drawer).getByRole('button', { name: t('settings.extensions.drawer.save') })
    );
    await waitFor(() =>
      expect(patch).toHaveBeenCalledWith(row.instance_id, {
        quotas: { 'messages.send': { per_hour: 5, per_day: 500 } },
      })
    );
  });

  it('uninstalls with the keep-data choice', async () => {
    const row = instance();
    vi.spyOn(api, 'getPlugins').mockResolvedValue([row]);
    const del = vi.spyOn(api, 'deletePlugin').mockResolvedValue(undefined);
    render(<SettingsExtensionsSection health={health()} />);
    fireEvent.click(
      await screen.findByRole('button', {
        name: t('settings.extensions.openDetails', { name: 'Weather' }),
      })
    );
    fireEvent.click(
      await screen.findByRole('button', { name: t('settings.extensions.drawer.uninstall') })
    );
    const dialog = await screen.findByText(t('settings.extensions.uninstall.text'));
    const box = dialog.closest('[role="dialog"]') as HTMLElement;
    fireEvent.click(within(box).getByRole('checkbox'));
    fireEvent.click(
      within(box).getByRole('button', { name: t('settings.extensions.uninstall.confirm') })
    );
    await waitFor(() => expect(del).toHaveBeenCalledWith(row.instance_id, true));
  });

  it('turns every write off when plugins are disabled on the server', async () => {
    vi.spyOn(api, 'getPlugins').mockResolvedValue([instance()]);
    render(<SettingsExtensionsSection health={health({ plugins_disabled_source: 'env' })} />);
    expect(await screen.findByRole('alert')).toHaveTextContent(
      t('settings.extensions.disabled.env')
    );
    expect(
      screen.getByRole('button', { name: t('settings.extensions.add.button') })
    ).toBeDisabled();
    expect(screen.getByRole('switch')).toBeDisabled();
  });

  it('asks for acknowledgement before starting an instance that needs a review', async () => {
    const row = instance({
      enabled: false,
      status: 'needs_attention',
      attention: { type: 'review_permissions' },
    });
    vi.spyOn(api, 'getPlugins').mockResolvedValue([row]);
    const patch = vi.spyOn(api, 'patchPlugin').mockResolvedValue(row);
    render(<SettingsExtensionsSection health={health()} />);
    expect(await screen.findByRole('switch')).toBeDisabled();
    fireEvent.click(
      screen.getByRole('button', {
        name: t('settings.extensions.openDetails', { name: 'Weather' }),
      })
    );
    fireEvent.click(
      await screen.findByRole('button', { name: t('settings.extensions.drawer.acceptAndEnable') })
    );
    await waitFor(() => expect(patch).toHaveBeenCalledTimes(2));
    expect(patch.mock.calls[0][1]).toHaveProperty('granted_permissions');
    expect(patch.mock.calls[1][1]).toEqual({ enabled: true });
  });
});

describe('re-consent, reorder and admin gate', () => {
  afterEach(() => {
    vi.restoreAllMocks();
    toastMocks.error.mockClear();
  });

  it('shows the full consent content before "Accept and enable"', async () => {
    const row = instance({
      enabled: false,
      status: 'needs_attention',
      attention: { type: 'review_permissions' },
      manifest: {
        permissions: {
          'messages.read': { channels: 'public', direct: true },
          http: { hosts: ['api.open-meteo.com'] },
        },
        privacy: { sends_to_third_parties: ['api.open-meteo.com'], stores_message_content: true },
      },
    });
    vi.spyOn(api, 'getPlugins').mockResolvedValue([row]);
    vi.spyOn(api, 'getPluginLogs').mockResolvedValue({ lines: [], truncated: false });
    render(<SettingsExtensionsSection health={health()} />);
    fireEvent.click(
      await screen.findByRole('button', {
        name: t('settings.extensions.openDetails', { name: 'Weather' }),
      })
    );
    expect(await screen.findByTestId('plugin-exfiltration-warning')).toHaveTextContent(
      t('plugins.permissions.http.private_messages_warning', { hosts: 'api.open-meteo.com' })
    );
    const privacy = screen.getByTestId('plugin-privacy');
    expect(privacy).toHaveTextContent('api.open-meteo.com');
    expect(privacy).toHaveTextContent(t('settings.extensions.privacy.stores'));
  });

  it('does not show the consent content for a config_required instance', async () => {
    const row = instance({
      enabled: false,
      status: 'needs_attention',
      attention: { type: 'config_required', fields: ['greeting'] },
    });
    vi.spyOn(api, 'getPlugins').mockResolvedValue([row]);
    vi.spyOn(api, 'getPluginLogs').mockResolvedValue({ lines: [], truncated: false });
    render(<SettingsExtensionsSection health={health()} />);
    fireEvent.click(
      await screen.findByRole('button', {
        name: t('settings.extensions.openDetails', { name: 'Weather' }),
      })
    );
    await screen.findByText(t('settings.extensions.attention.config_required.text'));
    expect(screen.queryByTestId('plugin-exfiltration-warning')).not.toBeInTheDocument();
  });

  it('offers no "Accept and enable" for a restored instance that must be reinstalled', async () => {
    const row = instance({
      enabled: false,
      status: 'needs_attention',
      attention: { type: 'reinstall', was_enabled: true, secrets_missing: false },
    });
    vi.spyOn(api, 'getPlugins').mockResolvedValue([row]);
    vi.spyOn(api, 'getPluginLogs').mockResolvedValue({ lines: [], truncated: false });
    render(<SettingsExtensionsSection health={health()} />);
    expect(await screen.findByRole('switch')).toBeDisabled();
    expect(screen.getByText(t('settings.extensions.attention.reinstall.badge'))).toBeVisible();
    fireEvent.click(
      screen.getByRole('button', {
        name: t('settings.extensions.openDetails', { name: 'Weather' }),
      })
    );
    await screen.findByText(t('settings.extensions.attention.reinstall.text'));
    expect(
      screen.queryByRole('button', { name: t('settings.extensions.drawer.acceptAndEnable') })
    ).not.toBeInTheDocument();
    expect(screen.queryByTestId('plugin-exfiltration-warning')).not.toBeInTheDocument();
  });

  it('says when a bundled update is held only because its privacy declaration widened', async () => {
    const row = instance({
      enabled: false,
      status: 'needs_attention',
      attention: { type: 'review_permissions', added: [], privacy_widened: true },
    });
    vi.spyOn(api, 'getPlugins').mockResolvedValue([row]);
    vi.spyOn(api, 'getPluginLogs').mockResolvedValue({ lines: [], truncated: false });
    render(<SettingsExtensionsSection health={health()} />);
    expect(
      await screen.findByText(t('settings.extensions.attention.privacy_widened.badge'))
    ).toBeVisible();
    fireEvent.click(
      screen.getByRole('button', {
        name: t('settings.extensions.openDetails', { name: 'Weather' }),
      })
    );
    await screen.findByText(t('settings.extensions.attention.privacy_widened.text'));
    expect(
      screen.queryByText(t('settings.extensions.attention.review_permissions.text'))
    ).not.toBeInTheDocument();
    expect(
      screen.getByRole('button', { name: t('settings.extensions.drawer.acceptAndEnable') })
    ).toBeInTheDocument();
  });

  it('names both reasons when new permissions come with a wider privacy declaration', async () => {
    const row = instance({
      enabled: false,
      status: 'needs_attention',
      attention: { type: 'review_permissions', added: ['http'], privacy_widened: true },
    });
    vi.spyOn(api, 'getPlugins').mockResolvedValue([row]);
    vi.spyOn(api, 'getPluginLogs').mockResolvedValue({ lines: [], truncated: false });
    render(<SettingsExtensionsSection health={health()} />);
    fireEvent.click(
      await screen.findByRole('button', {
        name: t('settings.extensions.openDetails', { name: 'Weather' }),
      })
    );
    await screen.findByText(t('settings.extensions.attention.review_permissions_privacy.text'));
  });

  it('translates the code mismatch error of a failed bundled update', async () => {
    vi.spyOn(api, 'getPlugins').mockResolvedValue([
      instance({ status: 'error', last_error: 'plugin_code_mismatch' }),
    ]);
    render(<SettingsExtensionsSection health={health()} />);
    expect(await screen.findByText(t('errors.plugin_code_mismatch'))).toBeInTheDocument();
    expect(screen.queryByText('plugin_code_mismatch')).not.toBeInTheDocument();
  });

  it('has the privacy and code mismatch texts in French and English', () => {
    for (const lng of ['en', 'fr']) {
      for (const key of [
        'settings.extensions.attention.privacy_widened.badge',
        'settings.extensions.attention.privacy_widened.text',
        'settings.extensions.attention.review_permissions_privacy.badge',
        'settings.extensions.attention.review_permissions_privacy.text',
        'errors.plugin_code_mismatch',
      ]) {
        expect(i18n.exists(key, { lng })).toBe(true);
      }
    }
  });

  it('reloads the list when a reorder fails half-way', async () => {
    const a = instance({ label: 'A', sort_order: 0 });
    const b = instance({
      instance_id: 'aaaaaaaa-0000-4000-8000-000000000009',
      label: 'B',
      sort_order: 1,
    });
    const list = vi.spyOn(api, 'getPlugins').mockResolvedValue([a, b]);
    vi.spyOn(api, 'patchPlugin')
      .mockResolvedValueOnce(a)
      .mockRejectedValueOnce(new ApiError('x', 500));
    render(<SettingsExtensionsSection health={health()} />);
    fireEvent.click(
      await screen.findByRole('button', { name: t('settings.extensions.moveDown', { name: 'A' }) })
    );
    await waitFor(() => expect(toastMocks.error).toHaveBeenCalled());
    await waitFor(() => expect(list).toHaveBeenCalledTimes(2));
  });

  it.each([
    ['admin', true],
    ['member', true],
    ['viewer', false],
    ['kiosk', false],
  ] as const)('the extensions section is open to %s: %s', (role, open) => {
    // Members read the list read-only (`GET /plugins` is member and up); kiosk and
    // viewer get no section.
    expect(capabilitiesFor(role).canOpenSettingsSection('extensions')).toBe(open);
  });
});

describe('global pause and member view', () => {
  afterEach(() => {
    vi.restoreAllMocks();
    toastMocks.error.mockClear();
    toastMocks.success.mockClear();
  });

  it('pauses every extension after a confirmation, then resumes from the banner', async () => {
    vi.spyOn(api, 'getPlugins').mockResolvedValue([instance()]);
    // The server state the section reads back after each change.
    let server: { paused: boolean; since: number | null; by: string | null } = NOT_PAUSED;
    vi.spyOn(api, 'getPluginsPause').mockImplementation(async () => server);
    const set = vi.spyOn(api, 'setPluginsPause').mockImplementation(async (paused) => {
      server = paused ? { paused: true, since: 1_760_000_000, by: 'alice' } : NOT_PAUSED;
      return server;
    });
    render(<SettingsExtensionsSection health={health()} />);
    fireEvent.click(
      await screen.findByRole('button', { name: t('settings.extensions.pause.button') })
    );
    // Nothing happens before the confirmation.
    expect(set).not.toHaveBeenCalled();
    const dialog = await screen.findByRole('dialog');
    expect(dialog).toHaveTextContent(t('settings.extensions.pause.confirmText'));
    fireEvent.click(
      within(dialog).getByRole('button', { name: t('settings.extensions.pause.confirm') })
    );
    await waitFor(() => expect(set).toHaveBeenCalledWith(true));
    const banner = await screen.findByTestId('plugins-paused-banner');
    expect(banner).toHaveTextContent('alice');
    expect(
      screen.queryByRole('button', { name: t('settings.extensions.pause.button') })
    ).toBeNull();
    fireEvent.click(
      within(banner).getByRole('button', { name: t('settings.extensions.pause.resume') })
    );
    await waitFor(() => expect(set).toHaveBeenLastCalledWith(false));
    await waitFor(() => expect(screen.queryByTestId('plugins-paused-banner')).toBeNull());
  });

  it('shows paused instances and keeps the writes on while paused', async () => {
    vi.spyOn(api, 'getPluginsPause').mockResolvedValue({
      paused: true,
      since: 1_760_000_000,
      by: 'alice',
    });
    vi.spyOn(api, 'getPlugins').mockResolvedValue([instance({ status: 'paused' })]);
    render(
      <SettingsExtensionsSection
        health={health({ plugins_disabled_source: 'paused', plugins_paused_since: 1_760_000_000 })}
      />
    );
    expect(await screen.findByText(t('settings.extensions.status.paused'))).toBeInTheDocument();
    expect(await screen.findByTestId('plugins-paused-banner')).toHaveTextContent(
      t('settings.extensions.pause.bannerExplain')
    );
    // The pause is not the host lock: no "turned off" alert, changes stay possible.
    expect(screen.queryByRole('alert')).toBeNull();
    expect(screen.getByRole('button', { name: t('settings.extensions.add.button') })).toBeEnabled();
    expect(screen.getByRole('switch')).toBeEnabled();
  });

  it('explains an unreadable pause state instead of showing an empty date', async () => {
    vi.spyOn(api, 'getPluginsPause').mockResolvedValue({ paused: true, since: null, by: null });
    vi.spyOn(api, 'getPlugins').mockResolvedValue([instance({ status: 'paused' })]);
    render(
      <SettingsExtensionsSection
        health={health({ plugins_disabled_source: 'paused', plugins_pause_unreadable: true })}
      />
    );
    const banner = await screen.findByTestId('plugins-paused-banner');
    expect(banner).toHaveTextContent(t('settings.extensions.pause.bannerUnreadable'));
    expect(banner).not.toHaveTextContent(/since\s*\.|depuis\s*\./);
    expect(within(banner).getByRole('button')).toBeEnabled();
  });

  it('never prints an empty date when the pause has none', async () => {
    vi.spyOn(api, 'getPluginsPause').mockResolvedValue({ paused: true, since: null, by: null });
    vi.spyOn(api, 'getPlugins').mockResolvedValue([instance({ status: 'paused' })]);
    render(<SettingsExtensionsSection health={health({ plugins_disabled_source: 'paused' })} />);
    expect(await screen.findByTestId('plugins-paused-banner')).toHaveTextContent(
      t('settings.extensions.pause.bannerNoTime')
    );
  });

  it('gives a member the list read-only, with the reason and no write control', async () => {
    roleRef.current = 'member';
    vi.spyOn(api, 'getPluginsPause').mockResolvedValue({
      paused: true,
      since: 1_760_000_000,
      by: 'alice',
    });
    const logs = vi.spyOn(api, 'getPluginLogs');
    const diagnostic = vi.spyOn(api, 'getPluginDiagnostic');
    vi.spyOn(api, 'getPlugins').mockResolvedValue([
      instance({ config: {}, secret_fields: [], status: 'paused' }),
    ]);
    render(<SettingsExtensionsSection health={health({ plugins_disabled_source: 'paused' })} />);
    expect(await screen.findByTestId('plugins-member-readonly')).toHaveTextContent(
      t('settings.extensions.memberReadOnly')
    );
    const banner = await screen.findByTestId('plugins-paused-banner');
    expect(banner).toHaveTextContent('alice');
    expect(within(banner).queryByRole('button')).toBeNull();
    expect(
      screen.queryByRole('button', { name: t('settings.extensions.pause.button') })
    ).toBeNull();
    expect(screen.queryByRole('button', { name: t('settings.extensions.add.button') })).toBeNull();
    expect(
      screen.queryByRole('button', { name: t('settings.extensions.local.newButton') })
    ).toBeNull();
    expect(screen.queryByRole('switch')).toBeNull();

    fireEvent.click(
      screen.getByRole('button', {
        name: t('settings.extensions.openDetails', { name: 'Weather' }),
      })
    );
    expect(await screen.findByTestId('plugin-drawer-member-readonly')).toBeInTheDocument();
    for (const name of [
      'settings.extensions.drawer.restart',
      'settings.extensions.drawer.uninstall',
      'settings.extensions.drawer.copyDiagnostic',
    ]) {
      expect(screen.queryByRole('button', { name: t(name) })).toBeNull();
    }
    expect(screen.queryByText(t('settings.extensions.logs.title'))).toBeNull();
    expect(logs).not.toHaveBeenCalled();
    expect(diagnostic).not.toHaveBeenCalled();
  });

  it('has the pause and member texts in French and English', () => {
    for (const lng of ['en', 'fr']) {
      for (const key of [
        'settings.extensions.status.paused',
        'settings.extensions.memberReadOnly',
        'settings.extensions.drawerMemberReadOnly',
        'settings.extensions.pause.button',
        'settings.extensions.pause.confirmText',
        'settings.extensions.pause.banner',
        'settings.extensions.pause.resume',
      ]) {
        expect(i18n.exists(key, { lng })).toBe(true);
      }
    }
  });
});

describe('MigrationBanner', () => {
  it('acknowledges', () => {
    const onAcknowledge = vi.fn();
    render(<MigrationBanner onAcknowledge={onAcknowledge} />);
    fireEvent.click(
      screen.getByRole('button', { name: t('settings.extensions.migration.acknowledge') })
    );
    expect(onAcknowledge).toHaveBeenCalledOnce();
  });

  it('shows the converted and attention counts', () => {
    render(<MigrationBanner converted={3} attention={2} onAcknowledge={vi.fn()} />);
    expect(screen.getByTestId('migration-counts')).toHaveTextContent(
      t('settings.extensions.migration.counts', { count: 3, attention: 2 })
    );
  });
});

describe('converted bots notice in the Extensions section', () => {
  beforeEach(() => {
    vi.spyOn(api, 'getPlugins').mockResolvedValue([]);
  });
  afterEach(() => {
    vi.restoreAllMocks();
  });

  it('is absent when health carries no converted_bots', async () => {
    render(<SettingsExtensionsSection health={health({ converted_bots: null })} />);
    await screen.findByText(t('settings.extensions.empty'));
    expect(screen.queryByText(t('settings.extensions.migration.title'))).toBeNull();
  });

  it('shows the banner, acknowledges it, then refreshes health', async () => {
    const ack = vi.spyOn(api, 'acknowledgeConvertedBots').mockResolvedValue({ acknowledged: true });
    const onHealthRefresh = vi.fn(async () => {});
    render(
      <SettingsExtensionsSection
        health={health({ converted_bots: { converted: 2, attention: 1 } })}
        onHealthRefresh={onHealthRefresh}
      />
    );
    expect(await screen.findByText(t('settings.extensions.migration.title'))).toBeInTheDocument();
    expect(screen.getByTestId('migration-counts')).toHaveTextContent(
      t('settings.extensions.migration.counts', { count: 2, attention: 1 })
    );
    fireEvent.click(
      screen.getByRole('button', { name: t('settings.extensions.migration.acknowledge') })
    );
    await waitFor(() => expect(ack).toHaveBeenCalledOnce());
    await waitFor(() => expect(onHealthRefresh).toHaveBeenCalledOnce());
  });
});

describe('lastErrorText', () => {
  it('translates only the host codes, in English and French; plugin text stays raw', () => {
    for (const lng of ['en', 'fr']) {
      const fixed = i18n.getFixedT(lng);
      const tl = (key: string, options?: Record<string, unknown>) => String(fixed(key, options));
      const mismatch = lastErrorText(tl, 'plugin_code_mismatch');
      expect(mismatch).toBe(tl('errors.plugin_code_mismatch'));
      expect(mismatch).not.toBe('plugin_code_mismatch');
      // A snake_case string that names another host message is a plugin's: shown as is.
      expect(lastErrorText(tl, 'plugin_revoked')).toBe('plugin_revoked');
      expect(lastErrorText(tl, 'plugins_disabled')).toBe('plugins_disabled');
      expect(lastErrorText(tl, 'crash: exited unexpectedly (code 1)')).toBe(
        'crash: exited unexpectedly (code 1)'
      );
    }
  });
});
