import { act, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

const mocks = vi.hoisted(() => ({
  inspectBackup: vi.fn(),
  startFullRestore: vi.fn(),
  getRestoreJob: vi.fn(),
  getLatestRestoreJob: vi.fn(),
  rebootRadio: vi.fn(),
  toast: { success: vi.fn(), error: vi.fn(), info: vi.fn() },
}));

vi.mock('../api', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../api')>();
  return {
    ...actual,
    api: {
      inspectBackup: mocks.inspectBackup,
      startFullRestore: mocks.startFullRestore,
      getRestoreJob: mocks.getRestoreJob,
      getLatestRestoreJob: mocks.getLatestRestoreJob,
      rebootRadio: mocks.rebootRadio,
    },
  };
});

vi.mock('../components/ui/sonner', () => ({ toast: mocks.toast }));

import { ApiError } from '../api';
import {
  BackupRestoreFlow,
  restoreCodeText,
  restoreWarningText,
} from '../components/settings/BackupRestoreFlow';
import i18n from '../i18n';
import type { BackupInspectResult, BackupRestoreJob } from '../types';

const LIVE = 'bb'.repeat(32);

function inspectResult(overrides: Partial<BackupInspectResult> = {}): BackupInspectResult {
  return {
    format: 'meshloom-backup-v2',
    encrypted: false,
    exported_at: 1760000000,
    meshloom_version: '4.19.0',
    public_key: 'aa'.repeat(32),
    radio_name: 'Base',
    has_private_key: true,
    private_key_status: 'included',
    has_radio_settings: true,
    has_wifi: true,
    counts: { contacts: 3, channels: 2, groups: 1, fanout_configs: 0, push_subscriptions: 0 },
    bot_names: [],
    bots_will_be_skipped: false,
    integrations: [],
    radio_proxy_enabled: null,
    identity_relation: 'same',
    identity_requires: { replace_live_identity: false, wipe_history: false },
    identity_refusal: null,
    live_radio_public_key: LIVE,
    live_radio_name: 'Fresh',
    ble_pin_differs_on_ble: false,
    wifi_ssid_differs_on_tcp: false,
    warnings: [],
    ...overrides,
  };
}

function job(overrides: Partial<BackupRestoreJob> = {}): BackupRestoreJob {
  return {
    job_id: 'job-1',
    state: 'done',
    started_at: 1,
    finished_at: 2,
    steps: [
      { step: 'identity', status: 'done', code: 'restored', detail: null, items: [] },
      { step: 'meshloom', status: 'done', code: null, detail: null, items: [] },
      { step: 'radio_settings', status: 'done', code: null, detail: null, items: [] },
      { step: 'channels', status: 'done', code: null, detail: null, items: [] },
      { step: 'contacts', status: 'done', code: null, detail: null, items: [] },
      { step: 'wifi', status: 'done', code: null, detail: null, items: [] },
    ],
    result: {
      steps: [],
      contacts_upserted: 3,
      channels_upserted: 2,
      groups_upserted: 1,
      settings_updated: true,
      channels_on_radio: 2,
      contacts_on_radio: 3,
      identity_restored: true,
      reboot_required: true,
      warnings: [],
    },
    ...overrides,
  };
}

function pickFile(content: unknown) {
  const input = screen.getByLabelText(i18n.t('settings.fullRestore.choose'));
  const text = typeof content === 'string' ? content : JSON.stringify(content);
  const file = new File([text], 'backup.json', { type: 'application/json' });
  Object.defineProperty(file, 'text', { value: () => Promise.resolve(text) });
  fireEvent.change(input, { target: { files: [file] } });
}

const V2 = { format: 'meshloom-backup-v2', exported_at: 1, identity: {} };

function checkbox(key: string, params?: Record<string, unknown>): HTMLInputElement {
  return screen.getByLabelText(i18n.t(key, params)) as HTMLInputElement;
}

describe('BackupRestoreFlow', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mocks.getLatestRestoreJob.mockResolvedValue(null);
    mocks.inspectBackup.mockResolvedValue(inspectResult());
    mocks.startFullRestore.mockResolvedValue({ job_id: 'job-1' });
    mocks.getRestoreJob.mockResolvedValue(job());
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  it('inspects a file and shows its summary before anything is written', async () => {
    render(<BackupRestoreFlow />);
    pickFile(V2);
    expect(await screen.findByText(i18n.t('settings.fullRestore.hasIdentity'))).toBeInTheDocument();
    expect(mocks.inspectBackup).toHaveBeenCalledWith({ backup: V2 });
    expect(screen.getByTestId('identity-relation')).toHaveTextContent(
      i18n.t('settings.fullRestore.identity.same')
    );
    expect(mocks.startFullRestore).not.toHaveBeenCalled();
    const restore = screen.getByRole('button', { name: i18n.t('settings.fullRestore.restore') });
    expect(restore).toBeDisabled();
    fireEvent.click(checkbox('settings.fullRestore.confirm'));
    expect(restore).toBeEnabled();
  });

  it('treats a file without format as a v1 backup', async () => {
    mocks.inspectBackup.mockResolvedValue(
      inspectResult({ format: 'meshloom-backup-v1', has_private_key: false, has_wifi: false })
    );
    render(<BackupRestoreFlow />);
    pickFile({ contacts: [] });
    expect(await screen.findByText(i18n.t('settings.fullRestore.v1Only'))).toBeInTheDocument();
    expect(mocks.inspectBackup).toHaveBeenCalledWith({ backup: { contacts: [] } });
    expect(screen.queryByLabelText(i18n.t('settings.fullRestore.steps.wifi'))).toBeNull();
    expect(screen.queryByLabelText(i18n.t('settings.fullRestore.steps.identity'))).toBeNull();
  });

  it('refuses an unknown format before inspect', async () => {
    render(<BackupRestoreFlow />);
    pickFile({ format: 'something-else' });
    expect(await screen.findByRole('alert')).toHaveTextContent(i18n.t('errors.backup_invalid'));
    expect(mocks.inspectBackup).not.toHaveBeenCalled();
  });

  it('asks the passphrase of an encrypted file', async () => {
    const envelope = { format: 'meshloom-backup-v2-encrypted', ciphertext: 'x' };
    render(<BackupRestoreFlow />);
    pickFile(envelope);
    const field = await screen.findByLabelText(i18n.t('settings.fullRestore.passphrase'));
    expect(mocks.inspectBackup).not.toHaveBeenCalled();
    fireEvent.change(field, { target: { value: 'a long passphrase' } });
    fireEvent.click(screen.getByRole('button', { name: i18n.t('settings.fullRestore.unlock') }));
    await waitFor(() =>
      expect(mocks.inspectBackup).toHaveBeenCalledWith({
        backup: envelope,
        passphrase: 'a long passphrase',
      })
    );
  });

  it('asks a separate confirmation naming the live radio, and sends its key', async () => {
    mocks.inspectBackup.mockResolvedValue(
      inspectResult({
        identity_relation: 'replaces_live',
        identity_requires: { replace_live_identity: true, wipe_history: true },
      })
    );
    render(<BackupRestoreFlow />);
    pickFile(V2);
    const replace = await screen.findByLabelText(
      i18n.t('settings.fullRestore.identityReplacesLiveConfirm', {
        name: 'Fresh',
        key: LIVE.slice(0, 12),
      })
    );
    fireEvent.click(checkbox('settings.fullRestore.confirm'));
    const restore = screen.getByRole('button', { name: i18n.t('settings.fullRestore.restore') });
    expect(restore).toBeDisabled();
    fireEvent.click(replace);
    expect(restore).toBeDisabled();
    fireEvent.click(checkbox('settings.fullRestore.identityWipeConfirm'));
    expect(restore).toBeEnabled();
    fireEvent.click(restore);
    await waitFor(() => expect(mocks.startFullRestore).toHaveBeenCalled());
    expect(mocks.startFullRestore.mock.calls[0][0]).toMatchObject({
      confirm: true,
      replace_live_identity: true,
      wipe_history: true,
      expected_live_public_key: LIVE,
    });
  });

  it.each([false, true])(
    'offers the optional history wipe unchecked, and sends it only when ticked (%s)',
    async (tick) => {
      mocks.inspectBackup.mockResolvedValue(
        inspectResult({
          identity_relation: 'replaces_live',
          identity_requires: { replace_live_identity: true, wipe_history: false },
          identity_allows_wipe: true,
        })
      );
      render(<BackupRestoreFlow />);
      pickFile(V2);
      const optional = (await screen.findByLabelText(
        i18n.t('settings.fullRestore.identityWipeOptional')
      )) as HTMLInputElement;
      expect(optional.checked).toBe(false);
      expect(
        screen.queryByLabelText(i18n.t('settings.fullRestore.identityWipeConfirm'))
      ).toBeNull();
      fireEvent.click(
        screen.getByLabelText(
          i18n.t('settings.fullRestore.identityReplacesLiveConfirm', {
            name: 'Fresh',
            key: LIVE.slice(0, 12),
          })
        )
      );
      fireEvent.click(checkbox('settings.fullRestore.confirm'));
      if (tick) fireEvent.click(optional);
      const restore = screen.getByRole('button', { name: i18n.t('settings.fullRestore.restore') });
      expect(restore).toBeEnabled();
      fireEvent.click(restore);
      await waitFor(() => expect(mocks.startFullRestore).toHaveBeenCalled());
      expect(mocks.startFullRestore.mock.calls[0][0]).toMatchObject({
        replace_live_identity: true,
        wipe_history: tick,
      });
    }
  );

  it.each([
    [LIVE, 'settings.fullRestore.identity.same'],
    [null, 'settings.fullRestore.identity.sameNotConnected'],
  ])('says whether a radio is there when the identity is the same (%s)', async (live, key) => {
    mocks.inspectBackup.mockResolvedValue(
      inspectResult({ identity_relation: 'same', live_radio_public_key: live })
    );
    render(<BackupRestoreFlow />);
    pickFile(V2);
    expect(await screen.findByTestId('identity-relation')).toHaveTextContent(i18n.t(key));
  });

  it('asks no identity confirmation when the identity step is unchecked', async () => {
    mocks.inspectBackup.mockResolvedValue(
      inspectResult({
        identity_relation: 'conflict',
        identity_refusal: 'backup_identity_conflict',
        identity_requires: { replace_live_identity: true, wipe_history: true },
      })
    );
    render(<BackupRestoreFlow />);
    pickFile(V2);
    fireEvent.click(await screen.findByLabelText(i18n.t('settings.fullRestore.steps.identity')));
    fireEvent.click(checkbox('settings.fullRestore.confirm'));
    expect(screen.queryByLabelText(i18n.t('settings.fullRestore.identityWipeConfirm'))).toBeNull();
    const restore = screen.getByRole('button', { name: i18n.t('settings.fullRestore.restore') });
    expect(restore).toBeEnabled();
    fireEvent.click(restore);
    await waitFor(() => expect(mocks.startFullRestore).toHaveBeenCalled());
    expect(mocks.startFullRestore.mock.calls[0][0]).toMatchObject({
      steps: expect.objectContaining({ identity: false }),
      replace_live_identity: false,
      wipe_history: false,
    });
  });

  it.each(['needs_radio', 'conflict'] as const)(
    'cannot restore when the identity check refuses (%s)',
    async (relation) => {
      mocks.inspectBackup.mockResolvedValue(
        inspectResult({
          identity_relation: relation,
          identity_refusal: `backup_identity_${relation}`,
        })
      );
      render(<BackupRestoreFlow />);
      pickFile(V2);
      expect(
        await screen.findByText(i18n.t(`settings.fullRestore.identity.${relation}`))
      ).toBeInTheDocument();
      fireEvent.click(checkbox('settings.fullRestore.confirm'));
      expect(
        screen.getByRole('button', { name: i18n.t('settings.fullRestore.restore') })
      ).toBeDisabled();
    }
  );

  it('defaults Wi-Fi off on TCP with another SSID, and the PIN off on BLE', async () => {
    mocks.inspectBackup.mockResolvedValue(
      inspectResult({ wifi_ssid_differs_on_tcp: true, ble_pin_differs_on_ble: true })
    );
    render(<BackupRestoreFlow />);
    pickFile(V2);
    expect(await screen.findByText(i18n.t('settings.fullRestore.wifiTcpWarning'))).toBeVisible();
    expect(screen.getByText(i18n.t('settings.fullRestore.blePinWarning'))).toBeVisible();
    expect(checkbox('settings.fullRestore.steps.wifi').checked).toBe(false);
    expect(checkbox('settings.fullRestore.steps.ble_pin').checked).toBe(false);
    expect(checkbox('settings.fullRestore.steps.radio_settings').checked).toBe(true);
  });

  it('defaults Wi-Fi off on TCP when the file turns Wi-Fi off, and names a flag-only block', async () => {
    mocks.inspectBackup.mockResolvedValue(
      inspectResult({ has_wifi_credentials: false, wifi_off_on_tcp: true })
    );
    render(<BackupRestoreFlow />);
    pickFile(V2);
    expect(await screen.findByText(i18n.t('settings.fullRestore.wifiOffTcpWarning'))).toBeVisible();
    expect(screen.getByText(i18n.t('settings.fullRestore.hasWifiFlagOnly'))).toBeVisible();
    expect(screen.queryByText(i18n.t('settings.fullRestore.hasWifi'))).toBeNull();
    expect(checkbox('settings.fullRestore.steps.wifi').checked).toBe(false);
  });

  it('disables channels and contacts while the Meshloom step is off', async () => {
    render(<BackupRestoreFlow />);
    pickFile(V2);
    const meshloom = await screen.findByLabelText(i18n.t('settings.fullRestore.steps.meshloom'));
    fireEvent.click(meshloom);
    expect(checkbox('settings.fullRestore.steps.channels')).toBeDisabled();
    expect(checkbox('settings.fullRestore.steps.channels').checked).toBe(false);
    expect(checkbox('settings.fullRestore.steps.contacts')).toBeDisabled();
    expect(screen.getByText(i18n.t('settings.fullRestore.meshloomStepRequired'))).toBeVisible();
  });

  it('lists the bots that come back disabled', async () => {
    mocks.inspectBackup.mockResolvedValue(inspectResult({ bot_names: ['Greeter', 'Echo'] }));
    render(<BackupRestoreFlow />);
    pickFile(V2);
    expect(
      await screen.findByText(
        i18n.t('settings.fullRestore.botsRestoredDisabled', { names: 'Greeter, Echo' })
      )
    ).toBeVisible();
  });

  it('polls the job, then offers the reboot', async () => {
    mocks.getRestoreJob
      .mockResolvedValueOnce(job({ state: 'running', result: null }))
      .mockResolvedValue(job());
    render(<BackupRestoreFlow />);
    pickFile(V2);
    fireEvent.click(await screen.findByLabelText(i18n.t('settings.fullRestore.confirm')));
    fireEvent.click(screen.getByRole('button', { name: i18n.t('settings.fullRestore.restore') }));
    const reboot = await screen.findByRole(
      'button',
      { name: i18n.t('settings.fullRestore.rebootNow') },
      { timeout: 3000 }
    );
    expect(mocks.getRestoreJob.mock.calls.length).toBeGreaterThanOrEqual(2);
    expect(screen.getByText(i18n.t('settings.fullRestore.done'))).toBeVisible();
    fireEvent.click(reboot);
    await waitFor(() => expect(mocks.rebootRadio).toHaveBeenCalled());
  });

  it('re-runs inspect when a 409 says the identity decision changed', async () => {
    mocks.startFullRestore.mockRejectedValue(
      new ApiError('wipe', 409, { code: 'backup_identity_wipe_required' })
    );
    render(<BackupRestoreFlow />);
    pickFile(V2);
    fireEvent.click(await screen.findByLabelText(i18n.t('settings.fullRestore.confirm')));
    fireEvent.click(screen.getByRole('button', { name: i18n.t('settings.fullRestore.restore') }));
    expect(
      await screen.findByText(i18n.t('settings.fullRestore.identityRecheck'))
    ).toBeInTheDocument();
    await waitFor(() => expect(mocks.inspectBackup).toHaveBeenCalledTimes(2));
  });

  it('re-runs inspect when the job stops on an identity code', async () => {
    mocks.getRestoreJob.mockResolvedValue(
      job({
        state: 'failed',
        steps: [
          {
            step: 'identity',
            status: 'failed',
            code: 'backup_identity_wipe_required',
            detail: null,
            items: [],
          },
        ],
        result: null,
      })
    );
    render(<BackupRestoreFlow />);
    pickFile(V2);
    fireEvent.click(await screen.findByLabelText(i18n.t('settings.fullRestore.confirm')));
    fireEvent.click(screen.getByRole('button', { name: i18n.t('settings.fullRestore.restore') }));
    await waitFor(() => expect(mocks.inspectBackup).toHaveBeenCalledTimes(2));
    expect(screen.getByText(i18n.t('settings.fullRestore.identityRecheck'))).toBeInTheDocument();
  });

  it('shows the latest job after a reload', async () => {
    mocks.getLatestRestoreJob.mockResolvedValue(
      job({
        steps: [
          {
            step: 'radio_settings',
            status: 'skipped',
            code: 'radio_not_connected',
            detail: null,
            items: [],
          },
        ],
        result: null,
      })
    );
    await act(async () => {
      render(<BackupRestoreFlow resumeLatest />);
    });
    expect(await screen.findByText(i18n.t('settings.fullRestore.rerunHint'))).toBeVisible();
    expect(
      screen.getByText(
        `${i18n.t('settings.fullRestore.status.skipped')} (${i18n.t(
          'settings.fullRestore.codes.radio_not_connected'
        )})`
      )
    ).toBeVisible();
  });

  it('runs onFinished once for a job that finished while no view was open', async () => {
    mocks.getLatestRestoreJob.mockResolvedValue(job({ job_id: 'job-ended-unseen' }));
    const onFinished = vi.fn();
    let view!: ReturnType<typeof render>;
    await act(async () => {
      view = render(<BackupRestoreFlow resumeLatest onFinished={onFinished} />);
    });
    await waitFor(() => expect(onFinished).toHaveBeenCalledTimes(1));
    expect(onFinished.mock.calls[0][0].job_id).toBe('job-ended-unseen');
    view.unmount();

    // Opened again: already handled.
    await act(async () => {
      render(<BackupRestoreFlow resumeLatest onFinished={onFinished} />);
    });
    await screen.findByText(i18n.t('settings.fullRestore.done'));
    expect(onFinished).toHaveBeenCalledTimes(1);
  });

  it('reports while a job runs', async () => {
    mocks.getRestoreJob.mockResolvedValueOnce(job({ job_id: 'job-1', state: 'running' }));
    mocks.getRestoreJob.mockResolvedValue(job());
    const onRunningChange = vi.fn();
    render(<BackupRestoreFlow onRunningChange={onRunningChange} />);
    pickFile(V2);
    fireEvent.click(await screen.findByLabelText(i18n.t('settings.fullRestore.confirm')));
    fireEvent.click(screen.getByRole('button', { name: i18n.t('settings.fullRestore.restore') }));
    await waitFor(() => expect(onRunningChange).toHaveBeenLastCalledWith(true));
    await waitFor(() => expect(onRunningChange).toHaveBeenLastCalledWith(false), {
      timeout: 3000,
    });
  });

  async function runJob(finished: BackupRestoreJob) {
    mocks.getRestoreJob.mockResolvedValue(finished);
    render(<BackupRestoreFlow />);
    pickFile(V2);
    fireEvent.click(await screen.findByLabelText(i18n.t('settings.fullRestore.confirm')));
    fireEvent.click(screen.getByRole('button', { name: i18n.t('settings.fullRestore.restore') }));
    await screen.findByText(
      i18n.t(
        finished.state === 'done' ? 'settings.fullRestore.done' : 'settings.fullRestore.failed'
      )
    );
  }

  it('shows the finished job warnings, translated, and unknown ones as is', async () => {
    const finished = job();
    finished.result!.warnings = ['bots_disabled:Greeter', 'push_kept_existing', 'brand_new:x'];
    await runJob(finished);
    expect(
      screen.getByText(i18n.t('settings.fullRestore.warnings.bots_disabled', { value: 'Greeter' }))
    ).toBeVisible();
    expect(
      screen.getByText(i18n.t('settings.fullRestore.warnings.push_kept_existing'))
    ).toBeVisible();
    expect(screen.getByText('brand_new:x')).toBeVisible();
  });

  it('shows a full contact table under a done contacts step', async () => {
    const finished = job();
    finished.steps[4] = {
      step: 'contacts',
      status: 'done',
      code: null,
      detail: null,
      items: [{ name: 'table_full:3', status: 'failed', code: 'contacts_table_full' }],
    };
    await runJob(finished);
    const item = document.querySelector('[data-item="table_full:3"]');
    expect(item).toHaveTextContent(i18n.t('settings.fullRestore.items.table_full', { value: '3' }));
    expect(item).toHaveTextContent(i18n.t('settings.fullRestore.codes.contacts_table_full'));
  });

  it('names the radio setting group that failed', async () => {
    const finished = job();
    finished.steps[2] = {
      step: 'radio_settings',
      status: 'failed',
      code: null,
      detail: null,
      items: [
        { name: 'name', status: 'done', code: null },
        { name: 'radio_params', status: 'failed', code: 'radio_params_rejected' },
      ],
    };
    await runJob(finished);
    expect(document.querySelector('[data-item="name"]')).toBeNull(); // done items stay quiet
    const item = document.querySelector('[data-item="radio_params"]');
    expect(item).toHaveTextContent(i18n.t('settings.fullRestore.items.radio_params'));
    expect(item).toHaveTextContent(i18n.t('settings.fullRestore.codes.radio_params_rejected'));
  });

  it('names the Wi-Fi part that failed after the credentials were stored', async () => {
    const finished = job({ state: 'done' });
    finished.steps[5] = {
      step: 'wifi',
      status: 'failed',
      code: null,
      detail: null,
      items: [
        { name: 'wifi_credentials', status: 'done', code: null },
        { name: 'wifi_enabled', status: 'failed', code: 'cli_unexpected_reply' },
      ],
    };
    await runJob(finished);
    expect(document.querySelector('[data-item="wifi_credentials"]')).toBeNull();
    const item = document.querySelector('[data-item="wifi_enabled"]');
    expect(item).toHaveTextContent(i18n.t('settings.fullRestore.items.wifi_enabled'));
  });

  it('translates every code and warning the backend can report', () => {
    const codes = [
      // step codes
      'radio_not_connected',
      'radio_rebooting',
      'meshloom_step_off',
      'step_off',
      'not_in_backup',
      'v1_backup',
      'no_identity',
      'identity_failed',
      'identity_gate_open',
      'device_info_unknown',
      'wifi_unsupported',
      'already_this_identity',
      'restored',
      'bound',
      'radio_busy',
      'internal_error',
      'radio_contacts_unreadable',
      'live_identity_changed',
      'private_key_import_disabled',
      'private_key_import_rejected',
      'private_key_import_unverified',
      'backup_identity_wipe_required',
      'backup_identity_replaces_live',
      'backup_identity_needs_radio',
      'backup_identity_conflict',
      // CLI codes on the Wi-Fi step
      'cli_unexpected_reply',
      'cli_timeout',
      'cli_unknown_command',
      'cli_rejected',
      'setup_incomplete',
      'wifi_store_mismatch',
      'wifi_store_partial',
      'wifi_store_unverified',
      'proxy_link',
      'firmware_unknown',
      'firmware_too_old',
      'link_unreliable',
      // item codes
      'contacts_table_full',
      'cli_unsupported',
      'path_hash_mode_unsupported',
      'name_rejected',
      'coords_rejected',
      'tx_power_rejected',
      'radio_params_rejected',
      'path_hash_mode_rejected',
      'client_repeat_not_allowed',
      'other_params_rejected',
      'tuning_rejected',
      'custom_var_rejected',
      'default_flood_scope_rejected',
      'autoadd_rejected',
      'ble_pin_invalid',
      'ble_pin_rejected',
      'cli_pref_rejected',
    ];
    const warnings = [
      'fanout_reserved:system:x',
      'bots_disabled:B',
      'fanout_invalid:id:webhook',
      'bots_restored_disabled:B',
      'push_kept_existing',
      'proxy_port_managed',
      'proxy_targets_transport',
      'tx_power_clamped',
      'client_repeat_not_allowed',
      'flood_scope_not_applied',
      'channel_key_invalid',
      'channel_rejected',
      'channel_clear_failed',
      'channels_over_radio_limit',
      'radio_contacts_unreadable',
      'contact_load_failed',
    ];
    for (const language of ['en', 'fr']) {
      const t = i18n.getFixedT(language);
      for (const code of codes) expect(restoreCodeText(code, t), code).not.toBe(code);
      for (const warning of warnings) {
        expect(restoreWarningText(warning, t), warning).not.toBe(warning);
      }
    }
  });

  it('stops polling a job the server no longer knows and offers a way out', async () => {
    mocks.getRestoreJob.mockRejectedValue(
      new ApiError('gone', 404, { code: 'backup_restore_job_not_found' })
    );
    render(<BackupRestoreFlow />);
    pickFile(V2);
    fireEvent.click(await screen.findByLabelText(i18n.t('settings.fullRestore.confirm')));
    fireEvent.click(screen.getByRole('button', { name: i18n.t('settings.fullRestore.restore') }));
    expect(await screen.findByText(i18n.t('settings.fullRestore.jobLost'))).toBeVisible();
    const calls = mocks.getRestoreJob.mock.calls.length;
    await new Promise((resolve) => setTimeout(resolve, 1200));
    expect(mocks.getRestoreJob.mock.calls.length).toBe(calls);
    fireEvent.click(screen.getByRole('button', { name: i18n.t('settings.fullRestore.another') }));
    expect(screen.getByLabelText(i18n.t('settings.fullRestore.choose'))).toBeInTheDocument();
  });

  it('says bots are skipped when they are turned off on this server', async () => {
    mocks.inspectBackup.mockResolvedValue(
      inspectResult({ bot_names: ['Greeter'], bots_will_be_skipped: true })
    );
    render(<BackupRestoreFlow />);
    pickFile(V2);
    expect(
      await screen.findByText(
        i18n.t('settings.fullRestore.botsWillBeSkipped', { names: 'Greeter' })
      )
    ).toBeVisible();
    expect(
      screen.queryByText(i18n.t('settings.fullRestore.botsRestoredDisabled', { names: 'Greeter' }))
    ).toBeNull();
  });

  it('names the integrations, the proxy and the file warnings before restoring', async () => {
    mocks.inspectBackup.mockResolvedValue(
      inspectResult({
        integrations: [{ name: 'Hook', type: 'webhook', enabled: true }],
        radio_proxy_enabled: true,
        warnings: ['radio_read_failed:tuning', 'radio_read_failed:autoadd'],
      })
    );
    render(<BackupRestoreFlow />);
    pickFile(V2);
    const on = i18n.t('settings.fullRestore.integrationOn', { name: 'Hook', type: 'webhook' });
    expect(
      await screen.findByText(i18n.t('settings.fullRestore.integrations', { list: on }))
    ).toBeVisible();
    expect(screen.getByText(i18n.t('settings.fullRestore.proxyEnabled'))).toBeVisible();
    expect(
      screen.getAllByText(i18n.t('settings.fullRestore.fileWarnings.radio_read_failed'))
    ).toHaveLength(1);
  });

  it('does not report a job that had already ended when the view opened', async () => {
    const onFinished = vi.fn();
    mocks.getLatestRestoreJob.mockResolvedValue(job());
    await act(async () => {
      render(<BackupRestoreFlow resumeLatest onFinished={onFinished} />);
    });
    expect(await screen.findByText(i18n.t('settings.fullRestore.done'))).toBeVisible();
    expect(onFinished).not.toHaveBeenCalled();
  });

  it('reports a job that finishes while the view is open', async () => {
    const onFinished = vi.fn();
    render(<BackupRestoreFlow onFinished={onFinished} />);
    pickFile(V2);
    fireEvent.click(await screen.findByLabelText(i18n.t('settings.fullRestore.confirm')));
    fireEvent.click(screen.getByRole('button', { name: i18n.t('settings.fullRestore.restore') }));
    await waitFor(() => expect(onFinished).toHaveBeenCalledTimes(1));
  });
});
