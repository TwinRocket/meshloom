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
    vi.spyOn(api, 'suggestCommunityIata').mockResolvedValue({
      neighbors: null,
      nearest_airport: null,
      source: null,
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
    fireEvent.click(
      await screen.findByRole('button', { name: i18n.t('settings.community.optOut.confirm') })
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
    fireEvent.click(
      await screen.findByRole('button', { name: i18n.t('settings.community.optOut.confirm') })
    );

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

  it('explains IATA as the nearest airport and links the IATA directory', async () => {
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
  });

  async function clickSuggest() {
    fireEvent.click(
      await screen.findByRole('button', { name: i18n.t('settings.community.suggest.button') })
    );
  }

  it('asks nothing while the join form shows, only on a click', async () => {
    render(<SettingsCommunitySection />);
    expect(
      await screen.findByRole('button', { name: i18n.t('settings.community.joinCta') })
    ).toBeInTheDocument();
    const suggest = screen.getByRole('button', {
      name: i18n.t('settings.community.suggest.button'),
    });
    // The button says what leaves before anything does.
    expect(suggest).toHaveAccessibleDescription(i18n.t('settings.community.suggest.help'));
    await act(async () => {
      await new Promise((resolve) => setTimeout(resolve, 20));
    });
    expect(api.suggestCommunityIata).not.toHaveBeenCalled();

    fireEvent.click(suggest);
    await waitFor(() => expect(api.suggestCommunityIata).toHaveBeenCalledTimes(1));
  });

  it('asks nothing right after an opt-out either', async () => {
    vi.mocked(api.getCommunity).mockResolvedValue({ ...offStatus, iata: 'CDG' });
    render(<SettingsCommunitySection />);
    expect(
      await screen.findByRole('button', { name: i18n.t('settings.community.joinCta') })
    ).toBeInTheDocument();
    expect(api.suggestCommunityIata).not.toHaveBeenCalled();
  });

  it('asks nothing on display once joined either', async () => {
    vi.mocked(api.getCommunity).mockResolvedValue({
      ...offStatus,
      enabled: true,
      iata: 'CDG',
      publisher_configured: true,
    });
    render(<SettingsCommunitySection />);
    const suggest = await screen.findByRole('button', {
      name: i18n.t('settings.community.suggest.button'),
    });
    expect(api.suggestCommunityIata).not.toHaveBeenCalled();
    fireEvent.click(suggest);
    await waitFor(() => expect(api.suggestCommunityIata).toHaveBeenCalledTimes(1));
  });

  it('proposes the neighbours’ code and the nearest airport, assigning nothing', async () => {
    vi.mocked(api.suggestCommunityIata).mockResolvedValue({
      neighbors: 'LYS',
      nearest_airport: 'LYN',
      source: 'community',
    });

    render(<SettingsCommunitySection />);
    await clickSuggest();

    const group = await screen.findByRole('group', {
      name: i18n.t('settings.community.suggest.title'),
    });
    const neighbours = await screen.findByRole('button', {
      name: `LYS ${i18n.t('settings.community.suggest.neighbors')}`,
    });
    const nearest = screen.getByRole('button', {
      name: `LYN ${i18n.t('settings.community.suggest.nearest')}`,
    });
    expect(group).toContainElement(neighbours);
    expect(api.suggestCommunityIata).toHaveBeenCalledTimes(1);
    expect(nearest).toHaveAttribute('aria-pressed', 'false');
    expect(screen.getByLabelText(i18n.t('settings.community.iata'))).toHaveValue('');

    fireEvent.click(nearest);
    expect(screen.getByLabelText(i18n.t('settings.community.iata'))).toHaveValue('LYN');
    expect(nearest).toHaveAttribute('aria-pressed', 'true');
    expect(neighbours).toHaveAttribute('aria-pressed', 'false');
    // Picking only fills the field: joining is still the user's click.
    expect(api.bindCommunityIata).not.toHaveBeenCalled();

    // Another code can still be typed.
    fireEvent.change(screen.getByLabelText(i18n.t('settings.community.iata')), {
      target: { value: 'cdg' },
    });
    expect(screen.getByLabelText(i18n.t('settings.community.iata'))).toHaveValue('CDG');
    expect(nearest).toHaveAttribute('aria-pressed', 'false');
  });

  it('shows one code when the neighbours’ code is the nearest airport', async () => {
    vi.mocked(api.suggestCommunityIata).mockResolvedValue({
      neighbors: 'CDG',
      nearest_airport: 'CDG',
      source: 'community',
    });

    render(<SettingsCommunitySection />);
    await clickSuggest();

    expect(
      await screen.findByRole('button', {
        name: `CDG ${i18n.t('settings.community.suggest.both')}`,
      })
    ).toBeInTheDocument();
    expect(screen.getAllByRole('button', { name: /^CDG / })).toHaveLength(1);
  });

  it('offers the nearest airport alone when Community did not answer', async () => {
    vi.mocked(api.suggestCommunityIata).mockResolvedValue({
      neighbors: null,
      nearest_airport: 'LBG',
      source: 'local',
    });

    render(<SettingsCommunitySection />);
    await clickSuggest();

    expect(
      await screen.findByRole('button', {
        name: `LBG ${i18n.t('settings.community.suggest.nearest')}`,
      })
    ).toBeInTheDocument();
    expect(
      screen.queryByText(i18n.t('settings.community.suggest.neighbors'))
    ).not.toBeInTheDocument();
  });

  it('says so when there is no position', async () => {
    render(<SettingsCommunitySection />);
    await clickSuggest();
    expect(await screen.findByText(i18n.t('settings.community.suggest.none'))).toBeInTheDocument();
    expect(screen.queryByRole('group')).toBeNull();
  });

  it('says so when the request fails, and the code can still be typed', async () => {
    vi.mocked(api.suggestCommunityIata).mockRejectedValue(new Error('down'));
    render(<SettingsCommunitySection />);
    await clickSuggest();
    expect(
      await screen.findByText(i18n.t('settings.community.suggest.failed'))
    ).toBeInTheDocument();
    expect(screen.queryByRole('group')).toBeNull();
    fireEvent.change(screen.getByLabelText(i18n.t('settings.community.iata')), {
      target: { value: 'lys' },
    });
    expect(screen.getByLabelText(i18n.t('settings.community.iata'))).toHaveValue('LYS');
  });

  it('never asks the airport lookup from the settings', async () => {
    const lookup = vi.spyOn(api, 'searchCommunityAirports');

    render(<SettingsCommunitySection />);

    expect(
      await screen.findByRole('button', { name: i18n.t('settings.community.joinCta') })
    ).toBeInTheDocument();
    expect(lookup).not.toHaveBeenCalled();
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

  it('says the radio is banned, calmly, with the contact address', async () => {
    vi.mocked(api.getCommunity).mockResolvedValue({
      ...offStatus,
      enabled: true,
      iata: 'CDG',
      publisher_configured: true,
      state: 'active',
      banned: true,
    });
    render(<SettingsCommunitySection />);

    const notice = await screen.findByTestId('community-banned');
    expect(notice).toHaveAttribute('role', 'status');
    expect(notice).toHaveTextContent(i18n.t('settings.community.banned.title'));
    const link = screen.getByRole('link', { name: 'ban@meshloom.app' });
    expect(link).toHaveAttribute('href', 'mailto:ban@meshloom.app');
    // Leaving stays possible.
    expect(
      screen.getByRole('button', { name: i18n.t('settings.community.leave') })
    ).toBeInTheDocument();
  });

  it('shows no ban notice otherwise', async () => {
    render(<SettingsCommunitySection />);
    await screen.findByRole('button', { name: i18n.t('settings.community.joinCta') });
    expect(screen.queryByTestId('community-banned')).toBeNull();
  });

  it('translates a join refused because the radio is banned', async () => {
    vi.mocked(api.bindCommunityIata).mockRejectedValue(
      new ApiError('This radio has been banned from Meshloom Community', 403, {
        code: 'community_banned',
        message: 'This radio has been banned from Meshloom Community',
      })
    );
    render(<SettingsCommunitySection />);

    const iata = await screen.findByLabelText(i18n.t('settings.community.iata'));
    fireEvent.change(iata, { target: { value: 'lys' } });
    fireEvent.click(screen.getByRole('button', { name: i18n.t('settings.community.joinCta') }));

    await waitFor(() => {
      expect(toastMocks.error).toHaveBeenCalledWith(i18n.t('errors.community_banned'));
    });
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
