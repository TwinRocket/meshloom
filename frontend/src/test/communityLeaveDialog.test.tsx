import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

const toastMocks = vi.hoisted(() => ({ error: vi.fn(), success: vi.fn() }));
vi.mock('../components/ui/sonner', () => ({ toast: toastMocks }));

import { SettingsCommunitySection } from '../components/settings/SettingsCommunitySection';
import { api, ApiError } from '../api';
import i18n from '../i18n';
import { getCommunitySnapshot, resetCommunityStoreForTests } from '../stores/communityStore';
import type { CommunityStatus } from '../types';
import { DEFAULT_LOCALE } from '../utils/languagePreference';

const onStatus: CommunityStatus = {
  enabled: true,
  iata: 'CDG',
  publisher_configured: true,
  publisher_connected: true,
  state: 'active',
};
const offStatus: CommunityStatus = {
  ...onStatus,
  enabled: false,
  publisher_configured: false,
  publisher_connected: false,
  state: 'off',
};
const pendingStatus: CommunityStatus = {
  ...offStatus,
  opt_out_pending: { requested_at: '2026-10-14T10:00:00+00:00', reason: 'privacy' },
};

async function openExitDialog(): Promise<HTMLElement> {
  fireEvent.click(await screen.findByRole('button', { name: i18n.t('settings.community.leave') }));
  return screen.findByTestId('community-leave-dialog');
}

describe('Community exit dialog and unconfirmed opt-out', () => {
  beforeEach(() => {
    resetCommunityStoreForTests();
    vi.spyOn(api, 'getCommunity').mockResolvedValue(onStatus);
    vi.spyOn(api, 'updateCommunity').mockResolvedValue(offStatus);
    vi.spyOn(api, 'retryCommunityOptOut').mockResolvedValue(offStatus);
    vi.spyOn(api, 'getCommunityStats').mockResolvedValue({
      observers_online: 1,
      iata_active: 1,
      unique_hashes_24h: 1,
    });
    vi.spyOn(api, 'getCommunityMeStats').mockResolvedValue({
      unique_hashes_24h: 1,
      unique_hashes_7d: 1,
      iata: 'CDG',
      concordance: 'ok',
      rank_in_iata: 1,
    });
  });

  afterEach(() => {
    vi.restoreAllMocks();
    toastMocks.error.mockClear();
    toastMocks.success.mockClear();
    void i18n.changeLanguage(DEFAULT_LOCALE);
  });

  it('says what Community keeps and deletes, and where to ask for a full deletion', async () => {
    render(<SettingsCommunitySection />);
    const dialog = await openExitDialog();
    expect(dialog).toHaveTextContent(i18n.t('settings.community.optOut.kept'));
    expect(dialog).toHaveTextContent(i18n.t('settings.community.optOut.position'));
    expect(dialog).toHaveTextContent(i18n.t('settings.community.optOut.advert'));
    const mail = within(dialog).getByRole('link', { name: 'privacy@meshloom.app' });
    expect(mail).toHaveAttribute('href', 'mailto:privacy@meshloom.app');
    expect(dialog.textContent ?? '').not.toMatch(/sealed|scell/i);
    expect(api.updateCommunity).not.toHaveBeenCalled();
  });

  it('cancel sends nothing', async () => {
    render(<SettingsCommunitySection />);
    const dialog = await openExitDialog();
    fireEvent.click(
      within(dialog).getByRole('button', { name: i18n.t('settings.community.optOut.cancel') })
    );
    await waitFor(() =>
      expect(screen.queryByTestId('community-leave-dialog')).not.toBeInTheDocument()
    );
    expect(api.updateCommunity).not.toHaveBeenCalled();
  });

  it('sends the optional reason with the opt-out', async () => {
    render(<SettingsCommunitySection />);
    const dialog = await openExitDialog();
    fireEvent.click(
      within(dialog).getByRole('radio', {
        name: i18n.t('settings.community.optOut.reasons.not_useful'),
      })
    );
    fireEvent.click(
      within(dialog).getByRole('button', { name: i18n.t('settings.community.optOut.confirm') })
    );
    await waitFor(() =>
      expect(api.updateCommunity).toHaveBeenCalledWith({
        enabled: false,
        opt_out_reason: 'not_useful',
      })
    );
    expect(toastMocks.success).toHaveBeenCalledWith(i18n.t('settings.community.left'));
  });

  it('an unconfirmed opt-out shows "Deletion not confirmed" and retries on demand', async () => {
    vi.mocked(api.updateCommunity).mockResolvedValue(pendingStatus);
    render(<SettingsCommunitySection />);
    const dialog = await openExitDialog();
    fireEvent.click(
      within(dialog).getByRole('button', { name: i18n.t('settings.community.optOut.confirm') })
    );
    await waitFor(() =>
      expect(toastMocks.error).toHaveBeenCalledWith(
        i18n.t('settings.community.optOut.notConfirmedToast')
      )
    );
    const pending = await screen.findByTestId('community-opt-out-pending');
    expect(pending).toHaveTextContent(i18n.t('settings.community.optOut.pendingTitle'));
    expect(api.retryCommunityOptOut).not.toHaveBeenCalled();

    fireEvent.click(
      within(pending).getByRole('button', { name: i18n.t('settings.community.optOut.retry') })
    );
    await waitFor(() => expect(api.retryCommunityOptOut).toHaveBeenCalledTimes(1));
    await waitFor(() =>
      expect(screen.queryByTestId('community-opt-out-pending')).not.toBeInTheDocument()
    );
    expect(toastMocks.success).toHaveBeenCalledWith(i18n.t('settings.community.optOut.retryDone'));
    expect(getCommunitySnapshot().status?.opt_out_pending).toBeFalsy();
  });

  it('a failed retry keeps the notice and says why', async () => {
    vi.mocked(api.getCommunity).mockResolvedValue(pendingStatus);
    vi.mocked(api.retryCommunityOptOut).mockRejectedValue(
      new ApiError('conflict', 409, { code: 'opt_out_other_radio', message: 'x' })
    );
    render(<SettingsCommunitySection />);
    const pending = await screen.findByTestId('community-opt-out-pending');
    fireEvent.click(
      within(pending).getByRole('button', { name: i18n.t('settings.community.optOut.retry') })
    );
    await waitFor(() =>
      expect(toastMocks.error).toHaveBeenCalledWith(i18n.t('errors.opt_out_other_radio'))
    );
    expect(screen.getByTestId('community-opt-out-pending')).toBeInTheDocument();
  });

  it('speaks French with "vous"', async () => {
    await i18n.changeLanguage('fr');
    vi.mocked(api.getCommunity).mockResolvedValue(pendingStatus);
    render(<SettingsCommunitySection />);
    expect(await screen.findByText('Suppression non confirmée')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Réessayer' })).toBeInTheDocument();
  });
});
