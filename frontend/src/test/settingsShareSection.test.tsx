import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi, type Mock } from 'vitest';

const toastMocks = vi.hoisted(() => ({ error: vi.fn(), success: vi.fn() }));
vi.mock('../components/ui/sonner', () => ({ toast: toastMocks }));

import { SettingsShareSection } from '../components/settings/SettingsShareSection';
import { api } from '../api';
import i18n from '../i18n';
import { resetShareStatusStoreForTests } from '../stores/shareStatusStore';
import type { AppSettingsUpdate, ShareStatus } from '../types';
import { DEFAULT_LOCALE } from '../utils/languagePreference';

const offStatus: ShareStatus = {
  enabled: false,
  host: 'meshl.ink',
  hosts: ['meshl.ink'],
  identity_ready: false,
  dm_encryption_available: true,
  banned: false,
  shorten_mode: 'auto',
  preview_policy: 'private',
};

describe('SettingsShareSection', () => {
  let current: ShareStatus;
  let save: Mock<(update: AppSettingsUpdate) => Promise<void>>;

  beforeEach(() => {
    resetShareStatusStoreForTests();
    current = offStatus;
    vi.spyOn(api, 'getShareStatus').mockImplementation(async () => current);
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
    expect(hrefs).toContain('https://meshl.ink/terms');
    expect(hrefs).toContain('https://meshl.ink/privacy');
    expect(hrefs.some((h) => h?.includes('/docs/deep/security/'))).toBe(true);
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

  it('accepting the terms turns sharing on', async () => {
    renderSection();
    fireEvent.click(await enableBox());
    const dialog = await screen.findByTestId('share-consent-dialog');
    fireEvent.click(dialog.querySelector('#share-accept-terms') as HTMLElement);
    fireEvent.click(screen.getByRole('button', { name: i18n.t('settings.share.consent.confirm') }));
    await waitFor(() => expect(save).toHaveBeenCalledWith({ share_enabled: true }));
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
});
