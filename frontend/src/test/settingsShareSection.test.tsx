import { act, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi, type Mock } from 'vitest';

const toastMocks = vi.hoisted(() => ({ error: vi.fn(), success: vi.fn() }));
vi.mock('../components/ui/sonner', () => ({ toast: toastMocks }));

const roleRef = vi.hoisted(() => ({ current: null as string | null }));
vi.mock('../auth/SessionProvider', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../auth/SessionProvider')>();
  const { capabilitiesFor } = await import('../auth/capabilities');
  return {
    ...actual,
    useCapabilities: () =>
      roleRef.current ? capabilitiesFor(roleRef.current as AuthRole) : actual.useCapabilities(),
  };
});

import { SettingsShareSection } from '../components/settings/SettingsShareSection';
import { api } from '../api';
import i18n from '../i18n';
import { resetShareStatusStoreForTests } from '../stores/shareStatusStore';
import type { AuthRole } from '../auth/types';
import type { AppSettingsUpdate, ShareStatus } from '../types';
import { DEFAULT_LOCALE } from '../utils/languagePreference';

const offStatus: ShareStatus = {
  enabled: false,
  host: 'meshl.ink',
  hosts: ['meshl.ink'],
  identity_ready: false,
  dm_encryption_available: true,
  banned: false,
  unreachable: false,
  shorten_mode: 'auto',
  preview_policy: 'private',
};

describe('SettingsShareSection', () => {
  let current: ShareStatus;
  let save: Mock<(update: AppSettingsUpdate) => Promise<void>>;

  beforeEach(() => {
    resetShareStatusStoreForTests();
    current = offStatus;
    roleRef.current = null;
    vi.spyOn(api, 'getShareStatus').mockImplementation(async () => current);
    vi.spyOn(api, 'listShareMine').mockResolvedValue({ objects: [], next_cursor: null });
    save = vi.fn(async (update: AppSettingsUpdate) => {
      current = {
        ...current,
        ...(update.share_enabled !== undefined && { enabled: update.share_enabled }),
        ...(update.share_shorten_mode !== undefined && {
          shorten_mode: update.share_shorten_mode,
        }),
        ...(update.share_preview_policy !== undefined && {
          preview_policy: update.share_preview_policy,
        }),
      };
    });
  });

  afterEach(() => {
    vi.restoreAllMocks();
    toastMocks.error.mockClear();
    void i18n.changeLanguage(DEFAULT_LOCALE);
  });

  const renderSection = () => render(<SettingsShareSection onSaveAppSettings={save} />);
  const enableBox = () => screen.findByRole('checkbox', { name: i18n.t('settings.share.enable') });

  it('is off by default and enabling opens the consent without saving', async () => {
    renderSection();
    const box = await enableBox();
    expect(box).toHaveAttribute('aria-checked', 'false');
    fireEvent.click(box);
    expect(await screen.findByTestId('share-consent-dialog')).toBeInTheDocument();
    expect(save).not.toHaveBeenCalled();
  });

  it('keeps the confirm button disabled until the terms are ticked', async () => {
    renderSection();
    fireEvent.click(await enableBox());
    const confirm = await screen.findByRole('button', {
      name: i18n.t('settings.share.consent.confirm'),
    });
    expect(confirm).toBeDisabled();
    const dialog = screen.getByTestId('share-consent-dialog');
    const terms = dialog.querySelector('#share-accept-terms') as HTMLElement;
    fireEvent.click(terms);
    expect(confirm).toBeEnabled();
    fireEvent.click(terms);
    expect(confirm).toBeDisabled();
    expect(save).not.toHaveBeenCalled();
  });

  it('links the terms and privacy pages of the share service', async () => {
    renderSection();
    fireEvent.click(await enableBox());
    const dialog = await screen.findByTestId('share-consent-dialog');
    const hrefs = Array.from(dialog.querySelectorAll('a')).map((a) => a.getAttribute('href'));
    // Pages of the share service itself, on the status host.
    expect(hrefs).toContain(`https://${offStatus.host}/terms?lang=fr`);
    expect(hrefs).toContain(`https://${offStatus.host}/privacy?lang=fr`);
    expect(hrefs.some((h) => h?.includes('meshloom.app/fr/docs/deep/security/'))).toBe(true);
    expect(hrefs.some((h) => h?.includes('/docs/deep/security/'))).toBe(true);
  });

  it('takes the legal pages host from the status, not a constant', async () => {
    current = { ...offStatus, host: 'share.example.test' };
    renderSection();
    fireEvent.click(await enableBox());
    const dialog = await screen.findByTestId('share-consent-dialog');
    const hrefs = Array.from(dialog.querySelectorAll('a')).map((a) => a.getAttribute('href'));
    expect(hrefs).toContain('https://share.example.test/terms?lang=fr');
  });

  it('keeps the toggle disabled with an explanation while the service is unreachable', async () => {
    current = { ...offStatus, unreachable: true };
    renderSection();
    const box = await enableBox();
    expect(box).toBeDisabled();
    expect(screen.getByTestId('share-unreachable')).toHaveTextContent(
      i18n.t('settings.share.unreachable')
    );
    fireEvent.click(box);
    expect(screen.queryByTestId('share-consent-dialog')).toBeNull();
    expect(save).not.toHaveBeenCalled();
  });

  it('shows the ban banner with the contact in both languages, and none otherwise', async () => {
    current = { ...offStatus, enabled: true, banned: true };
    renderSection();
    await enableBox();
    for (const lng of ['fr', 'en']) {
      await act(async () => {
        await i18n.changeLanguage(lng);
      });
      const banner = screen.getByTestId('share-banned');
      expect(banner).toHaveTextContent(i18n.t('settings.share.banned.title'));
      expect(banner.querySelector('a')).toHaveAttribute('href', 'mailto:ban@meshloom.app');
      expect(banner).toHaveTextContent('ban@meshloom.app');
    }
  });

  it('shows no ban banner while not banned', async () => {
    renderSection();
    await enableBox();
    expect(screen.queryByTestId('share-banned')).toBeNull();
  });

  it('lets an already enabled instance turn sharing off while unreachable', async () => {
    current = { ...offStatus, enabled: true, unreachable: true };
    renderSection();
    const box = await enableBox();
    expect(box).toBeEnabled();
    expect(screen.queryByTestId('share-unreachable')).toBeNull();
    fireEvent.click(box);
    await waitFor(() => expect(save).toHaveBeenCalledWith({ share_enabled: false }));
  });

  it('disables the confirm button if the service drops while the dialog is open', async () => {
    renderSection();
    fireEvent.click(await enableBox());
    const dialog = await screen.findByTestId('share-consent-dialog');
    fireEvent.click(dialog.querySelector('#share-accept-terms') as HTMLElement);
    const confirm = screen.getByRole('button', { name: i18n.t('settings.share.consent.confirm') });
    expect(confirm).toBeEnabled();
    const { setShareStatus } = await import('../stores/shareStatusStore');
    act(() => setShareStatus({ ...offStatus, unreachable: true }));
    expect(confirm).toBeDisabled();
    expect(screen.getByTestId('share-consent-unreachable')).toBeInTheDocument();
  });

  it('cancelling leaves sharing off', async () => {
    renderSection();
    fireEvent.click(await enableBox());
    fireEvent.click(
      await screen.findByRole('button', { name: i18n.t('settings.share.consent.cancel') })
    );
    await waitFor(() => expect(screen.queryByTestId('share-consent-dialog')).toBeNull());
    expect(save).not.toHaveBeenCalled();
    expect(current.enabled).toBe(false);
  });

  it('shows a notice and asks again when the terms changed since the acceptance', async () => {
    current = {
      ...offStatus,
      terms_version: 2,
      terms_accepted_version: 1,
      terms_accepted_at: 1_790_000_000,
      terms_stale: true,
    } as ShareStatus;
    renderSection();
    expect(await screen.findByTestId('share-terms-stale')).toHaveTextContent(
      i18n.t('settings.share.termsStale')
    );
    const box = await enableBox();
    expect(box).toHaveAttribute('aria-checked', 'false');
    fireEvent.click(box);
    expect(await screen.findByTestId('share-consent-dialog')).toBeInTheDocument();
  });

  it('shows no stale notice when the acceptance is current', async () => {
    renderSection();
    await enableBox();
    expect(screen.queryByTestId('share-terms-stale')).toBeNull();
  });

  it('accepting the terms turns sharing on', async () => {
    renderSection();
    fireEvent.click(await enableBox());
    const dialog = await screen.findByTestId('share-consent-dialog');
    fireEvent.click(dialog.querySelector('#share-accept-terms') as HTMLElement);
    fireEvent.click(screen.getByRole('button', { name: i18n.t('settings.share.consent.confirm') }));
    await waitFor(() =>
      expect(save).toHaveBeenCalledWith({ share_enabled: true, share_terms_accepted: true })
    );
    await waitFor(() => expect(screen.queryByTestId('share-consent-dialog')).toBeNull());
    expect(await enableBox()).toHaveAttribute('aria-checked', 'true');
  });

  it('turning sharing off needs no consent', async () => {
    current = { ...offStatus, enabled: true };
    renderSection();
    fireEvent.click(await enableBox());
    await waitFor(() => expect(save).toHaveBeenCalledWith({ share_enabled: false }));
    expect(screen.queryByTestId('share-consent-dialog')).toBeNull();
  });

  it('saves the shorten mode and the preview policy', async () => {
    const { container } = renderSection();
    await enableBox();
    const radio = (name: string, value: string) =>
      container.querySelector(`input[name="${name}"][value="${value}"]`) as HTMLElement;
    fireEvent.click(radio('share-shorten-mode', 'never'));
    await waitFor(() => expect(save).toHaveBeenCalledWith({ share_shorten_mode: 'never' }));
    fireEvent.click(radio('share-preview-policy', 'always'));
    await waitFor(() => expect(save).toHaveBeenCalledWith({ share_preview_policy: 'always' }));
  });

  it('explains the radio-key edge case only while it is unavailable', async () => {
    const { unmount } = renderSection();
    await enableBox();
    expect(screen.queryByTestId('share-dm-unavailable')).toBeNull();
    unmount();
    resetShareStatusStoreForTests();
    current = { ...offStatus, dm_encryption_available: false };
    renderSection();
    expect(await screen.findByTestId('share-dm-unavailable')).toBeInTheDocument();
  });

  it('shows a failed save and keeps the real state', async () => {
    save.mockRejectedValueOnce(new Error('boom'));
    renderSection();
    fireEvent.click(await enableBox());
    const dialog = await screen.findByTestId('share-consent-dialog');
    fireEvent.click(dialog.querySelector('#share-accept-terms') as HTMLElement);
    fireEvent.click(screen.getByRole('button', { name: i18n.t('settings.share.consent.confirm') }));
    await waitFor(() => expect(toastMocks.error).toHaveBeenCalled());
    expect(screen.getByTestId('share-consent-dialog')).toBeInTheDocument();
    expect(current.enabled).toBe(false);
  });

  it('clears the cache and says how much it freed', async () => {
    const clear = vi.spyOn(api, 'clearShareCache').mockResolvedValue({ freed_bytes: 2_500_000 });
    renderSection();
    await enableBox();
    fireEvent.click(screen.getByRole('button', { name: i18n.t('settings.share.cache.clear') }));
    expect(await screen.findByTestId('share-cache-freed')).toHaveTextContent(/2,5\s?MB/);
    expect(clear).toHaveBeenCalledTimes(1);
  });

  it('explains why there is no upload list while sharing is off', async () => {
    renderSection();
    await enableBox();
    expect(screen.getByTestId('share-uploads-off')).toBeInTheDocument();
    expect(screen.queryByTestId('share-uploads')).toBeNull();
    expect(api.listShareMine).not.toHaveBeenCalled();
  });

  it('lists the uploads while sharing is on', async () => {
    current = { ...offStatus, enabled: true };
    renderSection();
    expect(await screen.findByTestId('share-uploads-empty')).toBeInTheDocument();
    expect(api.listShareMine).toHaveBeenCalledTimes(1);
  });

  it('shows a member only the upload list, never the settings or the cache', async () => {
    roleRef.current = 'member';
    current = { ...offStatus, enabled: true };
    renderSection();
    expect(await screen.findByTestId('share-uploads-empty')).toBeInTheDocument();
    expect(screen.queryByRole('checkbox', { name: i18n.t('settings.share.enable') })).toBeNull();
    expect(screen.queryByTestId('share-cache')).toBeNull();
    expect(document.querySelector('input[name="share-preview-policy"]')).toBeNull();
  });

  it.each(['viewer', 'kiosk'] as const)('shows the %s role no upload list', async (role) => {
    roleRef.current = role;
    current = { ...offStatus, enabled: true };
    renderSection();
    await waitFor(() => expect(api.getShareStatus).toHaveBeenCalled());
    await act(async () => {});
    expect(screen.queryByTestId('share-uploads')).toBeNull();
    expect(screen.queryByTestId('share-uploads-off')).toBeNull();
    expect(api.listShareMine).not.toHaveBeenCalled();
  });
});
