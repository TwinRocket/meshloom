import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { ApiError, api } from '../api';
import { BulkDeleteContactsModal } from '../components/settings/BulkDeleteContactsModal';
import { toast } from '../components/ui/sonner';
import i18n from '../i18n';
import type { Contact } from '../types';

const KEYS = ['aa', 'bb', 'cc'].map((k) => k.repeat(32));
const contacts = KEYS.map(
  (public_key) => ({ public_key, name: public_key.slice(0, 6), type: 1 }) as unknown as Contact
);
const timeout = () =>
  new ApiError('Too slow', 408, { code: 'request_timeout', message: 'Too slow' });

async function clickDelete() {
  const button = (await screen.findAllByRole('button')).find((b) =>
    b.textContent?.startsWith(i18n.t('settings.radioApp.bulk.confirmDelete').slice(0, 10))
  );
  fireEvent.click(button!);
}

async function selectAllAndDelete() {
  fireEvent.click(screen.getByRole('button', { name: i18n.t('settings.radioApp.bulk.selectAll') }));
  fireEvent.click(
    screen.getByRole('button', { name: i18n.t('settings.radioApp.bulk.proceed', { count: 3 }) })
  );
  await clickDelete();
}

describe('BulkDeleteContactsModal, a batch fails', () => {
  beforeEach(async () => {
    await i18n.changeLanguage('fr');
  });

  afterEach(async () => {
    vi.restoreAllMocks();
    await i18n.changeLanguage('en');
  });

  it('shows the error in the UI language, reports and refreshes what went, and totals a retry', async () => {
    const error = vi.spyOn(toast, 'error').mockImplementation(() => 'id');
    const success = vi.spyOn(toast, 'success').mockImplementation(() => 'id');
    const onDeleted = vi.fn();
    const bulk = vi
      .spyOn(api, 'bulkDeleteContacts')
      .mockImplementationOnce(async (_keys, onBatch) => {
        onBatch?.(KEYS.slice(0, 2), 2);
        throw timeout();
      })
      .mockImplementationOnce(async (keys, onBatch) => {
        onBatch?.(keys, keys.length);
        return { deleted: keys.length };
      });
    render(
      <BulkDeleteContactsModal open onClose={() => {}} contacts={contacts} onDeleted={onDeleted} />
    );

    await selectAllAndDelete();
    await waitFor(() => expect(error).toHaveBeenCalled());

    expect(onDeleted).toHaveBeenCalledWith(KEYS.slice(0, 2));
    const description = error.mock.calls[0][1]?.description as string;
    expect(description).toContain(i18n.t('errors.request_timeout'));
    expect(description).toContain(i18n.t('settings.radioApp.bulk.deleted', { count: 2 }));
    expect(description).not.toContain('Too slow');

    await clickDelete();
    await waitFor(() => expect(success).toHaveBeenCalled());
    expect(bulk.mock.calls[1][0]).toEqual(KEYS.slice(2));
    expect(success.mock.calls[0][0]).toBe(i18n.t('settings.radioApp.bulk.deleted', { count: 3 }));
  });

  it('reports no deletion when the first batch fails', async () => {
    const error = vi.spyOn(toast, 'error').mockImplementation(() => 'id');
    const onDeleted = vi.fn();
    vi.spyOn(api, 'bulkDeleteContacts').mockRejectedValueOnce(timeout());
    render(
      <BulkDeleteContactsModal open onClose={() => {}} contacts={contacts} onDeleted={onDeleted} />
    );

    await selectAllAndDelete();
    await waitFor(() => expect(error).toHaveBeenCalled());

    expect(onDeleted).not.toHaveBeenCalled();
    expect(error.mock.calls[0][1]?.description).toBe(i18n.t('errors.request_timeout'));
  });
});
