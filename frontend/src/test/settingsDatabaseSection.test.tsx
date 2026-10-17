import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';

const mocks = vi.hoisted(() => ({
  exportFullBackup: vi.fn(),
  getLatestRestoreJob: vi.fn(),
  downloadDatabaseBackup: vi.fn(),
  getJsonBackup: vi.fn(),
  toast: { success: vi.fn(), error: vi.fn(), info: vi.fn() },
}));

vi.mock('../api', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../api')>();
  return {
    ...actual,
    api: {
      exportFullBackup: mocks.exportFullBackup,
      getLatestRestoreJob: mocks.getLatestRestoreJob,
      downloadDatabaseBackup: mocks.downloadDatabaseBackup,
      getJsonBackup: mocks.getJsonBackup,
    },
  };
});

vi.mock('../components/ui/sonner', () => ({ toast: mocks.toast }));

import { filenameFromDisposition } from '../api';
import { rememberSettingsAnchor } from '../utils/urlHash';
import { SettingsDatabaseSection } from '../components/settings/SettingsDatabaseSection';
import i18n from '../i18n';
import type { AppSettings } from '../types';

function renderSection() {
  return render(
    <SettingsDatabaseSection
      appSettings={{ auto_decrypt_dm_on_advert: false } as AppSettings}
      health={null}
      onSaveAppSettings={vi.fn(async () => {})}
      onHealthRefresh={vi.fn(async () => {})}
    />
  );
}

describe('SettingsDatabaseSection backups', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mocks.getLatestRestoreJob.mockResolvedValue(null);
    mocks.exportFullBackup.mockResolvedValue({
      data: { format: 'meshloom-backup-v2' },
      filename: 'meshloom-backup-Base-20261017-0800.json',
      warnings: [],
    });
    URL.createObjectURL = vi.fn(() => 'blob:x');
    URL.revokeObjectURL = vi.fn();
  });

  it('wraps the backup group in a stable anchor', () => {
    const { container } = renderSection();
    expect(container.querySelector('#settings-backup')).not.toBeNull();
  });

  it('opens at the backup group from the deep link', () => {
    const scroll = vi.fn();
    Element.prototype.scrollIntoView = scroll;
    window.location.hash = '#settings/database/backup';
    rememberSettingsAnchor();
    try {
      renderSection();
    } finally {
      window.location.hash = '';
    }
    expect(scroll).toHaveBeenCalledTimes(1);
    expect(scroll.mock.contexts[0]).toHaveProperty('id', 'settings-backup');
  });

  it('shows the widened warning and leaves encryption off by default', () => {
    renderSection();
    expect(screen.getByText(i18n.t('settings.fullBackup.keyWarning'))).toBeVisible();
    const encrypt = screen.getByLabelText(
      i18n.t('settings.fullBackup.encrypt')
    ) as HTMLInputElement;
    expect(encrypt.checked).toBe(false);
    expect(screen.queryByLabelText(i18n.t('settings.fullBackup.passphrase'))).toBeNull();
  });

  it('replaces the old JSON export by the full backup', () => {
    renderSection();
    expect(
      screen.queryByRole('button', { name: i18n.t('settings.backupJsonDownload') })
    ).toBeNull();
    expect(
      screen.getByRole('button', { name: i18n.t('settings.fullBackup.download') })
    ).toBeVisible();
    expect(screen.getByRole('button', { name: i18n.t('settings.backupDbDownload') })).toBeVisible();
  });

  it('downloads a plain backup and shows its warnings', async () => {
    mocks.exportFullBackup.mockResolvedValue({
      data: { format: 'meshloom-backup-v2' },
      filename: 'f.json',
      warnings: ['private_key_export_disabled', 'wifi_not_stored', 'radio_read_failed:tuning'],
    });
    renderSection();
    fireEvent.click(screen.getByRole('button', { name: i18n.t('settings.fullBackup.download') }));
    await waitFor(() => expect(mocks.exportFullBackup).toHaveBeenCalledWith(undefined));
    expect(
      await screen.findByText(i18n.t('settings.fullBackup.warningIdentityDisabled'))
    ).toBeVisible();
    expect(screen.getByText(i18n.t('settings.fullBackup.warningWifiNotStored'))).toBeVisible();
    expect(screen.getByText(i18n.t('settings.fullBackup.warningReadFailed'))).toBeVisible();
  });

  it('enforces the passphrase rules before an encrypted download', async () => {
    renderSection();
    fireEvent.click(screen.getByLabelText(i18n.t('settings.fullBackup.encrypt')));
    const download = screen.getByRole('button', { name: i18n.t('settings.fullBackup.download') });
    expect(download).toBeDisabled();
    fireEvent.change(screen.getByLabelText(i18n.t('settings.fullBackup.passphrase')), {
      target: { value: 'short' },
    });
    expect(screen.getByText(i18n.t('settings.fullBackup.passphraseTooShort'))).toBeVisible();
    fireEvent.change(screen.getByLabelText(i18n.t('settings.fullBackup.passphrase')), {
      target: { value: 'a long passphrase' },
    });
    fireEvent.change(screen.getByLabelText(i18n.t('settings.fullBackup.passphraseConfirm')), {
      target: { value: 'a long passphras' },
    });
    expect(screen.getByText(i18n.t('settings.fullBackup.passphraseMismatch'))).toBeVisible();
    expect(download).toBeDisabled();
    fireEvent.change(screen.getByLabelText(i18n.t('settings.fullBackup.passphraseConfirm')), {
      target: { value: 'a long passphrase' },
    });
    expect(download).toBeEnabled();
    fireEvent.click(download);
    await waitFor(() => expect(mocks.exportFullBackup).toHaveBeenCalledWith('a long passphrase'));
  });

  it('reads the server file name, UTF-8 first', () => {
    expect(
      filenameFromDisposition(
        `attachment; filename="meshloom-backup-Re-seau.json"; filename*=UTF-8''meshloom-backup-R%C3%A9seau.json`,
        'x.json'
      )
    ).toBe('meshloom-backup-Réseau.json');
    expect(filenameFromDisposition('attachment; filename="plain.json"', 'x.json')).toBe(
      'plain.json'
    );
    expect(filenameFromDisposition(null, 'x.json')).toBe('x.json');
  });
});
