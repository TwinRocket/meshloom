import { act, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

const toastMocks = vi.hoisted(() => ({ error: vi.fn(), success: vi.fn() }));
vi.mock('../components/ui/sonner', () => ({ toast: toastMocks }));

import { SettingsCommunitySection } from '../components/settings/SettingsCommunitySection';
import { api, ApiError } from '../api';
import i18n from '../i18n';
import { resetCommunityStoreForTests, setCommunityStatus } from '../stores/communityStore';
import type { CommunityStatus } from '../types';
import { DEFAULT_LOCALE } from '../utils/languagePreference';

const offStatus: CommunityStatus = {
  enabled: false,
  iata: '',
  publisher_configured: false,
  publisher_connected: false,
};

describe('SettingsCommunitySection', () => {
  beforeEach(() => {
    resetCommunityStoreForTests();
    vi.spyOn(api, 'getCommunity').mockResolvedValue(offStatus);
    vi.spyOn(api, 'updateCommunity').mockResolvedValue({
      ...offStatus,
      enabled: true,
      iata: 'CDG',
      publisher_configured: true,
    });
    vi.spyOn(api, 'bindCommunityIata').mockResolvedValue({
      iata: 'CDG',
      concordance: 'ok',
      honored_for_buckets: false,
      distance_km: 12.4,
    });
    vi.spyOn(api, 'overrideCommunityIata').mockResolvedValue({
      iata: 'CDG',
      concordance: 'ok',
      honored_for_buckets: true,
      distance_km: 12.4,
    });
    vi.spyOn(api, 'getCommunityMeStats').mockResolvedValue({
      unique_hashes_24h: 11,
      unique_hashes_7d: 40,
      iata: 'CDG',
      concordance: 'warn',
      rank_in_iata: 3,
    });
    vi.spyOn(api, 'getCommunityStats').mockResolvedValue({
      observers_online: 8,
      iata_active: 4,
      unique_hashes_24h: 120,
    });
  });

  afterEach(() => {
    vi.restoreAllMocks();
    toastMocks.error.mockClear();
    toastMocks.success.mockClear();
    void i18n.changeLanguage(DEFAULT_LOCALE);
  });

  it('waiting for a position: offers Leave, says why, asks no stats', async () => {
    vi.mocked(api.getCommunity).mockResolvedValue({
      ...offStatus,
      enabled: true,
      iata: 'CDG',
      publisher_configured: true,
      state: 'waiting_for_position',
    });
    render(<SettingsCommunitySection />);
    expect(
      await screen.findByText(i18n.t('settings.community.waiting.settingsNote'))
    ).toBeInTheDocument();
    expect(
      screen.getByRole('button', { name: i18n.t('settings.community.leave') })
    ).toBeInTheDocument();
    expect(screen.queryByText(i18n.t('settings.community.meTitle'))).not.toBeInTheDocument();
    expect(api.getCommunityStats).not.toHaveBeenCalled();
    expect(api.getCommunityMeStats).not.toHaveBeenCalled();
  });

  it('follows an opt-out made in another tab without a reload', async () => {
    vi.mocked(api.getCommunity).mockResolvedValue({ ...offStatus, enabled: true, iata: 'CDG' });
    render(<SettingsCommunitySection />);
    expect(
      await screen.findByRole('button', { name: i18n.t('settings.community.leave') })
    ).toBeInTheDocument();

    // The store re-read after a `community_live` broadcast from the other tab.
    act(() => {
      setCommunityStatus({ ...offStatus, iata: 'CDG' });
    });
    await waitFor(() => {
      expect(
        screen.queryByRole('button', { name: i18n.t('settings.community.leave') })
      ).not.toBeInTheDocument();
    });
    expect(
      screen.getByRole('button', { name: i18n.t('settings.community.joinCta') })
    ).toBeInTheDocument();
  });

  it('shows a join CTA when community is off', async () => {
    render(<SettingsCommunitySection />);

    expect(
      await screen.findByRole('button', { name: i18n.t('settings.community.joinCta') })
    ).toBeInTheDocument();
    expect(screen.getByText(i18n.t('settings.community.privacyAccount'))).toBeInTheDocument();
    expect(screen.getByText(i18n.t('settings.community.privacyPrivateKey'))).toBeInTheDocument();
    expect(screen.getByText(i18n.t('settings.community.privacyPackets'))).toBeInTheDocument();
    expect(screen.getByText(i18n.t('settings.community.privacyRadio'))).toBeInTheDocument();
    expect(screen.getByText(i18n.t('settings.community.privacyHashtags'))).toBeInTheDocument();
    expect(screen.getByText(i18n.t('settings.community.privacyDirectory'))).toBeInTheDocument();
    // No enable checkbox: joining is the only way on, and it binds the IATA first.
    expect(screen.queryByRole('checkbox')).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: i18n.t('settings.community.leave') })).toBeNull();
    expect(api.updateCommunity).not.toHaveBeenCalled();
  });

  it('leaves through PATCH enabled:false and only then', async () => {
    vi.mocked(api.getCommunity).mockResolvedValue({ ...offStatus, enabled: true, iata: 'CDG' });
    vi.mocked(api.updateCommunity).mockResolvedValue({ ...offStatus, iata: 'CDG' });
    render(<SettingsCommunitySection />);

    fireEvent.click(
      await screen.findByRole('button', { name: i18n.t('settings.community.leave') })
    );

    await waitFor(() => {
      expect(api.updateCommunity).toHaveBeenCalledWith({ enabled: false });
    });
    expect(api.updateCommunity).toHaveBeenCalledTimes(1);
    expect(
      await screen.findByRole('button', { name: i18n.t('settings.community.joinCta') })
    ).toBeInTheDocument();
  });

  it('says it is leaving while the PATCH runs, and ties the button to its help', async () => {
    vi.mocked(api.getCommunity).mockResolvedValue({ ...offStatus, enabled: true, iata: 'CDG' });
    let finish: (status: CommunityStatus) => void = () => {};
    vi.mocked(api.updateCommunity).mockReturnValue(
      new Promise<CommunityStatus>((resolve) => {
        finish = resolve;
      })
    );
    render(<SettingsCommunitySection />);

    const leave = await screen.findByRole('button', {
      name: i18n.t('settings.community.leave'),
    });
    expect(leave).toHaveAccessibleDescription(i18n.t('settings.community.disableHelp'));
    fireEvent.click(leave);

    const leaving = await screen.findByRole('button', {
      name: i18n.t('settings.community.leaving'),
    });
    expect(leaving).toBeDisabled();
    await act(async () => {
      finish({ ...offStatus, iata: 'CDG' });
    });
    expect(
      await screen.findByRole('button', { name: i18n.t('settings.community.joinCta') })
    ).toBeInTheDocument();
  });

  it('explains IATA as the nearest airport and offers a search', async () => {
    vi.spyOn(api, 'searchCommunityAirports').mockResolvedValue([
      {
        iata: 'LYS',
        name: 'Lyon-Saint-Exupéry',
        city: 'Lyon',
        country: 'France',
        label: 'Lyon, France (LYS)',
      },
    ]);
    vi.mocked(api.getCommunity).mockResolvedValue({
      ...offStatus,
      enabled: true,
    });

    render(<SettingsCommunitySection />);

    expect(
      await screen.findByText(i18n.t('settings.community.iataHelp'), { exact: false })
    ).toBeInTheDocument();
    expect(
      screen.getByRole('link', { name: i18n.t('settings.community.iataDirectory') })
    ).toHaveAttribute('href', 'https://www.iata.org/en/publications/directories/code-search/');

    fireEvent.change(screen.getByLabelText(i18n.t('settings.community.iataSearch')), {
      target: { value: 'Lyon' },
    });
    fireEvent.click(await screen.findByRole('option', { name: /LYS/ }));
    expect(screen.getByLabelText(i18n.t('settings.community.iata'))).toHaveValue('LYS');
  });

  it('offers no airport search while Community is off', async () => {
    const search = vi.spyOn(api, 'searchCommunityAirports');

    render(<SettingsCommunitySection />);

    expect(
      await screen.findByRole('button', { name: i18n.t('settings.community.joinCta') })
    ).toBeInTheDocument();
    expect(screen.queryByLabelText(i18n.t('settings.community.iataSearch'))).toBeNull();
    expect(search).not.toHaveBeenCalled();
  });

  it('explains that publish is off when enabled without IATA', async () => {
    vi.mocked(api.getCommunity).mockResolvedValue({
      ...offStatus,
      enabled: true,
      publisher_configured: false,
    });

    render(<SettingsCommunitySection />);

    expect(
      await screen.findByText(i18n.t('settings.community.noIataPublishOff'))
    ).toBeInTheDocument();
    expect(
      screen.queryByRole('button', { name: i18n.t('settings.community.joinCta') })
    ).not.toBeInTheDocument();
  });

  it('shows me/community stats and the I-am-sure override for warn concordance', async () => {
    vi.mocked(api.getCommunity).mockResolvedValue({
      ...offStatus,
      enabled: true,
      iata: 'CDG',
      publisher_configured: true,
      publisher_connected: true,
    });

    render(<SettingsCommunitySection />);

    expect(await screen.findByText(i18n.t('settings.community.meTitle'))).toBeInTheDocument();
    expect(await screen.findByText('11')).toBeInTheDocument();
    expect(await screen.findByText('8')).toBeInTheDocument();
    expect(screen.getByText(i18n.t('settings.community.concordanceWarn'))).toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: i18n.t('settings.community.iAmSure') }));
    await waitFor(() => {
      expect(api.overrideCommunityIata).toHaveBeenCalledTimes(1);
    });
  });

  it('joins in one bind call: Community binds the IATA before anything is turned on', async () => {
    vi.mocked(api.getCommunity)
      .mockResolvedValueOnce(offStatus)
      .mockResolvedValue({ ...offStatus, enabled: true, iata: 'CDG', publisher_configured: true });
    render(<SettingsCommunitySection />);

    const iata = await screen.findByLabelText(i18n.t('settings.community.iata'));
    fireEvent.change(iata, { target: { value: 'cdg' } });
    fireEvent.click(screen.getByRole('button', { name: i18n.t('settings.community.joinCta') }));

    await waitFor(() => {
      expect(api.bindCommunityIata).toHaveBeenCalledWith({ iata: 'CDG', enable: true });
    });
    // No PATCH first: it would start the publisher on the new topic before
    // Community binds the IATA.
    expect(api.updateCommunity).not.toHaveBeenCalled();
    expect(
      await screen.findByRole('button', { name: i18n.t('settings.community.leave') })
    ).toBeInTheDocument();
    expect(toastMocks.success).toHaveBeenCalledWith(i18n.t('settings.community.bindSuccess'));
  });

  it('stays off and says why when Community refuses the join bind (IATA change cap)', async () => {
    vi.mocked(api.bindCommunityIata).mockRejectedValue(
      new ApiError('Community IATA change cap reached', 429)
    );
    render(<SettingsCommunitySection />);

    const iata = await screen.findByLabelText(i18n.t('settings.community.iata'));
    fireEvent.change(iata, { target: { value: 'lys' } });
    fireEvent.click(screen.getByRole('button', { name: i18n.t('settings.community.joinCta') }));

    await waitFor(() => {
      expect(toastMocks.error).toHaveBeenCalledWith(i18n.t('settings.community.joinIataChangeCap'));
    });
    expect(api.updateCommunity).not.toHaveBeenCalled();
    expect(screen.queryByRole('button', { name: i18n.t('settings.community.leave') })).toBeNull();
    expect(
      screen.getByRole('button', { name: i18n.t('settings.community.joinCta') })
    ).toBeInTheDocument();
  });

  it('shows why the status could not be loaded', async () => {
    vi.mocked(api.getCommunity).mockRejectedValue(new ApiError('Radio key is not available', 503));
    render(<SettingsCommunitySection />);

    expect(await screen.findByText('Radio key is not available')).toBeInTheDocument();
  });

  it('changes the IATA in one bind call, saved by the backend after Community accepts it', async () => {
    vi.mocked(api.getCommunity).mockResolvedValue({
      ...offStatus,
      enabled: true,
      publisher_configured: false,
    });
    vi.mocked(api.updateCommunity).mockResolvedValue({
      ...offStatus,
      enabled: true,
      iata: 'LYS',
      publisher_configured: true,
    });

    render(<SettingsCommunitySection />);

    const iata = await screen.findByLabelText(i18n.t('settings.community.iata'));
    fireEvent.change(iata, { target: { value: 'lys' } });
    fireEvent.click(screen.getByRole('button', { name: i18n.t('settings.community.bind') }));

    await waitFor(() => {
      expect(api.bindCommunityIata).toHaveBeenCalledWith({ iata: 'LYS' });
    });
    // No PATCH first: it would reconnect with the new IATA before Community
    // binds it (and before the old region's status is cleared).
    expect(api.updateCommunity).not.toHaveBeenCalled();
    await waitFor(() => expect(api.getCommunity).toHaveBeenCalledTimes(2));
  });

  it('shows the store status at once', async () => {
    act(() => {
      setCommunityStatus({ ...offStatus, enabled: true, iata: 'NCE' });
    });
    vi.mocked(api.getCommunity).mockReturnValue(new Promise(() => {}));
    render(<SettingsCommunitySection />);

    expect(
      screen.getByRole('button', { name: i18n.t('settings.community.leave') })
    ).toBeInTheDocument();
    expect(screen.getByLabelText(i18n.t('settings.community.iata'))).toHaveValue('NCE');
  });

  it('surfaces the IATA change cap on 429', async () => {
    vi.mocked(api.getCommunity).mockResolvedValue({
      ...offStatus,
      enabled: true,
      iata: 'CDG',
      publisher_configured: true,
    });
    vi.mocked(api.bindCommunityIata).mockRejectedValue(
      new ApiError('Community IATA change cap reached', 429)
    );

    render(<SettingsCommunitySection />);

    const bind = await screen.findByRole('button', { name: i18n.t('settings.community.bind') });
    fireEvent.click(bind);

    await waitFor(() => {
      expect(toastMocks.error).toHaveBeenCalledWith(i18n.t('settings.community.iataChangeCap'));
    });
  });
});
