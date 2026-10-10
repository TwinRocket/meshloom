import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { SettingsCommunitySection } from '../components/settings/SettingsCommunitySection';
import { api, ApiError } from '../api';
import i18n from '../i18n';
import type { CommunityStatus } from '../types';
import { DEFAULT_LOCALE } from '../utils/languagePreference';

const offStatus: CommunityStatus = {
  enabled: false,
  iata: '',
  broker_host: '',
  api_base: '',
  publisher_configured: false,
  publisher_connected: false,
  env_seeded: false,
};

describe('SettingsCommunitySection', () => {
  beforeEach(() => {
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
    void i18n.changeLanguage(DEFAULT_LOCALE);
  });

  it('shows a join CTA when community is off', async () => {
    render(<SettingsCommunitySection />);

    expect(
      await screen.findByRole('button', { name: i18n.t('settings.community.joinCta') })
    ).toBeInTheDocument();
    expect(screen.getByText(i18n.t('settings.community.privacyAccount'))).toBeInTheDocument();
    expect(screen.getByText(i18n.t('settings.community.privacyPrivateKey'))).toBeInTheDocument();
    expect(screen.getByText(i18n.t('settings.community.privacyHashtags'))).toBeInTheDocument();
    expect(screen.getByLabelText(i18n.t('settings.community.enable'))).not.toBeDisabled();
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

  it('joins with enable + IATA then binds', async () => {
    render(<SettingsCommunitySection />);

    const iata = await screen.findByLabelText(i18n.t('settings.community.iata'));
    fireEvent.change(iata, { target: { value: 'cdg' } });
    fireEvent.click(screen.getByRole('button', { name: i18n.t('settings.community.joinCta') }));

    await waitFor(() => {
      expect(api.updateCommunity).toHaveBeenCalledWith({ enabled: true, iata: 'CDG' });
      expect(api.bindCommunityIata).toHaveBeenCalledWith({ iata: 'CDG' });
    });
  });

  it('persists IATA before bind when already enabled', async () => {
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
      expect(api.updateCommunity).toHaveBeenCalledWith({ iata: 'LYS' });
      expect(api.bindCommunityIata).toHaveBeenCalledWith({ iata: 'LYS' });
    });
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
      expect(api.bindCommunityIata).toHaveBeenCalled();
    });
  });
});
