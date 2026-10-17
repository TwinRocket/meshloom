import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { beforeEach, describe, expect, it, vi } from 'vitest';

const mocks = vi.hoisted(() => ({
  adoptRadioIdentity: vi.fn(),
  rejectRadioIdentity: vi.fn(),
  inspectBackup: vi.fn(),
  getLatestRestoreJob: vi.fn(),
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
  },
  formatApiError: (err: unknown) => (err instanceof Error ? err.message : String(err)),
}));

vi.mock('../components/ui/sonner', () => ({
  toast: mocks.toast,
}));

import { RadioIdentityModal } from '../components/RadioIdentityModal';
import i18n from '../i18n';
import type { HealthStatus, RadioIdentityInfo } from '../types';

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
});
