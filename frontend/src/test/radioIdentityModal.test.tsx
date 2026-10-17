import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { beforeEach, describe, expect, it, vi } from 'vitest';

const mocks = vi.hoisted(() => ({
  adoptRadioIdentity: vi.fn(),
  rejectRadioIdentity: vi.fn(),
  inspectBackup: vi.fn(),
  getLatestRestoreJob: vi.fn(),
  startFullRestore: vi.fn(),
  getRestoreJob: vi.fn(),
  toast: {
    success: vi.fn(),
    error: vi.fn(),
    info: vi.fn(),
  },
}));

vi.mock('../api', async (importOriginal) => ({
  ...(await importOriginal<typeof import('../api')>()),
  api: {
    adoptRadioIdentity: mocks.adoptRadioIdentity,
    rejectRadioIdentity: mocks.rejectRadioIdentity,
    inspectBackup: mocks.inspectBackup,
    getLatestRestoreJob: mocks.getLatestRestoreJob,
    startFullRestore: mocks.startFullRestore,
    getRestoreJob: mocks.getRestoreJob,
  },
  formatApiError: (err: unknown) => (err instanceof Error ? err.message : String(err)),
}));

vi.mock('../components/ui/sonner', () => ({
  toast: mocks.toast,
}));

import { RadioIdentityModal } from '../components/RadioIdentityModal';
import i18n from '../i18n';
import type {
  BackupInspectResult,
  BackupRestoreJob,
  HealthStatus,
  RadioIdentityInfo,
} from '../types';

const identity: RadioIdentityInfo = {
  previous_public_key: 'aabbccddeeff00112233445566778899',
  new_public_key: '11223344556677889900aabbccddeeff',
  new_name: 'TrailRadio',
  mesh_contacts: 4,
  mesh_messages: 18,
  last_activity: 1700000000,
};

const baseHealth: HealthStatus = {
  status: 'degraded',
  radio_connected: false,
  radio_initializing: false,
  radio_state: 'identity_mismatch',
  connection_info: null,
  transport_configured: true,
  identity,
  database_size_mb: 1.2,
  oldest_undecrypted_timestamp: null,
  fanout_statuses: {},
  bots_disabled: false,
};

describe('RadioIdentityModal', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mocks.adoptRadioIdentity.mockResolvedValue({
      status: 'ok',
      radio_state: 'connected',
      bound_public_key: identity.new_public_key,
      connected: true,
    });
    mocks.rejectRadioIdentity.mockResolvedValue({
      status: 'ok',
      radio_state: 'paused',
      bound_public_key: identity.previous_public_key,
      connected: false,
    });
  });

  it('does not render when radio identity is healthy', () => {
    render(
      <RadioIdentityModal
        health={{ ...baseHealth, radio_state: 'connected', identity: null }}
        onAdopted={vi.fn()}
      />
    );

    expect(screen.queryByText(i18n.t('radioIdentity.mismatchTitle'))).not.toBeInTheDocument();
  });

  it('says the radio is rebooting when setup reset its clock after the adopt', async () => {
    const user = userEvent.setup();
    const onAdopted = vi.fn();
    mocks.adoptRadioIdentity.mockResolvedValue({
      status: 'ok',
      radio_state: 'rebooting',
      bound_public_key: identity.new_public_key,
      connected: false,
    });

    render(
      <RadioIdentityModal
        health={{ ...baseHealth, radio_state: 'identity_unbound_legacy' }}
        onAdopted={onAdopted}
      />
    );

    await user.click(screen.getByRole('button', { name: i18n.t('radioIdentity.bindWithoutWipe') }));
    await waitFor(() => {
      // Same id as the health-driven toast, so the two never stack.
      expect(mocks.toast.info).toHaveBeenCalledWith(i18n.t('toast.radioRebooting'), {
        id: 'radio-rebooting',
        description: i18n.t('toast.radioRebootingDetail'),
      });
      expect(onAdopted).toHaveBeenCalledTimes(1);
    });
    expect(mocks.toast.error).not.toHaveBeenCalled();
  });

  it('blocks mismatch until the operator rejects or wipes', async () => {
    const user = userEvent.setup();
    const onAdopted = vi.fn();
    const onResolved = vi.fn();

    render(
      <RadioIdentityModal health={baseHealth} onAdopted={onAdopted} onResolved={onResolved} />
    );

    expect(screen.getByText(i18n.t('radioIdentity.mismatchTitle'))).toBeInTheDocument();
    expect(screen.getByText('aabbccddeeff')).toBeInTheDocument();
    expect(screen.getByText('112233445566')).toBeInTheDocument();
    expect(
      screen.getByText(i18n.t('radioIdentity.meshContacts', { count: 4 }))
    ).toBeInTheDocument();
    expect(
      screen.getByText(i18n.t('radioIdentity.meshMessages', { count: 18 }))
    ).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /close/i })).not.toBeInTheDocument();

    await user.click(screen.getByRole('button', { name: i18n.t('radioIdentity.reject') }));
    await waitFor(() => {
      expect(mocks.rejectRadioIdentity).toHaveBeenCalledTimes(1);
      expect(onResolved).toHaveBeenCalledTimes(1);
    });
    expect(mocks.adoptRadioIdentity).not.toHaveBeenCalled();
    expect(onAdopted).not.toHaveBeenCalled();

    await user.click(screen.getByRole('button', { name: i18n.t('radioIdentity.wipeContinue') }));
    await waitFor(() => {
      expect(mocks.adoptRadioIdentity).toHaveBeenCalledWith({ confirm_wipe: true });
      expect(onAdopted).toHaveBeenCalledTimes(1);
    });
  });

  it('binds a historical radio without wipe, or adopts a new radio with wipe', async () => {
    const user = userEvent.setup();
    const onAdopted = vi.fn();

    render(
      <RadioIdentityModal
        health={{ ...baseHealth, radio_state: 'identity_unbound_legacy' }}
        onAdopted={onAdopted}
      />
    );

    expect(screen.getByText(i18n.t('radioIdentity.unboundTitle'))).toBeInTheDocument();
    expect(
      screen.queryByRole('button', { name: i18n.t('radioIdentity.reject') })
    ).not.toBeInTheDocument();

    await user.click(screen.getByRole('button', { name: i18n.t('radioIdentity.bindWithoutWipe') }));
    await waitFor(() => {
      expect(mocks.adoptRadioIdentity).toHaveBeenCalledWith({ confirm_wipe: false });
      expect(onAdopted).toHaveBeenCalledTimes(1);
    });

    await user.click(screen.getByRole('button', { name: i18n.t('radioIdentity.newRadio') }));
    await waitFor(() => {
      expect(mocks.adoptRadioIdentity).toHaveBeenCalledWith({ confirm_wipe: true });
    });
  });

  it.each(['identity_mismatch', 'identity_unbound_legacy'] as const)(
    'offers to restore from a backup (%s)',
    async (state) => {
      const user = userEvent.setup();
      const onResolved = vi.fn();
      const { rerender } = render(
        <RadioIdentityModal
          health={{ ...baseHealth, radio_state: state }}
          onAdopted={vi.fn()}
          onResolved={onResolved}
        />
      );
      await user.click(
        screen.getByRole('button', { name: i18n.t('radioIdentity.restoreFromBackup') })
      );
      expect(screen.getByLabelText(i18n.t('settings.fullRestore.choose'))).toBeInTheDocument();
      expect(mocks.adoptRadioIdentity).not.toHaveBeenCalled();

      // The restore closes the gate: the window stays until the user closes it.
      rerender(
        <RadioIdentityModal
          health={{ ...baseHealth, radio_state: 'connected', identity: null }}
          onAdopted={vi.fn()}
          onResolved={onResolved}
        />
      );
      expect(screen.getByLabelText(i18n.t('settings.fullRestore.choose'))).toBeInTheDocument();
      await user.click(screen.getByRole('button', { name: i18n.t('settings.fullRestore.close') }));
      await waitFor(() => expect(onResolved).toHaveBeenCalled());
      expect(screen.queryByLabelText(i18n.t('settings.fullRestore.choose'))).toBeNull();
    }
  );

  it('keeps the window open while the restore runs, then reloads even after a late failure', async () => {
    const inspected: BackupInspectResult = {
      format: 'meshloom-backup-v2',
      encrypted: false,
      exported_at: 1,
      meshloom_version: null,
      public_key: 'aa'.repeat(32),
      radio_name: 'Base',
      has_private_key: true,
      private_key_status: 'included',
      has_radio_settings: false,
      has_wifi: false,
      counts: { contacts: 0, channels: 0, groups: 0, fanout_configs: 0, push_subscriptions: 0 },
      bot_names: [],
      bots_will_be_skipped: false,
      integrations: [],
      radio_proxy_enabled: null,
      identity_relation: 'same',
      identity_requires: { replace_live_identity: false, wipe_history: false },
      identity_refusal: null,
      live_radio_public_key: null,
      live_radio_name: null,
      ble_pin_differs_on_ble: false,
      wifi_ssid_differs_on_tcp: false,
      warnings: [],
    };
    const failedLate: BackupRestoreJob = {
      job_id: 'job-window',
      state: 'failed',
      started_at: 1,
      finished_at: 2,
      steps: [
        { step: 'identity', status: 'done', code: 'bound', detail: null, items: [] },
        { step: 'meshloom', status: 'failed', code: 'internal_error', detail: null, items: [] },
      ],
      result: null,
    };
    let finish!: (job: BackupRestoreJob) => void;
    mocks.inspectBackup.mockResolvedValue(inspected);
    mocks.startFullRestore.mockResolvedValue({ job_id: 'job-window' });
    mocks.getRestoreJob.mockReturnValue(
      new Promise<BackupRestoreJob>((resolve) => {
        finish = resolve;
      })
    );
    const onAdopted = vi.fn();
    const user = userEvent.setup();
    render(<RadioIdentityModal health={baseHealth} onAdopted={onAdopted} />);
    await user.click(
      screen.getByRole('button', { name: i18n.t('radioIdentity.restoreFromBackup') })
    );
    const text = JSON.stringify({ format: 'meshloom-backup-v2', exported_at: 1 });
    const file = new File([text], 'backup.json', { type: 'application/json' });
    Object.defineProperty(file, 'text', { value: () => Promise.resolve(text) });
    fireEvent.change(screen.getByLabelText(i18n.t('settings.fullRestore.choose')), {
      target: { files: [file] },
    });
    await user.click(await screen.findByLabelText(i18n.t('settings.fullRestore.confirm')));
    await user.click(screen.getByRole('button', { name: i18n.t('settings.fullRestore.restore') }));

    const close = () => {
      const buttons = screen.getAllByRole('button', {
        name: i18n.t('settings.fullRestore.close'),
      });
      return buttons[buttons.length - 1] as HTMLButtonElement;
    };
    await waitFor(() => expect(close()).toBeDisabled());

    finish(failedLate);
    await waitFor(() => expect(onAdopted).toHaveBeenCalledTimes(1));
    await waitFor(() => expect(close()).toBeEnabled());
  });
});
