import { fireEvent, render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';

import { SettingsAboutSection } from '../components/settings/SettingsAboutSection';
import i18n from '../i18n';
import type { HealthStatus, OssUpdateJob, OssUpdateStatus } from '../types';

const idleJob: OssUpdateJob = {
  state: 'idle',
  phase: null,
  percent: null,
  error: null,
  started_at: null,
};

const available: OssUpdateStatus = {
  current: '3.2.0-test',
  latest: '3.3.0',
  update_available: true,
  html_url: 'https://github.com/TwinRocket/meshloom/releases/tag/v3.3.0',
  install_kind: 'source',
  apply_supported: false,
  auto_update: false,
  job: idleJob,
};

const health: HealthStatus = {
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
};

describe('SettingsAboutSection', () => {
  it('renders the debug support snapshot link', () => {
    render(<SettingsAboutSection health={health} />);

    const link = screen.getByRole('link', { name: i18n.t('settings.about.debugSnapshot') });
    expect(link).toHaveAttribute('href', './api/debug');
    expect(link).toHaveAttribute('target', '_blank');
  });

  it('shows the update banner when update_available', () => {
    const onOpenUpdate = vi.fn();
    render(
      <SettingsAboutSection health={health} updates={available} onOpenUpdate={onOpenUpdate} />
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
      <SettingsAboutSection health={health} updates={{ ...available, update_available: false }} />
    );

    expect(screen.queryByText(i18n.t('settings.about.updateAvailable'))).not.toBeInTheDocument();
  });

  it('shows Install and auto-update when apply is supported', () => {
    const onApply = vi.fn();
    const onAutoUpdate = vi.fn();
    render(
      <SettingsAboutSection
        health={health}
        updates={{ ...available, apply_supported: true, install_kind: 'package' }}
        onApply={onApply}
        onAutoUpdate={onAutoUpdate}
      />
    );

    fireEvent.click(screen.getByRole('button', { name: i18n.t('settings.about.install') }));
    expect(onApply).toHaveBeenCalled();
    expect(
      screen.queryByRole('button', { name: i18n.t('settings.about.showInstructions') })
    ).not.toBeInTheDocument();
    expect(screen.getByText(i18n.t('settings.about.autoUpdate'))).toBeInTheDocument();
    fireEvent.click(screen.getByRole('checkbox', { name: i18n.t('settings.about.autoUpdate') }));
    expect(onAutoUpdate).toHaveBeenCalledWith(true);
  });

  it('keeps the auto-update toggle when no update is available', () => {
    render(
      <SettingsAboutSection
        health={health}
        updates={{
          ...available,
          update_available: false,
          apply_supported: true,
          install_kind: 'compose',
        }}
        onAutoUpdate={vi.fn()}
      />
    );

    expect(screen.queryByText(i18n.t('settings.about.updateAvailable'))).not.toBeInTheDocument();
    expect(screen.getByText(i18n.t('settings.about.autoUpdate'))).toBeInTheDocument();
  });
});
