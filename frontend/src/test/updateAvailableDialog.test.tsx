import { fireEvent, render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';

import { UpdateAvailableDialog } from '../components/UpdateAvailableDialog';
import i18n from '../i18n';
import type { OssUpdateJob, OssUpdateStatus } from '../types';

const idleJob: OssUpdateJob = {
  state: 'idle',
  phase: null,
  percent: null,
  error: null,
  started_at: null,
};

const updates: OssUpdateStatus = {
  current: '1.0.0',
  latest: '1.1.0',
  update_available: true,
  html_url: 'https://github.com/TwinRocket/meshloom/releases/tag/v1.1.0',
  install_kind: 'package',
  apply_supported: false,
  auto_update: false,
  job: idleJob,
};

describe('UpdateAvailableDialog', () => {
  it('shows both upgrade recipes and the release link', () => {
    render(<UpdateAvailableDialog open updates={updates} onClose={vi.fn()} />);

    expect(screen.getByText(i18n.t('updates.title'))).toBeInTheDocument();
    expect(screen.getByText(/sudo apt-get install --only-upgrade meshloom/)).toBeInTheDocument();
    expect(screen.getByText(/sudo dnf install meshloom/)).toBeInTheDocument();
    expect(screen.getByText(/sudo docker compose pull/)).toBeInTheDocument();

    const notes = screen.getByRole('link', { name: i18n.t('updates.changelog') });
    expect(notes).toHaveAttribute('href', updates.html_url);
    expect(notes).toHaveAttribute('target', '_blank');
  });

  it('closes from the footer', () => {
    const onClose = vi.fn();
    render(<UpdateAvailableDialog open updates={updates} onClose={onClose} />);
    fireEvent.click(screen.getByRole('button', { name: i18n.t('updates.close') }));
    expect(onClose).toHaveBeenCalled();
  });

  it('tells addon installs to update via Home Assistant and hides recipes', () => {
    render(
      <UpdateAvailableDialog
        open
        updates={{ ...updates, install_kind: 'addon' }}
        onClose={vi.fn()}
      />
    );

    expect(screen.getByText(i18n.t('updates.addonManual'))).toBeInTheDocument();
    expect(
      screen.queryByText(/sudo apt-get install --only-upgrade meshloom/)
    ).not.toBeInTheDocument();
    expect(screen.queryByText(/sudo docker compose pull/)).not.toBeInTheDocument();
  });

  it('tells container and source installs to update manually and keeps recipes', () => {
    render(
      <UpdateAvailableDialog
        open
        updates={{ ...updates, install_kind: 'container' }}
        onClose={vi.fn()}
      />
    );

    expect(screen.getByText(i18n.t('updates.manual'))).toBeInTheDocument();
    expect(screen.getByText(/sudo apt-get install --only-upgrade meshloom/)).toBeInTheDocument();
    expect(screen.getByText(/sudo docker compose pull/)).toBeInTheDocument();
  });

  it('tells source installs to update manually and keeps recipes', () => {
    render(
      <UpdateAvailableDialog
        open
        updates={{ ...updates, install_kind: 'source' }}
        onClose={vi.fn()}
      />
    );

    expect(screen.getByText(i18n.t('updates.manual'))).toBeInTheDocument();
    expect(screen.getByText(/sudo apt-get install --only-upgrade meshloom/)).toBeInTheDocument();
    expect(screen.getByText(/sudo docker compose pull/)).toBeInTheDocument();
  });

  it('shows Install and auto-update when apply is supported, without recipes', () => {
    const onApply = vi.fn();
    const onAutoUpdate = vi.fn();
    render(
      <UpdateAvailableDialog
        open
        updates={{ ...updates, apply_supported: true, install_kind: 'compose' }}
        onClose={vi.fn()}
        onApply={onApply}
        onAutoUpdate={onAutoUpdate}
      />
    );

    fireEvent.click(screen.getByRole('button', { name: i18n.t('updates.install') }));
    expect(onApply).toHaveBeenCalled();
    expect(screen.getByText(i18n.t('updates.helpApply'))).toBeInTheDocument();
    expect(
      screen.queryByText(/sudo apt-get install --only-upgrade meshloom/)
    ).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole('checkbox', { name: i18n.t('updates.autoUpdate') }));
    expect(onAutoUpdate).toHaveBeenCalledWith(true);
  });

  it('shows a real phase progress bar while applying', () => {
    render(
      <UpdateAvailableDialog
        open
        updates={updates}
        applying
        progressPercent={50}
        progressPhase="downloading"
        onClose={vi.fn()}
      />
    );

    expect(screen.getByText(i18n.t('updates.progressTitle'))).toBeInTheDocument();
    expect(screen.getByRole('progressbar')).toHaveAttribute('aria-valuenow', '50');
    expect(screen.getAllByText(i18n.t('updates.phase.downloading')).length).toBeGreaterThan(0);
    expect(screen.queryByRole('button', { name: i18n.t('updates.close') })).not.toBeInTheDocument();
  });

  it('shows a failed job error without a close-blocking overlay', () => {
    render(
      <UpdateAvailableDialog
        open
        updates={updates}
        applying={false}
        applyError="disk full"
        progressPercent={80}
        progressPhase="installing"
        onClose={vi.fn()}
      />
    );

    expect(screen.getByText(i18n.t('updates.failed'))).toBeInTheDocument();
    expect(screen.getByText('disk full')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: i18n.t('updates.close') })).toBeInTheDocument();
  });
});
