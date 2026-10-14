import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

const toastMocks = vi.hoisted(() => ({ error: vi.fn(), success: vi.fn() }));
vi.mock('../components/ui/sonner', () => ({ toast: toastMocks }));

const leafletClick = vi.hoisted(() => ({
  handler: null as ((event: { latlng: { lat: number; lng: number } }) => void) | null,
}));

vi.mock('react-leaflet', () => ({
  MapContainer: ({ children }: { children: React.ReactNode }) => (
    <div data-testid="picker-map">
      <button
        type="button"
        onClick={() => leafletClick.handler?.({ latlng: { lat: 45.764043, lng: 4.835659 } })}
      >
        map-click
      </button>
      {children}
    </div>
  ),
  TileLayer: () => null,
  CircleMarker: () => <div data-testid="picked-marker" />,
  useMap: () => ({
    getContainer: () => ({ getBoundingClientRect: () => ({ width: 400, height: 300 }) }),
    invalidateSize: vi.fn(),
  }),
  useMapEvents: ({
    click,
  }: {
    click: (event: { latlng: { lat: number; lng: number } }) => void;
  }) => {
    leafletClick.handler = click;
    return null;
  },
}));

import { CommunityWaitingBanner } from '../components/CommunityWaitingBanner';
import { api } from '../api';
import i18n from '../i18n';
import {
  communityStatusActive,
  communityWaitingForPosition,
  getCommunitySnapshot,
  resetCommunityStoreForTests,
  setCommunityStatus,
} from '../stores/communityStore';
import type { CommunityStatus } from '../types';
import { DEFAULT_LOCALE } from '../utils/languagePreference';
import * as timezoneLocation from '../utils/ianaTimezoneLocation';

const waitingStatus: CommunityStatus = {
  enabled: true,
  iata: 'LYS',
  publisher_configured: true,
  publisher_connected: true,
  state: 'waiting_for_position',
};

describe('CommunityWaitingBanner', () => {
  beforeEach(() => {
    resetCommunityStoreForTests();
    setCommunityStatus(waitingStatus);
    leafletClick.handler = null;
    vi.spyOn(timezoneLocation, 'getBrowserTimeZone').mockReturnValue('UTC');
    vi.spyOn(api, 'getCommunity').mockResolvedValue(waitingStatus);
    vi.spyOn(api, 'searchCommunityAirports').mockResolvedValue([]);
    vi.spyOn(api, 'updateRadioConfig').mockResolvedValue(undefined as never);
    vi.spyOn(api, 'updateCommunity').mockResolvedValue({
      ...waitingStatus,
      enabled: false,
      state: 'off',
    });
  });

  afterEach(() => {
    vi.restoreAllMocks();
    toastMocks.error.mockClear();
    toastMocks.success.mockClear();
    void i18n.changeLanguage(DEFAULT_LOCALE);
  });

  it('says why Community needs a position, in a named region', () => {
    render(<CommunityWaitingBanner />);
    const banner = screen.getByRole('region', {
      name: i18n.t('settings.community.waiting.regionLabel'),
    });
    expect(banner).not.toHaveAttribute('role', 'status');
    expect(banner).toHaveTextContent(i18n.t('settings.community.waiting.banner'));
    expect(
      within(banner).getByRole('button', {
        name: i18n.t('settings.community.waiting.chooseLocation'),
      })
    ).toBeInTheDocument();
  });

  it('warns that the radio may broadcast the chosen position', async () => {
    render(<CommunityWaitingBanner />);
    fireEvent.click(
      screen.getByRole('button', { name: i18n.t('settings.community.waiting.learnMore') })
    );
    const dialog = await screen.findByRole('dialog');
    expect(dialog).toHaveTextContent(i18n.t('settings.radio.includeLocation'));
    await i18n.changeLanguage('fr');
    expect(i18n.t('settings.community.waiting.privacy')).toContain('Inclure la position du nœud');
  });

  it('reads the French text with "vous"', async () => {
    await i18n.changeLanguage('fr');
    render(<CommunityWaitingBanner />);
    expect(
      screen.getByText('Pour fonctionner, Meshloom Community a besoin de votre position')
    ).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Choisir ma position' })).toBeInTheDocument();
  });

  it('explains in a dialog that offers both ways out', async () => {
    render(<CommunityWaitingBanner />);
    fireEvent.click(
      screen.getByRole('button', { name: i18n.t('settings.community.waiting.learnMore') })
    );
    const dialog = await screen.findByRole('dialog');
    expect(dialog).toHaveAccessibleName(i18n.t('settings.community.waiting.title'));
    expect(dialog).toHaveAccessibleDescription(i18n.t('settings.community.waiting.why'));
    expect(
      within(dialog).getByRole('button', { name: i18n.t('settings.community.waiting.leave') })
    ).toBeInTheDocument();
    expect(
      within(dialog).getByRole('button', {
        name: i18n.t('settings.community.waiting.chooseLocation'),
      })
    ).toBeInTheDocument();
  });

  it('saves the chosen position on the radio', async () => {
    render(<CommunityWaitingBanner />);
    fireEvent.click(
      screen.getByRole('button', { name: i18n.t('settings.community.waiting.chooseLocation') })
    );
    fireEvent.click(await screen.findByRole('button', { name: 'map-click' }));
    fireEvent.click(screen.getByRole('button', { name: i18n.t('settings.radio.pickOnMapSave') }));

    await waitFor(() =>
      expect(api.updateRadioConfig).toHaveBeenCalledWith({ lat: 45.764043, lon: 4.835659 })
    );
    await waitFor(() =>
      expect(toastMocks.success).toHaveBeenCalledWith(
        i18n.t('settings.community.waiting.positionSaved')
      )
    );
    // The banner stays a few seconds (debounce, then the bind): the toast says so.
    expect(i18n.t('settings.community.waiting.positionSaved')).toMatch(/Community/);
    // The airport search is a Community feature: never asked while waiting.
    expect(api.searchCommunityAirports).not.toHaveBeenCalled();
  });

  it('reports a radio that refused the position', async () => {
    vi.mocked(api.updateRadioConfig).mockRejectedValue(new Error('Radio not connected'));
    render(<CommunityWaitingBanner />);
    fireEvent.click(
      screen.getByRole('button', { name: i18n.t('settings.community.waiting.chooseLocation') })
    );
    fireEvent.click(await screen.findByRole('button', { name: 'map-click' }));
    fireEvent.click(screen.getByRole('button', { name: i18n.t('settings.radio.pickOnMapSave') }));

    await waitFor(() =>
      expect(toastMocks.error).toHaveBeenCalledWith(
        i18n.t('settings.community.waiting.positionFailed'),
        expect.anything()
      )
    );
  });

  it('leaves Community through the usual opt-out', async () => {
    render(<CommunityWaitingBanner />);
    fireEvent.click(
      screen.getByRole('button', { name: i18n.t('settings.community.waiting.learnMore') })
    );
    const dialog = await screen.findByRole('dialog');
    fireEvent.click(
      within(dialog).getByRole('button', { name: i18n.t('settings.community.waiting.leave') })
    );

    // The exit dialog first: nothing is sent before it is confirmed.
    const exit = await screen.findByTestId('community-leave-dialog');
    expect(exit).toHaveAccessibleName(i18n.t('settings.community.optOut.title'));
    expect(api.updateCommunity).not.toHaveBeenCalled();
    fireEvent.click(
      within(exit).getByRole('button', { name: i18n.t('settings.community.optOut.confirm') })
    );

    await waitFor(() => expect(api.updateCommunity).toHaveBeenCalledWith({ enabled: false }));
    await waitFor(() => expect(getCommunitySnapshot().status?.enabled).toBe(false));
    expect(toastMocks.success).toHaveBeenCalledWith(i18n.t('settings.community.left'));
    await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument());
  });
});

describe('communityStore while waiting for a position', () => {
  beforeEach(() => resetCommunityStoreForTests());

  it('treats waiting as off for every Community surface', () => {
    setCommunityStatus(waitingStatus);
    expect(getCommunitySnapshot().enabled).toBe(false);
    expect(communityWaitingForPosition(waitingStatus)).toBe(true);
    expect(communityStatusActive(waitingStatus)).toBe(false);
  });

  it('is on again once active, and an older backend without state reads as active', () => {
    setCommunityStatus({ ...waitingStatus, state: 'active' });
    expect(getCommunitySnapshot().enabled).toBe(true);
    const { state: _state, ...legacy } = waitingStatus;
    expect(communityStatusActive(legacy)).toBe(true);
    expect(communityWaitingForPosition(legacy)).toBe(false);
    expect(communityWaitingForPosition({ ...waitingStatus, enabled: false })).toBe(false);
  });
});
