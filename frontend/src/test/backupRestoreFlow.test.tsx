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
import { BackupRestoreFlow } from '../components/settings/BackupRestoreFlow';
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
});
