import { fireEvent, render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';

import { SettingsAboutSection } from '../components/settings/SettingsAboutSection';
import i18n from '../i18n';
import type { OssUpdateStatus } from '../types';

const available: OssUpdateStatus = {
  current: '3.2.0-test',
  latest: '3.3.0',
  update_available: true,
  html_url: 'https://github.com/TwinRocket/meshloom/releases/tag/v3.3.0',
};

describe('SettingsAboutSection', () => {
  it('renders the debug support snapshot link', () => {
    render(
      <SettingsAboutSection
        health={{
          status: 'ok',
          radio_connected: true,
          radio_initializing: false,
          connection_info: 'Serial: /dev/ttyUSB0',
          app_info: {
            version: '3.2.0-test',
            commit_hash: 'deadbeef',
          },
          database_size_mb: 1.2,
          oldest_undecrypted_timestamp: null,
          fanout_statuses: {},
          bots_disabled: false,
        }}
      />
    );

    const link = screen.getByRole('link', { name: i18n.t('settings.about.debugSnapshot') });
    expect(link).toHaveAttribute('href', './api/debug');
    expect(link).toHaveAttribute('target', '_blank');
  });

  it('shows the update banner when update_available', () => {
    const onOpenUpdate = vi.fn();
    render(
      <SettingsAboutSection
        health={{
          status: 'ok',
          radio_connected: true,
          radio_initializing: false,
          connection_info: 'Serial: /dev/ttyUSB0',
          app_info: { version: '3.2.0-test', commit_hash: 'deadbeef' },
          database_size_mb: 1.2,
          oldest_undecrypted_timestamp: null,
          fanout_statuses: {},
          bots_disabled: false,
        }}
        updates={available}
        onOpenUpdate={onOpenUpdate}
      />
    );

    expect(screen.getByText(i18n.t('settings.about.updateAvailable'))).toBeInTheDocument();
    expect(
      screen.getByText(
        i18n.t('settings.about.updateAvailableHelp', { current: '3.2.0-test', latest: '3.3.0' })
      )
    ).toBeInTheDocument();
    fireEvent.click(
      screen.getByRole('button', { name: i18n.t('settings.about.showInstructions') })
    );
    expect(onOpenUpdate).toHaveBeenCalled();
  });

  it('hides the update banner when update_available is false', () => {
    render(
      <SettingsAboutSection
        health={{
          status: 'ok',
          radio_connected: true,
          radio_initializing: false,
          connection_info: 'Serial: /dev/ttyUSB0',
          app_info: { version: '3.2.0-test', commit_hash: 'deadbeef' },
          database_size_mb: 1.2,
          oldest_undecrypted_timestamp: null,
          fanout_statuses: {},
          bots_disabled: false,
        }}
        updates={{ ...available, update_available: false }}
      />
    );

    expect(screen.queryByText(i18n.t('settings.about.updateAvailable'))).not.toBeInTheDocument();
  });
});
