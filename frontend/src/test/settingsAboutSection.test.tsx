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

  it('shows a discreet updates link when update_available', () => {
    const onOpenUpdates = vi.fn();
    render(
      <SettingsAboutSection health={health} updates={available} onOpenUpdates={onOpenUpdates} />
    );

    fireEvent.click(screen.getByRole('button', { name: i18n.t('settings.about.updateAvailable') }));
    expect(onOpenUpdates).toHaveBeenCalled();
    expect(
      screen.queryByText(
        i18n.t('settings.about.updateAvailableHelp', { current: '3.2.0-test', latest: '3.3.0' })
      )
    ).not.toBeInTheDocument();
    expect(screen.queryByText(i18n.t('settings.about.autoUpdate'))).not.toBeInTheDocument();
    expect(
      screen.queryByRole('button', { name: i18n.t('settings.about.install') })
    ).not.toBeInTheDocument();
  });

  it('hides the updates link when update_available is false', () => {
    render(
      <SettingsAboutSection health={health} updates={{ ...available, update_available: false }} />
    );

    expect(screen.queryByText(i18n.t('settings.about.updateAvailable'))).not.toBeInTheDocument();
  });

  it('does not show an auto-update checkbox even when apply is supported', () => {
    render(
      <SettingsAboutSection
        health={health}
        updates={{ ...available, apply_supported: true, install_kind: 'package' }}
        onOpenUpdates={vi.fn()}
      />
    );

    expect(screen.queryByText(i18n.t('settings.about.autoUpdate'))).not.toBeInTheDocument();
    expect(screen.queryByRole('checkbox')).not.toBeInTheDocument();
  });
});
