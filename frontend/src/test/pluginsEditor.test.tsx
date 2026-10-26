import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

const toastMocks = vi.hoisted(() => ({ error: vi.fn(), success: vi.fn() }));
vi.mock('../components/ui/sonner', () => ({ toast: toastMocks }));
vi.mock('../components/BotCodeEditor', () => ({
  BotCodeEditor: ({
    value,
    onChange,
    ariaLabel,
  }: {
    value: string;
    onChange: (v: string) => void;
    ariaLabel?: string;
  }) => (
    <textarea
      data-testid="code-editor"
      aria-label={ariaLabel}
      value={value}
      onChange={(e) => onChange(e.target.value)}
    />
  ),
}));

import { api, ApiError } from '../api';
import { SettingsExtensionsSection } from '../components/settings/SettingsExtensionsSection';
import type { PluginInstance } from '../components/settings/extensions/pluginsTypes';
import i18n from '../i18n';
import type { HealthStatus } from '../types';

const t = (key: string, options?: Record<string, unknown>) => i18n.t(key, options);
const health = { plugins_isolation: 'bwrap', plugins_disabled_source: null } as HealthStatus;

function instance(overrides: Partial<PluginInstance> = {}): PluginInstance {
  return {
    instance_id: 'aaaaaaaa-0000-4000-8000-000000000001',
    plugin_id: 'meshloom.weather',
    label: 'Weather',
    kind: 'bundled',
    version: '1.0.0',
    enabled: true,
    manifest: { permissions: {} },
    config: { city: 'Paris', api_key: '********' },
    config_schema: {
      type: 'object',
      required: ['city'],
      properties: {
        city: { type: 'string', title: { en: 'City', fr: 'Ville' }, minLength: 2 },
        api_key: { type: 'string', title: { en: 'API key', fr: 'Clé API' }, writeOnly: true },
      },
    },
    secret_fields: ['api_key'],
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
    restarts: 0,
    dropped_events: 0,
    ...overrides,
  };
}

function local(overrides: Partial<PluginInstance> = {}): PluginInstance {
  return instance({
    instance_id: 'bbbbbbbb-0000-4000-8000-000000000002',
    plugin_id: 'local.pling-abc123',
    label: 'Pling',
    kind: 'local',
    manifest: { hooks: ['message'], permissions: { 'messages.send': { targets: 'reply' } } },
    granted_permissions: { 'messages.send': { targets: 'reply' } },
    config: {},
    config_schema: {},
    secret_fields: [],
    source: 'async def on_message(event):\n    pass\n',
    ...overrides,
  });
}

async function openDrawer(name: string) {
  fireEvent.click(
    await screen.findByRole('button', { name: t('settings.extensions.openDetails', { name }) })
  );
}

describe('plugin config form', () => {
  beforeEach(() => {
    vi.spyOn(api, 'getPluginLogs').mockResolvedValue({ lines: [], truncated: false });
  });
  afterEach(() => {
    vi.restoreAllMocks();
    toastMocks.success.mockClear();
  });

  it('keeps a set secret by leaving it out, and never shows its value', async () => {
    vi.spyOn(api, 'getPlugins').mockResolvedValue([instance()]);
    const patch = vi.spyOn(api, 'patchPlugin').mockResolvedValue(instance({ updated_at: 2 }));
    render(<SettingsExtensionsSection health={health} />);
    await openDrawer('Weather');
    expect(await screen.findByText(t('schemaForm.secretSet'))).toBeInTheDocument();
    expect(document.body.innerHTML).not.toContain('********');
    fireEvent.change(screen.getByLabelText(/Ville|City/), { target: { value: 'Lyon' } });
    fireEvent.click(screen.getByRole('button', { name: t('settings.extensions.config.save') }));
    await waitFor(() =>
      expect(patch).toHaveBeenCalledWith(instance().instance_id, { config: { city: 'Lyon' } })
    );
    expect(toastMocks.success).toHaveBeenCalledWith(t('settings.extensions.config.saved'));
  });

  it('shows the settings an update reset, and drops the notice once the config is saved', async () => {
    const notice = instance({
      attention: {
        type: 'config_dropped',
        fields: ['city'],
        from_version: '1.0.0',
        to_version: '1.1.0',
      },
    });
    vi.spyOn(api, 'getPlugins').mockResolvedValue([notice]);
    vi.spyOn(api, 'patchPlugin').mockResolvedValue(instance({ updated_at: 2 }));
    render(<SettingsExtensionsSection health={health} />);
    // A notice, not a hold: the instance keeps running.
    expect(
      await screen.findByText(t('settings.extensions.attention.config_dropped.badge'))
    ).toBeVisible();
    await openDrawer('Weather');
    expect(await screen.findByTestId('plugin-config-dropped')).toHaveTextContent('city');
    fireEvent.click(screen.getByRole('button', { name: t('settings.extensions.config.save') }));
    await waitFor(() => expect(screen.queryByTestId('plugin-config-dropped')).toBeNull());
  });

  it('lists the settings an update reset on a held instance too', async () => {
    vi.spyOn(api, 'getPlugins').mockResolvedValue([
      instance({
        enabled: false,
        status: 'needs_attention',
        attention: { type: 'consent_required', permissions: ['http'], config_dropped: ['city'] },
      }),
    ]);
    render(<SettingsExtensionsSection health={health} />);
    await openDrawer('Weather');
    expect(await screen.findByTestId('plugin-config-dropped')).toHaveTextContent('city');
  });

  it('replaces a secret only when asked and clears the typed value after the save', async () => {
    vi.spyOn(api, 'getPlugins').mockResolvedValue([instance()]);
    const patch = vi.spyOn(api, 'patchPlugin').mockResolvedValue(instance({ updated_at: 2 }));
    render(<SettingsExtensionsSection health={health} />);
    await openDrawer('Weather');
    fireEvent.click(await screen.findByRole('button', { name: /Remplacer|Replace/ }));
    fireEvent.change(screen.getByLabelText(/Clé API|API key/, { selector: 'input' }), {
      target: { value: 'k-123' },
    });
    fireEvent.click(screen.getByRole('button', { name: t('settings.extensions.config.save') }));
    await waitFor(() =>
      expect(patch).toHaveBeenCalledWith(instance().instance_id, {
        config: { city: 'Paris', api_key: 'k-123' },
      })
    );
    await waitFor(() => expect(screen.queryByDisplayValue('k-123')).toBeNull());
    expect(document.body.innerHTML).not.toContain('k-123');
  });

  it('maps a config_invalid answer to the field', async () => {
    vi.spyOn(api, 'getPlugins').mockResolvedValue([instance()]);
    vi.spyOn(api, 'patchPlugin').mockRejectedValue(
      new ApiError('bad', 400, {
        code: 'config_invalid',
        params: { issues: [{ code: 'config_value_invalid', path: 'city' }] },
      })
    );
    render(<SettingsExtensionsSection health={health} />);
    await openDrawer('Weather');
    fireEvent.click(
      await screen.findByRole('button', { name: t('settings.extensions.config.save') })
    );
    const field = screen.getByLabelText(/Ville|City/);
    await waitFor(() => expect(field).toHaveAttribute('aria-invalid', 'true'));
    expect(
      screen.getByText(t('settings.extensions.config.issue.config_value_invalid'))
    ).toBeInTheDocument();
  });

  it('explains config_required and jumps to the flagged field', async () => {
    vi.spyOn(api, 'getPlugins').mockResolvedValue([
      instance({
        enabled: false,
        status: 'needs_attention',
        config: {},
        attention: { type: 'config_required', fields: ['city'] },
      }),
    ]);
    render(<SettingsExtensionsSection health={health} />);
    await openDrawer('Weather');
    fireEvent.click(
      await screen.findByRole('button', { name: t('settings.extensions.config.goTo') })
    );
    expect(screen.getByLabelText(/Ville|City/)).toHaveFocus();
    expect(document.querySelector('[data-field="city"]')).toHaveAttribute('data-flagged');
  });
});

describe('local plugin editor', () => {
  beforeEach(() => {
    vi.spyOn(api, 'getPluginLogs').mockResolvedValue({ lines: [], truncated: false });
  });
  afterEach(() => {
    vi.restoreAllMocks();
    toastMocks.success.mockClear();
  });

  it('creates a local plugin from the template and opens its editor', async () => {
    vi.spyOn(api, 'getPlugins').mockResolvedValue([]);
    const create = vi.spyOn(api, 'createLocalPlugin').mockResolvedValue(local({ enabled: false }));
    render(<SettingsExtensionsSection health={health} />);
    await screen.findByText(t('settings.extensions.empty'));
    fireEvent.click(screen.getByRole('button', { name: t('settings.extensions.local.newButton') }));
    const confirm = await screen.findByRole('button', {
      name: t('settings.extensions.local.create.confirm'),
    });
    expect(confirm).toBeDisabled();
    fireEvent.change(screen.getByLabelText(t('settings.extensions.local.create.label')), {
      target: { value: 'Pling' },
    });
    fireEvent.click(confirm);
    await waitFor(() =>
      expect(create).toHaveBeenCalledWith({
        label: 'Pling',
        hooks: ['message'],
        permissions: {
          'messages.read': { channels: 'public' },
          'messages.send': { targets: 'reply' },
        },
        template: 'echo',
      })
    );
    expect(await screen.findByTestId('local-plugin-editor')).toBeInTheDocument();
    expect(await screen.findByTestId('code-editor')).toHaveValue(local().source!);
  });

  it('saves the code, lists the scan with line numbers and accepts a suggested host', async () => {
    vi.spyOn(api, 'getPlugins').mockResolvedValue([local()]);
    const put = vi.spyOn(api, 'putPluginSource').mockResolvedValue({
      instance: local({ source: 'x = 1\n' }),
      scan: [
        { code: 'print_call', line: 4, detail: null, informative: true },
        { code: 'import_not_allowed', line: 1, detail: 'socket', informative: false },
      ],
      http_hosts: ['api.example.com'],
    });
    const patch = vi.spyOn(api, 'patchPlugin').mockResolvedValue(local());
    render(<SettingsExtensionsSection health={health} />);
    await openDrawer('Pling');
    const editor = await screen.findByTestId('code-editor');
    const save = screen.getByRole('button', { name: t('settings.extensions.local.saveCode') });
    expect(save).toBeDisabled();
    fireEvent.change(editor, { target: { value: 'x = 1\n' } });
    expect(save).toBeEnabled();
    fireEvent.click(save);
    await waitFor(() => expect(put).toHaveBeenCalledWith(local().instance_id, 'x = 1\n'));
    const scan = await screen.findByTestId('local-scan');
    expect(
      within(scan).getByText(t('settings.extensions.local.line', { line: 4 }))
    ).toBeInTheDocument();
    expect(scan).toHaveTextContent('socket');

    fireEvent.click(
      screen.getByRole('button', {
        name: t('settings.extensions.local.allowHost', { host: 'api.example.com' }),
      })
    );
    await waitFor(() =>
      expect(patch).toHaveBeenCalledWith(local().instance_id, {
        granted_permissions: {
          'messages.send': { targets: 'reply' },
          http: { hosts: ['api.example.com'] },
        },
      })
    );
  });

  it('shows a syntax error with its line', async () => {
    vi.spyOn(api, 'getPlugins').mockResolvedValue([local()]);
    vi.spyOn(api, 'putPluginSource').mockRejectedValue(
      new ApiError('bad', 400, { code: 'source_syntax_error', params: { line: 7 } })
    );
    render(<SettingsExtensionsSection health={health} />);
    await openDrawer('Pling');
    fireEvent.change(await screen.findByTestId('code-editor'), { target: { value: 'def (' } });
    fireEvent.click(screen.getByRole('button', { name: t('settings.extensions.local.saveCode') }));
    expect(
      await screen.findByText(t('settings.extensions.local.syntaxError', { line: 7 }))
    ).toBeInTheDocument();
  });

  it('checks the plugin and lists findings with their line', async () => {
    vi.spyOn(api, 'getPlugins').mockResolvedValue([local()]);
    vi.spyOn(api, 'checkPlugin').mockResolvedValue({
      ok: false,
      errors: [
        {
          stage: 'package',
          code: 'manifest_invalid',
          message: 'bad',
          path: '',
          line: null,
          informative: false,
        },
      ],
      warnings: [
        {
          stage: 'scan',
          code: 'eval_call',
          message: 'eval_call',
          path: '',
          line: 3,
          informative: false,
        },
      ],
      http_hosts: [],
    });
    render(<SettingsExtensionsSection health={health} />);
    await openDrawer('Pling');
    fireEvent.click(
      await screen.findByRole('button', { name: t('settings.extensions.local.check') })
    );
    const result = await screen.findByTestId('local-check');
    expect(result).toHaveTextContent(t('settings.extensions.local.checkFailed'));
    expect(result).toHaveTextContent(t('settings.extensions.local.reason.manifest_invalid'));
    expect(result).toHaveTextContent(t('settings.extensions.local.line', { line: 3 }));
    expect(result).toHaveTextContent(t('settings.extensions.local.reason.eval_call'));
  });

  it('blocks Check and Test while the code is unsaved', async () => {
    vi.spyOn(api, 'getPlugins').mockResolvedValue([local()]);
    render(<SettingsExtensionsSection health={health} />);
    await openDrawer('Pling');
    fireEvent.change(await screen.findByTestId('code-editor'), { target: { value: 'y = 2\n' } });
    expect(
      screen.getByRole('button', { name: t('settings.extensions.local.check') })
    ).toBeDisabled();
    expect(
      screen.getByRole('button', { name: t('settings.extensions.local.test') })
    ).toBeDisabled();
  });

  it('tests with a message: shows the echo reply and says nothing was sent', async () => {
    vi.spyOn(api, 'getPlugins').mockResolvedValue([local()]);
    const test = vi.spyOn(api, 'testPlugin').mockResolvedValue({
      result: null,
      sends: [{ to: 'reply', text: 'Plong!' }],
      truncated: false,
      logs: ['handled !pling'],
    });
    render(<SettingsExtensionsSection health={health} />);
    await openDrawer('Pling');
    const run = await screen.findByRole('button', { name: t('settings.extensions.local.test') });
    expect(run).toBeDisabled();
    fireEvent.change(screen.getByLabelText(t('settings.extensions.local.testText')), {
      target: { value: '!pling' },
    });
    fireEvent.click(run);
    await waitFor(() => expect(test).toHaveBeenCalledWith(local().instance_id, { text: '!pling' }));
    const result = await screen.findByTestId('local-test-result');
    expect(result).toHaveTextContent(t('settings.extensions.local.nothingSent'));
    expect(result).toHaveTextContent('Plong!');
    expect(result).toHaveTextContent('handled !pling');
  });

  it('tests against a chosen channel', async () => {
    vi.spyOn(api, 'getPlugins').mockResolvedValue([local()]);
    vi.spyOn(api, 'getChannels').mockResolvedValue([
      { key: 'AB'.repeat(16), name: '#lyon' } as never,
    ]);
    const test = vi
      .spyOn(api, 'testPlugin')
      .mockResolvedValue({ result: null, sends: [], truncated: false, logs: [] });
    render(<SettingsExtensionsSection health={health} />);
    await openDrawer('Pling');
    fireEvent.change(await screen.findByLabelText(t('settings.extensions.local.testTarget')), {
      target: { value: 'channel' },
    });
    fireEvent.change(
      await screen.findByRole('combobox', { name: t('settings.extensions.local.targetChannel') }),
      {
        target: { value: 'AB'.repeat(16) },
      }
    );
    fireEvent.change(screen.getByLabelText(t('settings.extensions.local.testText')), {
      target: { value: 'hi' },
    });
    fireEvent.click(screen.getByRole('button', { name: t('settings.extensions.local.test') }));
    await waitFor(() =>
      expect(test).toHaveBeenCalledWith(local().instance_id, {
        text: 'hi',
        channel: 'AB'.repeat(16),
      })
    );
    expect(await screen.findByText(t('settings.extensions.local.noReply'))).toBeInTheDocument();
  });

  it('saves hooks and permissions in one PATCH, and nothing is half-saved on refusal', async () => {
    vi.spyOn(api, 'getPlugins').mockResolvedValue([local()]);
    const patch = vi
      .spyOn(api, 'patchPlugin')
      .mockRejectedValueOnce(new ApiError('bad', 400, { code: 'manifest_invalid' }))
      .mockResolvedValue(local());
    render(<SettingsExtensionsSection health={health} />);
    await openDrawer('Pling');
    fireEvent.click(await screen.findByLabelText(t('settings.extensions.local.hooks.health')));
    fireEvent.click(screen.getByLabelText(t('plugins.permissions.storage.title')));
    const save = screen.getByRole('button', {
      name: t('settings.extensions.local.savePermissions'),
    });
    fireEvent.click(save);
    await waitFor(() => expect(patch).toHaveBeenCalledTimes(1));
    expect(patch).toHaveBeenCalledWith(local().instance_id, {
      granted_permissions: { 'messages.send': { targets: 'reply' }, storage: {} },
      hooks: ['message', 'health'],
    });
    // Still pending after the refusal: the button stays and can be retried.
    await waitFor(() => expect(save).toBeEnabled());
    fireEvent.click(save);
    await waitFor(() => expect(patch).toHaveBeenCalledTimes(2));
  });

  it('sends only hooks when only hooks changed', async () => {
    vi.spyOn(api, 'getPlugins').mockResolvedValue([local()]);
    const patch = vi.spyOn(api, 'patchPlugin').mockResolvedValue(local());
    render(<SettingsExtensionsSection health={health} />);
    await openDrawer('Pling');
    fireEvent.click(await screen.findByLabelText(t('settings.extensions.local.hooks.health')));
    fireEvent.click(
      screen.getByRole('button', { name: t('settings.extensions.local.savePermissions') })
    );
    await waitFor(() =>
      expect(patch).toHaveBeenCalledWith(local().instance_id, { hooks: ['message', 'health'] })
    );
  });

  it('accepting a host does not save other pending edits', async () => {
    vi.spyOn(api, 'getPlugins').mockResolvedValue([local()]);
    vi.spyOn(api, 'putPluginSource').mockResolvedValue({
      instance: local({ source: 'x\n' }),
      scan: [],
      http_hosts: ['api.example.com'],
    });
    const patch = vi.spyOn(api, 'patchPlugin').mockResolvedValue(local());
    render(<SettingsExtensionsSection health={health} />);
    await openDrawer('Pling');
    fireEvent.click(await screen.findByLabelText(t('plugins.permissions.storage.title')));
    fireEvent.change(screen.getByTestId('code-editor'), { target: { value: 'x\n' } });
    fireEvent.click(screen.getByRole('button', { name: t('settings.extensions.local.saveCode') }));
    fireEvent.click(
      await screen.findByRole('button', {
        name: t('settings.extensions.local.allowHost', { host: 'api.example.com' }),
      })
    );
    await waitFor(() =>
      expect(patch).toHaveBeenCalledWith(local().instance_id, {
        granted_permissions: {
          'messages.send': { targets: 'reply' },
          http: { hosts: ['api.example.com'] },
        },
      })
    );
    // The unsaved storage edit is still pending.
    expect(screen.getByLabelText(t('plugins.permissions.storage.title'))).toBeChecked();
  });
  it('suggests local-network devices one by one, with their line and a service-port warning', async () => {
    const source = "A = 'https://api.example.com'\nB = 'http://admin:pw@192.168.1.20:8123/api'\n";
    vi.spyOn(api, 'getPlugins').mockResolvedValue([local()]);
    vi.spyOn(api, 'putPluginSource').mockResolvedValue({
      instance: local({ source }),
      scan: [],
      http_hosts: [],
      lan_hosts: [
        { host: 'shelly.local', line: 1, service_port: false },
        { host: '192.168.1.20:8123', line: 2, service_port: true },
      ],
      lan_hosts_truncated: true,
    });
    const patch = vi.spyOn(api, 'patchPlugin').mockResolvedValue(local());
    render(<SettingsExtensionsSection health={health} />);
    await openDrawer('Pling');
    fireEvent.change(await screen.findByTestId('code-editor'), { target: { value: source } });
    fireEvent.click(screen.getByRole('button', { name: t('settings.extensions.local.saveCode') }));
    const box = await screen.findByTestId('local-lan-hosts');
    expect(box).toHaveTextContent(t('settings.extensions.local.lanHostsFound'));
    expect(box).toHaveTextContent(t('settings.extensions.local.line', { line: 2 }));
    expect(box).toHaveTextContent('192.168.1.20:8123/api');
    expect(box).not.toHaveTextContent('admin:pw');
    expect(within(box).getAllByRole('note')).toHaveLength(1);
    expect(box).toHaveTextContent(t('settings.extensions.local.lanHostServicePort'));
    expect(box).toHaveTextContent(t('settings.extensions.local.lanHostsTruncated'));

    fireEvent.click(
      within(box).getByRole('button', {
        name: t('settings.extensions.local.allowLanHost', { host: 'shelly.local' }),
      })
    );
    await waitFor(() =>
      expect(patch).toHaveBeenCalledWith(local().instance_id, {
        granted_permissions: {
          'messages.send': { targets: 'reply' },
          http: { lan_hosts: ['shelly.local'] },
        },
      })
    );
    // Added: no longer suggested; the other one still is.
    await waitFor(() =>
      expect(
        screen.queryByRole('button', {
          name: t('settings.extensions.local.allowLanHost', { host: 'shelly.local' }),
        })
      ).not.toBeInTheDocument()
    );
    expect(
      screen.getByRole('button', {
        name: t('settings.extensions.local.allowLanHost', { host: '192.168.1.20:8123' }),
      })
    ).toBeInTheDocument();
  });

  it('keeps public hosts when a device is added, and refuses past the lan_hosts maximum', async () => {
    const full = Array.from({ length: 10 }, (_, i) => `192.168.1.${i + 1}`);
    const withHttp = (lan: string[]) =>
      local({
        manifest: {
          hooks: ['message'],
          permissions: { http: { hosts: ['api.example.com'], lan_hosts: lan } },
        },
        granted_permissions: { http: { hosts: ['api.example.com'], lan_hosts: lan } },
      });
    vi.spyOn(api, 'getPlugins').mockResolvedValue([withHttp(full)]);
    vi.spyOn(api, 'checkPlugin').mockResolvedValue({
      ok: true,
      errors: [],
      warnings: [],
      http_hosts: [],
      lan_hosts: [{ host: 'nas.local', line: 1, service_port: false }],
      lan_hosts_truncated: false,
    });
    render(<SettingsExtensionsSection health={health} />);
    await openDrawer('Pling');
    fireEvent.click(
      await screen.findByRole('button', { name: t('settings.extensions.local.check') })
    );
    const add = await screen.findByRole('button', {
      name: t('settings.extensions.local.allowLanHost', { host: 'nas.local' }),
    });
    expect(add).toBeDisabled();
    expect(screen.getByTestId('local-lan-hosts')).toHaveTextContent(
      t('settings.extensions.local.lanHostsFull', { max: 10 })
    );
  });

  it('has every LAN suggestion string in French and English', () => {
    for (const key of [
      'lanHostsFound',
      'allowLanHost',
      'lanHostServicePort',
      'lanHostsFull',
      'lanHostsTruncated',
    ]) {
      for (const lng of ['fr', 'en']) {
        expect(i18n.exists(`settings.extensions.local.${key}`, { lng })).toBe(true);
      }
      expect(i18n.t(`settings.extensions.local.${key}`, { lng: 'fr' })).not.toBe(
        i18n.t(`settings.extensions.local.${key}`, { lng: 'en' })
      );
    }
  });
});
