import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

const toastMocks = vi.hoisted(() => ({ error: vi.fn(), success: vi.fn() }));
vi.mock('../components/ui/sonner', () => ({ toast: toastMocks }));

import { ShareUploadsList } from '../components/settings/ShareUploadsList';
import { api, ApiError } from '../api';
import i18n from '../i18n';
import type { ShareMineObject } from '../types';

const base: ShareMineObject = {
  id: 'AbCdEfG',
  kind: 'file',
  status: 'active',
  blocked_category: null,
  created_at: 1_790_000_000,
  expires_at: 1_792_592_000,
  target_host: null,
  mode: 'public',
  content_type: 'image/webp',
  size: 1_500,
  size_kib: null,
};
const link: ShareMineObject = {
  ...base,
  id: 'HjKmNpQ',
  kind: 'link',
  mode: null,
  content_type: null,
  size: null,
  target_host: 'example.org',
};
const blocked: ShareMineObject = {
  ...base,
  id: 'RsTuVwX',
  status: 'blocked',
  blocked_category: 'spam',
};

describe('ShareUploadsList', () => {
  beforeEach(() => {
    vi.spyOn(api, 'listShareMine').mockResolvedValue({
      objects: [base, link, blocked],
      next_cursor: null,
    });
  });

  afterEach(() => {
    vi.restoreAllMocks();
    toastMocks.error.mockClear();
    toastMocks.success.mockClear();
  });

  const renderList = () => render(<ShareUploadsList host="meshl.ink" />);

  it('shows kind, type or host, size and status', async () => {
    renderList();
    const file = await screen.findByTestId(`share-upload-${base.id}`);
    expect(file).toHaveTextContent(i18n.t('settings.share.uploads.kind.file'));
    expect(file).toHaveTextContent('image/webp');
    expect(file).toHaveTextContent(/1,5\s?KB/);
    expect(file).toHaveTextContent(i18n.t('settings.share.uploads.status.active'));
    const row = screen.getByTestId(`share-upload-${link.id}`);
    expect(row).toHaveTextContent(i18n.t('settings.share.uploads.kind.link'));
    expect(row).toHaveTextContent('example.org');
  });

  it('greys a blocked row, names its category, links the contact page and offers no delete', async () => {
    renderList();
    const row = await screen.findByTestId(`share-upload-${blocked.id}`);
    expect(row).toHaveAttribute('data-status', 'blocked');
    expect(row.className).toMatch(/opacity-60/);
    expect(row).toHaveTextContent(i18n.t('sharePayload.report.reasons.spam'));
    expect(within(row).getByRole('link')).toHaveAttribute(
      'href',
      'https://meshl.ink/legal?lang=fr'
    );
    expect(within(row).queryByRole('button')).toBeNull();
  });

  it('deletes an active row only after the confirm', async () => {
    const del = vi.spyOn(api, 'deleteShare').mockResolvedValue(undefined);
    renderList();
    const row = await screen.findByTestId(`share-upload-${base.id}`);
    fireEvent.click(within(row).getByRole('button'));
    const dialog = await screen.findByTestId('share-delete-dialog');
    expect(del).not.toHaveBeenCalled();
    fireEvent.click(
      within(dialog).getByRole('button', { name: i18n.t('settings.share.uploads.confirm.confirm') })
    );
    await waitFor(() => expect(del).toHaveBeenCalledWith(base.id));
    await waitFor(() => expect(screen.queryByTestId(`share-upload-${base.id}`)).toBeNull());
    expect(toastMocks.success).toHaveBeenCalled();
  });

  it('cancelling the confirm deletes nothing', async () => {
    const del = vi.spyOn(api, 'deleteShare').mockResolvedValue(undefined);
    renderList();
    const row = await screen.findByTestId(`share-upload-${base.id}`);
    fireEvent.click(within(row).getByRole('button'));
    const dialog = await screen.findByTestId('share-delete-dialog');
    fireEvent.click(
      within(dialog).getByRole('button', { name: i18n.t('settings.share.uploads.confirm.cancel') })
    );
    await waitFor(() => expect(screen.queryByTestId('share-delete-dialog')).toBeNull());
    expect(del).not.toHaveBeenCalled();
    expect(screen.getByTestId(`share-upload-${base.id}`)).toBeInTheDocument();
  });

  it('reloads the list when the service refuses a delete', async () => {
    vi.spyOn(api, 'deleteShare').mockRejectedValue(
      new ApiError('blocked', 409, { code: 'share_object_blocked' })
    );
    renderList();
    const row = await screen.findByTestId(`share-upload-${base.id}`);
    fireEvent.click(within(row).getByRole('button'));
    const dialog = await screen.findByTestId('share-delete-dialog');
    fireEvent.click(
      within(dialog).getByRole('button', { name: i18n.t('settings.share.uploads.confirm.confirm') })
    );
    await waitFor(() => expect(toastMocks.error).toHaveBeenCalled());
    await waitFor(() => expect(api.listShareMine).toHaveBeenCalledTimes(2));
  });

  it('shows the empty state', async () => {
    vi.mocked(api.listShareMine).mockResolvedValue({ objects: [], next_cursor: null });
    renderList();
    expect(await screen.findByTestId('share-uploads-empty')).toBeInTheDocument();
  });

  it('shows the error state and recovers on refresh', async () => {
    vi.mocked(api.listShareMine).mockRejectedValueOnce(new Error('boom'));
    renderList();
    expect(await screen.findByTestId('share-uploads-error')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: i18n.t('settings.share.uploads.refresh') }));
    expect(await screen.findByTestId(`share-upload-${base.id}`)).toBeInTheDocument();
    expect(screen.queryByTestId('share-uploads-error')).toBeNull();
  });

  it('loads the next page with the cursor', async () => {
    vi.mocked(api.listShareMine)
      .mockResolvedValueOnce({ objects: [base], next_cursor: 'c1' })
      .mockResolvedValueOnce({ objects: [link], next_cursor: null });
    renderList();
    await screen.findByTestId(`share-upload-${base.id}`);
    fireEvent.click(screen.getByRole('button', { name: i18n.t('settings.share.uploads.more') }));
    expect(await screen.findByTestId(`share-upload-${link.id}`)).toBeInTheDocument();
    expect(api.listShareMine).toHaveBeenLastCalledWith('c1');
    expect(screen.getByTestId(`share-upload-${base.id}`)).toBeInTheDocument();
  });
});
